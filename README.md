# RedRouter Benchmarks

Reproducible streaming API measurements for refusal-reduced open-weight models and standard-model controls. Maintained by **RedRouter, an inference provider**; RedRouter routes are excluded. Listings describe advertised configurations, not verified weights or serving health.

With Python 3.12 and [uv](https://docs.astral.sh/uv/):

```sh
uv sync --locked
uv run redrouter-benchmarks demo-data --output out/demo --days 14 --seed 7
uv run redrouter-benchmarks build-site --data out/demo --output dist
uv run redrouter-benchmarks serve --dist dist --port 8787
```

Open `http://localhost:8787`. Demo data uses a simulated clock and local transport, with no provider calls. Seed 7 ends at `2026-11-05T00:00:00Z`, includes degradation and stale observations, and marks every record `demo: true`.

To measure a route, set its provider's `api_key_env` from [the catalogue](catalog/catalog.toml), then use:

```sh
uv run redrouter-benchmarks run --region us-east-1 --output out/requests \
  --route nanogpt-heabsy/deepseek-v4.1-flash-1017/uncensored --seed 7
uv run redrouter-benchmarks aggregate --input out/requests --output out/data \
  --now 2026-11-05T12:00:00Z
```

`--route` is repeatable; omitting it selects enabled routes with credentials. Explicit selections require credentials before any calls. The region labels the actual client location. See [METHODOLOGY](METHODOLOGY.md) for workloads, metric definitions, sampling and limitations.

`run` flushes one JSONL record per attempt, including failures. `aggregate` reads JSONL or `.jsonl.gz` archives and writes `summary.json`, `summary.csv`, `routes/<provider>/<model>/<modification>.json` and retained `raw/<date>/<region>.jsonl.gz`. Use a complete input archive and a fresh output directory. Identical inputs, catalogue and `--now` produce identical bytes. Public contracts are in [catalog/schema](catalog/schema).

`fetch-external --output out/data` imports Epoch and explicitly CC-licensed Arena results before `build-site`; `--zip PATH` uses a local Epoch archive. External stock-model scores are separate from host measurements.

For catalogue changes, adapter plug-ins and checks, see [CONTRIBUTING](CONTRIBUTING.md). Code and original prompts are [Apache-2.0](LICENSE). Result data is [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/); attribute RedRouter Benchmarks with the methodology version and observation period. Corrections: [issues](https://github.com/redrouter/benchmarks/issues) or `support@redrouter.org`, with reproducible evidence and no credentials.
