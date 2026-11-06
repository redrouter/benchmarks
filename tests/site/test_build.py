import importlib
import json
import re
import shutil
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

build = importlib.import_module("redrouter_benchmarks.site.build")
ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "site-data"


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.resources = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a":
            self.links.append(attrs.get("href", ""))
        if tag in ("script", "img", "link"):
            self.resources.append(attrs.get("src", attrs.get("href", "")))


@pytest.fixture
def source(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    for folder in ("site", "schema"):
        (source / folder).symlink_to(ROOT / folder, target_is_directory=True)
    (source / "METHODOLOGY.md").write_text(
        "# Fixture methodology\n\n## Metrics\n\nMedian of successful samples.\n"
    )
    (source / "LICENSE").write_text("Fixture licence\n")
    monkeypatch.setattr(build, "ROOT", source)
    return source


@pytest.fixture
def dist(tmp_path, source):
    output = tmp_path / "dist"
    build.build_site(FIXTURE, output)
    return output


def test_fixture_matches_both_schemas():
    summary = json.loads((FIXTURE / "summary.json").read_text())
    validator = Draft202012Validator(
        json.loads((ROOT / "schema/summary.schema.json").read_text()),
        format_checker=FormatChecker(),
    )
    validator.validate(summary)
    validator = Draft202012Validator(
        json.loads((ROOT / "schema/route-history.schema.json").read_text()),
        format_checker=FormatChecker(),
    )
    for route in summary["routes"]:
        history = json.loads((FIXTURE / route["history"]).read_text())
        validator.validate(history)
        assert history["route_id"] == route["id"]
    assert (
        len(summary["models"]),
        len(summary["routes"]),
        len(summary["regions"]),
    ) == (3, 8, 2)


def test_build_pages_and_downloads(dist):
    assert len(list(dist.rglob("*.html"))) == 17
    for name in (
        "summary.json",
        "summary.csv",
        "schema/summary.schema.json",
        "schema/route-history.schema.json",
        "LICENSE",
    ):
        assert (dist / "data" / name).is_file()
    assert "Demo data" in (dist / "index.html").read_text()
    assert "Fixture methodology" in (dist / "methodology/index.html").read_text()
    assert (
        'data-days="7"'
        in (dist / "models/deepseek-v4.1-flash-1017/index.html").read_text()
    )


def test_every_internal_link_and_resource_exists(dist):
    for page in dist.rglob("*.html"):
        parser = Links()
        parser.feed(page.read_text())
        for href in parser.links + parser.resources:
            if not href.startswith("/"):
                continue
            target = dist / href.lstrip("/").split("?")[0].split("#")[0]
            assert target.is_file() or (target / "index.html").is_file(), (page, href)


def test_assets_load_without_external_requests(dist):
    for page in dist.rglob("*.html"):
        parser = Links()
        parser.feed(page.read_text())
        assert all(url.startswith("/") for url in parser.resources)
    for path in (dist / "assets").glob("*.css"):
        assert not re.search(
            r'(?:url\(|@import\s)[\s"\']*(?:https?:)?//', path.read_text()
        )
    assert "fetch(" not in (dist / "assets/site.js").read_text()


def test_provider_marks_are_local_and_sanitized(dist):
    sources = json.loads((dist / "assets/providers/sources.json").read_text())
    assert len(sources) == 14
    for source in sources:
        path = dist / f"assets/providers/{source['id']}.{source['format']}"
        assert path.is_file()
        if source["format"] != "svg":
            continue
        tree = ET.parse(path)
        for node in tree.iter():
            assert node.tag.split("}")[-1] not in (
                "script",
                "foreignObject",
                "image",
                "style",
                "animate",
                "set",
            )
            for key, value in node.attrib.items():
                assert not key.lower().startswith("on")
                assert not (
                    key.split("}")[-1] in ("href", "src") and not value.startswith("#")
                )
                assert not re.search(r'url\(\s*["\']?(?!#)', value)


def test_missing_methodology_fails_clearly(source, tmp_path):
    (source / "METHODOLOGY.md").unlink()
    with pytest.raises(FileNotFoundError, match="Methodology is required"):
        build.build_site(FIXTURE, tmp_path / "missing")


def test_history_path_cannot_escape_data_directory(source, tmp_path):
    data = tmp_path / "data"
    shutil.copytree(FIXTURE, data)
    summary = json.loads((data / "summary.json").read_text())
    summary["routes"][0]["history"] = "../secret.json"
    (data / "summary.json").write_text(json.dumps(summary))
    with pytest.raises(ValueError, match="Path leaves data directory"):
        build.build_site(data, tmp_path / "escaped")


def test_nulls_and_history_regions_are_not_averaged():
    summary = json.loads((FIXTURE / "summary.json").read_text())
    route = summary["routes"][4] | {"label": "LarpRouter", "color": "#b0161c"}
    assert build.metric_value(route, "us-west-2", "ttft_ms") is None
    assert build.metric_value(route, "all", "ttft_ms") == 0.69
    history = json.loads((FIXTURE / route["history"]).read_text())
    svg = str(
        build.history_chart(
            [route],
            {route["id"]: history},
            "all",
            "ttft_ms",
            "TTFT",
            "s",
            14,
            summary["generated_at"],
        )
    )
    assert "us-east-1" in svg and "us-west-2" in svg
    assert "<polyline" in svg


def test_serve_rejects_missing_build(tmp_path):
    from redrouter_benchmarks.site.serve import serve

    with pytest.raises(FileNotFoundError, match="Built site not found"):
        serve(tmp_path, 0)


def test_preview_serves_custom_404(tmp_path):
    from functools import partial
    from http.client import HTTPConnection
    from http.server import ThreadingHTTPServer
    from threading import Thread

    from redrouter_benchmarks.site.serve import SiteHandler

    (tmp_path / "404.html").write_text("<h1>Page not found</h1>")
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(SiteHandler, directory=str(tmp_path))
    )
    worker = Thread(
        target=partial(server.serve_forever, poll_interval=0.01), daemon=True
    )
    worker.start()
    try:
        connection = HTTPConnection("127.0.0.1", server.server_port)
        connection.request("GET", "/missing-page/")
        response = connection.getresponse()
        assert response.status == 404
        assert response.read() == b"<h1>Page not found</h1>"
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=1)
