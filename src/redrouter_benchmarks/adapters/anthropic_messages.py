import httpx

from redrouter_benchmarks.adapters.base import (
    Content,
    Done,
    Error,
    Event,
    Finish,
    ProtocolError,
    Usage,
    count,
    decode_event,
    request,
    workload,
)

FINISH_REASONS = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "max_tokens": "length",
    "tool_use": "tool_calls",
}


def normalize_usage(value: dict) -> Usage:
    if not isinstance(value, dict):
        raise ProtocolError
    prompt = count(value.get("input_tokens"))
    cached = count(value.get("cache_read_input_tokens"))
    created = count(value.get("cache_creation_input_tokens"))
    if prompt is not None:
        prompt += (cached or 0) + (created or 0)
    return Usage(
        {"prompt": prompt, "cached": cached, "completion": count(value.get("output_tokens"))}
    )


class AnthropicMessages:
    def __init__(self):
        self.finish_reason: str | None = None

    def build_request(
        self, provider: dict, route: dict, prompt: str, nonce: str, api_key: str
    ) -> httpx.Request:
        body = {**workload(route, prompt, nonce), **route.get("extra_body", {})}
        return request(provider, api_key, "/messages", body)

    def parse_event(self, data: str) -> list[Event]:
        value = decode_event(data)
        if value is None or not isinstance(value.get("type"), str):
            raise ProtocolError
        match value["type"]:
            case "message_start":
                return [normalize_usage(value["message"]["usage"])]
            case "content_block_delta":
                delta = value["delta"]
                field = {
                    "text_delta": "text",
                    "thinking_delta": "thinking",
                    "input_json_delta": "partial_json",
                }.get(delta["type"])
                return [Content()] if field and delta.get(field) else []
            case "message_delta":
                events: list[Event] = [normalize_usage(value["usage"])] if "usage" in value else []
                reason = value["delta"].get("stop_reason")
                if reason is not None:
                    if not isinstance(reason, str) or reason not in FINISH_REASONS:
                        return [*events, Error()]
                    self.finish_reason = FINISH_REASONS[reason]
                return events
            case "message_stop":
                if self.finish_reason is None:
                    raise ProtocolError
                return [Finish(self.finish_reason), Done()]
            case "error":
                error = value.get("error")
                status = error.get("code") if isinstance(error, dict) else None
                return [Error(status if type(status) is int else None)]
            case _:
                return []
