import csv
import gzip
import io
import json
import math
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from redrouter_benchmarks import METHODOLOGY_VERSION
from redrouter_benchmarks.catalog import Catalog
from redrouter_benchmarks.clock import iso, parse_time
from redrouter_benchmarks.runner import ERRORS, sample_metrics

WINDOW_HOURS = 72
REGIONS = {"us-east-1": "US East (N. Virginia)", "us-west-2": "US West (Oregon)"}
METRICS = ("ttft_ms", "output_tokens_per_second", "end_to_end_500_ms")


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    index = (len(values) - 1) * q
    low, high = math.floor(index), math.ceil(index)
    return round(values[low] + (values[high] - values[low]) * (index - low), 6)


def metrics(records: list[dict]) -> dict:
    successes = [r for r in records if r["error_class"] is None]
    result = {
        "requests": len(records),
        "successes": len(successes),
        "success_rate": len(successes) / len(records) if records else None,
        "errors": {kind: sum(r["error_class"] == kind for r in records) for kind in ERRORS},
    }
    samples = [sample_metrics(r) for r in successes]
    for name in METRICS:
        values = [s[name] for s in samples if s[name] is not None]
        if len(successes) < 8 or len(values) < 8:
            values = []
        result[name] = {"p50": percentile(values, 0.5), "p90": percentile(values, 0.9)}
    return result


def status(records: list[dict], window: list[dict], now: datetime) -> str:
    if records and max(parse_time(r["completed_at"]) for r in records) < now - timedelta(hours=12):
        return "stale"
    if len(window) < 8:
        return "insufficient_data"
    successes = [r for r in window if r["error_class"] is None]
    rate = len(successes) / len(window)
    if rate < 0.8:
        return "failing"
    if rate < 0.95 or not any(
        parse_time(r["completed_at"]) >= now - timedelta(hours=6) for r in successes
    ):
        return "degraded"
    return "ok"


def load_records(root: Path, catalog: Catalog, now: datetime) -> list[dict]:
    if not root.is_dir():
        raise FileNotFoundError(f"Raw input directory does not exist: {root}")
    records = {}
    paths = sorted({*root.rglob("*.jsonl"), *root.rglob("*.jsonl.gz")})
    for path in paths:
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rt", encoding="utf-8") as handle:
            for number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                    if record["methodology_version"] != METHODOLOGY_VERSION:
                        raise ValueError("Mixed methodology versions")
                    if record["route_id"] not in catalog.routes:
                        raise ValueError("Unknown route")
                    if record["error_class"] not in (*ERRORS, None):
                        raise ValueError("Unknown error class")
                    if record["region"] not in REGIONS:
                        raise ValueError("Unknown region")
                    if (
                        parse_time(record["completed_at"]) > now
                        or parse_time(record["started_at"]) > now
                    ):
                        continue
                    key = (
                        record["run_id"],
                        record["region"],
                        record["route_id"],
                        record["stratum"],
                    )
                    if key in records and records[key] != record:
                        raise ValueError("Conflicting duplicate sample")
                    records[key] = record
                except (KeyError, ValueError, TypeError) as exc:
                    raise ValueError(f"Invalid record at {path}:{number}: {exc}") from exc
    result = sorted(
        records.values(),
        key=lambda r: (r["started_at"], r["region"], r["route_id"], r["stratum"], r["run_id"]),
    )
    if len({r.get("demo", False) for r in result}) > 1:
        raise ValueError("Cannot mix demonstration and real observations")
    return result


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def write_raw(records: list[dict], output: Path) -> None:
    groups = defaultdict(list)
    for record in records:
        day = parse_time(record["started_at"]).date().isoformat()
        groups[(day, record["region"])].append(record)
    for (day, region), samples in sorted(groups.items()):
        destination = output / "raw" / day / f"{region}.jsonl.gz"
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("wb") as raw:
            with gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as gz:
                with io.TextIOWrapper(gz, encoding="utf-8", newline="\n") as handle:
                    for record in samples:
                        handle.write(json.dumps(record, sort_keys=True, allow_nan=False) + "\n")


def history(route_id: str, records: list[dict], now: datetime) -> dict:
    buckets = defaultdict(list)
    by_region = defaultdict(list)
    for record in records:
        completed = parse_time(record["completed_at"])
        bucket = completed.replace(hour=completed.hour // 3 * 3, minute=0, second=0, microsecond=0)
        buckets[(bucket, record["region"])].append(record)
        by_region[record["region"]].append(record)
    points = []
    for (bucket, region), samples in sorted(buckets.items()):
        end = min(now, bucket + timedelta(hours=3))
        rolling = [
            r
            for r in by_region[region]
            if end - timedelta(hours=WINDOW_HOURS) < parse_time(r["completed_at"]) <= end
        ]
        m = metrics(rolling)
        points.append(
            {
                "t": iso(bucket),
                "region": region,
                "requests": len(samples),
                "successes": sum(r["error_class"] is None for r in samples),
                **{f"{name}_p50": m[name]["p50"] for name in METRICS},
            }
        )
    return {"schema_version": 1, "route_id": route_id, "bucket_hours": 3, "points": points}


def aggregate(
    input_dir: Path, output: Path, *, now: datetime | None = None, catalog: Catalog | None = None
) -> dict:
    now = now or datetime.now(UTC)
    catalog = catalog or Catalog.load()
    records = load_records(input_dir, catalog, now)
    route_records = defaultdict(list)
    for record in records:
        route_records[record["route_id"]].append(record)
    result = {
        "schema_version": 1,
        "methodology_version": METHODOLOGY_VERSION,
        "generated_at": iso(now),
        "window_hours": WINDOW_HOURS,
        "demo": any(r.get("demo", False) for r in records),
        "regions": [{"id": key, "label": label} for key, label in REGIONS.items()],
        "providers": [
            {k: p[k] for k in ("id", "name", "url", "logo")} for p in catalog.providers.values()
        ],
        "models": list(catalog.models.values()),
        "routes": [],
    }
    csv_rows = []
    for route_id, route in catalog.routes.items():
        samples = route_records[route_id]
        if not route["enabled"] and not samples:
            continue
        window = [
            r
            for r in samples
            if now - timedelta(hours=WINDOW_HOURS) < parse_time(r["completed_at"]) <= now
        ]
        last_success = max(
            (parse_time(r["completed_at"]) for r in samples if r["error_class"] is None),
            default=None,
        )
        price = route["price"]
        entry = {
            **{
                key: route[key]
                for key in ("id", "provider_id", "model_id", "provider_model", "modification")
            },
            **{key: route.get(key) for key in ("upstream", "declared_precision", "context_tokens")},
            "price": {
                **price,
                "checked_at": str(price["checked_at"]),
                "cached_input_usd_per_mtok": price.get("cached_input_usd_per_mtok"),
                "blended_usd_per_mtok": (
                    3 * price["input_usd_per_mtok"] + price["output_usd_per_mtok"]
                )
                / 4,
            },
            "status": status(samples, window, now),
            "last_success_at": iso(last_success) if last_success else None,
            "history": f"routes/{route_id}.json",
            "metrics": {"all": metrics(window)},
        }
        for region in REGIONS:
            entry["metrics"][region] = metrics([r for r in window if r["region"] == region])
        result["routes"].append(entry)
        write_json(output / entry["history"], history(route_id, samples, now))
        for scope, m in entry["metrics"].items():
            scoped = samples if scope == "all" else [r for r in samples if r["region"] == scope]
            scoped_window = (
                window if scope == "all" else [r for r in window if r["region"] == scope]
            )
            csv_rows.append(
                {
                    "route_id": route_id,
                    "region": scope,
                    "methodology_version": METHODOLOGY_VERSION,
                    "generated_at": iso(now),
                    "window_hours": WINDOW_HOURS,
                    "demo": result["demo"],
                    "status": status(scoped, scoped_window, now),
                    "requests": m["requests"],
                    "successes": m["successes"],
                    "success_rate": m["success_rate"],
                    **{f"{name}_{p}": m[name][p] for name in METRICS for p in ("p50", "p90")},
                    **entry["price"],
                    **{f"errors_{kind}": m["errors"][kind] for kind in ERRORS},
                }
            )
    write_raw(records, output)
    write_json(output / "summary.json", result)
    if csv_rows:
        with (output / "summary.csv").open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(csv_rows[0]))
            writer.writeheader()
            writer.writerows(csv_rows)
    return result
