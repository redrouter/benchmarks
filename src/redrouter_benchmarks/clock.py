import asyncio
import time
from datetime import UTC, datetime, timedelta


def iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timestamp must include a timezone")
    return parsed.astimezone(UTC)


class Clock:
    def now(self) -> datetime:
        return datetime.now(UTC)

    def monotonic_ns(self) -> int:
        return time.monotonic_ns()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)

    async def wait_for(self, awaitable, seconds: float):
        return await asyncio.wait_for(awaitable, max(0, seconds))


class SimulatedClock(Clock):
    def __init__(self, start: datetime):
        self.start = start
        self.elapsed_ns = 0

    def now(self) -> datetime:
        return self.start + timedelta(microseconds=self.elapsed_ns / 1000)

    def monotonic_ns(self) -> int:
        return self.elapsed_ns

    async def sleep(self, seconds: float) -> None:
        self.elapsed_ns += round(seconds * 1_000_000_000)

    async def wait_for(self, awaitable, seconds: float):
        start = self.elapsed_ns
        try:
            result = await awaitable
        except TimeoutError:
            self.elapsed_ns = min(self.elapsed_ns, start + round(seconds * 1_000_000_000))
            raise
        if self.elapsed_ns - start > round(seconds * 1_000_000_000):
            self.elapsed_ns = start + round(seconds * 1_000_000_000)
            raise TimeoutError
        return result
