import random

import pytest

from redrouter_benchmarks.sse import ProtocolError, SSEParser, decode_event


def test_arbitrary_boundaries_preserve_unicode_and_event_framing():
    wire = ': heartbeat\r\ndata: {"text":"café 🌍"}\r\n\r\ndata: [DONE]\n\n'.encode()
    expected = ['{"text":"café 🌍"}', "[DONE]"]
    for split in range(len(wire) + 1):
        parser = SSEParser()
        assert parser.feed(wire[:split]) + parser.feed(wire[split:], final=True) == expected
    for seed in range(30):
        rng = random.Random(seed)
        parser, events, pos = SSEParser(), [], 0
        while pos < len(wire):
            end = pos + rng.randint(1, 9)
            events.extend(parser.feed(wire[pos:end]))
            pos = end
        assert events + parser.feed(b"", final=True) == expected


def test_multiline_sse_and_ignored_fields():
    parser = SSEParser()
    assert parser.feed(b'event: message\rid: 42\rdata: {\rdata: "a": 1}\r\r', final=True) == [
        '{\n"a": 1}'
    ]
    assert decode_event('{\n"a": 1}') == {"a": 1}


@pytest.mark.parametrize("wire", [b"data: {}", b"data: {}\n", b"data: \xff\n\n"])
def test_truncated_or_invalid_utf8_is_protocol_error(wire):
    with pytest.raises(ProtocolError):
        SSEParser().feed(wire, final=True)


@pytest.mark.parametrize("event", ["[]", "null", "garbage", "{broken"])
def test_non_object_json_is_protocol_error(event):
    with pytest.raises(ProtocolError):
        decode_event(event)
