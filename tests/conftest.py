import copy
from datetime import timedelta

import pytest

from redrouter_benchmarks.catalog import Catalog
from redrouter_benchmarks.clock import iso
from redrouter_benchmarks.demo import DEMO_NOW


@pytest.fixture(scope="session")
def catalog():
    return Catalog.load()


@pytest.fixture
def route(catalog):
    return copy.deepcopy(next(r for r in catalog.routes.values() if r["enabled"]))


@pytest.fixture
def record(route):
    return {
        "run_id": "test-run",
        "methodology_version": "1.0.0",
        "harness_version": "0.1.0",
        "region": "us-east-1",
        "route_id": route["id"],
        "stratum": "prose-1000",
        "started_at": iso(DEMO_NOW - timedelta(hours=1, seconds=12)),
        "completed_at": iso(DEMO_NOW - timedelta(hours=1)),
        "timestamps": {
            "sent_ns": 1_000_000_000,
            "first_delta_ns": 2_000_000_000,
            "last_delta_ns": 12_000_000_000,
            "completed_ns": 13_000_000_000,
        },
        "usage": {"prompt": 1000, "cached": 0, "completion": 501, "reasoning": 0},
        "error_class": None,
        "first_chunk_tokens": 1,
        "demo": False,
    }


@pytest.fixture(scope="session")
def site_input(tmp_path_factory):
    from site_data import site_data

    return site_data(tmp_path_factory.getbasetemp() / "site-data")
