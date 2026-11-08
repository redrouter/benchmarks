import json
import shutil
from pathlib import Path


def site_data(output):
    fixtures = Path(__file__).parent / "fixtures"
    shutil.copytree(fixtures / "site-data", output)
    summary = json.loads((fixtures / "site.json").read_text())
    routes = {r["id"]: r for r in summary["routes"]}
    for route in routes.values():
        route["metrics"] = {}
    for route, scope, requests, successes, *values in summary.pop("metrics"):
        routes[route]["metrics"][scope] = {
            "requests": requests,
            "successes": successes,
            "success_rate": successes / requests,
            **{
                key: {"p50": values[2 * i], "p90": values[2 * i + 1]}
                for i, key in enumerate(
                    ("ttft_ms", "output_tokens_per_second", "end_to_end_500_ms")
                )
            },
            "errors": {
                "timeout": requests - successes,
                **dict.fromkeys(
                    (
                        "rate_limited",
                        "server_error",
                        "client_error",
                        "network",
                        "empty_output",
                        "protocol",
                    ),
                    0,
                ),
            },
        }
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return output


if __name__ == "__main__":
    import sys

    site_data(Path(sys.argv[1]))
