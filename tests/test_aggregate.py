import copy
import csv
import gzip
import json
from datetime import timedelta

import pytest

from redrouter_benchmarks.aggregate import (
    aggregate,
    history,
    load_records,
    metrics,
    percentile,
    status,
)
from redrouter_benchmarks.clock import iso
from redrouter_benchmarks.demo import DEMO_NOW


def test_interpolated_percentiles_and_minimum_samples(record):
    records = []
    for index in range(10):
        sample = copy.deepcopy(record)
        sample["timestamps"]["first_delta_ns"] = sample["timestamps"]["sent_ns"] + index * 1_000_000
        records.append(sample)
    assert metrics(records)["ttft_ms"] == {"p50": 4.5, "p90": 8.1}
    assert metrics(records[:7])["ttft_ms"] == {"p50": None, "p90": None}
    records[-1]["error_class"] = "timeout"
    assert metrics(records)["success_rate"] == 0.9
    assert metrics(records)["errors"]["timeout"] == 1
    assert metrics(records)["ttft_ms"]["p50"] == 4
    assert percentile([], 0.9) is None


def test_each_metric_needs_eight_usable_samples(record):
    records = [copy.deepcopy(record) for _ in range(8)]
    records[0]["usage"]["completion"] = None
    result = metrics(records)
    assert result["ttft_ms"]["p50"] == 1000
    assert result["output_tokens_per_second"]["p50"] is None
    assert result["end_to_end_500_ms"]["p50"] is None


@pytest.mark.parametrize(
    ("successes", "total", "hours", "expected"),
    [
        (95, 100, 1, "ok"),
        (94, 100, 1, "degraded"),
        (80, 100, 1, "degraded"),
        (79, 100, 1, "failing"),
        (7, 7, 1, "insufficient_data"),
        (8, 8, 13, "stale"),
        (7, 7, 13, "stale"),
        (100, 100, 7, "degraded"),
        (0, 0, 0, "insufficient_data"),
    ],
)
def test_status_precedence_and_boundaries(record, successes, total, hours, expected):
    samples = [
        {
            **record,
            "completed_at": iso(DEMO_NOW - timedelta(hours=hours)),
            "error_class": None if i < successes else "server_error",
        }
        for i in range(total)
    ]
    assert status(samples, samples, DEMO_NOW) == expected


def test_aggregation_excludes_future_and_window_boundary_keeps_failures(catalog, record, tmp_path):
    raw = tmp_path / "input"
    raw.mkdir()
    records = []
    for index in range(12):
        hours = 72 if index == 0 else (-1 if index == 1 else 1)
        records.append(
            {
                **record,
                "run_id": str(index),
                "started_at": iso(DEMO_NOW - timedelta(hours=hours, minutes=1)),
                "completed_at": iso(DEMO_NOW - timedelta(hours=hours)),
                "error_class": "rate_limited" if index == 2 else None,
            }
        )
    (raw / "run.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))
    output = tmp_path / "data"
    result = aggregate(raw, output, now=DEMO_NOW, catalog=catalog)
    route = next(r for r in result["routes"] if r["id"] == record["route_id"])
    assert route["metrics"]["all"]["requests"] == 10
    assert route["metrics"]["all"]["successes"] == 9
    assert route["metrics"]["all"]["errors"]["rate_limited"] == 1
    assert route["metrics"]["us-west-2"]["requests"] == 0
    assert route["status"] == "degraded"
    assert (
        route["price"]["blended_usd_per_mtok"]
        == (3 * route["price"]["input_usd_per_mtok"] + route["price"]["output_usd_per_mtok"]) / 4
    )
    archived = [
        json.loads(line) for p in (output / "raw").rglob("*.gz") for line in gzip.open(p, "rt")
    ]
    assert len(archived) == 11
    csv_rows = list(csv.DictReader((output / "summary.csv").open()))
    assert len(csv_rows) == len(result["routes"]) * 3


def test_raw_deduplication_conflicts_and_mixed_demo_rejected(catalog, record, tmp_path):
    first, second = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    first.write_text(json.dumps(record) + "\n")
    second.write_text(json.dumps(record) + "\n")
    assert len(load_records(tmp_path, catalog, DEMO_NOW)) == 1
    second.write_text(json.dumps({**record, "error_class": "timeout"}) + "\n")
    with pytest.raises(ValueError, match="Conflicting duplicate"):
        load_records(tmp_path, catalog, DEMO_NOW)
    second.write_text(json.dumps({**record, "run_id": "other", "demo": True}) + "\n")
    with pytest.raises(ValueError, match="Cannot mix"):
        load_records(tmp_path, catalog, DEMO_NOW)
    second.write_text(json.dumps({**record, "methodology_version": "2.0.0"}) + "\n")
    with pytest.raises(ValueError, match="Mixed methodology"):
        load_records(tmp_path, catalog, DEMO_NOW)


def test_history_uses_bucket_counts_and_trailing_window_percentiles(record):
    samples = []
    for run in range(3):
        completed = DEMO_NOW - timedelta(hours=run * 3 + 1)
        samples.extend({**record, "completed_at": iso(completed)} for _ in range(4))
    points = history(record["route_id"], samples, DEMO_NOW)["points"]
    assert [p["requests"] for p in points] == [4, 4, 4]
    assert [p["ttft_ms_p50"] for p in points] == [None, 1000, 1000]
