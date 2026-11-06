import asyncio
import gzip
import json
from collections import Counter
from pathlib import Path

import pytest
from validate_data import validate_data

from redrouter_benchmarks.aggregate import aggregate
from redrouter_benchmarks.catalog import Catalog, https_url
from redrouter_benchmarks.demo import DEGRADING_ROUTE, DEMO_NOW, STALE_ROUTE, demo_data


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    output = tmp_path_factory.mktemp("demo")
    summary = asyncio.run(demo_data(output, days=14, seed=7))
    return output, summary


def test_catalogue_has_sourced_prices_and_no_affiliated_routes(catalog):
    assert len(catalog.providers) == 13
    assert len(catalog.routes) == 25
    assert sum(r["enabled"] for r in catalog.routes.values()) == 22
    for route in catalog.routes.values():
        assert "redrouter" not in route["id"]
        if route["enabled"]:
            assert route["price"]["input_usd_per_mtok"] >= 0
            assert route["price"]["output_usd_per_mtok"] >= 0
            assert https_url(route["price"]["source_url"])


def test_catalogue_rejects_enabled_route_without_price(catalog, tmp_path):
    import shutil

    root = Path(__file__).resolve().parents[1] / "catalog"
    target = tmp_path / "catalog"
    shutil.copytree(root, target)
    route = next(r for r in catalog.routes.values() if r["enabled"])
    path = target / "routes" / (route["id"] + ".toml")
    path.write_text(path.read_text().split("[price]")[0])
    with pytest.raises(ValueError, match="Invalid price"):
        Catalog.load(target)


def test_full_demo_matches_both_schemas(demo):
    output, summary = demo
    assert summary["demo"] is True
    assert validate_data(output) == 23
    assert len(list((output / "raw").rglob("*.jsonl.gz"))) == 28


def test_demo_has_failures_regions_schedule_and_scenarios(demo):
    output, summary = demo
    records = [
        json.loads(line)
        for p in sorted((output / "raw").rglob("*.gz"))
        for line in gzip.open(p, "rt")
    ]
    assert len({r["run_id"] for r in records}) == 224
    assert len(records) == 19584
    assert {r["region"] for r in records} == {"us-east-1", "us-west-2"}
    assert {r["error_class"] for r in records} == {None, "rate_limited", "server_error", "timeout"}
    assert set(Counter((r["run_id"], r["route_id"]) for r in records).values()) == {4}
    routes = {r["id"]: r for r in summary["routes"]}
    assert routes[STALE_ROUTE]["status"] == "stale"
    assert routes[DEGRADING_ROUTE]["status"] in {"degraded", "failing"}
    assert routes[DEGRADING_ROUTE]["metrics"]["all"]["output_tokens_per_second"]["p50"] > 0
    serialized = json.dumps(records)
    assert '"content"' not in serialized and '"messages"' not in serialized


def test_demo_reaggregation_is_byte_reproducible(demo, tmp_path):
    output, _ = demo
    aggregate(output / "raw", tmp_path, now=DEMO_NOW)
    expected = {p.relative_to(output): p.read_bytes() for p in output.rglob("*") if p.is_file()}
    actual = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert actual == expected


def test_demo_generator_is_seed_reproducible(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    asyncio.run(demo_data(first, days=1, seed=19))
    asyncio.run(demo_data(second, days=1, seed=19))
    assert {p.relative_to(first): p.read_bytes() for p in first.rglob("*") if p.is_file()} == {
        p.relative_to(second): p.read_bytes() for p in second.rglob("*") if p.is_file()
    }
