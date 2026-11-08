import csv
import io
import json
import shutil
import zipfile

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from redrouter_benchmarks import external
from redrouter_benchmarks.catalog import resource
from redrouter_benchmarks.site import build_site


def test_import_filters_and_preserves_provenance(tmp_path, site_input):
    site_input = shutil.copytree(site_input, tmp_path / "data")
    tables = {
        "benchmark_metadata.csv": [
            {
                "benchmark": "GPQA diamond",
                "source_file": "gpqa_diamond.csv",
                "score_column": "Best score (across scorers)",
                "scale": "1.0",
            },
        ],
        "gpqa_diamond.csv": [
            {
                "Model version": model,
                "Best score (across scorers)": score,
                "Started at": "2026-10-20T12:00:00Z",
                "Benchmark version": "1.0.6",
            }
            for model, score in [
                ("glm-5.3_max", "0.75"),
                ("glm-5.3-vision", "0.5"),
                ("glm-5.3_max", "NaN"),
            ]
        ],
        "webdev_arena_external.csv": [
            {"Model version": "glm-5.3_max", "Arena Score": "1500", "Source": source}
            for source in ["https://arena.ai/leaderboard", "https://unknown.example.org/"]
        ],
    }
    path = tmp_path / "epoch.zip"
    with zipfile.ZipFile(path, "w") as archive:
        for name, rows in tables.items():
            buffer = io.StringIO()
            writer = csv.DictWriter(buffer, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
            archive.writestr(name, buffer.getvalue())
    data = external.fetch_external(site_input, path)
    schema = json.loads((resource("schema") / "external.schema.json").read_text())
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(data)
    assert data["counts"] == {
        "kept_epoch": 1,
        "kept_upstream": 1,
        "dropped_licence": 1,
        "dropped_model": 1,
        "dropped_format": 0,
        "dropped_score": 1,
    }
    epoch, arena = data["results"]
    assert epoch["model_version"] == "glm-5.3_max"
    assert epoch["run_date"] == "2026-10-20" and epoch["version"] == "1.0.6"
    assert epoch["score"]["value"] == 0.75 and epoch["score"]["unit"] == "fraction"
    assert arena["score"]["unit"] == "points" and arena["run_date"] is None
    assert arena["epoch_attribution"] == epoch["attribution"]
    assert external.load_external(site_input / "external.json") == data
    build_site(site_input, tmp_path / "dist")
    assert "Source: Epoch AI, CC BY 4.0" in (tmp_path / "dist/external/index.html").read_text()
    data["results"][0]["licence_id"] = "CC-BY-NC-4.0"
    (site_input / "external.json").write_text(json.dumps(data))
    with pytest.raises(ValueError, match="approved source licence"):
        external.load_external(site_input / "external.json")


@pytest.mark.parametrize("licence", ["CC-BY-NC-4.0", "CC-BY-ND-4.0", "unknown"])
def test_licences_without_commercial_reuse_are_rejected(licence):
    assert not external.allowed_licence(
        {
            "name": "Example",
            "licence_id": licence,
            "licence_url": "https://creativecommons.org/licenses/by/4.0/",
            "evidence_url": "https://example.org/licence",
        }
    )
