import httpx

from redrouter_benchmarks.adapters.base import (
    Content,
    Done,
    Error,
    Event,
    Finish,
    Usage,
    count,
    request,
    workload,
)
from redrouter_benchmarks.sse import ProtocolError, decode_event


class OpenAIChat:
    def build_request(
        self, provider: dict, route: dict, prompt: str, nonce: str, api_key: str
    ) -> httpx.Request:
        body = workload(route, prompt, nonce)
        if route.get("include_usage", True):
            body["stream_options"] = {"include_usage": True}
        return request(
            provider, api_key, "/chat/completions", {**body, **route.get("extra_body", {})}
        )

    def parse_event(self, data: str) -> list[Event]:
        value = decode_event(data)
        if value is None:
            return [Done()]
        if value.get("error"):
            error = value["error"]
            code = error.get("code") if isinstance(error, dict) else None
            return [Error(code if type(code) is int else None)]
        events: list[Event] = []
        usage = value.get("usage")
        if usage is not None:
            if not isinstance(usage, dict):
                raise ProtocolError
            events.append(
                Usage(
                    {
                        "prompt": count(usage.get("prompt_tokens")),
                        "cached": count(
                            (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
                        ),
                        "completion": count(usage.get("completion_tokens")),
                        "reasoning": count(
                            (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
                        ),
                    }
                )
            )
        try:
            for choice in value.get("choices", []):
                if choice.get("index", 0) != 0:
                    continue
                delta = choice.get("delta") or {}
                content = any(delta.get(k) for k in ("content", "reasoning_content", "reasoning"))
                tools = any(
                    (tool.get("function") or {}).get("arguments")
                    for tool in delta.get("tool_calls", [])
                )
                if content or tools:
                    events.append(Content(count((usage or {}).get("completion_tokens"))))
                if choice.get("finish_reason") is not None:
                    finish = choice["finish_reason"]
                    if not isinstance(finish, str) or finish not in {
                        "stop",
                        "length",
                        "tool_calls",
                        "function_call",
                    }:
                        return [*events, Error()]
                    events.append(Finish(finish))
        except (ValueError, TypeError, AttributeError, KeyError):
            return [*events, Error()]
        return events
