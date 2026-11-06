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
