import json
import math
import re
import shutil
from datetime import UTC, datetime, timedelta
from functools import partial
from html import escape
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from statistics import median
from urllib.parse import urlsplit
from xml.etree.ElementTree import Element, SubElement, tostring

import markdown
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

from redrouter_benchmarks.aggregate import CADENCE_HOURS
from redrouter_benchmarks.badges import write_badges
from redrouter_benchmarks.catalog import resource
from redrouter_benchmarks.external import EPOCH, licence_label, load_external

SITE_URL = "https://benchmarks.redrouter.org"
SITE_NAME = "Abliterated Benchmarks by RedRouter"
INDEXNOW_KEY = "384fbe6b22e027070c349c862cddbc5c"

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
    return f'<svg role="img" viewBox="0 0 {width} {height}"><title>{escape(label)}</title>'


def truncate(text, length):
    return text if len(text) <= length else text[: length - 1].rstrip() + "…"


def bar_chart(routes, region, key, title, unit, higher):
    values = [(r, metric_value(r, region, key)) for r in routes]
    values.sort(key=lambda pair: (pair[1] is None, -(pair[1] or 0) if higher else pair[1] or 0))
    maximum = max([v for _, v in values if v is not None] or [1]) * 1.12 or 1
    height = max(115, 44 + len(values) * 38)
    left, plot = 0, 325
    parts = [
        svg_start(
            f"{title}, {unit}; {'higher' if higher else 'lower'} is better",
            height,
            600,
        )
    ]
    parts.append('<g class="bar-plot">')
    for tick in range(5):
        x = left + tick * plot / 4
        parts.append(
            f'<line class="gridline" x1="{x}" y1="12" x2="{x}" y2="{height - 32}"/><text '
            f'class="axis" '
            f'x="{x}" y="{height - 10}" '
            f'text-anchor="middle">{number(maximum * tick / 4, 2 if key == "price" else 1)}</text>'
        )
    labels = []
    for i, (route, value) in enumerate(values):
        y = 16 + i * 38
        label = route["label"]
        tip = f"{label}: {number(value, 3 if key == 'price' else 1)} {unit}"
        labels.append(f'<use href="#label-{route["chart_label"]}" y="{y}"/>')
        if value is not None:
            width = max(0, value) / maximum * plot
            parts.append(
                f'<rect tabindex="0" aria-label="{escape(tip, quote=True)}" x="{left}" y="{y}" '
                f'width="{width:.2f}" '
                f'height="23" rx="3" fill="{route["color"]}"></rect>'
            )
            parts.append(
                f'<text class="value" x="{left + 5 + width:.2f}" '
                f'y="{y + 16}">{number(value, 3 if key == "price" else 1)}</text>'
            )
        else:
            parts.append(f'<text class="axis" x="{left}" y="{y + 16}">Insufficient data</text>')
    parts.append("</g>" + "".join(labels))
    if not values:
        parts.append('<text class="axis" x="200" y="45">No routes measured</text>')
    return Markup("".join(parts) + "</svg>")


def scatter_chart(routes, region):
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
    plot = 500
    mid = plot / 2
    parts = [
        svg_start(
            "Output speed versus blended price. Upper left is faster and cheaper. Shading divides "
            "each axis at its midpoint.",
            340,
            600,
        )
    ]
    parts.append('<g class="scatter-plot">')
    for x, y, cls in [
        (0, 20, "quadrant-good"),
        (mid, 20, "quadrant"),
        (0, 150, "quadrant"),
        (mid, 150, "quadrant-muted"),
    ]:
        parts.append(f'<rect class="{cls}" x="{x}" y="{y}" width="{plot / 2}" height="130"/>')
    parts.append('<text class="axis quadrant-label" x="13" y="40">Faster · cheaper</text>')
    for tick in range(5):
        x, y = plot * tick / 4, 280 - 260 * tick / 4
        parts.append(
            f'<text class="axis" x="{x}" y="300" '
            f'text-anchor="middle">{number(xmax * tick / 4, 2)}</text><text '
            f'class="axis vertical-axis" x="-11" y="{y + 4}" text-anchor="end">'
            f"{number(ymax * tick / 4, 0)}</text>"
        )
    parts.append(
        f'<text class="axis" x="{mid}" y="330" text-anchor="middle">Blended price ($ / 1M tokens) '
        '→</text></g><text class="axis" transform="translate(16 150) rotate(-90)" '
        f'text-anchor="middle">Output '
        f"speed (tok/s) →</text>"
    )
    parts.append('<g class="scatter-plot">')
    for r, x, y in values:
        label = f"{r['label']}: ${x:.3f} / 1M tokens; {y:.1f} tok/s"
        parts.append(
            f'<circle tabindex="0" aria-label="{escape(label, quote=True)}" '
            f'cx="{x / xmax * plot:.2f}" '
            f'cy="{280 - y / ymax * 260:.2f}" r="6" fill="{r["color"]}"></circle>'
        )
    if not values:
        parts.append(
            f'<text class="axis" x="{mid}" y="150" text-anchor="middle">No speed samples '
            f"available</text>"
        )
    return Markup("".join(parts) + "</g></svg>")


def history_chart(routes, histories, region, key, title, unit, days, generated_at):
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
    first = min((t for _, ps in series for t, _ in ps), default=start)
    start = max(start, first - timedelta(hours=2))
    span = (end - start).total_seconds()
    short = span < 3 * 86400
    maximum = (
        100
        if key == "success_rate"
        else max([v for _, ps in series for _, v in ps if v is not None] or [1]) * 1.12 or 1
    )
    left, plot = 0, 513
    parts = [
        svg_start(f"{title}, {unit}, trailing {days} days", 290),
        '<g class="history-plot">',
    ]
    for tick in range(5):
        y = 235 - tick * 210 / 4
        parts.append(
            f'<line class="gridline" x1="{left}" y1="{y}" x2="{left + plot}" y2="{y}"/><text '
            f'class="axis" '
            f'x="{left - 10}" y="{y + 4}" text-anchor="end">{number(maximum * tick / 4)}</text>'
        )
        cls = "axis compact-hidden" if short and tick % 2 else "axis"
        t = start + timedelta(seconds=span * tick / 4)
        if short:
            t = (t + timedelta(minutes=30)).replace(minute=0, second=0, microsecond=0)
        label = f"{t:%d %b %H:%M}" if short else f"{t:%d %b}"
        x = left + (t - start).total_seconds() / span * plot if short else left + plot * tick / 4
        anchor = "end" if short and tick == 4 else "middle"
        parts.append(
            f'<text class="{cls}" x="{x:.2f}" y="260" text-anchor="{anchor}">{label}</text>'
        )
    count = 0
    for route, points in series:
        segment = []
        previous = None
        for t, value in points + [(end, None)]:
            if (
                value is None
                or previous is not None
                and (t - previous).total_seconds() > CADENCE_HOURS * 3600 * 1.5
            ):
                if segment:
                    parts.append(
                        f'<polyline fill="none" stroke="{route["color"]}" stroke-width="2" '
                        f'stroke-dasharray="{route["dash"]}" '
                        f'points="{" ".join(segment)}"/>'
                    )
                segment = []
            if value is not None:
                x = left + (t - start).total_seconds() / span * plot
                y = 235 - value / maximum * 210
                segment.append(f"{x:.2f},{y:.2f}")
                tip = f"{route['label']} · {t:%d %b %H:%M} UTC: {value:.1f} {unit}"
                parts.append(
                    f'<circle tabindex="0" aria-label="{escape(tip, quote=True)}" cx="{x:.2f}" '
                    f'cy="{y:.2f}" '
                    f'r="3" fill="{route["color"]}"></circle>'
                )
                count += 1
            previous = t
    if not count:
        parts.append(
            f'<text class="axis" x="{left + plot / 2}" y="130" text-anchor="middle">No history '
            f"available "
            f"for this region</text>"
        )
    parts.append(f'</g><text class="axis" x="62" y="284">{escape(unit)} · UTC</text>')
    return Markup("".join(parts) + "</svg>")


def finite(value):
    return isinstance(value, (int, float)) and math.isfinite(value)


def llms_text(summary, routes, data_files):
    lines = [
        f"# {SITE_NAME}",
        "",
        "> RedRouter benchmarks inference APIs serving abliterated and uncensored open-weight "
        "models, with standard-model controls. Speed, latency, observed success rate, and "
        f"declared prices cover a trailing {summary['window_hours']}-hour window; "
        f"built from {'demo' if summary.get('demo') else 'observed'} data generated "
        f"{summary['generated_at']}. Performance does not establish answer quality "
        "or refusal rate.",
        "",
        "## Models",
    ]
    for model in summary["models"]:
        candidates = [
            (r, metric_value(r, "all", "output_tokens_per_second"))
            for r in routes
            if r["model_id"] == model["id"] and r["metrics"].get("all", {}).get("requests", 0) >= 8
        ]
        fastest = max(
            ((r, speed) for r, speed in candidates if finite(speed)),
            key=lambda pair: pair[1],
            default=None,
        )
        detail = "No route has sufficient pooled speed data."
        if fastest:
            route, speed = fastest
            upstream = f" ({route['upstream']})" if route["upstream"] else ""
            detail = f"Fastest route: {route['id']}{upstream}, pooled p50 {speed:.1f} tok/s."
        lines.append(f"- [{model['name']}]({SITE_URL}/models/{model['id']}/): {detail}")
    lines += ["", "## Providers"]
    lines += [
        f"- [{p['name']}]({SITE_URL}/providers/{p['id']}/): Measured API routes and performance."
        for p in summary["providers"]
    ]
    lines += [
        "",
        "## Documentation",
        f"- [Methodology]({SITE_URL}/methodology/): Workload, metrics, sampling, and limitations.",
        f"- [External results]({SITE_URL}/external/): "
        "Licensed stock-model scores from other evaluators.",
        f"- [What is abliteration?]({SITE_URL}/what-is-abliteration/): Weight edits, "
        "uncensored fine-tuning, and trade-offs.",
        "",
        "## Data",
    ]
    for path in data_files:
        description = (
            "Published route summary."
            if path.name in ("summary.csv", "summary.json")
            else "Public data schema."
            if path.parts[0] == "schema"
            else "Licensed external evaluation results."
            if path.name == "external.json"
            else "Route history with timing and success metrics."
        )
        lines.append(f"- [{path.as_posix()}]({SITE_URL}/data/{path.as_posix()}): {description}")
    return "\n".join(lines) + "\n"


def page_metadata(summary, routes, path, context, downloads):
    model, provider = context.get("model"), context.get("provider")
    article = context.get("article")
    page = path.removesuffix("index.html") if path != "404.html" else "404/"
    canonical = f"{SITE_URL}/{page}"
    eligible = [r for r in routes if r["metrics"].get("all", {}).get("requests", 0) >= 8]
    selected = [
        r
        for r in eligible
        if (not model or r["model_id"] == model["id"])
        and (not provider or r["provider_id"] == provider["id"])
    ]

    def speed(route):
        return route["metrics"]["all"].get("output_tokens_per_second", {}).get("p50")

    fastest = max((r for r in selected if finite(speed(r))), key=speed, default=None)

    def route_name(route):
        name = route["provider"]["name"]
        return f"{route['upstream']} via {name}" if route["upstream"] else name

    def describe(base, clauses):
        result = truncate(base, 160)
        for clause in clauses:
            if clause and len(result) + len(clause) + 1 <= 160:
                result += " " + clause
        return result

    fastest_clause = ""
    if fastest and not provider:
        label = route_name(fastest)
        if not model:
            label = f"{fastest['provider']['name']} on {fastest['model']['name']}"
        fastest_clause = f"Fastest: {label}, {speed(fastest):.0f} tok/s."
    window = summary["window_hours"]
    cadence = f"Updated every {CADENCE_HOURS} hours."
    titles = {
        "": "Abliterated & Uncensored LLM API Benchmarks: Speed, Price, Uptime",
        "models/": "Abliterated & Uncensored Models Compared by Provider",
        "providers/": "Uncensored & Abliterated LLM API Providers Compared",
        "methodology/": "Methodology: How We Benchmark Uncensored LLM APIs",
        "external/": "External Stock-Model Benchmark Results for Abliterated Model Families",
        "404/": f"Page not found · {SITE_NAME}",
    }
    if article:
        title, description = article["title"], article["description"]
    elif model:
        modified = any(r["modification"] != "none" for r in routes if r["model_id"] == model["id"])
        qualifier = " Abliterated / Uncensored" if modified else " Standard-Model"
        title = f"{model['name']}{qualifier} API: Speed, Price & Uptime by Provider"
        cheapest = min(
            (r for r in selected if finite(r["price"].get("blended_usd_per_mtok"))),
            key=lambda r: r["price"]["blended_usd_per_mtok"],
            default=None,
        )
        price_clause = (
            f"Lowest price: {route_name(cheapest)}, "
            f"${cheapest['price']['blended_usd_per_mtok']:.3g}/1M."
            if cheapest
            else ""
        )
        base = (
            f"{model['name']}{' abliterated and uncensored' if modified else ''} APIs: "
            f"{len({r['provider_id'] for r in selected})} providers over {window}h."
        )
        if cheapest and len(" ".join([base, fastest_clause, price_clause])) > 160:
            price_clause = f"Lowest: ${cheapest['price']['blended_usd_per_mtok']:.3g}/1M."
        description = describe(base, [fastest_clause, price_clause, cadence])
    elif provider:
        title = f"{provider['name']} API Speed & Uptime for Uncensored Models"
        if not any(
            r["modification"] != "none" for r in routes if r["provider_id"] == provider["id"]
        ):
            title = f"{provider['name']} API Speed & Uptime for Standard Models"
        medians = []
        for key, label, scale, unit in [
            ("output_tokens_per_second", "output", 1, "tok/s"),
            ("ttft_ms", "TTFT", 0.001, "s"),
            ("success_rate", "success", 100, "%"),
        ]:
            values = [
                r["metrics"]["all"].get(key)
                if key == "success_rate"
                else r["metrics"]["all"].get(key, {}).get("p50")
                for r in selected
            ]
            values = [v * scale for v in values if finite(v)]
            if values:
                medians.append(f"{median(values):.3g}{'' if unit == '%' else ' '}{unit} {label}")
        description = describe(
            f"{provider['name']}: {len(selected)} routes benchmarked over {window}h.",
            [("Route medians: " + ", ".join(medians) + ".") if medians else "", cadence],
        )
    else:
        title = titles[page]
        descriptions = {
            "": "Compare",
            "models/": "Browse",
            "providers/": "Explore",
        }
        if page in descriptions:
            description = describe(
                f"{descriptions[page]} abliterated and uncensored LLM APIs: "
                f"{len(summary['models'])} models, {len(summary['providers'])} providers "
                f"over {window}h.",
                [fastest_clause, cadence],
            )
        else:
            description = {
                "methodology/": "How RedRouter measures uncensored LLM API speed, latency, "
                "success rate and price: workloads, sampling, aggregation and limitations.",
                "external/": "Published stock-model scores for families with abliterated routes. "
                "Compare evaluators, benchmark versions, dates and licensed sources.",
                "404/": "This benchmark page could not be found. Browse model and provider "
                "comparisons or return to the RedRouter benchmark overview.",
            }[page]
    organization = {
        "@type": "Organization",
        "@id": "https://redrouter.org/#organization",
        "name": "RedRouter",
        "url": "https://redrouter.org",
        "logo": f"{SITE_URL}/assets/logo.svg",
    }
    graph = [organization]
    if not page:
        graph.append({"@type": "WebSite", "name": SITE_NAME, "url": canonical})
    else:
        crumbs = [("Overview", f"{SITE_URL}/")]
        if model or provider:
            kind = "models" if model else "providers"
            crumbs.append((kind.title(), f"{SITE_URL}/{kind}/"))
        crumbs.append((context["title"], canonical))
        graph.append(
            {
                "@type": "BreadcrumbList",
                "itemListElement": [
                    {"@type": "ListItem", "position": i, "name": name, "item": url}
                    for i, (name, url) in enumerate(crumbs, 1)
                ],
            }
        )
    if not page or model:
        generated = summary["generated_at"]
        start = datetime.fromisoformat(generated) - timedelta(hours=window)
        graph.append(
            {
                "@type": "Dataset",
                "name": f"{model['name']} API route benchmarks" if model else SITE_NAME,
                "description": (
                    f"{'Demonstration' if summary.get('demo') else 'Observed'} API speed, latency, "
                    f"success rate and declared blended price for "
                    f"{model['name'] if model else 'abliterated and uncensored model'} routes "
                    f"over a trailing {window}-hour window. Regional and pooled metrics are "
                    "published; speed and latency percentiles require eight usable successes."
                ),
                "url": canonical,
                "creator": organization,
                "license": "https://creativecommons.org/licenses/by/4.0/",
                "isAccessibleForFree": True,
                "dateModified": generated,
                "temporalCoverage": f"{start.isoformat()}/{generated}",
                "measurementTechnique": f"Serial streaming API requests every {CADENCE_HOURS} "
                "hours from US East and West; pooled request-level p50 over the trailing "
                f"{window} hours, success rate including failures, "
                "declared 3:1 input/output price.",
                "variableMeasured": [
                    "Output speed",
                    "Time to first token",
                    "Time to 500 tokens",
                    "Success rate",
                    "Blended price",
                ],
                "keywords": ["LLM", "API", "abliterated", "uncensored", "benchmarks"],
                "distribution": downloads,
            }
        )
    if article:
        graph += [
            {
                "@type": "Article",
                "headline": article["title"],
                "description": article["description"],
                "mainEntityOfPage": canonical,
                "publisher": organization,
                "datePublished": article["datePublished"],
                "dateModified": article["dateModified"],
            },
            {
                "@type": "FAQPage",
                "mainEntity": [
                    {
                        "@type": "Question",
                        "name": item["question"],
                        "acceptedAnswer": {"@type": "Answer", "text": item["answer"]},
                    }
                    for item in article["faq"]
                ],
            },
        ]
    return {
        "seo_title": title,
        "description": description,
        "canonical": canonical,
        "noindex": path == "404.html",
        "structured_data": {"@context": "https://schema.org", "@graph": graph},
    }


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

    def logo_for(provider, key="logo"):
        path = assets / "providers" / Path(provider[key]).name
        if path.suffix not in (".svg", ".png") or not path.is_file():
            raise ValueError(f"No local provider logo for {provider['id']}: {provider[key]}")
        return f"/assets/providers/{path.name}"

    provider_links = json.loads((assets / "providers" / "links.json").read_text())
    providers = {
        p["id"]: dict(
            p,
            url=safe_url(p["url"]),
            logo=logo_for(p),
            gateway_logo=logo_for(p, "gateway_logo") if p.get("gateway_logo") else None,
            terms=provider_links.get(Path(p.get("gateway_logo") or p["logo"]).stem, {}).get(
                "terms"
            ),
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
        upstream = route["upstream"]
        if upstream and upstream.lower() in provider["name"].lower():
            upstream = None
        label = provider["name"] + (f" · {upstream}" if upstream else "")
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
            chart_label=len(routes),
            upstream=upstream,
            color=COLORS[
                [r["id"] for r in summary["routes"] if r["model_id"] == route["model_id"]].index(
                    route["id"]
                )
                % len(COLORS)
            ],
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
    write_badges(summary, output_dir, (assets / "tokens.css").read_text())
    shutil.copytree(data_dir, output_dir / "data", dirs_exist_ok=True)
    shutil.copytree(resource("schema"), output_dir / "data" / "schema", dirs_exist_ok=True)
    shutil.copy2(resource("site") / "data" / "LICENSE.txt", output_dir / "data" / "LICENSE.txt")
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
        "truncate": truncate,
        "scatter_chart": scatter_chart,
        "history_chart": history_chart,
        "histories": histories,
        "bucket_hours": CADENCE_HOURS,
        "external": external,
        "epoch_url": EPOCH,
        "further_reading": further_reading,
    }
    downloads = [
        {
            "@type": "DataDownload",
            "contentUrl": f"{SITE_URL}/data/{path.relative_to(data_dir).as_posix()}",
            "encodingFormat": "text/csv" if path.suffix == ".csv" else "application/json",
        }
        for path in sorted(data_dir.rglob("*"))
        if path.is_file() and path.suffix in (".csv", ".json")
    ]
    sitemap = Element("urlset", xmlns="http://www.sitemaps.org/schemas/sitemap/0.9")

    def render(path, template, **context):
        target = output_dir / path
        target.parent.mkdir(parents=True, exist_ok=True)
        metadata = page_metadata(summary, routes, path, context, downloads)
        target.write_text(env.get_template(template).render(**common, **context, **metadata))
        if not metadata["noindex"]:
            entry = SubElement(sitemap, "url")
            SubElement(entry, "loc").text = metadata["canonical"]
            SubElement(entry, "lastmod").text = summary["generated_at"]

    render(
        "index.html",
        "comparison.html",
        title="Abliterated Model API Speed, Reliability and Price",
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
        badge_url = f"{SITE_URL}/badges/{provider['id']}.svg"
        badge_link = f"{SITE_URL}/providers/{provider['id']}/?utm_source=badge"
        badge_alt = f"{provider['name']} on Abliterated Benchmarks"
        render(
            f"providers/{provider['id']}/index.html",
            "provider.html",
            title=provider["name"],
            active="providers",
            provider=provider,
            badge_html=(
                f'<a href="{badge_link}"><img src="{badge_url}" '
                f'alt="{escape(badge_alt)}" height="20"></a>'
            ),
            badge_markdown=f"[![{badge_alt}]({badge_url})]({badge_link})",
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
    article = json.loads((resource("site") / "data" / "abliteration.json").read_text())
    render(
        "what-is-abliteration/index.html",
        "abliteration.html",
        title=article["title"],
        active="",
        article=article,
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
    (output_dir / "robots.txt").write_text(
        f"User-agent: *\nAllow: /\n\nSitemap: {SITE_URL}/sitemap.xml\n"
    )
    (output_dir / "sitemap.xml").write_bytes(
        tostring(sitemap, encoding="utf-8", xml_declaration=True)
    )
    (output_dir / f"{INDEXNOW_KEY}.txt").write_text(INDEXNOW_KEY)
    (output_dir / "llms.txt").write_text(
        llms_text(
            summary,
            routes,
            [
                p.relative_to(output_dir / "data")
                for p in sorted((output_dir / "data").rglob("*"))
                if p.is_file() and p.suffix in (".csv", ".json")
            ],
        )
    )


class SiteHandler(SimpleHTTPRequestHandler):
    def send_error(self, code, message=None, explain=None):
        page = Path(self.directory) / "404.html"
        if code != 404 or not page.is_file():
            return super().send_error(code, message, explain)
        body = page.read_bytes()
        self.send_response(404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)


def serve(dist_dir: Path, port: int) -> None:
    if not dist_dir.is_dir() or not (dist_dir / "index.html").is_file():
        raise FileNotFoundError(f"Built site not found: {dist_dir}")
    handler = partial(SiteHandler, directory=str(dist_dir.resolve()))
    with ThreadingHTTPServer(("127.0.0.1", port), handler) as server:
        print(f"Preview: http://127.0.0.1:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
