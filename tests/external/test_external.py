import csv
import hashlib
import importlib
import io
import json
import shutil
import zipfile
from pathlib import Path

import httpx
import pytest
from jsonschema import Draft202012Validator, FormatChecker

from redrouter_benchmarks import external
from redrouter_benchmarks.cli import main

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests/fixtures/site-data"


def write_zip(path, tables):
    with zipfile.ZipFile(path, "w") as archive:
        for name, rows in tables.items():
            buffer = io.StringIO()
            writer = csv.DictWriter(buffer, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
            archive.writestr(name, buffer.getvalue())


@pytest.fixture
def archive(tmp_path, monkeypatch):
    licences = json.loads(external.LICENCES.read_text())
    for source, licence_id in {
        "by": "CC-BY-4.0",
        "sa": "CC-BY-SA-4.0",
        "zero": "CC0-1.0",
        "nc": "CC-BY-NC-4.0",
        "nd": "CC-BY-ND-4.0",
    }.items():
        licences[f"https://example.org/{source}"] = {
            "name": source,
            "licence_id": licence_id,
            "licence_url": f"https://creativecommons.org/licenses/{licence_id[3:-4].lower()}/4.0/"
            if source != "zero"
            else "https://creativecommons.org/publicdomain/zero/1.0/",
            "evidence_url": f"https://example.org/{source}/licence",
        }
    licence_file = tmp_path / "licences.json"
    licence_file.write_text(json.dumps(licences))
    monkeypatch.setattr(external, "LICENCES", licence_file)
    internal = {
        "Model version": "glm-5.3_max",
        "Best score (across scorers)": "0.75",
        "Started at": "2026-10-20T12:00:00Z",
        "Release date": "2026-08-14",
        "Log viewer": "https://logs.epoch.ai/run",
        "Benchmark version": "1.0.6",
        "id": "run-1",
    }
    source_row = {
        "Model version": "glm-5.3_max",
        "Percent correct": "62.5",
        "Source": "https://example.org/by",
        "Harness": "Example scaffold",
        "Agent": "Example agent",
        "Release date": "2026-08-14",
    }
    tables = {
        "benchmark_metadata.csv": [
            {
                "benchmark": "GPQA diamond",
                "source_file": "gpqa_diamond.csv",
                "score_column": "Best score (across scorers)",
                "scale": "1.0",
            },
            {
                "benchmark": "Example benchmark",
                "source_file": "example_external.csv",
                "score_column": "Percent correct",
                "scale": "0.01",
            },
        ],
        "gpqa_diamond.csv": [internal],
        "example_external.csv": [
            source_row | {"Source": url}
            for url in [
                "https://example.org/by",
                "https://example.org/sa",
                "https://example.org/zero",
                "https://example.org/nc",
                "https://example.org/nd",
                "https://unknown.example.org/",
                "https://artificialanalysis.ai/",
                "https://www.vals.ai/",
                "https://livebench.ai/",
            ]
        ],
    }
    path = tmp_path / "epoch.zip"
    write_zip(path, tables)
    return path, tables


def validate(data):
    schema = json.loads((ROOT / "schema/external.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(data)


def test_licences_provenance_units_and_unknown_dates(archive, tmp_path):
    path, _ = archive
    data = external.fetch_external(tmp_path / "data", path)
    validate(data)
    assert data["zip_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert data["generator_version"] == external.__version__
    assert data["counts"] == {
        "kept_epoch": 1,
        "kept_upstream": 3,
        "dropped_licence": 6,
        "dropped_model": 0,
        "dropped_format": 0,
        "dropped_score": 0,
    }
    epoch = next(row for row in data["results"] if row["source"] == external.EPOCH)
    assert epoch["run_date"] == "2026-10-20"
    assert epoch["version"] == "1.0.6"
    assert epoch["score"] == {
        "value": 0.75,
        "unit": "fraction",
        "metric": "Best score (across scorers)",
    }
    assert epoch["attribution"] == (
        f"Source: Epoch AI, CC BY 4.0, accessed {data['accessed_at'][:10]}. Adapted by RedRouter."
    )
    for row in data["results"]:
        if row["source"] != external.EPOCH:
            assert row["run_date"] is None
            assert row["version"] is None
            assert row["epoch_attribution"] == epoch["attribution"]
            assert row["score"]["value"] == 62.5 and row["score"]["unit"] == "%"
            assert (row["agent"], row["scaffold"]) == ("Example agent", "Example scaffold")
    assert external.load_external(tmp_path / "data/external.json") == data


def test_exact_models_and_effort_never_merge_checkpoints(archive, tmp_path):
    path, tables = archive
    row = tables["gpqa_diamond.csv"][0]
    accepted = sorted(external.MODEL_IDS)
    rejected = [
        "deepseek-v4.1-flash-1017-abliterated",
        "deepseek-v4.1-flash-1017_unknown_extra",
        "deepseek-v4.1-flash-1018",
        "glm-5.3-flash-heretic",
        "glm-5.3-vision",
        "qwen3.8-270b",
        "qwen3.8-max",
        "GLM 5.3",
        "glm-5.3_fast",
    ]
    tables["gpqa_diamond.csv"] = [row | {"Model version": model + "_max"} for model in accepted]
    tables["gpqa_diamond.csv"] += [row | {"Model version": model} for model in rejected]
    write_zip(path, tables)
    data = external.fetch_external(tmp_path / "data", path)
    validate(data)
    assert {r["model_id"] for r in data["results"]} == set(accepted)
    assert data["counts"]["kept_epoch"] == 5
    assert data["counts"]["dropped_model"] == len(rejected)
    assert external.match_model("qwen3.8-27b", {"glm-5.3"}) is None


def test_invalid_scores_and_unknown_file_fail_closed(archive, tmp_path):
    path, tables = archive
    row = tables["gpqa_diamond.csv"][0]
    tables["gpqa_diamond.csv"] = [
        row | {"Best score (across scorers)": value} for value in ("NaN", "inf", "")
    ]
    tables["new_external.csv"] = [tables["example_external.csv"][0]]
    write_zip(path, tables)
    data = external.fetch_external(tmp_path / "data", path)
    assert data["counts"]["dropped_score"] == 3
    assert data["counts"]["dropped_format"] == 1
    validate(data)


def test_cli_local_zip_and_invalid_zip(archive, tmp_path, capsys):
    path, _ = archive
    main(["fetch-external", "--output", str(tmp_path / "data"), "--zip", str(path)])
    validate(json.loads((tmp_path / "data/external.json").read_text()))
    assert "4 kept" in capsys.readouterr().out
    path.write_bytes(b"not a zip")
    with pytest.raises(SystemExit) as exc:
        main(["fetch-external", "--output", str(tmp_path / "bad"), "--zip", str(path)])
    assert exc.value.code == 2
    assert "Invalid Epoch ZIP" in capsys.readouterr().err
    assert not (tmp_path / "bad").exists()


@pytest.mark.parametrize("present", [False, True])
def test_every_page_has_further_reading_and_optional_results(
    archive, tmp_path, monkeypatch, present
):
    build = importlib.import_module("redrouter_benchmarks.site.build")
    root = tmp_path / "root"
    root.mkdir()
    for folder in ("site", "schema"):
        (root / folder).symlink_to(ROOT / folder, target_is_directory=True)
    (root / "METHODOLOGY.md").write_text("# Test methodology\n")
    monkeypatch.setattr(build, "resource", lambda name: root / name)
    data = tmp_path / "data"
    shutil.copytree(FIXTURE, data)
    if present:
        external.fetch_external(data, archive[0])
    output = tmp_path / "dist"
    build.build_site(data, output)
    pages = list(output.rglob("*.html"))
    assert len(pages) == 17
    for page in pages:
        content = page.read_text()
        assert 'href="https://redrouter.org/blog/benchmarks-we-dont-run"' in content
        assert content.index("Further reading") < content.index("<footer>")
        assert 'href="/external/"' in content
        assert not any(
            name in content.lower()
            for name in ("artificialanalysis", "artificial analysis", "vals", "livebench")
        )
    results = (output / "external/index.html").read_text()
    model = (output / "models/glm-5.3-flash/index.html").read_text()
    assert "Published results from other evaluators" in model
    assert "not measurements of the listed hosts" in results
    assert ("Source: Epoch AI, CC BY 4.0" in results) == present
    assert (output / "data/external.json").exists() == present


def test_site_rejects_unapproved_licence(archive, tmp_path):
    external.fetch_external(tmp_path, archive[0])
    path = tmp_path / "external.json"
    data = json.loads(path.read_text())
    data["results"][0]["licence_id"] = "CC-BY-NC-4.0"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="approved source licence"):
        external.load_external(path)


def test_download_uses_public_zip(archive, tmp_path, monkeypatch):
    payload = archive[0].read_bytes()
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(200, content=payload, request=httpx.Request("GET", url))

    monkeypatch.setattr(external.httpx, "get", get)
    data = external.fetch_external(tmp_path / "download")
    validate(data)
    assert calls == [(external.ZIP_URL, {"follow_redirects": True, "timeout": 60})]
    assert data["zip_sha256"] == hashlib.sha256(payload).hexdigest()
