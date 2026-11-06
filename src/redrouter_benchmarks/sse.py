import codecs
import json


class ProtocolError(Exception):
    pass


class SSEParser:
    def __init__(self):
        self.decoder = codecs.getincrementaldecoder("utf-8")("strict")
        self.pending = ""
        self.data = []

    def feed(self, chunk: bytes, *, final: bool = False) -> list[str]:
        try:
            self.pending += self.decoder.decode(chunk, final=final)
        except UnicodeDecodeError as exc:
            raise ProtocolError from exc
        events = []
        while True:
            positions = [p for p in (self.pending.find("\r"), self.pending.find("\n")) if p >= 0]
            if not positions:
                break
            pos = min(positions)
            if self.pending[pos:] == "\r" and not final:
                break
            width = 2 if self.pending[pos : pos + 2] == "\r\n" else 1
            line, self.pending = self.pending[:pos], self.pending[pos + width :]
            if not line:
                if self.data:
                    events.append("\n".join(self.data))
                    self.data = []
            elif line == "data" or line.startswith("data:"):
                self.data.append(line[5:].removeprefix(" ") if line.startswith("data:") else "")
        if len(self.pending) + sum(map(len, self.data)) > 1_048_576:
            raise ProtocolError
        if final and (self.pending or self.data):
            raise ProtocolError
        return events


def decode_event(event: str) -> dict | None:
    if event == "[DONE]":
        return None
    try:
        value = json.loads(event)
    except (ValueError, TypeError) as exc:
        raise ProtocolError from exc
    if not isinstance(value, dict):
        raise ProtocolError
    return value
