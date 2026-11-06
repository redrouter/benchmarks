import sys
from types import ModuleType

import pytest

from redrouter_benchmarks.cli import main


def test_site_commands_call_the_shared_function_contract(monkeypatch, tmp_path):
    package = ModuleType("redrouter_benchmarks.site")
    build = ModuleType("redrouter_benchmarks.site.build")
    serve = ModuleType("redrouter_benchmarks.site.serve")
    calls = []
    build.build_site = lambda data, output: calls.append((data, output))
    serve.serve = lambda dist, port: calls.append((dist, port))
    for module in (package, build, serve):
        monkeypatch.setitem(sys.modules, module.__name__, module)
    main(["build-site", "--data", str(tmp_path / "data"), "--output", str(tmp_path / "dist")])
    main(["serve", "--dist", str(tmp_path / "dist"), "--port", "9000"])
    assert calls == [(tmp_path / "data", tmp_path / "dist"), (tmp_path / "dist", 9000)]


def test_disabled_route_is_rejected_before_credentials_or_requests(catalog, tmp_path, capsys):
    disabled = next(r for r in catalog.routes.values() if not r["enabled"])
    with pytest.raises(SystemExit) as exc:
        main(["run", "--region", "us-east-1", "--output", str(tmp_path), "--route", disabled["id"]])
    assert exc.value.code == 2
    assert "disabled" in capsys.readouterr().err
    assert not list(tmp_path.iterdir())


def test_only_enabled_routes_with_credentials_are_selected(catalog, monkeypatch, tmp_path):
    import redrouter_benchmarks.cli as cli

    for provider in catalog.providers.values():
        monkeypatch.delenv(provider["api_key_env"], raising=False)
    monkeypatch.setenv("NANOGPT_API_KEY", "local-test-secret")
    calls = []

    async def capture(catalog, **kwargs):
        calls.append(kwargs["routes"])
        return tmp_path / "run.jsonl"

    monkeypatch.setattr(cli, "run_suite", capture)
    main(["run", "--region", "us-east-1", "--output", str(tmp_path)])
    expected = [
        r
        for r in catalog.routes.values()
        if r["enabled"] and r["provider_id"].startswith("nanogpt-")
    ]
    assert calls == [expected]
