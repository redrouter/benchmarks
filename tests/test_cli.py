import pytest

from redrouter_benchmarks.cli import main


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
