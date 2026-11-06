# Contributing

Use Python 3.12 and [uv](https://docs.astral.sh/uv/). Keep changes small and reproducible:

```sh
uv sync --locked
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run redrouter-benchmarks demo-data --output out/demo --days 14 --seed 7
uv run python tests/validate_data.py out/demo
```

Tests use local transports and simulated clocks. Never add credentials, paid test calls, generated response text or personal data to fixtures. To preview, follow the [README](README.md). The Dockerfile installs the package on Python 3.12; it does not supply credentials or deploy anything.

## Add a provider or route

Add `catalog/providers/<id>.toml` with `id`, `name`, public `url`, logo filename, `base_url`, `protocol = "openai-chat"`, `api_key_env` and `terms_url`. The logo filename refers to `site/assets/providers/<id>.svg` or `.png`. Add the asset through the site implementation. Credentials are referenced by environment-variable name only.

Add `catalog/routes/<provider_id>/<model_id>/<modification>.toml`. The filename modification must be `none`, `abliterated`, `uncensored` or `heretic`; folder and file identities must match the route's fields. Add a model entry in `catalog/models.toml` only when it is a distinct base-model identity. Keep undated derivatives separate from a dated snapshot. Required route data:

- `provider_model`, `model_id`, `modification`, `enabled`, and `include_usage`.
- `upstream`, `declared_precision`, and `context_tokens` when publicly established; omit unknown values rather than inventing them. Missing TOML fields become null in public JSON.
- `[price]` with nonnegative `input_usd_per_mtok`, `output_usd_per_mtok`, optional `cached_input_usd_per_mtok`, primary `source_url`, and quoted ISO date `checked_at`.
- `[extra_body]` for documented reasoning controls and provider selection. Do not override model, messages, temperature, streaming or output limit.

Verify the endpoint and exact ID with public docs or an unauthenticated models GET. Record source and date in `catalog/verification.json`. An API listing confirms advertisement, not serving health. Leave unconfirmed candidates `enabled = false`, without a code comment. Do not guess prices or checkpoint identity. A price without a source cannot enable a route.

NanoGPT suppliers use provider IDs `nanogpt-<supplier>`, the common gateway base URL and `NANOGPT_API_KEY`; set `extra_body.provider.only = ["<supplier>"]` and `allow_fallbacks = false`. `order` alone is a soft preference there. OpenRouter controls set a single exact supplier tag in `provider.order` and `allow_fallbacks = false`; take prices from that endpoint's public supplier entry, not the cheapest model-wide offer. Supplier-selection markup is already included in NanoGPT's displayed pin prices.

Review the provider's terms and ordinary paid-account eligibility before operating a route. A catalogue entry is not a claim of contractual permission. RedRouter's own routes are outside the current scope. Include behavioral tests for changes to parsing, timing, scheduling or aggregation; schema compatibility is mandatory. The schemas are shared with the site and must not change without coordination.

## Changes and corrections

Use Conventional Commits, for example `feat(catalog): add a sourced supplier route` or `fix(runner): retain stream timeout failures`. Write commit identity as `RedRouter <support@redrouter.org>`. Include the exact validation commands and numeric results in the pull request. Keep code concise; add comments only for non-obvious constraints. Apply the version policy in [METHODOLOGY.md](METHODOLOGY.md) when changing measurement behavior.

For corrections, open an [issue](https://github.com/redrouter/benchmarks/issues) or contact `support@redrouter.org` with route ID, time interval, version and supporting public evidence. Describe the observable discrepancy. Do not include API keys, account screenshots or generated private content. Preserve original evidence and identify any reprocessed results. Code and original prompts are Apache-2.0; published result data is CC BY 4.0 as stated in the [README](README.md).
