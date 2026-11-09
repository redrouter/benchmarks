# RedRouter Benchmarks

Continuous, reproducible measurements of inference APIs serving refusal-reduced open-weight models, with standard-model controls. Results are published at [benchmarks.redrouter.org](https://benchmarks.redrouter.org).

Data is also published as a [Hugging Face dataset](https://huggingface.co/datasets/redrouter/abliterated-benchmarks).

Maintained by **RedRouter, an inference provider**. RedRouter routes are excluded. Provider names identify measured services; they imply no endorsement. Public catalogue listings confirm advertised configuration, not working inference or deployed weight identity.

## What is measured

| Measurement | Definition |
| --- | --- |
| Time to first token | Request sent to the first non-empty content, reasoning or tool-argument delta |
| Output speed | Provider-reported completion tokens after the first chunk, divided by first-to-last content-delta time |
| Time to 500 tokens | TTFT + 500 / output speed; an extrapolation |
| End-to-end latency | Raw records keep actual stream completion and final-delta times |
| Success rate | Successful requests / attempted requests; failures are kept, never retried |
| Price | Dated catalogue prices, USD per million tokens; 3:1 input/output blend |

Latency and speed are reported as p50/p90 over a trailing 72 hours and need at least eight successful samples. Runs happen every eight hours from `us-east-1` and `us-west-2`. [METHODOLOGY.md](METHODOLOGY.md) has the exact definitions and limitations.

## Preview locally, without keys

Install [uv](https://docs.astral.sh/uv/) and Python 3.12, then:

```sh
uv sync --locked
uv run redrouter-benchmarks demo-data --output out/demo --days 14 --seed 7
uv run redrouter-benchmarks build-site --data out/demo --output dist
uv run redrouter-benchmarks serve --dist dist --port 8787
```

Open `http://localhost:8787`. Demo data uses the real runner and aggregator with a simulated transport and clock, never contacts providers, and is marked `demo: true`.

## Measure providers

Set credentials only for the providers you want to measure:

| Provider | Environment variable |
| --- | --- |
| Refuseless | `REFUSELESS_API_KEY` |
| abliteration.ai | `ABLITERATION_API_KEY` |
| Venice | `VENICE_API_KEY` |
| Yeta Labs | `YETA_API_KEY` |
| LarpRouter | `LARPROUTER_API_KEY` |
| Experiential Labs | `EXPLABS_API_KEY` |
| NanoGPT supplier pins | `NANOGPT_API_KEY` |
| OpenRouter controls | `OPENROUTER_API_KEY` |

```sh
uv run redrouter-benchmarks run --region us-east-1 --output out/requests
uv run redrouter-benchmarks aggregate --input out/requests --output out/data
uv run redrouter-benchmarks build-site --data out/data --output dist
```

Without `--route`, every enabled route with a credential runs. Pass `--route <provider>/<model>/<modification>` (repeatable) for a subset and `--seed` to reproduce request order. `--region` is a label; run from the region you name. `fetch-external --output <data-dir>` adds licensed published results (Epoch AI and CC-licensed sources) before `build-site`.

## Catalogue

Providers, models and routes with dated price sources are in [`catalog/catalog.toml`](catalog/catalog.toml). Public endpoint checks are in [`catalog/verification.json`](catalog/verification.json). NanoGPT supplier pins have their own provider IDs, such as `nanogpt-heabsy`, and are measured through the gateway. To add a provider, route or protocol adapter, see [CONTRIBUTING.md](CONTRIBUTING.md).

## Data

`redrouter-benchmarks export-dataset --data <data-dir> --output <dataset-dir>` exports summary and history CSVs with a Hugging Face dataset card.

```text
<data-dir>/summary.json
<data-dir>/summary.csv
<data-dir>/routes/<provider_id>/<model_id>/<modification>.json
<data-dir>/raw/<YYYY-MM-DD>/<region>.jsonl.gz
```

[`summary.schema.json`](catalog/schema/summary.schema.json) and [`route-history.schema.json`](catalog/schema/route-history.schema.json) define the public format. Each raw record is one request, including failures, with timestamps, status, usage, price and cost. API keys, request headers, error bodies and generated text are never stored. Identical inputs, catalogue and `--now` produce identical output.

## Development

```sh
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run python tests/validate_data.py out/demo
```

## Licence

Licensing: code Apache-2.0, published results CC BY 4.0. See the [code licence](LICENSE) and [data licence](site/data/LICENSE.txt); attribute RedRouter Benchmarks with the methodology version and observation period.
