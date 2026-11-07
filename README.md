# RedRouter Benchmarks

Continuous, reproducible measurements of inference APIs serving refusal-reduced open-weight models and standard-model controls.

Maintained by **RedRouter, an inference provider**. RedRouter routes are excluded. Provider names identify measured services; they imply no endorsement. Public catalogue listings confirm advertised configuration, not working inference or deployed weight identity.

| Measurement | Definition |
| --- | --- |
| Time to first token | Request sent to the first non-empty content, reasoning or tool-argument delta |
| Output speed | Provider-reported completion tokens after the first chunk, divided by first-to-last content-delta time |
| Time to 500 tokens | Per-request TTFT + 500 / output speed; an extrapolation |
| End-to-end latency | Raw request timestamps retain actual stream completion and final-delta times |
| Observed success | Successful requests / attempted requests; failures retained without retries |
| Price | Dated catalogue prices, USD per million tokens; 3:1 input/output blend |

Summary latency and speed show p50/p90 over 72 hours. A metric needs eight usable successful samples. Missing usage leaves speed and time-to-500 null; an unreported first chunk counts as one token. See [METHODOLOGY.md](METHODOLOGY.md) for exact definitions and limitations.

## Local preview, without keys

Install [uv](https://docs.astral.sh/uv/), then use Python 3.12:

```sh
uv sync --locked
uv run redrouter-benchmarks demo-data --output out/demo --days 14 --seed 7
uv run redrouter-benchmarks build-site --data out/demo --output dist
uv run redrouter-benchmarks serve --dist dist --port 8787
```

Open `http://localhost:8787`. Demo generation uses the real runner and aggregator with an in-process transport and simulated clock; it never contacts providers. Seed 7 ends at the fixed reference time `2026-11-05T00:00:00Z`, includes a mid-window degradation and a route missing its final 48 hours, and marks the summary and every raw sample `demo: true`. Demonstration results are not live observations.

## Measure providers

Use ordinary paid accounts. Set only the credentials for providers you intend to measure:

| Provider | Environment variable |
| --- | --- |
| Refuseless | `REFUSELESS_API_KEY` |
| abliteration.ai | `ABLITERATION_API_KEY` |
| Venice | `VENICE_API_KEY` |
| Yeta Labs | `YETA_API_KEY` |
| LarpRouter | `LARPROUTER_API_KEY` |
| Experiential Labs | `EXPLABS_API_KEY` |
| All NanoGPT supplier pins | `NANOGPT_API_KEY` |
| OpenRouter controls | `OPENROUTER_API_KEY` |

```sh
uv run redrouter-benchmarks run --region us-east-1 --output out/requests \
  --route nanogpt-heabsy/deepseek-v4.1-flash-1017/uncensored
uv run redrouter-benchmarks aggregate --input out/requests --output out/data \
  --now 2026-11-05T12:00:00Z
```

Omit `--route` to select every enabled route whose provider has a credential. Repeated `--route` selects a subset; disabled routes cannot run. An explicit selection with a missing credential fails before making calls. `--seed` reproduces the request order and jitter. Run from the declared measurement region; the region argument is a label, not a network-location control. The cadence is every eight hours, at 02:00, 10:00 and 18:00 UTC, in `us-east-1` and `us-west-2`.

Provider settings live in [`catalog/providers/`](catalog/providers/); routes and dated price sources live in [`catalog/routes/`](catalog/routes/). NanoGPT supplier paths have distinct provider IDs, such as `nanogpt-heabsy`, and share `NANOGPT_API_KEY`; they are observations through the gateway, not independent direct-host measurements. Undated derivatives have a separate model identity from the dated 1017 release. See [`catalog/verification.json`](catalog/verification.json) for public checks and unresolved identities. Disabled candidates are retained as data without being measured.

## How a run works

1. The catalogue selects enabled routes, credentials, protocol and request settings.
2. A seeded run plan interleaves gateways and applies 0–2 seconds of start jitter.
3. The route's adapter builds the HTTP request and parses SSE into normalized events.
4. The runner times receipt, classifies failures and calculates usage-based cost.
5. Each attempt becomes a flushed JSONL record, including failures.
6. Aggregation produces summaries and histories; `build-site` renders them as a static site.

Built-in protocols are `openai-chat` and `anthropic-messages`. Installed adapters register through the `redrouter_benchmarks.adapters` Python entry-point group; see [CONTRIBUTING.md](CONTRIBUTING.md#add-a-protocol-adapter).

## External results

`uv run redrouter-benchmarks fetch-external --output <data-dir>` adds licensed published results for the same model families before `build-site`. Only Epoch AI results (CC BY 4.0) and sources with an explicit commercial-use Creative Commons grant are included. `--zip <path>` reads a local copy of Epoch's archive.

## Data

```text
<data-dir>/summary.json
<data-dir>/summary.csv
<data-dir>/routes/<provider_id>/<model_id>/<modification>.json
<data-dir>/raw/<YYYY-MM-DD>/<region>.jsonl.gz
```

[`summary.schema.json`](schema/summary.schema.json) and [`route-history.schema.json`](schema/route-history.schema.json) define the fixed public contract. Route IDs are `<provider_id>/<model_id>/<modification>`. CSV contains one row per route and region scope, including `all`. History uses UTC eight-hour buckets: counts describe the bucket and medians describe the trailing 72 hours ending at that bucket's end. Missing observations produce gaps.

`run` writes one flushed JSONL record per request into `<output>/<run_id>.jsonl`. Aggregation accepts these files or the compressed archives, deduplicates by run/region/route/stratum, rejects conflicting duplicates and mixed methodology or demo/real inputs, and preserves all records completed by `--now`. Supply a complete input archive and a fresh output directory for each build. Identical inputs, catalogue and `--now` produce identical JSON, CSV and gzip bytes.

Raw records include run, region, route, stratum, methodology/harness versions, random seed, UTC start/completion times, monotonic nanosecond timestamps, HTTP status, classified error, reported usage, first-chunk token count when known, finish reason, price snapshot and computed cost. `timestamps` contains `sent_ns`, `headers_ns`, `first_byte_ns`, `last_byte_ns`, `first_delta_ns`, `last_delta_ns` and `completed_ns`. Missing values are null. Only differences within a request are meaningful; monotonic origins are not comparable across processes. API keys, request headers, error bodies and generated text are never written. Response headers are limited to content type, retry delay and numeric request-limit metadata.

```sh
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run python tests/validate_data.py out/demo
```

Code and original prompts: [Apache-2.0](LICENSE), copyright RedRouter. Published benchmark results: [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/); attribute RedRouter Benchmarks and include the methodology version and observation period. Contributions and corrections: [CONTRIBUTING.md](CONTRIBUTING.md).
