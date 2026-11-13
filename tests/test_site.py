import hashlib
import json
import re
import shutil
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

from redrouter_benchmarks.site import SiteHandler, build_site, serve, write_assets


def structured_graph(page):
    return json.loads(
        re.search(r'<script type="application/ld\+json">(.*?)</script>', page, re.S)[1]
    )["@graph"]


def test_data_licence(site_input, tmp_path):
    build_site(site_input, tmp_path)
    notice, legalcode = (tmp_path / "data/LICENSE.txt").read_bytes().split(b"\n\n", 1)
    assert notice.decode() == "Benchmark results © 2026 RedRouter, licensed under CC BY 4.0."
    assert (
        hashlib.sha256(legalcode).hexdigest()
        == "9ba9550ad48438d0836ddab3da480b3b69ffa0aac7b7878b5a0039e7ab429411"
    )
    datasets = [
        item
        for page in tmp_path.rglob("*.html")
        for item in structured_graph(page.read_text())
        if item["@type"] == "Dataset"
    ]
    assert len(datasets) == 4
    assert all(d["license"] == "https://creativecommons.org/licenses/by/4.0/" for d in datasets)


def test_llms_tracks_built_data(site_input, tmp_path):
    data, output = tmp_path / "data", tmp_path / "dist"
    shutil.copytree(site_input, data)
    build_site(data, output)
    llms = (output / "llms.txt").read_text()
    summary = json.loads((data / "summary.json").read_text())
    assert llms.startswith("# Abliterated Benchmarks by RedRouter\n\n> ")
    assert summary["generated_at"] in llms
    for kind in ("models", "providers"):
        for item in summary[kind]:
            assert llms.count(f"/{kind}/{item['id']}/") == 1
    for path in (output / "data").rglob("*"):
        if path.suffix in (".csv", ".json"):
            assert f"/data/{path.relative_to(output / 'data').as_posix()}" in llms
    assert "larprouter/deepseek-v4.1-flash-1017/abliterated, pooled p50 153.0 tok/s" in llms
    for route in summary["routes"]:
        if route["model_id"] == "deepseek-v4.1-flash-1017":
            route["metrics"]["all"]["output_tokens_per_second"]["p50"] = None
        elif route["model_id"] == "glm-5.3-flash":
            route["metrics"]["all"]["output_tokens_per_second"]["p50"] = 9999
            route["metrics"]["all"]["requests"] = 7
        else:
            route["metrics"]["all"]["output_tokens_per_second"]["p50"] = 321
    (data / "summary.json").write_text(json.dumps(summary))
    build_site(data, output)
    updated = (output / "llms.txt").read_text()
    assert updated.count("No route has sufficient pooled speed data.") == 2
    assert "pooled p50 321.0 tok/s" in updated
    assert "153.0 tok/s" not in updated
    assert "9999" not in updated


def test_explainer_metadata_and_discovery(site_input, tmp_path):
    build_site(site_input, tmp_path)
    page = (tmp_path / "what-is-abliteration/index.html").read_text()
    graph = structured_graph(page)
    article = next(item for item in graph if item["@type"] == "Article")
    faq = next(item for item in graph if item["@type"] == "FAQPage")
    title = "What Is Abliteration? Abliterated vs Uncensored LLMs Explained"
    assert f"<title>{title}</title>" in page
    assert f"<h1>{title}</h1>" in page
    assert article["headline"] == title
    assert article["publisher"]["name"] == "RedRouter"
    assert "author" not in article
    assert article["datePublished"] == article["dateModified"] == "2026-11-07"
    url = "https://benchmarks.redrouter.org/what-is-abliteration/"
    blog_url = "https://redrouter.org/blog/what-is-abliteration"
    assert f'<link rel="canonical" href="{blog_url}">' in page
    assert f'<a href="{blog_url}">what is abliteration</a>' in page
    assert article["mainEntityOfPage"] == url
    assert f'<meta name="description" content="{article["description"]}">' in page
    visible = re.findall(r"<h3>(.*?)</h3>\s*<p>(.*?)</p>", page)
    assert [(unescape(q), unescape(a)) for q, a in visible] == [
        (q["name"], q["acceptedAnswer"]["text"]) for q in faq["mainEntity"]
    ]
    assert 4 <= len(visible) <= 5
    body = re.search(r'<article class="prose">(.*?)</article>', page, re.S)[1]
    assert 700 <= len(unescape(re.sub(r"<[^>]*>", " ", body)).split()) <= 1000
    sitemap = ElementTree.fromstring((tmp_path / "sitemap.xml").read_text())
    assert url in [node.text for node in sitemap.findall("{*}url/{*}loc")]
    for path in tmp_path.rglob("*.html"):
        html = path.read_text()
        assert "/what-is-abliteration/" not in re.search(r"<nav\b.*?</nav>", html)[0]
        assert "/what-is-abliteration/" in re.search(r"<footer>.*?</footer>", html)[0]
        if path != tmp_path / "what-is-abliteration/index.html":
            main = re.search(r"<main\b.*?</main>", html, re.S)[0]
            assert main.count('href="/what-is-abliteration/"') == (
                1 if path == tmp_path / "methodology/index.html" else 0
            )


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
    assert digest.hexdigest() == "2bc22e1e67f384079e29cd7522d75f62573f92ba472dab030e2ade948c99e3eb"


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


def test_asset_versions_follow_dependencies(tmp_path):
    source, output = tmp_path / "source", tmp_path / "output"
    (source / "fonts").mkdir(parents=True)
    (source / "fonts/geist.woff2").write_bytes(b"first font")
    (source / "site.css").write_text("@font-face{src:url('/assets/fonts/geist.woff2')}")
    (source / "tokens.css").write_text(":root{--color:red}")
    (source / "external.css").write_text(".external{color:var(--color)}")
    (source / "site.js").write_text("document.title='Ready'")
    (source / "og.png").write_bytes(b"public preview")
    first = write_assets(source, output)
    assert first == write_assets(source, output)
    css = output / first["/assets/site.css"].removeprefix("/assets/")
    assert first["/assets/fonts/geist.woff2"] in css.read_text()
    assert ":root{--color:red}" in css.read_text()
    assert ".external{color:var(--color)}" in css.read_text()
    assert (output / "og.png").read_bytes() == b"public preview"
    (source / "fonts/geist.woff2").write_bytes(b"second font")
    second = write_assets(source, output)
    assert second["/assets/fonts/geist.woff2"] != first["/assets/fonts/geist.woff2"]
    assert second["/assets/site.css"] != first["/assets/site.css"]
    assert second["/assets/site.js"] == first["/assets/site.js"]
    assert css.is_file()


def test_font_preloads_match_bundled_styles(site_input, tmp_path):
    build_site(site_input, tmp_path)
    css = next((tmp_path / "assets").glob("site.*.css")).read_text()
    for page in tmp_path.rglob("*.html"):
        html = page.read_text()
        assert len(re.findall(r'rel="stylesheet"', html)) == 1
        fonts = re.findall(r'<link rel="preload" href="([^"]+)"[^>]+crossorigin>', html)
        assert len(fonts) == 2
        for font in fonts:
            assert font in css
            assert (tmp_path / font.lstrip("/")).is_file()


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


def test_hugging_face_dataset_discovery(site_input, tmp_path):
    build_site(site_input, tmp_path)
    url = "https://huggingface.co/datasets/redrouter/abliterated-benchmarks"
    assert f"[Hugging Face dataset]({url})" in (tmp_path / "llms.txt").read_text()
    for path in tmp_path.rglob("*.html"):
        html = path.read_text()
        footer = re.search(r"<footer>.*?</footer>", html)[0]
        assert footer.count(f'<a href="{url}">Dataset</a>') == 1
        assert url not in re.search(r"<nav\b.*?</nav>", html)[0]


def test_hugging_face_header(site_input, tmp_path):
    build_site(site_input, tmp_path)
    for path in tmp_path.rglob("*.html"):
        header = re.search(r"<header>.*?</header>", path.read_text(), re.S)[0]
        assert header.count('aria-label="Dataset on Hugging Face"') == 1
        link = re.search(r'<a class="huggingface".*?</a>', header)[0]
        assert 'href="https://huggingface.co/datasets/redrouter/abliterated-benchmarks"' in link
        assert 'title="Dataset on Hugging Face"' in link
        svg = ElementTree.fromstring(re.search(r"<svg.*?</svg>", link)[0])
        assert svg.attrib == {
            "width": "20",
            "height": "20",
            "viewBox": "0 0 24 24",
            "aria-hidden": "true",
        }
        assert svg.find("path").attrib["fill"] == "currentColor"
        assert header.index('class="github"') < header.index(link) < header.index('class="sign-in"')


def test_hugging_face_structured_identities(site_input, tmp_path):
    build_site(site_input, tmp_path)
    for path in tmp_path.rglob("*.html"):
        graph = structured_graph(path.read_text())
        organization = next(item for item in graph if item["@type"] == "Organization")
        assert "https://huggingface.co/redrouter" in organization["sameAs"]
        for dataset in (item for item in graph if item["@type"] == "Dataset"):
            assert (
                "https://huggingface.co/datasets/redrouter/abliterated-benchmarks"
                in dataset["sameAs"]
            )
            assert dataset["creator"] == organization
