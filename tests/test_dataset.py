import csv
import json
import shutil

import pytest
import yaml

from redrouter_benchmarks.cli import main
from redrouter_benchmarks.dataset import export_dataset


def read_csv(path):
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_export_csvs_match_source_and_card_configs(site_input, tmp_path):
    output = tmp_path / "dataset"
    main(["export-dataset", "--data", str(site_input), "--output", str(output)])
    source = json.loads((site_input / "summary.json").read_text())
    rows = read_csv(output / "summary.csv")
    points = read_csv(output / "history.csv")
    assert len(rows) == len(source["routes"])
    assert len({row["route_id"] for row in rows}) == len(rows)
    for row in rows:
        route = next(route for route in source["routes"] if route["id"] == row["route_id"])
        assert row["generated_at"] == source["generated_at"]
        assert row["model_id"] == route["model_id"]
        assert row["provider_id"] == route["provider_id"]
        assert row["modification"] == route["modification"]
        assert row["upstream"] == (route["upstream"] or "")
        assert row["region"] == "all"
        assert float(row["success_rate"]) == route["metrics"]["all"]["success_rate"]
        assert float(row["blended_usd_per_mtok"]) == route["price"]["blended_usd_per_mtok"]
        for metric in ("ttft_ms", "output_tokens_per_second", "end_to_end_500_ms"):
            for percentile in ("p50", "p90"):
                value = route["metrics"]["all"][metric][percentile]
                assert row[f"{metric}_{percentile}"] == (str(value) if value is not None else "")
    expected = []
    for route in source["routes"]:
        history = json.loads((site_input / route["history"]).read_text())
        for point in history["points"]:
            row = next(
                row
                for row in points
                if (row["route_id"], row["region"], row["t"])
                == (route["id"], point["region"], point["t"])
            )
            for key, value in point.items():
                assert row[key] == (str(value) if value is not None else "")
            assert int(row["bucket_hours"]) == history["bucket_hours"]
            expected.append(row)
    assert len(points) == len(expected)
    card = (output / "README.md").read_text()
    metadata = yaml.safe_load(card.split("---", 2)[1])
    assert metadata["license"] == "cc-by-4.0"
    assert metadata["task_categories"] == ["text-generation"]
    assert metadata["configs"] == [
        {
            "config_name": "summary",
            "default": True,
            "data_files": [{"split": "summary", "path": "summary.csv"}],
        },
        {"config_name": "history", "data_files": [{"split": "history", "path": "history.csv"}]},
    ]
    assert "| GLM 5.3 Flash | nanogpt/glm-5.3-flash/abliterated | 187 |" in card
    assert "explabs/glm-5.3-flash/uncensored | 0.535 |" in card
    assert "author = {RedRouter}" in card
    assert "year = {2026}" in card


def test_export_is_deterministic_with_reordered_inputs(site_input, tmp_path):
    data = tmp_path / "data"
    shutil.copytree(site_input, data)
    first, second = tmp_path / "first", tmp_path / "second"
    export_dataset(data, first)
    summary = json.loads((data / "summary.json").read_text())
    for key in ("routes", "models", "providers"):
        summary[key].reverse()
    (data / "summary.json").write_text(json.dumps(summary))
    for route in summary["routes"]:
        path = data / route["history"]
        history = json.loads(path.read_text())
        history["points"].reverse()
        path.write_text(json.dumps(history))
    export_dataset(data, second)
    export_dataset(data, second)
    assert {path.name: path.read_bytes() for path in first.iterdir()} == {
        path.name: path.read_bytes() for path in second.iterdir()
    }


def test_export_empty_data_and_missing_metrics(site_input, tmp_path):
    data = tmp_path / "data"
    shutil.copytree(site_input, data)
    summary = json.loads((data / "summary.json").read_text())
    for route in summary["routes"]:
        route["metrics"]["all"]["requests"] = 0
        for metric in ("ttft_ms", "output_tokens_per_second", "end_to_end_500_ms"):
            route["metrics"]["all"][metric] = {"p50": None, "p90": None}
    (data / "summary.json").write_text(json.dumps(summary))
    output = tmp_path / "dataset"
    export_dataset(data, output)
    assert all(row["ttft_ms_p50"] == "" for row in read_csv(output / "summary.csv"))
    assert (
        "| Unavailable | Unavailable | Unavailable | Unavailable |"
        in (output / "README.md").read_text()
    )
    summary["routes"] = []
    (data / "summary.json").write_text(json.dumps(summary))
    export_dataset(data, output)
    assert read_csv(output / "summary.csv") == read_csv(output / "history.csv") == []


def test_history_path_cannot_leave_data_directory(site_input, tmp_path):
    data = tmp_path / "data"
    shutil.copytree(site_input, data)
    summary = json.loads((data / "summary.json").read_text())
    summary["routes"][0]["history"] = "../outside.json"
    (data / "summary.json").write_text(json.dumps(summary))
    with pytest.raises(ValueError, match="leaves data directory"):
        export_dataset(data, tmp_path / "dataset")
