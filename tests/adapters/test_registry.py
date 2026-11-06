import shutil
import sys

import pytest

from redrouter_benchmarks.adapters import get_adapter, registry
from redrouter_benchmarks.catalog import Catalog, resource_dir


@pytest.fixture(autouse=True)
def fresh_registry():
    registry.cache_clear()
    yield
    registry.cache_clear()


def install_entry_point(
    tmp_path, monkeypatch, name="fixture-protocol", target="fixture_adapter:Factory"
):
    distribution = tmp_path / "fixture_adapter-1.0.dist-info"
    distribution.mkdir()
    (distribution / "METADATA").write_text("Name: fixture-adapter\nVersion: 1.0\n")
    (distribution / "entry_points.txt").write_text(
        f"[redrouter_benchmarks.adapters]\n{name} = {target}\n"
    )
    (tmp_path / "fixture_adapter.py").write_text(
        "from redrouter_benchmarks.adapters.openai_chat import OpenAIChat\nFactory = OpenAIChat\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "fixture_adapter", raising=False)


def catalogue_with_protocol(tmp_path, protocol):
    root = tmp_path / "catalog"
    shutil.copytree(resource_dir("catalog"), root)
    path = next((root / "providers").glob("*.toml"))
    path.write_text(
        path.read_text().replace('protocol = "openai-chat"', f'protocol = "{protocol}"')
    )
    return root


def test_installed_entry_point_is_discovered_and_loaded_lazily(tmp_path, monkeypatch):
    install_entry_point(tmp_path, monkeypatch)
    root = catalogue_with_protocol(tmp_path, "fixture-protocol")
    Catalog.load(root)
    assert "fixture_adapter" not in sys.modules
    first, second = get_adapter("fixture-protocol"), get_adapter("fixture-protocol")
    assert type(first).__name__ == "OpenAIChat" and first is not second
    assert "fixture_adapter" in sys.modules


@pytest.mark.parametrize("name", ["openai-chat", "anthropic-messages"])
def test_entry_point_cannot_override_a_builtin(tmp_path, monkeypatch, name):
    install_entry_point(tmp_path, monkeypatch, name)
    with pytest.raises(ValueError, match=f"Duplicate adapter protocol: {name}"):
        Catalog.load()


def test_catalogue_rejects_unknown_protocol_with_available_names(tmp_path):
    root = catalogue_with_protocol(tmp_path, "missing-protocol")
    with pytest.raises(
        ValueError, match="Unknown adapter protocol 'missing-protocol'.*available:"
    ) as error:
        Catalog.load(root)
    assert "openai-chat" in str(error.value) and "anthropic-messages" in str(error.value)
    with pytest.raises(ValueError, match="Unknown adapter protocol 'missing-protocol'"):
        get_adapter("missing-protocol")


def test_catalogue_accepts_anthropic(tmp_path):
    catalog = Catalog.load(catalogue_with_protocol(tmp_path, "anthropic-messages"))
    assert any(p["protocol"] == "anthropic-messages" for p in catalog.providers.values())


def test_duplicate_third_party_names_are_rejected(tmp_path, monkeypatch):
    install_entry_point(tmp_path, monkeypatch)
    duplicate = tmp_path / "duplicate-1.0.dist-info"
    duplicate.mkdir()
    (duplicate / "METADATA").write_text("Name: duplicate\nVersion: 1.0\n")
    (duplicate / "entry_points.txt").write_text(
        "[redrouter_benchmarks.adapters]\nfixture-protocol = fixture_adapter:Factory\n"
    )
    with pytest.raises(ValueError, match="Duplicate adapter protocol: fixture-protocol"):
        Catalog.load()


def test_factory_must_implement_adapter(tmp_path, monkeypatch):
    install_entry_point(tmp_path, monkeypatch, target="builtins:object")
    with pytest.raises(ValueError, match="must implement build_request and parse_event"):
        get_adapter("fixture-protocol")
