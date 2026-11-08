import importlib
import json
import re
import shutil
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

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
        if tag == "script" and not attrs.get("src"):
            return
        if tag == "link" and attrs.get("rel") == "canonical":
            return
        if tag in ("script", "img", "link", "image"):
            self.resources.append(attrs.get("src", attrs.get("href", "")))


@pytest.fixture
def source(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    for folder in ("site", "catalog/schema"):
        (source / folder).parent.mkdir(parents=True, exist_ok=True)
        (source / folder).symlink_to(ROOT / folder, target_is_directory=True)
    (source / "METHODOLOGY.md").write_text(
        "# Fixture methodology\n\n## Metrics\n\nMedian of successful samples.\n"
    )
    (source / "LICENSE").write_text("Fixture licence\n")
    monkeypatch.setattr(
        build, "resource", lambda name: source / ("catalog/schema" if name == "schema" else name)
    )
    return source


@pytest.fixture
def dist(tmp_path, source):
    output = tmp_path / "dist"
    build.build_site(FIXTURE, output)
    return output


def test_fixture_matches_both_schemas():
    summary = json.loads((FIXTURE / "summary.json").read_text())
    validator = Draft202012Validator(
        json.loads((ROOT / "catalog/schema/summary.schema.json").read_text()),
        format_checker=FormatChecker(),
    )
    validator.validate(summary)
    validator = Draft202012Validator(
        json.loads((ROOT / "catalog/schema/route-history.schema.json").read_text()),
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
        "LICENSE.txt",
    ):
        assert (dist / "data" / name).is_file()
    assert "Demo data" in (dist / "index.html").read_text()
    methodology = (dist / "methodology/index.html").read_text()
    assert "Fixture methodology" in methodology
    assert "mailto:support@redrouter.org" in methodology
    assert not (dist / "data/index.html").exists()
    assert 'data-days="7"' in (dist / "models/deepseek-v4.1-flash-1017/index.html").read_text()


def test_pages_have_no_inline_styles(dist):
    for page in dist.rglob("*.html"):
        assert " style=" not in page.read_text(), page


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
        assert not re.search(r'(?:url\(|@import\s)[\s"\']*(?:https?:)?//', path.read_text())
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
                assert not (key.split("}")[-1] in ("href", "src") and not value.startswith("#"))
                assert not re.search(r'url\(\s*["\']?(?!#)', value)


def test_missing_methodology_fails_clearly(source, tmp_path):
    (source / "METHODOLOGY.md").unlink()
    with pytest.raises(FileNotFoundError, match="Methodology is required"):
        build.build_site(FIXTURE, tmp_path / "missing")


def test_bar_charts_show_logos_and_skip_repeated_upstream(source, tmp_path):
    data = tmp_path / "data"
    shutil.copytree(FIXTURE, data)
    summary = json.loads((data / "summary.json").read_text())
    next(p for p in summary["providers"] if p["id"] == "nanogpt")["name"] = (
        "NanoGPT → Heabsy gateway"
    )
    (data / "summary.json").write_text(json.dumps(summary))
    build.build_site(data, tmp_path / "dist")
    page = (tmp_path / "dist" / "models" / "glm-5.3-flash" / "index.html").read_text()
    assert "NanoGPT → Heabsy gateway" in page
    assert "· Heabsy" not in page and "via Heabsy" not in page
    assert 'class="logo-tile"' in page
    assert 'class="bar-label"' in page
    assert 'href="#logo-nanogpt"' in page
    assert 'class="label-wide">NanoGPT → Heabsy gateway</tspan>' in page
    assert 'class="label-compact">NanoGPT → Heabsy…</tspan>' in page


@pytest.mark.parametrize("page", ["index.html", "models/deepseek-v4.1-flash-1017/index.html"])
def test_charts_render_once_with_shared_labels(dist, page):
    html = (dist / page).read_text()
    assert html.count('<svg role="img"') == html.count('class="chart-scroll ') > 0
    assert 'class="chart-wide"' not in html and 'class="chart-compact"' not in html
    labels = re.findall(r'<g id="label-(\d+)">(.*?)</g>', html)
    assert labels and all('class="bar-label"' in label for _, label in labels)
    assert set(re.findall(r'href="#label-(\d+)"', html)) <= {key for key, _ in labels}
    assert any("NanoGPT" in label for _, label in labels)


def test_tooltips_reuse_accessible_labels(dist):
    html = (dist / "models/deepseek-v4.1-flash-1017/index.html").read_text()
    points = re.findall(r'<(?:rect|circle) [^>]*tabindex="0"[^>]*>', html)
    assert points and all('aria-label="' in point for point in points)
    assert "data-tooltip" not in html


def test_pages_have_link_previews(dist):
    page = (dist / "index.html").read_text()
    assert (
        '<meta property="og:image" content="https://benchmarks.redrouter.org/assets/og.png">'
        in page
    )
    assert '<meta name="twitter:card" content="summary_large_image">' in page
    assert (dist / "assets" / "og.png").is_file()


def metadata(page):
    head = page.split("</head>")[0]
    title = re.search(r"<title>(.*?)</title>", head).group(1)
    tags = dict(re.findall(r'<meta (?:name|property)="([^"]+)" content="([^"]*)">', head))
    canonical = re.findall(r'<link rel="canonical" href="([^"]+)">', head)
    graph = json.loads(
        re.search(r'<script type="application/ld\+json">(.*?)</script>', head).group(1)
    )["@graph"]
    return title, tags, canonical, graph


def test_search_metadata_and_sitemap(dist):
    summary = json.loads((FIXTURE / "summary.json").read_text())
    sitemap = ET.parse(dist / "sitemap.xml").getroot()
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    urls = {entry.findtext("s:loc", namespaces=ns) for entry in sitemap}
    expected = {
        f"{build.SITE_URL}/{page.relative_to(dist).as_posix().removesuffix('index.html')}"
        for page in dist.rglob("index.html")
    }
    assert urls == expected
    for entry in sitemap:
        assert entry.findtext("s:lastmod", namespaces=ns) == summary["generated_at"]
    for url in urls:
        assert url.endswith("/")
        assert (dist / urlsplit(url).path.lstrip("/") / "index.html").is_file()
    robots = RobotFileParser()
    robots.parse((dist / "robots.txt").read_text().splitlines())
    assert robots.can_fetch("*", "/models/")
    assert robots.site_maps() == [f"{build.SITE_URL}/sitemap.xml"]
    assert (dist / f"{build.INDEXNOW_KEY}.txt").read_bytes() == build.INDEXNOW_KEY.encode()
    titles, descriptions = [], []
    for page in dist.rglob("*.html"):
        title, tags, canonical, graph = metadata(page.read_text())
        titles.append(title)
        descriptions.append(tags["description"])
        assert title == tags["og:title"] == tags["twitter:title"]
        assert tags["description"] == tags["og:description"] == tags["twitter:description"]
        assert 0 < len(tags["description"]) <= 160
        assert len(canonical) == 1 and canonical[0].endswith("/")
        assert tags["og:url"] == canonical[0]
        assert (tags.get("robots") == "noindex") == (page.name == "404.html")
        assert tags["og:site_name"] == build.SITE_NAME
        types = {node["@type"]: node for node in graph}
        assert types["Organization"]["name"] == "RedRouter"
        overview = page == dist / "index.html"
        assert ("BreadcrumbList" in types) == (not overview)
        assert ("WebSite" in types) == overview
        has_dataset = overview or page.parent.parent == dist / "models"
        assert ("Dataset" in types) == has_dataset
        if has_dataset:
            dataset = types["Dataset"]
            assert {
                "name",
                "description",
                "url",
                "creator",
                "license",
                "isAccessibleForFree",
                "dateModified",
                "temporalCoverage",
                "measurementTechnique",
                "variableMeasured",
                "keywords",
                "distribution",
            } <= dataset.keys()
            assert len(dataset["description"]) >= 50
            assert dataset["url"] == canonical[0]
            assert dataset["dateModified"] == summary["generated_at"]
            assert dataset["license"] == f"{build.SITE_URL}/data/LICENSE.txt"
            assert dataset["isAccessibleForFree"] is True
            assert len(dataset["variableMeasured"]) == 5
            assert dataset["temporalCoverage"] == ("2026-11-02T18:00:00+00:00/2026-11-05T18:00:00Z")
            downloads = dataset["distribution"]
            assert any(
                d["contentUrl"].endswith("/data/summary.csv") and d["encodingFormat"] == "text/csv"
                for d in downloads
            )
            for download in downloads:
                assert download["@type"] == "DataDownload"
                assert (dist / urlsplit(download["contentUrl"]).path.lstrip("/")).is_file()
    assert len(titles) == len(set(titles))
    assert len(descriptions) == len(set(descriptions))


def test_descriptions_use_pooled_route_metrics(dist):
    model = metadata((dist / "models/glm-5.3-flash/index.html").read_text())[1]
    assert "2 providers" in model["description"]
    assert "Fastest: Heabsy via NanoGPT, 187 tok/s." in model["description"]
    assert "Lowest price: Experiential Labs, $0.535/1M." in model["description"]
    provider = metadata((dist / "providers/venice/index.html").read_text())[1]
    assert "1 routes benchmarked over 72h" in provider["description"]
    assert "119 tok/s output, 0.47 s TTFT, 96.9% success" in provider["description"]
    control = metadata((dist / "models/qwen3.8-27b/index.html").read_text())[0]
    assert "Standard-Model API" in control


def test_metadata_omits_insufficient_and_nonfinite_values(source, tmp_path):
    data = tmp_path / "data"
    shutil.copytree(FIXTURE, data)
    summary = json.loads((data / "summary.json").read_text())
    for route in summary["routes"]:
        route["metrics"]["all"]["requests"] = 0
        route["metrics"]["all"]["success_rate"] = float("nan")
        route["metrics"]["all"]["output_tokens_per_second"]["p50"] = 99999
        route["price"]["blended_usd_per_mtok"] = 0
    (data / "summary.json").write_text(json.dumps(summary))
    output = tmp_path / "dist"
    build.build_site(data, output)
    for page in output.rglob("*.html"):
        description = metadata(page.read_text())[1]["description"]
        assert not any(
            word in description
            for word in ("None", "nan", "99999", "Fastest:", "Lowest price:", "Route medians:")
        )
    page = metadata((output / "models/glm-5.3-flash/index.html").read_text())
    assert "0 providers" in page[1]["description"]
    assert "Abliterated / Uncensored" in page[0]


def test_metadata_uses_medians_and_omits_missing_metrics():
    summary = json.loads((FIXTURE / "summary.json").read_text())
    provider = summary["providers"][0]
    routes = []
    for route, speed in zip(summary["routes"][:3], (10, 20, 900), strict=True):
        route.update(provider=provider, provider_id=provider["id"])
        route["metrics"]["all"]["output_tokens_per_second"]["p50"] = speed
        route["metrics"]["all"]["ttft_ms"]["p50"] = None
        route["metrics"]["all"]["success_rate"] = float("nan")
        routes.append(route)
    result = build.page_metadata(
        summary,
        routes,
        f"providers/{provider['id']}/index.html",
        {"title": provider["name"], "provider": provider},
        [],
    )
    assert "20 tok/s output" in result["description"]
    assert "TTFT" not in result["description"] and "success" not in result["description"]


def test_structured_data_escapes_script_content(source, tmp_path):
    data = tmp_path / "data"
    shutil.copytree(FIXTURE, data)
    summary = json.loads((data / "summary.json").read_text())
    summary["models"][0]["name"] = '</script><script>alert("x")</script>'
    (data / "summary.json").write_text(json.dumps(summary))
    output = tmp_path / "dist"
    build.build_site(data, output)
    page = (output / "models" / summary["models"][0]["id"] / "index.html").read_text()
    dataset = next(node for node in metadata(page)[3] if node["@type"] == "Dataset")
    assert summary["models"][0]["name"] in dataset["name"]
    assert '<script>alert("x")</script>' not in page


def test_directories_show_logos(dist):
    for kind in ("models", "providers"):
        page = (dist / kind / "index.html").read_text()
        assert page.count('-logo" src=') == page.count('class="directory-item"') > 0


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
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(SiteHandler, directory=str(tmp_path)))
    worker = Thread(target=partial(server.serve_forever, poll_interval=0.01), daemon=True)
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
