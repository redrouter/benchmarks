import csv
import hashlib
import io
import json
import math
import re
import zipfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import httpx

from redrouter_benchmarks import __version__
from redrouter_benchmarks.catalog import https_url

EPOCH = "https://epoch.ai/benchmarks"
ZIP_URL = "https://epoch.ai/data/benchmark_data.zip"
LICENCES = Path(__file__).with_name("licences.json")
MODEL_IDS = {
    "deepseek-v4.1-flash",
    "deepseek-v4.1-flash-1017",
    "glm-5.3",
    "glm-5.3-flash",
    "qwen3.8-27b",
}
EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh", "max", "unknown"}
INTERNAL = {
    "gpqa_diamond.csv": "GPQA diamond",
    "math_level_5.csv": "MATH level 5",
    "swe_bench_verified.csv": "SWE-bench Verified",
    "otis_mock_aime_2024_2025.csv": "OTIS Mock AIME 2024-2025",
    "frontiermath.csv": "FrontierMath-2025-02-28-Private",
    "frontiermath_tier_4.csv": "FrontierMath-Tier-4-2025-07-01-Private",
    "frontiermath_tiers_1_3_v2.csv": "FrontierMath-Tiers-1-3-v2-Private",
    "frontiermath_tier_4_v2.csv": "FrontierMath-Tier-4-v2-Private",
    "frontiermath_erdos.csv": "FrontierMath-Erdos",
    "ebr_bench.csv": "EBR-bench",
    "simpleqa_verified.csv": "SimpleQA Verified",
    "chess_puzzles.csv": "Chess Puzzles",
    "mystery_game_puzzles.csv": "Mystery Game Puzzles",
    "mirrorcode.csv": "MirrorCode",
    "furniture_assembly.csv": "Furniture Assembly",
    "spireclimb.csv": "SpireClimb",
    "spireclimb_sts1_a20_heart.csv": "SpireClimb (Slay the Spire 1, Ascension 20 Heart)",
    "spireclimb_sts2_a10.csv": "SpireClimb (Slay the Spire 2, Ascension 10)",
}


def licence_label(licence_id: str) -> str:
    return " ".join(licence_id.replace("CC-", "CC ", 1).rsplit("-", 1))


def allowed_licence(entry: dict) -> bool:
    match = re.fullmatch(r"CC-(BY|BY-SA)-(1\.0|2\.0|2\.5|3\.0|4\.0)", entry.get("licence_id", ""))
    expected = (
        f"https://creativecommons.org/licenses/{match[1].lower()}/{match[2]}/"
        if match
        else "https://creativecommons.org/publicdomain/zero/1.0/"
        if entry.get("licence_id") == "CC0-1.0"
        else None
    )
    return bool(
        expected
        and entry.get("licence_url") == expected
        and entry.get("name")
        and https_url(entry.get("evidence_url", ""))
    )


def match_model(identifier: str, models: set[str]) -> str | None:
    model, separator, effort = identifier.partition("_")
    return model if model in models and (not separator or effort in EFFORTS) else None


def csv_rows(archive: zipfile.ZipFile, name: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(archive.read(name).decode("utf-8-sig"))))


def run_date(value: str) -> str | None:
    if not value:
        return None
    return datetime.fromisoformat(value).date().isoformat()


def fetch_external(output: Path, zip_path: Path | None = None) -> dict:
    accessed_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    if zip_path is None:
        try:
            response = httpx.get(ZIP_URL, follow_redirects=True, timeout=60)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ValueError(f"Could not download Epoch data: {exc}") from exc
        data = response.content
    else:
        data = zip_path.read_bytes()
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ValueError("Invalid Epoch ZIP") from exc
    licences = json.loads(LICENCES.read_text())
    counts = Counter(
        dict.fromkeys(
            (
                "kept_epoch",
                "kept_upstream",
                "dropped_licence",
                "dropped_model",
                "dropped_format",
                "dropped_score",
            ),
            0,
        )
    )
    results = []
    with archive:
        if "benchmark_metadata.csv" not in archive.namelist():
            raise ValueError("Epoch ZIP is missing benchmark_metadata.csv")
        metadata = {
            r["source_file"]: r
            for r in csv_rows(archive, "benchmark_metadata.csv")
            if r["source_file"]
        }
        for filename in sorted(archive.namelist()):
            if (
                "/" in filename
                or not filename.endswith(".csv")
                or filename.endswith("_metadata.csv")
            ):
                continue
            internal = filename in INTERNAL
            description = metadata.get(filename)
            # These published run files have no source_file entry in Epoch's metadata.
            if description is None and internal:
                description = {
                    "benchmark": INTERNAL[filename],
                    "score_column": "Best score (across scorers)",
                    "scale": "1.0",
                }
            if filename == "webdev_arena_external.csv":
                description = {
                    "benchmark": "WebDev Arena",
                    "score_column": "Arena Score",
                    "scale": "points",
                }
            for row in csv_rows(archive, filename):
                source = EPOCH if internal else row.get("Source", "").strip()
                licence = licences.get(source, {})
                if not allowed_licence(licence):
                    counts["dropped_licence"] += 1
                    continue
                identifier = row.get("Model version", "").strip()
                model = match_model(identifier, MODEL_IDS)
                if model is None:
                    counts["dropped_model"] += 1
                    continue
                if description is None:
                    counts["dropped_format"] += 1
                    continue
                column = description["score_column"]
                try:
                    score = float(row.get(column, ""))
                    if not math.isfinite(score):
                        raise ValueError("Non-finite score")
                    date = run_date(row.get("Started at", "") or row.get("Run date", ""))
                except ValueError:
                    counts["dropped_score"] += 1
                    continue
                unit = (
                    "points"
                    if description["scale"] == "points"
                    else "%"
                    if description["scale"] == "0.01" or "%" in column
                    else "fraction"
                    if description["scale"] == "1.0"
                    else "points"
                )
                attribution = (
                    f"Source: {licence['name']}, {licence_label(licence['licence_id'])}, "
                    f"accessed {accessed_at[:10]}. Adapted by RedRouter."
                )
                log_url = row.get("Log viewer", "")
                results.append(
                    {
                        "benchmark": description["benchmark"],
                        "version": row.get("Benchmark version") or None,
                        "model_id": model,
                        "model_version": identifier,
                        "score": {"value": score, "unit": unit, "metric": column},
                        "evaluator": licence["name"],
                        "agent": row.get("Agent") or None,
                        "scaffold": row.get("Harness") or None,
                        "run_date": date,
                        "source": source,
                        "source_url": log_url if internal and https_url(log_url) else source,
                        "source_file": filename,
                        "run_id": row.get("id") or None,
                        "licence_id": licence["licence_id"],
                        "licence_url": licence["licence_url"],
                        "licence_evidence_url": licence["evidence_url"],
                        "attribution": attribution,
                        "epoch_attribution": (
                            f"Source: Epoch AI, CC BY 4.0, accessed {accessed_at[:10]}. "
                            "Adapted by RedRouter."
                            if not internal
                            else None
                        ),
                    }
                )
                counts["kept_epoch" if internal else "kept_upstream"] += 1
    result = {
        "schema_version": 1,
        "accessed_at": accessed_at,
        "zip_sha256": hashlib.sha256(data).hexdigest(),
        "generator_version": __version__,
        "counts": dict(counts),
        "results": results,
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "external.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(
        f"External results: {len(results)} kept; "
        + ", ".join(f"{k}={v}" for k, v in counts.items())
    )
    return result


def load_external(path: Path) -> dict | None:
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    if data["schema_version"] != 1:
        raise ValueError("Unsupported external schema_version")
    licences = json.loads(LICENCES.read_text())
    for row in data["results"]:
        licence = licences.get(row["source"], {})
        if not allowed_licence(licence) or any(
            row[key] != licence[key] for key in ("licence_id", "licence_url")
        ):
            raise ValueError("External result has no approved source licence")
    return data
