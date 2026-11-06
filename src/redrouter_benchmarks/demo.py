import hashlib
import json
import math
import random
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from redrouter_benchmarks.aggregate import REGIONS, aggregate
from redrouter_benchmarks.catalog import Catalog
from redrouter_benchmarks.clock import SimulatedClock
from redrouter_benchmarks.runner import run_suite

DEMO_NOW = datetime(2026, 11, 5, tzinfo=UTC)
DEGRADING_ROUTE = "nanogpt-heabsy/deepseek-v4.1-flash-1017/uncensored"
STALE_ROUTE = "nanogpt-sable/deepseek-v4.1-flash-1017/heretic"


def sse(value: dict | str) -> bytes:
    return f"data: {value if isinstance(value, str) else json.dumps(value)}\n\n".encode()


class DemoStream(httpx.AsyncByteStream):
    def __init__(self, clock: SimulatedClock, ttft: float, speed: float, usage: dict):
        self.clock, self.ttft, self.speed, self.usage = clock, ttft, speed, usage

    async def __aiter__(self):
        await self.clock.sleep(self.ttft)
        yield sse(
            {
                "choices": [{"index": 0, "delta": {"content": "sample"}}],
                "usage": {"completion_tokens": 1},
            }
        )
        await self.clock.sleep(255 / self.speed)
        yield sse({"choices": [{"index": 0, "delta": {"content": "sample"}}]})
        await self.clock.sleep(256 / self.speed)
        yield sse(
            {"choices": [{"index": 0, "delta": {"content": "sample"}, "finish_reason": "length"}]}
        )
        await self.clock.sleep(0.015)
        yield sse({"choices": [], "usage": self.usage}) + sse("[DONE]")


class DemoTransport(httpx.AsyncBaseTransport):
    def __init__(
        self, catalog: Catalog, clock: SimulatedClock, seed: int, region: str, start: datetime
    ):
        self.clock, self.rng, self.region = clock, random.Random(seed), region
        self.catalog, self.start = catalog, start

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        pin = body.get("provider", {}).get("only")
        route = next(
            r
            for r in self.catalog.routes.values()
            if r["provider_model"] == body["model"]
            and self.catalog.providers[r["provider_id"]]["base_url"] in str(request.url)
            and (not pin or r["provider_id"] == f"nanogpt-{pin[0]}")
        )
        identity = int.from_bytes(hashlib.sha256(route["id"].encode()).digest()[:4])
        long_prompt = len(body["messages"][0]["content"]) > 10_000
        is_code = "Python module" in body["messages"][0]["content"]
        degraded = (
            route["id"] == DEGRADING_ROUTE
            and self.clock.now() > self.start + (DEMO_NOW - self.start) / 2
        )
        await self.clock.sleep(self.rng.uniform(0.025, 0.090))
        failure = self.rng.random()
        if failure < (0.04 if degraded else 0.008):
            await self.clock.sleep(61)
            raise httpx.ReadTimeout("simulated")
        if failure < (0.09 if degraded else 0.018):
            return httpx.Response(429, headers={"retry-after": "1"})
        if failure < (0.14 if degraded else 0.026):
            return httpx.Response(503)
        variation = self.rng.lognormvariate(0, 0.18)
        ttft = (0.18 + (identity % 1700) / 1000 + (0.6 if long_prompt else 0)) * variation
        ttft += 0.12 if self.region == "us-west-2" else 0
        speed = (40 + (identity % 230)) / variation * (1.15 if is_code else 1)
        ttft *= 2.8 if degraded else 1
        speed *= 0.4 if degraded else 1
        ttft *= 1 + 0.10 * math.sin(self.clock.now().hour / 24 * 2 * math.pi)
        usage = {
            "prompt_tokens": self.rng.randint(7600, 8400)
            if long_prompt
            else self.rng.randint(950, 1100),
            "completion_tokens": 512,
            "prompt_tokens_details": {"cached_tokens": 0},
            "completion_tokens_details": {"reasoning_tokens": 0},
        }
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=DemoStream(self.clock, ttft, speed, usage),
        )


async def demo_data(output: Path, days: int = 14, seed: int = 7) -> dict:
    if days < 1:
        raise ValueError("days must be positive")
    catalog = Catalog.load()
    start = DEMO_NOW - timedelta(days=days)
    routes = [r for r in catalog.routes.values() if r["enabled"]]
    with tempfile.TemporaryDirectory(prefix="redrouter-demo-") as temporary:
        raw = Path(temporary)
        for run in range(days * 8):
            for region_index, region in enumerate(REGIONS):
                scheduled = start + timedelta(hours=run * 3, minutes=region_index * 5)
                clock = SimulatedClock(scheduled)
                selected = [
                    r
                    for r in routes
                    if not (r["id"] == STALE_ROUTE and scheduled >= DEMO_NOW - timedelta(hours=48))
                ]
                run_seed = seed * 1_000_003 + run * 2 + region_index
                await run_suite(
                    catalog,
                    region=region,
                    output=raw,
                    routes=selected,
                    seed=run_seed,
                    clock=clock,
                    run_id=f"demo-{scheduled:%Y%m%dT%H%M%S}-{region}",
                    demo=True,
                    transport=DemoTransport(catalog, clock, run_seed, region, start),
                )
        return aggregate(raw, output, now=DEMO_NOW, catalog=catalog)
