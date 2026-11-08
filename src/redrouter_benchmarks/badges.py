import math
import re
from html import escape


def write_badges(summary, output_dir, tokens_css):
    tokens = dict(re.findall(r"--([\w-]+):\s*([^;]+);", tokens_css.split("}", 1)[0]))
    groups = {f"{provider['id']}.svg": [] for provider in summary["providers"]}
    for route in summary["routes"]:
        groups[f"{route['provider_id']}.svg"].append(route)
        path = f"{route['provider_id']}/{route['model_id']}.svg"
        groups.setdefault(path, []).append(route)
    for path, routes in groups.items():
        eligible = []
        for route in routes:
            metrics = route["metrics"].get("all", {})
            speed = metrics.get("output_tokens_per_second", {}).get("p50")
            rate = metrics.get("success_rate")
            if (
                summary["window_hours"] == 72
                and route["status"] not in ("stale", "insufficient_data")
                and metrics.get("requests", 0) >= 8
                and metrics.get("successes", 0) >= 8
                and isinstance(speed, (int, float))
                and math.isfinite(speed)
                and speed >= 0
                and isinstance(rate, (int, float))
                and math.isfinite(rate)
                and 0 <= rate <= 1
            ):
                eligible.append((speed, rate))
        value = "no recent data"
        if eligible:
            speed, rate = max(eligible, key=lambda pair: pair[0])
            value = f"{speed:.0f} tok/s · {100 * rate:.1f}% up"
        label = "Abliterated Benchmarks"
        description = escape(f"{label}: {value} (p50 output speed; 72-hour success rate)")
        left, right = 146, max(112, len(value) * 6 + 16)
        width = left + right
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" role="img" '
            f'aria-label="{description}" width="{width}" height="20" '
            f'viewBox="0 0 {width} 20"><title>{description}</title>'
            f'<clipPath id="r"><rect width="{width}" height="20" rx="3"/></clipPath>'
            f'<g clip-path="url(#r)"><rect width="{width}" height="20" '
            f'fill="{escape(tokens["text"])}"/><rect x="{left}" width="{right}" height="20" '
            f'fill="{escape(tokens["brand"])}"/></g>'
            f'<g fill="{escape(tokens["primary-fg"])}" '
            f'font-family="{escape(tokens["font-sans"])}" font-size="11" text-anchor="middle">'
            f'<text x="{left / 2:g}" y="14">{label}</text>'
            f'<text x="{left + right / 2:g}" y="14">{value}</text></g></svg>'
        )
        target = output_dir / "badges" / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(svg, encoding="utf-8")
