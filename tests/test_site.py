import hashlib
import json
from functools import partial
from html.parser import HTMLParser
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from threading import Thread

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
    assert digest.hexdigest() == "ec00f48674a0f52efcbccfb6bf7400094e95aee001e33c355acfb94bf54dad2c"


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
