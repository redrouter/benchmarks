import asyncio
import json
import random
from pathlib import Path

import httpx
import pytest

from redrouter_benchmarks.adapters import get_adapter, registry
from redrouter_benchmarks.adapters.base import Content, Done, Finish, Usage
from redrouter_benchmarks.catalog import resource_dir
from redrouter_benchmarks.clock import SimulatedClock
from redrouter_benchmarks.demo import DEMO_NOW, sse
from redrouter_benchmarks.runner import (
    STRATA,
    cost_usd,
    measure,
    run_suite,
    sample_metrics,
    schedule,
)


class Stream(httpx.AsyncByteStream):
    def __init__(self, clock, chunks):
        self.clock, self.chunks = clock, chunks

    async def __aiter__(self):
        for delay, chunk in self.chunks:
            await self.clock.sleep(delay)
            if isinstance(chunk, Exception):
                raise chunk
            yield chunk


def observation(catalog, route, chunks=(), status=200, header_delay=0, headers=None, provider=None):
    async def run():
        clock = SimulatedClock(DEMO_NOW)

        async def handler(request):
            await clock.sleep(header_delay)
            return httpx.Response(
                status,
                headers={"content-type": "text/event-stream", **(headers or {})},
                stream=Stream(clock, chunks),
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await measure(
                client,
                provider or catalog.providers[route["provider_id"]],
                route,
                "prose-1000",
                clock=clock,
                run_id="run",
                region="us-east-1",
                seed=7,
                nonce="fixed",
                api_key="never-log-this-secret",
            )

    return asyncio.run(run())


def event(delta=None, finish=None, usage=None):
    result = {"choices": [{"index": 0, "delta": delta or {}, "finish_reason": finish}]}
    if usage is not None:
        result["usage"] = usage
    return sse(result)


def test_timing_ignores_role_and_usage_only_events(catalog, route):
    record = observation(
        catalog,
        route,
        [
            (0.2, event({"role": "assistant"})),
            (0.8, event({"reasoning_content": "private-output"}, usage={"completion_tokens": 2})),
            (10, event({"content": "private-output"}, "length")),
            (5, sse({"choices": [], "usage": {"prompt_tokens": 1000, "completion_tokens": 502}})),
            (0, sse("[DONE]")),
        ],
        headers={"set-cookie": "secret", "authorization": "secret", "retry-after": "2"},
    )
    assert record["error_class"] is None
    assert record["timestamps"]["first_byte_ns"] == 200_000_000
    assert record["timestamps"]["first_delta_ns"] == 1_000_000_000
    assert record["timestamps"]["last_delta_ns"] == 11_000_000_000
    assert sample_metrics(record) == {
        "ttft_ms": 1000,
        "output_tokens_per_second": 50,
        "end_to_end_500_ms": 11000,
    }
    encoded = json.dumps(record)
    assert all(
        secret not in encoded
        for secret in ("private-output", "never-log", "cookie", "authorization")
    )
    assert record["response_headers"]["retry-after"] == "2"


@pytest.mark.parametrize(
    "delta",
    [{"content": "x"}, {"reasoning": "x"}, {"tool_calls": [{"function": {"arguments": "{}"}}]}],
)
def test_each_content_kind_starts_ttft(catalog, route, delta):
    record = observation(catalog, route, [(1, event(delta, "stop")), (0, sse("[DONE]"))])
    assert sample_metrics(record)["ttft_ms"] == 1000


def test_missing_usage_preserves_ttft_only(catalog, route):
    chunks = [(1, event({"content": "x"})), (2, event({"content": "y"}, "stop"))]
    record = observation(catalog, route, chunks)
    assert record["error_class"] is None
    assert sample_metrics(record) == {
        "ttft_ms": 1000,
        "output_tokens_per_second": None,
        "end_to_end_500_ms": None,
    }


def test_unreported_first_chunk_counts_as_one_token(catalog, route):
    usage = {"prompt_tokens": 1000, "completion_tokens": 512}
    chunks = [(1, event({"content": "x"})), (2, event({"content": "y"}, "stop", usage))]
    metrics = sample_metrics(observation(catalog, route, chunks))
    assert metrics["output_tokens_per_second"] == 255.5
    assert metrics["end_to_end_500_ms"] == 1000 + 500 / 255.5 * 1000


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (429, "rate_limited"),
        (500, "server_error"),
        (503, "server_error"),
        (400, "client_error"),
        (401, "client_error"),
        (302, "client_error"),
    ],
)
def test_http_failures_are_retained_without_reading_body(catalog, route, status, error):
    record = observation(catalog, route, [(0, b"never-log-response")], status=status)
    assert record["http_status"] == status
    assert record["error_class"] == error
    assert "never-log-response" not in json.dumps(record)


@pytest.mark.parametrize(
    ("chunks", "error"),
    [
        ([(61, event({"content": "x"}))], "timeout"),
        ([(0, httpx.ConnectError("secret"))], "network"),
        ([(0, b"data: malformed\n\n")], "protocol"),
        ([(0, sse("[DONE]"))], "empty_output"),
        ([(0, event({"role": "assistant"}))], "empty_output"),
        ([(0, event({"content": "partial"}))], "protocol"),
    ],
)
def test_stream_failures(catalog, route, chunks, error):
    record = observation(catalog, route, chunks)
    assert record["error_class"] == error
    assert "secret" not in json.dumps(record)


def test_first_byte_deadline_includes_wait_for_headers(catalog, route):
    record = observation(catalog, route, [(31, event({"content": "x"}))], header_delay=30)
    assert record["error_class"] == "timeout"
    assert record["timestamps"]["completed_ns"] == 60_000_000_000


def test_total_deadline_survives_heartbeat_traffic(catalog, route):
    record = observation(catalog, route, [(40, b": heartbeat\n\n")] * 6)
    assert record["error_class"] == "timeout"
    assert record["timestamps"]["completed_ns"] == 180_000_000_000


def test_coalesced_single_time_has_no_speed(record):
    record["timestamps"]["last_delta_ns"] = record["timestamps"]["first_delta_ns"]
    assert sample_metrics(record)["output_tokens_per_second"] is None


@pytest.mark.parametrize("finish", ["untrusted-output", {"text": "untrusted-output"}])
def test_invalid_finish_reason_cannot_leak_response_text(catalog, route, finish):
    record = observation(catalog, route, [(0, event({"content": "x"}, finish))])
    assert record["error_class"] == "protocol"
    assert "untrusted-output" not in json.dumps(record)


def test_failed_partial_stream_keeps_timing_but_cannot_contribute_metrics(catalog, route):
    record = observation(
        catalog,
        route,
        [
            (1, event({"content": "x"}, usage={"completion_tokens": 1})),
            (2, httpx.ReadError("private-error-body")),
        ],
    )
    assert record["error_class"] == "network"
    assert record["timestamps"]["first_delta_ns"] == 1_000_000_000
    assert all(value is None for value in sample_metrics(record).values())
    assert "private-error-body" not in json.dumps(record)


def test_cache_and_reasoning_cost_use_reported_completion_total():
    usage = {"prompt": 1000, "cached": 200, "completion": 500, "reasoning": 250}
    price = {"input_usd_per_mtok": 1, "cached_input_usd_per_mtok": 0.1, "output_usd_per_mtok": 2}
    assert cost_usd(usage, price) == pytest.approx(0.00182)
    assert cost_usd({**usage, "completion": None}, price) is None


def test_workload_and_supplier_pins_are_preserved(catalog):
    for route in catalog.routes.values():
        for stratum in STRATA:
            provider = catalog.providers[route["provider_id"]]
            prompt = (resource_dir("prompts") / "1.0.0" / f"{stratum}.txt").read_text()
            request = get_adapter(provider["protocol"]).build_request(
                provider, route, prompt, "unique-nonce", "test-key"
            )
            body = json.loads(request.content)
            assert body["messages"][0]["content"].startswith("Request nonce: unique-nonce\n")
            assert body["max_tokens"] == 512 and body["temperature"] == 0.5 and body["stream"]
            if route["provider_id"].startswith("nanogpt-"):
                assert body["provider"]["only"] == [route["upstream"]]
                assert body["provider"]["allow_fallbacks"] is False


def test_scheduler_is_seeded_interleaves_gateways_and_has_four_strata(catalog):
    routes = [r for r in catalog.routes.values() if r["enabled"]]
    tasks = schedule(catalog, routes, random.Random(7))
    assert tasks == schedule(catalog, routes, random.Random(7))
    assert tasks != schedule(catalog, routes, random.Random(8))
    assert len(tasks) == 4 * len(routes)
    for route in routes:
        assert {s for r, s in tasks if r["id"] == route["id"]} == set(STRATA)
    gateways = {catalog.providers[r["provider_id"]]["base_url"] for r in routes}
    assert len(
        {catalog.providers[r["provider_id"]]["base_url"] for r, _ in tasks[: len(gateways)]}
    ) == len(gateways)


def test_suite_has_no_retries_and_writes_failures(catalog, route, tmp_path):
    calls = []

    async def handler(request):
        calls.append(request)
        return httpx.Response(429)

    destination = asyncio.run(
        run_suite(
            catalog,
            region="us-east-1",
            output=tmp_path,
            routes=[route],
            seed=7,
            clock=SimulatedClock(DEMO_NOW),
            demo=True,
            transport=httpx.MockTransport(handler),
        )
    )
    records = [json.loads(line) for line in destination.read_text().splitlines()]
    assert len(calls) == len(records) == 4
    assert all(r["error_class"] == "rate_limited" for r in records)
    assert len({json.loads(call.content)["messages"][0]["content"] for call in calls}) == 4


FIXTURES = Path(__file__).parent / "adapters" / "fixtures"


def fixture_chunks(protocol, kind):
    prefix = protocol.split("-")[0]
    wire = (FIXTURES / f"{prefix}-{kind}.sse").read_bytes()
    return [(1, frame + b"\n\n") for frame in wire.split(b"\n\n") if frame.strip()]


@pytest.mark.parametrize(
    ("protocol", "first", "last", "completed", "tokens"),
    [("openai-chat", 3, 5, 7, 2), ("anthropic-messages", 4, 11, 15, None)],
)
def test_normalized_fixtures_keep_runner_timing_usage_and_cost(
    catalog, route, protocol, first, last, completed, tokens
):
    route["price"] = {
        "input_usd_per_mtok": 1,
        "cached_input_usd_per_mtok": 0.1,
        "output_usd_per_mtok": 2,
    }
    provider = {**catalog.providers[route["provider_id"]], "protocol": protocol}
    record = observation(catalog, route, fixture_chunks(protocol, "complete"), provider=provider)
    assert record["error_class"] is None and record["finish_reason"] == "tool_calls"
    assert record["timestamps"]["first_delta_ns"] == first * 1_000_000_000
    assert record["timestamps"]["last_delta_ns"] == last * 1_000_000_000
    assert record["timestamps"]["completed_ns"] == completed * 1_000_000_000
    assert record["content_delta_count"] == 3 and record["first_chunk_tokens"] == tokens
    assert record["usage"]["prompt"] == 1000 and record["usage"]["completion"] == 502
    assert record["cost_usd"] == pytest.approx(0.001824) and not record["cost_estimated"]
    metrics = sample_metrics(record)
    assert metrics["ttft_ms"] == first * 1000
    assert metrics["output_tokens_per_second"] == (502 - (tokens or 1)) / (last - first)
    assert all(secret not in json.dumps(record) for secret in ("sample", "never-log", "synthetic"))


@pytest.mark.parametrize(
    ("protocol", "error", "first", "completion"),
    [("openai-chat", "rate_limited", 1, 2), ("anthropic-messages", "protocol", 2, 1)],
)
def test_error_fixtures_retain_partial_evidence(catalog, route, protocol, error, first, completion):
    provider = {**catalog.providers[route["provider_id"]], "protocol": protocol}
    record = observation(catalog, route, fixture_chunks(protocol, "error"), provider=provider)
    assert record["error_class"] == error
    assert record["timestamps"]["first_delta_ns"] == first * 1_000_000_000
    assert record["usage"]["prompt"] == 1000 and record["usage"]["completion"] == completion
    assert record["cost_usd"] is not None
    assert all(v is None for v in sample_metrics(record).values())
    assert "synthetic-error-body" not in json.dumps(record)


@pytest.mark.parametrize("failure", ["eof", "framing", "network", "timeout"])
def test_anthropic_partial_stream_cannot_succeed(catalog, route, failure):
    provider = {**catalog.providers[route["provider_id"]], "protocol": "anthropic-messages"}
    chunks = fixture_chunks("anthropic-messages", "complete")[:-1]
    if failure == "framing":
        chunks.append((1, b'data: {"type":"message_stop"}'))
    elif failure == "network":
        chunks.append((1, httpx.ReadError("private-error-body")))
    elif failure == "timeout":
        chunks.append((61, b": heartbeat\n\n"))
    record = observation(catalog, route, chunks, provider=provider)
    assert record["error_class"] == {"eof": "protocol", "framing": "protocol"}.get(failure, failure)
    assert record["usage"]["completion"] == 502
    assert record["timestamps"]["last_delta_ns"] == 11_000_000_000
    assert record["content_delta_count"] == 3
    assert "private-error-body" not in json.dumps(record)


def test_anthropic_missing_usage_preserves_ttft_only(catalog, route):
    provider = {**catalog.providers[route["provider_id"]], "protocol": "anthropic-messages"}
    chunks = [
        (1, sse({"type": "message_start", "message": {"usage": {}}})),
        (
            1,
            sse({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "sample"}}),
        ),
        (1, sse({"type": "message_delta", "delta": {"stop_reason": "end_turn"}})),
        (1, sse({"type": "message_stop"})),
    ]
    record = observation(catalog, route, chunks, provider=provider)
    assert record["error_class"] is None
    assert record["usage"] == dict.fromkeys(("prompt", "cached", "completion", "reasoning"))
    assert record["cost_usd"] is None
    assert sample_metrics(record) == {
        "ttft_ms": 2000,
        "output_tokens_per_second": None,
        "end_to_end_500_ms": None,
    }


@pytest.mark.parametrize("protocol", ["openai-chat", "anthropic-messages"])
def test_invalid_finish_keeps_partial_timing(catalog, route, protocol):
    provider = {**catalog.providers[route["provider_id"]], "protocol": protocol}
    chunks = fixture_chunks(protocol, "complete")
    chunks = [
        (delay, chunk.replace(b'"tool_calls"', b'"invalid"').replace(b'"tool_use"', b'"invalid"'))
        for delay, chunk in chunks
    ]
    record = observation(catalog, route, chunks, provider=provider)
    assert record["error_class"] == "protocol"
    assert record["timestamps"]["first_delta_ns"] is not None
    assert record["content_delta_count"] >= 2
    assert record["finish_reason"] is None


def test_runner_accepts_registered_protocol_without_wire_knowledge(catalog, route, monkeypatch):
    class CustomAdapter:
        def build_request(self, provider, route, prompt, nonce, api_key):
            return httpx.Request("POST", "https://example.test/custom")

        def parse_event(self, data):
            return [Content(2), Usage({"prompt": 1000, "completion": 502}), Finish("stop"), Done()]

    monkeypatch.setitem(registry(), "custom", CustomAdapter)
    provider = {**catalog.providers[route["provider_id"]], "protocol": "custom"}
    record = observation(catalog, route, [(1, b"data: custom-wire\n\n")], provider=provider)
    assert record["error_class"] is None and record["finish_reason"] == "stop"
    assert record["usage"]["completion"] == 502 and record["first_chunk_tokens"] == 2
    assert sample_metrics(record)["ttft_ms"] == 1000


@pytest.mark.parametrize("choice", [None, "malformed", {"delta": ["malformed"]}])
def test_malformed_later_choice_keeps_already_received_usage_and_content(catalog, route, choice):
    wire = sse(
        {
            "choices": [{"index": 0, "delta": {"content": "sample"}}, choice],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 2},
        }
    )
    record = observation(catalog, route, [(1, wire)])
    assert record["error_class"] == "protocol"
    assert record["timestamps"]["first_delta_ns"] == 1_000_000_000
    assert record["content_delta_count"] == 1 and record["usage"]["completion"] == 2
