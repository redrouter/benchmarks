import codecs
import json
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import httpx


@dataclass(frozen=True)
class Content:
    tokens: int | None = None


@dataclass(frozen=True)
class Usage:
    counts: dict[str, int | None]


@dataclass(frozen=True)
class Finish:
    reason: str


@dataclass(frozen=True)
class Error:
    status: int | None = None


@dataclass(frozen=True)
class Done:
    pass


Event = Content | Usage | Finish | Error | Done


@runtime_checkable
class Adapter(Protocol):
    def build_request(
        self, provider: dict, route: dict, prompt: str, nonce: str, api_key: str
    ) -> httpx.Request: ...

    def parse_event(self, data: str) -> list[Event]: ...


def count(value: object) -> int | None:
    return value if type(value) is int and value >= 0 else None


def workload(route: dict, prompt: str, nonce: str) -> dict:
    return {
        "model": route["provider_model"],
        "messages": [{"role": "user", "content": f"Request nonce: {nonce}\n{prompt}"}],
        "max_tokens": 512,
        "temperature": 0.5,
        "stream": True,
    }


def request(provider: dict, api_key: str, path: str, body: dict) -> httpx.Request:
    credential = " ".join(filter(None, (provider.get("auth_scheme", "Bearer"), api_key)))
    return httpx.Request(
        "POST",
        provider["base_url"].rstrip("/") + path,
        headers={
            "Accept": "text/event-stream",
            **provider.get("extra_headers", {}),
            provider.get("auth_header", "Authorization"): credential,
        },
        json=body,
    )


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
