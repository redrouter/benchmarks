import math
import re
import tomllib
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

from redrouter_benchmarks.adapters import registry

SLUG = re.compile(r"[a-z0-9][a-z0-9.-]*\Z")
MODIFICATIONS = {"none", "abliterated", "uncensored", "heretic"}


def resource(name: str) -> Path:
    if name == "schema":
        return resource("catalog") / "schema"
    checkout = Path(__file__).resolve().parents[2] / name
    return checkout if checkout.exists() else Path(__file__).parent / f"_{name}"


def https_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme == "https" and bool(parsed.netloc) and not parsed.username


@dataclass(frozen=True)
class Catalog:
    providers: dict[str, dict]
    models: dict[str, dict]
    routes: dict[str, dict]

    @classmethod
    def load(cls, root: Path | None = None) -> "Catalog":
        root = root or resource("catalog")
        adapters = registry()
        data = tomllib.loads((root / "catalog.toml").read_text())
        providers = {}
        for identity, settings in sorted(data["providers"].items()):
            p = {"id": identity, **data["defaults"], **settings}
            if p["id"] != identity or not SLUG.fullmatch(identity):
                raise ValueError(f"Invalid provider identity: {identity}")
            if p["protocol"] not in adapters:
                raise ValueError(
                    f"Unknown adapter protocol {p['protocol']!r} in {identity}; "
                    f"available: {', '.join(sorted(adapters))}"
                )
            if not all(https_url(p[k]) for k in ("url", "base_url", "terms_url")):
                raise ValueError(f"Invalid provider URL: {identity}")
            logos = [p["logo"], *([p["gateway_logo"]] if "gateway_logo" in p else [])]
            if any(Path(logo).name != logo for logo in logos) or not p["api_key_env"]:
                raise ValueError(f"Invalid provider metadata: {identity}")
            providers[p["id"]] = p
        models = {m["id"]: m for m in data["models"]}
        routes = {}
        for route_id, settings in sorted(
            data["routes"].items(), key=lambda item: item[0].split("/")
        ):
            provider_id, model_id, modification = route_id.split("/")
            r = {**settings, "model_id": model_id, "modification": modification}
            if (
                provider_id not in providers
                or model_id not in models
                or not SLUG.fullmatch(model_id)
                or modification not in MODIFICATIONS
                or not isinstance(r["enabled"], bool)
                or not r["provider_model"]
            ):
                raise ValueError(f"Invalid route identity: {route_id}")
            price = r.get("price", {})
            if r["enabled"] or price:
                for key in ("input_usd_per_mtok", "output_usd_per_mtok"):
                    value = price.get(key)
                    if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                        raise ValueError(f"Invalid price: {route_id}")
                cached = price.get("cached_input_usd_per_mtok")
                if cached is not None and (not math.isfinite(cached) or cached < 0):
                    raise ValueError(f"Invalid cache price: {route_id}")
                if not https_url(price.get("source_url", "")):
                    raise ValueError(f"Missing price source: {route_id}")
                date.fromisoformat(str(price["checked_at"]))
                price["checked_at"] = str(price["checked_at"])
            extra = r.get("extra_body", {})
            if set(extra) & {"model", "messages", "max_tokens", "stream", "temperature"}:
                raise ValueError(f"Route overrides workload: {route_id}")
            selection = extra.get("provider", {})
            if provider_id.startswith("nanogpt-") and (
                selection.get("only") != [provider_id.removeprefix("nanogpt-")]
                or selection.get("allow_fallbacks") is not False
            ):
                raise ValueError(f"Missing hard supplier pin: {route_id}")
            if provider_id == "openrouter" and (
                len(selection.get("order", [])) != 1
                or selection.get("allow_fallbacks") is not False
            ):
                raise ValueError(f"Missing control supplier pin: {route_id}")
            routes[route_id] = {**r, "id": route_id, "provider_id": provider_id}
        if not providers or not routes:
            raise ValueError("Empty catalogue")
        return cls(providers, models, routes)
