import json
from pathlib import Path

import pytest

from redrouter_benchmarks.adapters import get_adapter
from redrouter_benchmarks.adapters.base import Content, Done, Finish, Usage
from redrouter_benchmarks.sse import ProtocolError, SSEParser

FIXTURES = Path(__file__).parent / "fixtures" / "sse"


def replay(protocol, wire, width):
    adapter, parser, events = get_adapter(protocol), SSEParser(), []
    for pos in range(0, len(wire), width):
        for data in parser.feed(wire[pos : pos + width]):
            events.extend(adapter.parse_event(data))
    for data in parser.feed(b"", final=True):
        events.extend(adapter.parse_event(data))
    return events


@pytest.mark.parametrize("width", [1, 7, 65536])
@pytest.mark.parametrize("protocol", ["openai-chat", "anthropic-messages"])
def test_upstream_recording(protocol, width):
    prefix = protocol.split("-")[0]
    events = replay(protocol, (FIXTURES / f"{prefix}-recorded.sse").read_bytes(), width)
    assert sum(isinstance(e, Content) for e in events) >= 2
    assert [e for e in events if isinstance(e, (Finish, Done))] == [Finish("stop"), Done()]
    if prefix == "anthropic":
        assert [e for e in events if isinstance(e, Usage)] == [
            Usage({"prompt": 11, "cached": None, "completion": 1}),
            Usage({"prompt": None, "cached": None, "completion": 6}),
        ]
    else:
        assert [e for e in events if isinstance(e, Usage)] == [
            Usage({"prompt": 14, "cached": None, "completion": 30, "reasoning": 0})
        ]


@pytest.mark.parametrize("protocol", ["openai-chat", "anthropic-messages"])
@pytest.mark.parametrize("data", ["garbage", "[]", '{"usage":[]}'])
def test_malformed_events(protocol, data):
    with pytest.raises(ProtocolError):
        get_adapter(protocol).parse_event(data)


@pytest.mark.parametrize("protocol", ["openai-chat", "anthropic-messages"])
def test_request_settings_are_catalogue_data(protocol, route):
    provider = {
        "base_url": "https://example.test/v1/",
        "protocol": protocol,
        "auth_header": "x-api-key",
        "auth_scheme": "",
        "extra_headers": {"api-version": "test"},
    }
    route = {**route, "extra_body": {"provider": {"only": ["supplier"], "allow_fallbacks": False}}}
    request = get_adapter(protocol).build_request(provider, route, "prompt", "nonce", "secret")
    assert request.method == "POST"
    assert str(request.url) == "https://example.test/v1/" + (
        "chat/completions" if protocol == "openai-chat" else "messages"
    )
    assert request.headers["x-api-key"] == "secret"
    assert request.headers["api-version"] == "test"
    assert request.headers["Accept"] == "text/event-stream"
    assert request.headers["Content-Type"] == "application/json"
    assert "authorization" not in request.headers
    body = json.loads(request.content)
    assert body["messages"] == [{"role": "user", "content": "Request nonce: nonce\nprompt"}]
    assert body["max_tokens"] == 512 and body["temperature"] == 0.5 and body["stream"]
    assert body["provider"] == route["extra_body"]["provider"]
    assert ("stream_options" in body) == (protocol == "openai-chat")
