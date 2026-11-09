import csv
import json
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from redrouter_benchmarks.aggregate import METRICS

IDENTITY_COLUMNS = (
    "route_id",
    "model_id",
    "model",
    "provider_id",
    "provider",
    "provider_model",
    "upstream",
    "modification",
    "region",
    "generated_at",
    "methodology_version",
    "window_hours",
    "demo",
)
SUMMARY_COLUMNS = (
    *IDENTITY_COLUMNS,
    "status",
    "requests",
    "successes",
    "success_rate",
    *(f"{metric}_{percentile}" for metric in METRICS for percentile in ("p50", "p90")),
    "blended_usd_per_mtok",
)
HISTORY_COLUMNS = (
    *IDENTITY_COLUMNS,
    "t",
    "bucket_hours",
    "requests",
    "successes",
    "success_rate",
    *(f"{metric}_p50" for metric in METRICS),
)


def write_csv(path: Path, columns: tuple, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def size_category(rows: int) -> str:
    for limit, label in (
        (1_000, "n<1K"),
        (10_000, "1K<n<10K"),
        (100_000, "10K<n<100K"),
        (1_000_000, "100K<n<1M"),
        (10_000_000, "1M<n<10M"),
        (100_000_000, "10M<n<100M"),
        (1_000_000_000, "100M<n<1B"),
    ):
        if rows < limit:
            return label
    return "n>1B"


def model_leaders(summary: dict, rows: list[dict]) -> list[dict]:
    leaders = []
    for model in sorted(summary["models"], key=lambda model: model["id"]):
        eligible = [row for row in rows if row["model_id"] == model["id"] and row["requests"] >= 8]
        fastest = max(
            (row for row in eligible if row["output_tokens_per_second_p50"] is not None),
            key=lambda row: row["output_tokens_per_second_p50"],
            default=None,
        )
        cheapest = min(
            (row for row in eligible if row["blended_usd_per_mtok"] is not None),
            key=lambda row: row["blended_usd_per_mtok"],
            default=None,
        )
        if any(row["model_id"] == model["id"] for row in rows):
            leaders.append({"model": model["name"], "fastest": fastest, "cheapest": cheapest})
    return leaders


def export_dataset(data: Path, output: Path) -> None:
    summary = json.loads((data / "summary.json").read_text(encoding="utf-8"))
    models = {model["id"]: model["name"] for model in summary["models"]}
    providers = {provider["id"]: provider["name"] for provider in summary["providers"]}
    rows, points = [], []
    for route in sorted(summary["routes"], key=lambda route: route["id"]):
        identity = {
            "route_id": route["id"],
            "model_id": route["model_id"],
            "model": models[route["model_id"]],
            "provider_id": route["provider_id"],
            "provider": providers[route["provider_id"]],
            **{key: route[key] for key in ("provider_model", "upstream", "modification")},
            **{
                key: summary[key]
                for key in ("generated_at", "methodology_version", "window_hours", "demo")
            },
        }
        metrics = route["metrics"]["all"]
        rows.append(
            {
                **identity,
                "region": "all",
                "status": route["status"],
                **{key: metrics[key] for key in ("requests", "successes", "success_rate")},
                **{
                    f"{metric}_{percentile}": metrics[metric][percentile]
                    for metric in METRICS
                    for percentile in ("p50", "p90")
                },
                "blended_usd_per_mtok": route["price"]["blended_usd_per_mtok"],
            }
        )
        history_path = (data / route["history"]).resolve()
        if not history_path.is_relative_to(data.resolve()):
            raise ValueError(f"History path leaves data directory: {route['history']}")
        history = json.loads(history_path.read_text(encoding="utf-8"))
        for point in sorted(history["points"], key=lambda point: (point["region"], point["t"])):
            points.append(
                {
                    **identity,
                    **point,
                    "bucket_hours": history["bucket_hours"],
                    "success_rate": (
                        point["successes"] / point["requests"] if point["requests"] else None
                    ),
                }
            )
    output.mkdir(parents=True, exist_ok=True)
    write_csv(output / "summary.csv", SUMMARY_COLUMNS, rows)
    write_csv(output / "history.csv", HISTORY_COLUMNS, points)
    environment = Environment(
        loader=FileSystemLoader(Path(__file__).parent),
        autoescape=False,
        keep_trailing_newline=True,
    )
    environment.filters["table_cell"] = lambda value: (
        str(value).replace("|", "\\|").replace("\n", " ")
    )
    card = environment.get_template("dataset_card.md").render(
        summary=summary,
        leaders=model_leaders(summary, rows),
        size_category=size_category(len(rows) + len(points)),
    )
    (output / "README.md").write_text(card, encoding="utf-8")
