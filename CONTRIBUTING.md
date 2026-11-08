# Contributing

Use Python 3.12 and uv. Tests use local transports and simulated clocks; never add credentials, paid calls or private response text.

```sh
uv sync --locked
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run redrouter-benchmarks demo-data --output out/demo --days 14 --seed 7
uv run python tests/validate_data.py out/demo
```

## Catalogue

Edit [catalog/catalog.toml](catalog/catalog.toml). `[defaults]` supplies protocol and authentication settings; `[providers.<id>]` supplies name, URLs, local logo filenames, endpoint and credential environment-variable name. Providers using an existing protocol need no Python changes. Models are in [catalog/models.toml](catalog/models.toml).

Each `[routes."<provider>/<model>/<modification>"]` names its identity; modification is `none`, `abliterated`, `uncensored` or `heretic`. Supply `provider_model`, `enabled`, `include_usage`, and publicly established upstream, precision and context size. Omit unknown values. `price` holds nonnegative input/output USD per million tokens, optional cached-input price, HTTPS source URL and quoted ISO check date. `extra_body` holds documented reasoning and supplier controls; never override the workload.

NanoGPT pins use `provider.only = ["<supplier>"]`; OpenRouter controls use one exact supplier in `provider.order`. Both require `allow_fallbacks = false`. Keep undated derivatives separate from dated checkpoints. Record public verification in [catalog/verification.json](catalog/verification.json); leave unconfirmed routes disabled. Never guess prices or identities, and exclude RedRouter routes.

## Protocol adapters

Implement `Adapter` in [adapters/base.py](src/redrouter_benchmarks/adapters/base.py): `build_request(provider, route, prompt, nonce, api_key)` returns an HTTP request; `parse_event(data)` yields normalized `Content`, cumulative `Usage`, `Finish`, `Error` and `Done` events without text or error bodies. Raise `ProtocolError` for malformed streams. Each request gets a fresh adapter; timing, transport, failures and cost belong to the runner. Provider quirks belong in TOML.

Register installed zero-argument factories through `[project.entry-points."redrouter_benchmarks.adapters"]`, using the protocol name as key and `package.module:Factory` as value. Duplicate names fail validation; plug-ins load only when selected. Cover recorded SSE, arbitrary byte boundaries, missing usage, partial failures and secret retention with behavioural tests.

## Contracts

Keep [JSON schemas](catalog/schema) compatible. Follow the version policy in [METHODOLOGY](METHODOLOGY.md) for measurement changes. External results require an explicit commercial-use Creative Commons grant recorded in [external/licences.json](src/redrouter_benchmarks/external/licences.json), with offline filtering tests.

Use Conventional Commits and identity `RedRouter <support@redrouter.org>`. Include exact validation commands and results in the pull request. Preserve original evidence when correcting results; report discrepancies through the contacts in [README](README.md).
