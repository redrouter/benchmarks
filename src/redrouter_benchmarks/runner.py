import json
import os
import random
import uuid
from collections import defaultdict, deque
from pathlib import Path

import httpx

from redrouter_benchmarks import METHODOLOGY_VERSION, __version__
from redrouter_benchmarks.catalog import Catalog, resource_dir
from redrouter_benchmarks.clock import Clock, iso
from redrouter_benchmarks.sse import ProtocolError, SSEParser, decode_event

STRATA = ("prose-1000", "code-1000", "prose-8000", "code-8000")
ERRORS = (
    "timeout",
    "rate_limited",
    "server_error",
    "client_error",
    "network",
    "empty_output",
    "protocol",
)
HEADER_ALLOWLIST = {
    "content-type",
    "retry-after",
    "x-ratelimit-limit-requests",
    "x-ratelimit-remaining-requests",
}
TOTAL_TIMEOUT = 180.0
STALL_TIMEOUT = 60.0


def classify_status(status: int) -> str | None:
    if status == 429:
        return "rate_limited"
    if status >= 500:
        return "server_error"
    if not 200 <= status < 300:
        return "client_error"
    return None


def count(value):
    return value if type(value) is int and value >= 0 else None


def normalize_usage(usage: dict) -> dict:
    return {
        "prompt": count(usage.get("prompt_tokens")),
        "cached": count((usage.get("prompt_tokens_details") or {}).get("cached_tokens")),
        "completion": count(usage.get("completion_tokens")),
        "reasoning": count((usage.get("completion_tokens_details") or {}).get("reasoning_tokens")),
    }


def cost_usd(usage: dict, price: dict) -> float | None:
    prompt, completion, cached = (usage.get(k) for k in ("prompt", "completion", "cached"))
    if prompt is None or completion is None:
        return None
    cached = min(cached or 0, prompt)
    cache_price = price.get("cached_input_usd_per_mtok", price["input_usd_per_mtok"])
    return (
        (prompt - cached) * price["input_usd_per_mtok"]
        + cached * cache_price
        + completion * price["output_usd_per_mtok"]
    ) / 1_000_000


def sample_metrics(record: dict) -> dict:
    t = record["timestamps"]
    first, last, start = (t.get(k) for k in ("first_delta_ns", "last_delta_ns", "sent_ns"))
    result = {"ttft_ms": None, "output_tokens_per_second": None, "end_to_end_500_ms": None}
    if record["error_class"] is not None or first is None:
        return result
    result["ttft_ms"] = (first - start) / 1_000_000
    completion = record["usage"]["completion"]
    first_tokens = record.get("first_chunk_tokens") or 1
    if (
        completion is not None
        and completion > first_tokens
        and record.get("content_delta_count", 0) >= 2
        and last is not None
        and last > first
    ):
        speed = (completion - first_tokens) / ((last - first) / 1_000_000_000)
        result["output_tokens_per_second"] = speed
        result["end_to_end_500_ms"] = result["ttft_ms"] + 500 / speed * 1000
    return result


def payload(route: dict, stratum: str, nonce: str) -> dict:
    prompt = (resource_dir("prompts") / "1.0.0" / f"{stratum}.txt").read_text()
    body = {
        "model": route["provider_model"],
        "messages": [{"role": "user", "content": f"Request nonce: {nonce}\n{prompt}"}],
        "max_tokens": 512,
        "temperature": 0.5,
        "stream": True,
    }
    if route.get("include_usage", True):
        body["stream_options"] = {"include_usage": True}
    return {**body, **route.get("extra_body", {})}


async def measure(
    client: httpx.AsyncClient,
    provider: dict,
    route: dict,
    stratum: str,
    *,
    clock: Clock,
    run_id: str,
    region: str,
    seed: int,
    nonce: str,
    api_key: str,
    demo: bool = False,
) -> dict:
    request = client.build_request(
        "POST",
        provider["base_url"].rstrip("/") + "/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Accept": "text/event-stream"},
        json=payload(route, stratum, nonce),
    )
    start = clock.monotonic_ns()
    record = {
        "run_id": run_id,
        "methodology_version": METHODOLOGY_VERSION,
        "harness_version": __version__,
        "region": region,
        "route_id": route["id"],
        "started_at": iso(clock.now()),
        "completed_at": None,
        "timestamps": {
            "sent_ns": start,
            "headers_ns": None,
            "first_byte_ns": None,
            "last_byte_ns": None,
            "first_delta_ns": None,
            "last_delta_ns": None,
            "completed_ns": None,
        },
        "http_status": None,
        "error_class": None,
        "usage": dict.fromkeys(("prompt", "cached", "completion", "reasoning")),
        "finish_reason": None,
        "stratum": stratum,
        "cost_usd": None,
        "cost_estimated": True,
        "price": route["price"],
        "response_headers": {},
        "first_chunk_tokens": None,
        "content_delta_count": 0,
        "seed": seed,
        "demo": demo,
    }
    stamps = record["timestamps"]
    response = None
    parser = SSEParser()
    done = False

    def consume(event: str, timestamp: int):
        nonlocal done
        if done:
            raise ProtocolError
        data = decode_event(event)
        if data is None:
            done = True
            return
        if data.get("error"):
            error = data["error"]
            status = error.get("code") if isinstance(error, dict) else None
            record["error_class"] = classify_status(status) if type(status) is int else "protocol"
            raise ProtocolError
        usage = data.get("usage")
        if usage is not None:
            if not isinstance(usage, dict):
                raise ProtocolError
            normalized = normalize_usage(usage)
            record["usage"].update({k: v for k, v in normalized.items() if v is not None})
        for choice in data.get("choices", []):
            if choice.get("index", 0) != 0:
                continue
            delta = choice.get("delta") or {}
            content = any(delta.get(k) for k in ("content", "reasoning_content", "reasoning"))
            tools = any(
                (tool.get("function") or {}).get("arguments")
                for tool in delta.get("tool_calls", [])
            )
            if content or tools:
                if stamps["first_delta_ns"] is None:
                    stamps["first_delta_ns"] = timestamp
                    record["first_chunk_tokens"] = count((usage or {}).get("completion_tokens"))
                stamps["last_delta_ns"] = timestamp
                record["content_delta_count"] += 1
            if choice.get("finish_reason") is not None:
                finish = choice["finish_reason"]
                if not isinstance(finish, str) or finish not in {
                    "stop",
                    "length",
                    "tool_calls",
                    "function_call",
                }:
                    raise ProtocolError
                record["finish_reason"] = finish

    try:
        response = await clock.wait_for(client.send(request, stream=True), STALL_TIMEOUT)
        stamps["headers_ns"] = clock.monotonic_ns()
        record["http_status"] = response.status_code
        record["response_headers"] = {
            key: value for key, value in response.headers.items() if key in HEADER_ALLOWLIST
        }
        record["error_class"] = classify_status(response.status_code)
        if record["error_class"] is None:
            if "text/event-stream" not in response.headers.get("content-type", ""):
                raise ProtocolError
            iterator = response.aiter_bytes().__aiter__()
            while not done:
                now = clock.monotonic_ns()
                remaining = TOTAL_TIMEOUT - (now - start) / 1_000_000_000
                stall = STALL_TIMEOUT
                if stamps["first_byte_ns"] is None:
                    stall -= (now - start) / 1_000_000_000
                if min(remaining, stall) <= 0:
                    raise TimeoutError
                try:
                    chunk = await clock.wait_for(anext(iterator), min(remaining, stall))
                except StopAsyncIteration:
                    for event in parser.feed(b"", final=True):
                        consume(event, clock.monotonic_ns())
                    break
                if not chunk:
                    continue
                timestamp = clock.monotonic_ns()
                if stamps["first_byte_ns"] is None:
                    stamps["first_byte_ns"] = timestamp
                stamps["last_byte_ns"] = timestamp
                for event in parser.feed(chunk):
                    consume(event, timestamp)
            if stamps["first_delta_ns"] is None:
                record["error_class"] = "empty_output"
            elif record["finish_reason"] not in {"stop", "length", "tool_calls", "function_call"}:
                raise ProtocolError
    except (TimeoutError, httpx.TimeoutException):
        record["error_class"] = "timeout"
    except httpx.TransportError:
        record["error_class"] = "network"
    except (ProtocolError, ValueError, TypeError, AttributeError, KeyError):
        record["error_class"] = record["error_class"] or "protocol"
    finally:
        if response is not None:
            try:
                await response.aclose()
            except httpx.TransportError:
                record["error_class"] = record["error_class"] or "network"
        stamps["completed_ns"] = clock.monotonic_ns()
        record["completed_at"] = iso(clock.now())
    record["cost_usd"] = cost_usd(record["usage"], route["price"])
    record["cost_estimated"] = record["usage"]["cached"] is None or (
        record["usage"]["cached"] > 0 and "cached_input_usd_per_mtok" not in route["price"]
    )
    return record


def schedule(catalog: Catalog, routes: list[dict], rng: random.Random) -> list[tuple[dict, str]]:
    groups = defaultdict(list)
    for route in routes:
        p = catalog.providers[route["provider_id"]]
        groups[(p["base_url"], p["api_key_env"])].extend((route, stratum) for stratum in STRATA)
    queues = {}
    for key, tasks in sorted(groups.items()):
        rng.shuffle(tasks)
        queues[key] = deque(tasks)
    result = []
    while queues:
        keys = sorted(queues)
        rng.shuffle(keys)
        for key in keys:
            result.append(queues[key].popleft())
            if not queues[key]:
                del queues[key]
    return result


async def run_suite(
    catalog: Catalog,
    *,
    region: str,
    output: Path,
    routes: list[dict] | None = None,
    seed: int | None = None,
    clock: Clock | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    run_id: str | None = None,
    demo: bool = False,
) -> Path:
    clock = clock or Clock()
    seed = seed if seed is not None else random.SystemRandom().getrandbits(64)
    rng = random.Random(seed)
    routes = routes if routes is not None else [r for r in catalog.routes.values() if r["enabled"]]
    if not routes:
        raise ValueError("No routes selected")
    keys = {}
    for route in routes:
        p = catalog.providers[route["provider_id"]]
        key = "demo" if demo else os.environ.get(p["api_key_env"])
        if not key:
            raise ValueError(f"Missing environment variable: {p['api_key_env']}")
        keys[p["id"]] = key
    run_id = run_id or f"{clock.now():%Y%m%dT%H%M%S}-{region}-{uuid.uuid4().hex[:12]}"
    output.mkdir(parents=True, exist_ok=True)
    destination = output / f"{run_id}.jsonl"
    async with httpx.AsyncClient(
        transport=transport or httpx.AsyncHTTPTransport(retries=0),
        timeout=httpx.Timeout(STALL_TIMEOUT),
        follow_redirects=False,
        trust_env=False,
    ) as client:
        with destination.open("x") as handle:
            for route, stratum in schedule(catalog, routes, rng):
                await clock.sleep(rng.uniform(0, 2))
                record = await measure(
                    client,
                    catalog.providers[route["provider_id"]],
                    route,
                    stratum,
                    clock=clock,
                    run_id=run_id,
                    region=region,
                    seed=seed,
                    nonce=f"{rng.getrandbits(128):032x}",
                    api_key=keys[route["provider_id"]],
                    demo=demo,
                )
                handle.write(json.dumps(record, sort_keys=True, allow_nan=False) + "\n")
                handle.flush()
    return destination
