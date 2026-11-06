from collections.abc import Callable
from functools import cache
from importlib.metadata import EntryPoint, entry_points

from redrouter_benchmarks.adapters.anthropic_messages import AnthropicMessages
from redrouter_benchmarks.adapters.base import Adapter
from redrouter_benchmarks.adapters.openai_chat import OpenAIChat

ENTRY_POINT_GROUP = "redrouter_benchmarks.adapters"


@cache
def registry() -> dict[str, Callable[[], Adapter] | EntryPoint]:
    adapters: dict[str, Callable[[], Adapter] | EntryPoint] = {
        "openai-chat": OpenAIChat,
        "anthropic-messages": AnthropicMessages,
    }
    for entry in sorted(entry_points(group=ENTRY_POINT_GROUP), key=lambda e: e.name):
        if entry.name in adapters:
            raise ValueError(f"Duplicate adapter protocol: {entry.name}")
        adapters[entry.name] = entry
    return adapters


def get_adapter(protocol: str) -> Adapter:
    adapters = registry()
    if protocol not in adapters:
        raise ValueError(
            f"Unknown adapter protocol {protocol!r}; available: {', '.join(sorted(adapters))}"
        )
    factory = adapters[protocol]
    if isinstance(factory, EntryPoint):
        factory = factory.load()
    adapter = factory()
    if not isinstance(adapter, Adapter):
        raise ValueError(f"Adapter {protocol!r} must implement build_request and parse_event")
    return adapter
