import json
import re
import shutil
from datetime import UTC, datetime, timedelta
from html import escape
from pathlib import Path
from urllib.parse import urlsplit

import markdown
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

from redrouter_benchmarks.catalog import resource
from redrouter_benchmarks.external import EPOCH, licence_label, load_external

COLORS = [
    "#b0161c",
    "#3b5bdb",
    "#15803d",
    "#a15c07",
    "#8b5ac4",
    "#087e8b",
    "#c05b28",
    "#667080",
]
METRICS = [
    (
        "output_tokens_per_second",
        "Output speed",
        "tok/s",
        True,
        "Reported completion tokens after the first chunk, divided by streaming time. "
        "Samples without reported usage are excluded.",
    ),
    (
        "ttft_ms",
        "Time to first token",
        "s",
        False,
        "Request sent to first non-empty content, reasoning or tool-argument delta. Includes "
        "network "
        "time from the selected region.",
    ),
    (
        "end_to_end_500_ms",
        "Time to 500 tokens",
        "s",
        False,
        "Per request: TTFT + 500 ÷ output tok/s. Percentiles are computed after this calculation; "
        "this is a derived response time.",
    ),
    (
        "price",
        "Blended price",
        "$ / 1M tokens",
        False,
        "Declared price: (3 × input + output) ÷ 4. Excludes cache discounts. Prices are not "
        "measured "
        "billing receipts.",
    ),
]


def safe_file(base, relative):
    path = (base / relative).resolve()
    if not path.is_relative_to(base.resolve()):
        raise ValueError(f"Path leaves data directory: {relative}")
    return path


def safe_url(value):
    return value if urlsplit(value).scheme in ("http", "https") else "#"


def number(value, digits=1):
    return "—" if value is None else f"{value:,.{digits}f}"


def timestamp(value):
    return (
        "Never"
        if not value
        else datetime.fromisoformat(value).astimezone(UTC).strftime("%d %b %Y, %H:%M UTC")
    )


def metric_value(route, region, key):
    if key == "price":
        return route["price"]["blended_usd_per_mtok"]
    value = route["metrics"].get(region, {}).get(key, {}).get("p50")
    return value / 1000 if value is not None and key.endswith("_ms") else value


def svg_start(label, height, width=600):
    return (
        f'<svg role="img" aria-label="{escape(label, quote=True)}" viewBox="0 0 {width} {height}" '
        f'xmlns="http://www.w3.org/2000/svg"><title>{escape(label)}</title>'
    )


def bar_chart(routes, region, key, title, unit, higher, compact=False):
    values = [(r, metric_value(r, region, key)) for r in routes]
    values.sort(key=lambda pair: (pair[1] is None, -(pair[1] or 0) if higher else pair[1] or 0))
    maximum = max([v for _, v in values if v is not None] or [1]) * 1.12 or 1
    height = max(115, 44 + len(values) * 38)
    left, plot = (143, 165) if compact else (200, 340)
    parts = [
        svg_start(
            f"{title}, {unit}; {'higher' if higher else 'lower'} is better",
            height,
            360 if compact else 600,
        )
    ]
    for tick in range(5):
        x = left + tick * plot / 4
        parts.append(
            f'<line class="gridline" x1="{x}" y1="12" x2="{x}" y2="{height - 32}"/><text '
            f'class="axis" '
            f'x="{x}" y="{height - 10}" '
            f'text-anchor="middle">{number(maximum * tick / 4, 2 if key == "price" else 1)}</text>'
        )
    for i, (route, value) in enumerate(values):
        y = 16 + i * 38
        label = route["label"]
        tip = f"{label}: {number(value, 3 if key == 'price' else 1)} {unit}"
        short_label = label[:20] if compact else label[:25]
        parts.append(f'<text class="bar-label" x="0" y="{y + 16}">{escape(short_label)}</text>')
        if value is not None:
            width = max(0, value) / maximum * plot
            parts.append(
                f'<rect tabindex="0" aria-label="{escape(tip, quote=True)}" x="{left}" y="{y}" '
                f'width="{width:.2f}" '
                f'height="23" rx="3" fill="{route["color"]}" '
                f'data-tooltip="{escape(tip, quote=True)}"><title>{escape(tip)}</title></rect>'
            )
            parts.append(
                f'<text class="value" x="{left + 5 + width:.2f}" '
                f'y="{y + 16}">{number(value, 3 if key == "price" else 1)}</text>'
            )
        else:
            parts.append(f'<text class="axis" x="{left}" y="{y + 16}">Insufficient data</text>')
    if not values:
        parts.append('<text class="axis" x="200" y="45">No routes measured</text>')
    return Markup("".join(parts) + "</svg>")


def scatter_chart(routes, region, compact=False):
    values = [
        (
            r,
            r["price"]["blended_usd_per_mtok"],
            metric_value(r, region, "output_tokens_per_second"),
        )
        for r in routes
    ]
    values = [(r, x, y) for r, x, y in values if y is not None]
    xmax = max([x for _, x, _ in values] + [0.1]) * 1.15
    ymax = max([y for _, _, y in values] + [1]) * 1.15
    plot = 260 if compact else 500
    mid = 65 + plot / 2
    parts = [
        svg_start(
            "Output speed versus blended price. Upper left is faster and cheaper. Shading divides "
            "each axis at its midpoint.",
            340,
            350 if compact else 600,
        )
    ]
    for x, y, cls in [
        (65, 20, "quadrant-good"),
        (mid, 20, "quadrant"),
        (65, 150, "quadrant"),
        (mid, 150, "quadrant-muted"),
    ]:
        parts.append(f'<rect class="{cls}" x="{x}" y="{y}" width="{plot / 2}" height="130"/>')
    parts.append('<text class="axis" x="78" y="40">Faster · cheaper</text>')
    for tick in range(5):
        x, y = 65 + plot * tick / 4, 280 - 260 * tick / 4
        parts.append(
            f'<text class="axis" x="{x}" y="300" '
            f'text-anchor="middle">{number(xmax * tick / 4, 2)}</text><text '
            f'class="axis" x="54" y="{y + 4}" text-anchor="end">{number(ymax * tick / 4, 0)}</text>'
        )
    parts.append(
        f'<text class="axis" x="{mid}" y="330" text-anchor="middle">Blended price ($ / 1M tokens) '
        f'→</text><text class="axis" transform="translate(16 150) rotate(-90)" '
        f'text-anchor="middle">Output '
        f"speed (tok/s) →</text>"
    )
    for r, x, y in values:
        label = f"{r['label']}: ${x:.3f} / 1M tokens; {y:.1f} tok/s"
        parts.append(
            f'<circle tabindex="0" aria-label="{escape(label, quote=True)}" '
            f'cx="{65 + x / xmax * plot:.2f}" '
            f'cy="{280 - y / ymax * 260:.2f}" r="6" fill="{r["color"]}" '
            f'data-tooltip="{escape(label, quote=True)}"><title>{escape(label)}</title></circle>'
        )
    if not values:
        parts.append(
            f'<text class="axis" x="{mid}" y="150" text-anchor="middle">No speed samples '
            f"available</text>"
        )
    return Markup("".join(parts) + "</svg>")


def history_chart(routes, histories, region, key, title, unit, days, generated_at, compact=False):
    end = datetime.fromisoformat(generated_at).astimezone(UTC)
    start = end - timedelta(days=days)
    series = []
    for route in routes:
        history = histories[route["id"]]["points"]
        scopes = sorted({p["region"] for p in history}) if region == "all" else [region]
        if region == "all" and "all" in scopes:
            scopes = ["all"]
        for scope in scopes:
            points = []
            for p in history:
                t = datetime.fromisoformat(p["t"]).astimezone(UTC)
                if p["region"] != scope or not start <= t <= end:
                    continue
                value = (
                    100 * p["successes"] / p["requests"]
                    if key == "success_rate" and p["requests"]
                    else None
                )
                if key != "success_rate":
                    value = p[key + "_p50"]
                    if value is not None and key == "ttft_ms":
                        value /= 1000
                points.append((t, value))
            series.append(
                (
                    dict(
                        route,
                        label=route["label"] + (f" · {scope}" if region == "all" else ""),
                        dash="5 3" if scope == "us-west-2" and region == "all" else "none",
                    ),
                    sorted(points),
                )
            )
    maximum = (
        100
        if key == "success_rate"
        else max([v for _, ps in series for _, v in ps if v is not None] or [1]) * 1.12 or 1
    )
    left, plot = (50, 285) if compact else (62, 513)
    parts = [svg_start(f"{title}, {unit}, trailing {days} days", 290, 360 if compact else 600)]
    for tick in range(5):
        y = 235 - tick * 210 / 4
        parts.append(
            f'<line class="gridline" x1="{left}" y1="{y}" x2="{left + plot}" y2="{y}"/><text '
            f'class="axis" '
            f'x="{left - 10}" y="{y + 4}" text-anchor="end">{number(maximum * tick / 4)}</text>'
        )
        t = start + timedelta(days=days * tick / 4)
        parts.append(
            f'<text class="axis" x="{left + plot * tick / 4}" y="260" '
            f'text-anchor="middle">{t:%d %b}</text>'
        )
    count = 0
    for route, points in series:
        segment = []
        previous = None
        for t, value in points + [(end, None)]:
            if (
                value is None
                or previous is not None
                and (t - previous).total_seconds() > 3 * 3600 * 1.5
            ):
                if segment:
                    parts.append(
                        f'<polyline fill="none" stroke="{route["color"]}" stroke-width="2" '
                        f'stroke-dasharray="{route["dash"]}" '
                        f'points="{" ".join(segment)}"/>'
                    )
                segment = []
            if value is not None:
                x = left + (t - start).total_seconds() / (days * 86400) * plot
                y = 235 - value / maximum * 210
                segment.append(f"{x:.2f},{y:.2f}")
                tip = f"{route['label']} · {t:%d %b %H:%M} UTC: {value:.1f} {unit}"
                parts.append(
                    f'<circle tabindex="0" aria-label="{escape(tip, quote=True)}" cx="{x:.2f}" '
                    f'cy="{y:.2f}" '
                    f'r="3" fill="{route["color"]}" '
                    f'data-tooltip="{escape(tip, quote=True)}">'
                    f"<title>{escape(tip)}</title></circle>"
                )
                count += 1
            previous = t
    if not count:
        parts.append(
            f'<text class="axis" x="{left + plot / 2}" y="130" text-anchor="middle">No history '
            f"available "
            f"for this region</text>"
        )
    parts.append(f'<text class="axis" x="62" y="284">{escape(unit)} · UTC</text>')
    return Markup("".join(parts) + "</svg>")


def build_site(data_dir: Path, output_dir: Path) -> None:
    data_dir, output_dir = Path(data_dir).resolve(), Path(output_dir).resolve()
    if (
        output_dir == data_dir
        or output_dir.is_relative_to(data_dir)
        or data_dir.is_relative_to(output_dir)
    ):
        raise ValueError("Data and output directories must not overlap")
    methodology = resource("METHODOLOGY.md")
    if not methodology.is_file():
        raise FileNotFoundError(f"Methodology is required to build the site: {methodology}")
    summary = json.loads((data_dir / "summary.json").read_text())
    external = load_external(data_dir / "external.json")
    further_reading = json.loads((resource("site") / "data" / "further-reading.json").read_text())
    if summary["schema_version"] != 1:
        raise ValueError("Unsupported summary schema_version")
    assets = resource("site") / "assets"

    def logo_for(provider):
        path = assets / "providers" / Path(provider["logo"]).name
        if path.suffix not in (".svg", ".png") or not path.is_file():
            raise ValueError(f"No local provider logo for {provider['id']}: {provider['logo']}")
        return f"/assets/providers/{path.name}"

    provider_links = json.loads((assets / "providers" / "links.json").read_text())
    providers = {
        p["id"]: dict(
            p,
            url=safe_url(p["url"]),
            logo=logo_for(p),
            terms=provider_links.get(Path(p["logo"]).stem, {}).get("terms"),
        )
        for p in summary["providers"]
    }

    def model_logo(model):
        path = assets / "models" / (re.sub(r"[^a-z0-9]", "", model["creator"].lower()) + ".svg")
        if not path.is_file():
            raise ValueError(f"No local model logo for {model['id']}: {model['creator']}")
        return f"/assets/models/{path.name}"

    models = {m["id"]: dict(m, logo=model_logo(m)) for m in summary["models"]}
    histories = {}
    routes = []
    for route in summary["routes"]:
        for field in ("provider_id", "model_id"):
            if not re.fullmatch(r"[a-z0-9][a-z0-9.-]*", route[field]):
                raise ValueError(f"Invalid {field}: {route[field]}")
        provider = providers[route["provider_id"]]
        label = provider["name"] + (f" · {route['upstream']}" if route["upstream"] else "")
        if (
            sum(
                r["provider_id"] == route["provider_id"] and r["model_id"] == route["model_id"]
                for r in summary["routes"]
            )
            > 1
        ):
            label += f" · {route['modification']}"
        route = dict(
            route,
            provider=provider,
            model=models[route["model_id"]],
            label=label,
            color=COLORS[list(providers).index(route["provider_id"]) % len(COLORS)],
        )
        route["price"] = dict(route["price"], source_url=safe_url(route["price"]["source_url"]))
        history = json.loads(safe_file(data_dir, route["history"]).read_text())
        if history["route_id"] != route["id"] or history["schema_version"] != 1:
            raise ValueError(f"History does not match route: {route['id']}")
        histories[route["id"]] = history
        routes.append(route)
    for entity in [*providers, *models]:
        if not re.fullmatch(r"[a-z0-9][a-z0-9.-]*", entity):
            raise ValueError(f"Invalid page id: {entity}")
    output_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(assets, output_dir / "assets", dirs_exist_ok=True)
    shutil.copytree(data_dir, output_dir / "data", dirs_exist_ok=True)
    shutil.copytree(resource("schema"), output_dir / "data" / "schema", dirs_exist_ok=True)
    if resource("LICENSE").is_file():
        shutil.copy2(resource("LICENSE"), output_dir / "data" / "LICENSE")
    env = Environment(
        loader=FileSystemLoader(resource("site") / "templates"),
        autoescape=select_autoescape(),
    )
    env.filters.update(
        number=number, timestamp=timestamp, safe_url=safe_url, licence_label=licence_label
    )
    default_model = next(
        (m for m in models if "deepseek" in m and "1017" in m), next(iter(models), "")
    )
    regions = [{"id": "all", "label": "All regions"}, *summary["regions"]]
    common = {
        "summary": summary,
        "providers": providers,
        "models": models,
        "regions": regions,
        "routes": routes,
        "default_model": default_model,
        "metrics": METRICS,
        "bar_chart": bar_chart,
        "scatter_chart": scatter_chart,
        "history_chart": history_chart,
        "histories": histories,
        "external": external,
        "epoch_url": EPOCH,
        "further_reading": further_reading,
    }

    def render(path, template, **context):
        target = output_dir / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(env.get_template(template).render(**common, **context))

    render(
        "index.html",
        "comparison.html",
        title="Inference provider benchmarks",
        active="overview",
        model=None,
    )
    render(
        "models/index.html",
        "directory.html",
        title="Models",
        active="models",
        kind="models",
    )
    render(
        "external/index.html",
        "external.html",
        title="External results",
        active="external",
    )
    for model in models.values():
        render(
            f"models/{model['id']}/index.html",
            "comparison.html",
            title=model["name"],
            active="models",
            model=model,
        )
    render(
        "providers/index.html",
        "directory.html",
        title="Providers",
        active="providers",
        kind="providers",
    )
    for provider in providers.values():
        render(
            f"providers/{provider['id']}/index.html",
            "provider.html",
            title=provider["name"],
            active="providers",
            provider=provider,
        )
    render(
        "methodology/index.html",
        "content.html",
        title="Methodology",
        active="methodology",
        data_request=True,
        content=Markup(
            markdown.markdown(methodology.read_text(), extensions=["tables", "fenced_code", "toc"])
        ),
    )
    render(
        "404.html",
        "content.html",
        title="Page not found",
        active="",
        content=Markup(
            '<p>This page could not be found. <a href="/">Return to the overview</a>.</p>'
        ),
    )
