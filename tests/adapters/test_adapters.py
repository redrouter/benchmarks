import json
from pathlib import Path

import pytest

from redrouter_benchmarks.adapters import get_adapter
from redrouter_benchmarks.adapters.base import Content, Done, Error, Finish, Usage
from redrouter_benchmarks.sse import ProtocolError, SSEParser

FIXTURES = Path(__file__).parent / "fixtures"


def replay(protocol, wire, width):
    adapter, parser, events = get_adapter(protocol), SSEParser(), []
    for pos in range(0, len(wire), width):
        for data in parser.feed(wire[pos : pos + width]):
            events.extend(adapter.parse_event(data))
    for data in parser.feed(b"", final=True):
        events.extend(adapter.parse_event(data))
    return events


@pytest.mark.parametrize("width", [1, 7, 101, 65536])
@pytest.mark.parametrize("protocol", ["openai-chat", "anthropic-messages"])
def test_complete_wire_fixture(protocol, width):
    prefix = protocol.split("-")[0]
    events = replay(protocol, (FIXTURES / f"{prefix}-complete.sse").read_bytes(), width)
    assert [e for e in events if isinstance(e, Content)] == [
        Content(2 if prefix == "openai" else None),
        Content(),
        Content(),
    ]
    counts = {}
    for event in events:
        if isinstance(event, Usage):
            counts.update({k: v for k, v in event.counts.items() if v is not None})
    assert counts == {
        "prompt": 1000,
        "cached": 200,
        "completion": 502,
        **({"reasoning": 20} if prefix == "openai" else {}),
    }
    assert [e for e in events if isinstance(e, (Finish, Done))] == [Finish("tool_calls"), Done()]
    assert "sample" not in repr(events)


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
def test_error_fixture(protocol):
    prefix = protocol.split("-")[0]
    events = replay(protocol, (FIXTURES / f"{prefix}-error.sse").read_bytes(), 7)
    assert events[-1] == Error(429 if prefix == "openai" else None)
    assert any(isinstance(e, Content) for e in events)
    assert not any(isinstance(e, Done) for e in events)
    assert "synthetic-error-body" not in repr(events)


@pytest.mark.parametrize("protocol", ["openai-chat", "anthropic-messages"])
@pytest.mark.parametrize("data", ["garbage", "[]", '{"usage":[]}'])
def test_malformed_events(protocol, data):
    with pytest.raises(ProtocolError):
        get_adapter(protocol).parse_event(data)


@pytest.mark.parametrize(
    ("reason", "normalized"),
    [
        ("end_turn", "stop"),
        ("stop_sequence", "stop"),
        ("max_tokens", "length"),
        ("tool_use", "tool_calls"),
    ],
)
def test_anthropic_finish_waits_for_message_stop(reason, normalized):
    adapter = get_adapter("anthropic-messages")
    assert (
        adapter.parse_event(json.dumps({"type": "message_delta", "delta": {"stop_reason": reason}}))
        == []
    )
    assert adapter.parse_event('{"type":"message_stop"}') == [Finish(normalized), Done()]


def test_anthropic_signature_empty_and_unknown_events_are_not_content():
    adapter = get_adapter("anthropic-messages")
    for value in (
        {"type": "ping"},
        {"type": "future_event"},
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": ""}},
        {
            "type": "content_block_delta",
            "delta": {"type": "signature_delta", "signature": "sample"},
        },
    ):
        assert adapter.parse_event(json.dumps(value)) == []


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


def test_openai_usage_can_be_disabled(route, catalog):
    provider = catalog.providers[route["provider_id"]]
    request = get_adapter("openai-chat").build_request(
        provider, {**route, "include_usage": False}, "prompt", "nonce", "secret"
    )
    assert request.headers["Authorization"] == "Bearer secret"
    assert "stream_options" not in json.loads(request.content)
