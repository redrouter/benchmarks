import argparse
import asyncio
import os
from pathlib import Path

from redrouter_benchmarks.aggregate import REGIONS, aggregate
from redrouter_benchmarks.catalog import Catalog
from redrouter_benchmarks.clock import parse_time
from redrouter_benchmarks.demo import demo_data
from redrouter_benchmarks.runner import run_suite


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="redrouter-benchmarks")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Measure enabled routes using ordinary provider accounts")
    run.add_argument("--region", choices=REGIONS, required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--route", action="append", default=[])
    run.add_argument("--seed", type=int)
    agg = sub.add_parser("aggregate", help="Aggregate retained raw observations")
    agg.add_argument("--input", type=Path, required=True)
    agg.add_argument("--output", type=Path, required=True)
    agg.add_argument("--now", type=parse_time)
    demo = sub.add_parser(
        "demo-data", help="Generate synthetic observations without network access"
    )
    demo.add_argument("--output", type=Path, required=True)
    demo.add_argument("--days", type=int, default=14)
    demo.add_argument("--seed", type=int, default=7)
    external = sub.add_parser(
        "fetch-external", help="Import licensed stock-model results from Epoch"
    )
    external.add_argument("--output", type=Path, required=True)
    external.add_argument("--zip", type=Path)
    build = sub.add_parser("build-site", help="Build the static benchmark site")
    build.add_argument("--data", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    serve_parser = sub.add_parser("serve", help="Preview a static site locally")
    serve_parser.add_argument("--dist", type=Path, required=True)
    serve_parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            catalog = Catalog.load()
            if args.route:
                unknown = set(args.route) - catalog.routes.keys()
                if unknown:
                    raise ValueError("Unknown route: " + ", ".join(sorted(unknown)))
                routes = [catalog.routes[r] for r in sorted(set(args.route))]
                if any(not r["enabled"] for r in routes):
                    raise ValueError("Selected route is disabled")
            else:
                routes = [
                    r
                    for r in catalog.routes.values()
                    if r["enabled"]
                    and os.environ.get(catalog.providers[r["provider_id"]]["api_key_env"])
                ]
            path = asyncio.run(
                run_suite(
                    catalog, region=args.region, output=args.output, routes=routes, seed=args.seed
                )
            )
            print(path)
        elif args.command == "aggregate":
            result = aggregate(args.input, args.output, now=args.now)
            print(f"Aggregated {len(result['routes'])} routes into {args.output}")
        elif args.command == "demo-data":
            result = asyncio.run(demo_data(args.output, args.days, args.seed))
            print(f"Demo data: {len(result['routes'])} routes, {args.days} days, seed {args.seed}")
        elif args.command == "fetch-external":
            from redrouter_benchmarks.external import fetch_external

            fetch_external(args.output, args.zip)
        elif args.command == "build-site":
            from redrouter_benchmarks.site import build_site

            build_site(args.data, args.output)
        elif args.command == "serve":
            from redrouter_benchmarks.site import serve

            serve(args.dist, args.port)
    except (ValueError, FileNotFoundError, FileExistsError) as exc:
        parser.exit(2, f"error: {exc}\n")


if __name__ == "__main__":
    main()
