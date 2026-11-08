import hashlib
import json
from copy import deepcopy
from functools import partial
from html import unescape
from html.parser import HTMLParser
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from threading import Thread
from xml.etree import ElementTree

import pytest
from validate_data import validate_data

from redrouter_benchmarks.site import SiteHandler, build_site, serve


def test_site_matches_approved_output(site_input, tmp_path):
    assert validate_data(site_input) == 9
    output = tmp_path / "dist"
    build_site(site_input, output)
    digest = hashlib.sha256()
    for path in sorted(output.rglob("*")):
        if path.is_file():
            digest.update(
                path.relative_to(output).as_posix().encode()
                + b"\0"
                + hashlib.sha256(path.read_bytes()).digest()
            )
    assert digest.hexdigest() == "6e2541e48eca33b4773fb5fb54b2e958ac06a47e8313bcad9e0457a2f1c0a8c9"


class Links(HTMLParser):
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ("a", "link", "script", "img", "image"):
            url = attrs.get("src", attrs.get("href", ""))
            if url.startswith("/"):
                target = self.root / url.lstrip("/").split("?")[0].split("#")[0]
                assert target.is_file() or (target / "index.html").is_file(), url


def test_internal_links_resolve(site_input, tmp_path):
    build_site(site_input, tmp_path / "dist")
    parser = Links()
    parser.root = tmp_path / "dist"
    for page in parser.root.rglob("*.html"):
        parser.feed(page.read_text())


def test_provider_and_route_badges(site_input, tmp_path):
    summary = json.loads((site_input / "summary.json").read_text())
    build_site(site_input, tmp_path)
    expected = {f"{p['id']}.svg" for p in summary["providers"]} | {
        f"{r['provider_id']}/{r['model_id']}.svg" for r in summary["routes"]
    }
    badges = tmp_path / "badges"
    assert {p.relative_to(badges).as_posix() for p in badges.rglob("*.svg")} == expected
    for path in badges.rglob("*.svg"):
        assert path.stat().st_size < 2000
        svg = ElementTree.parse(path).getroot()
        assert svg.tag == "{http://www.w3.org/2000/svg}svg"
        assert svg.attrib["role"] == "img"
        assert svg.attrib["aria-label"] == svg.find("{*}title").text
        assert svg.attrib["height"] == "20"
        assert not any("href" in key for e in svg.iter() for key in e.attrib)
        assert "@import" not in path.read_text() and "@font-face" not in path.read_text()
    assert "85 tok/s · 96.9% up" in (badges / "refuseless.svg").read_text()
    assert "/badges/" not in (tmp_path / "sitemap.xml").read_text()
    for provider in summary["providers"]:
        page = (tmp_path / "providers" / provider["id"] / "index.html").read_text()
        link = f"https://benchmarks.redrouter.org/providers/{provider['id']}/?utm_source=badge"
        image = f"https://benchmarks.redrouter.org/badges/{provider['id']}.svg"
        alt = f"{provider['name']} on Abliterated Benchmarks"
        snippet = f'<a href="{link}"><img src="{image}" alt="{alt}" height="20"></a>'
        assert f"<code>{snippet}</code>" in unescape(page)
        assert f"[![{alt}]({image})]({link})" in page
        assert page.count('<details class="badge-embed footnote">') == 1
        assert page.index("Embed badge") > page.index('class="further-reading"')


def test_badges_choose_fastest_eligible_route(site_input, tmp_path):
    from redrouter_benchmarks.badges import write_badges
    from redrouter_benchmarks.catalog import resource

    summary = json.loads((site_input / "summary.json").read_text())
    route = deepcopy(summary["routes"][0])
    route["model_id"] = "another-model"
    route["metrics"]["all"]["output_tokens_per_second"]["p50"] = 248
    route["metrics"]["all"]["success_rate"] = 0.996
    summary["routes"].append(route)
    duplicate = deepcopy(route)
    duplicate["status"] = "insufficient_data"
    duplicate["metrics"]["all"]["output_tokens_per_second"]["p50"] = 999
    summary["routes"].append(duplicate)
    write_badges(summary, tmp_path, (resource("site") / "assets/tokens.css").read_text())
    for path in ("refuseless.svg", "refuseless/another-model.svg"):
        assert "248 tok/s · 99.6% up" in (tmp_path / "badges" / path).read_text()
    assert "85 tok/s" in (tmp_path / "badges/refuseless/deepseek-v4.1-flash-1017.svg").read_text()


@pytest.mark.parametrize(
    "missing",
    ["requests", "successes", "speed", "rate", "stale", "window", "routes"],
)
def test_badges_without_recent_data(site_input, tmp_path, missing):
    from redrouter_benchmarks.badges import write_badges
    from redrouter_benchmarks.catalog import resource

    summary = json.loads((site_input / "summary.json").read_text())
    for route in summary["routes"]:
        metrics = route["metrics"]["all"]
        if missing in ("requests", "successes"):
            metrics[missing] = 7
        elif missing == "speed":
            metrics["output_tokens_per_second"]["p50"] = None
        elif missing == "rate":
            metrics["success_rate"] = None
        elif missing == "stale":
            route["status"] = "stale"
    if missing == "window":
        summary["window_hours"] = 24
    if missing == "routes":
        summary["routes"] = []
    write_badges(summary, tmp_path, (resource("site") / "assets/tokens.css").read_text())
    for provider in summary["providers"]:
        assert (tmp_path / "badges" / f"{provider['id']}.svg").is_file()
    for path in (tmp_path / "badges").rglob("*.svg"):
        assert path.stat().st_size < 2000
        svg = ElementTree.parse(path).getroot()
        assert "no recent data" in svg.attrib["aria-label"]
        assert "no recent data" in [e.text for e in svg.findall(".//{*}text")]


@pytest.mark.parametrize("invalid", ["../secret.json", "/tmp/secret.json"])
def test_history_cannot_escape_input(site_input, tmp_path, invalid):
    import shutil

    data = tmp_path / "data"
    shutil.copytree(site_input, data)
    summary = json.loads((data / "summary.json").read_text())
    summary["routes"][0]["history"] = invalid
    (data / "summary.json").write_text(json.dumps(summary))
    with pytest.raises(ValueError, match="Path leaves data directory"):
        build_site(data, tmp_path / "dist")


def test_preview_returns_custom_404(tmp_path):
    with pytest.raises(FileNotFoundError, match="Built site not found"):
        serve(tmp_path, 0)
    (tmp_path / "404.html").write_text("<h1>Page not found</h1>")
    with ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(SiteHandler, directory=str(tmp_path))
    ) as server:
        worker = Thread(target=partial(server.serve_forever, poll_interval=0.01), daemon=True)
        worker.start()
        try:
            connection = HTTPConnection("127.0.0.1", server.server_port)
            connection.request("GET", "/missing/")
            response = connection.getresponse()
            assert response.status == 404
            assert response.read() == b"<h1>Page not found</h1>"
            connection.close()
        finally:
            server.shutdown()
            worker.join(timeout=1)
