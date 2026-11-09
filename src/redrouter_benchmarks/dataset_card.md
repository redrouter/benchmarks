---
license: cc-by-4.0
pretty_name: Abliterated Model API Benchmarks
tags:
  - abliterated
  - uncensored
  - llm
  - benchmark
  - inference
  - latency
  - throughput
task_categories:
  - text-generation
size_categories:
  - {{ size_category }}
configs:
  - config_name: summary
    default: true
    data_files:
      - split: summary
        path: summary.csv
  - config_name: history
    data_files:
      - split: history
        path: history.csv
---

# Abliterated Model API Benchmarks

Customer-observed API latency, output speed, reliability and catalogue prices for abliterated and uncensored open-weight models, with standard-model controls. Data and charts: [benchmarks.redrouter.org](https://benchmarks.redrouter.org). These measurements do not assess model quality, refusal rates or worldwide uptime.

Generated: {{ summary.generated_at }}. Methodology: {{ summary.methodology_version }}. Demonstration data: {{ 'yes' if summary.demo else 'no' }}.

## Current routes by model

Fastest means highest pooled {{ summary.window_hours }}-hour p50 output speed; cheapest means lowest catalogue blended price among routes with at least eight attempted requests in that window. Missing speed is excluded; unavailable results are marked `Unavailable`. Ties use route ID order. Modifications are distinct products.

| Model | Fastest route | p50 tok/s | Cheapest route | USD / million tokens |
| --- | --- | ---: | --- | ---: |
{% for leader in leaders -%}
| {{ leader.model | table_cell }} | {{ leader.fastest.route_id if leader.fastest else 'Unavailable' }} | {{ '%g' | format(leader.fastest.output_tokens_per_second_p50) if leader.fastest else 'Unavailable' }} | {{ leader.cheapest.route_id if leader.cheapest else 'Unavailable' }} | {{ '%g' | format(leader.cheapest.blended_usd_per_mtok) if leader.cheapest else 'Unavailable' }} |
{% endfor %}
## Columns

`summary.csv` contains one row per route. `history.csv` contains one row per route, region and time bucket. Empty CSV fields are missing values, not zeroes.

| Columns | Definition |
| --- | --- |
| `route_id`, `model_id`, `provider_id` | Stable route, model and provider identifiers |
| `model`, `provider` | Display names |
| `provider_model`, `upstream`, `modification` | Requested model identifier, declared supplier if known, and modification label; `none` denotes a standard-model control |
| `region` | Client measurement region; `all` in summary pools observed requests from both regions |
| `generated_at`, `methodology_version`, `window_hours`, `demo` | Source generation time in UTC, protocol version, rolling window length, and synthetic-data flag |
| `status` | Summary availability: ok, degraded, failing, stale or insufficient_data |
| `requests`, `successes`, `success_rate` | Attempted and successful request counts and their ratio (0–1); summary counts cover the rolling window, history counts cover the bucket |
| `ttft_ms_p50`, `ttft_ms_p90` | Time from sending to the first content delta, milliseconds |
| `output_tokens_per_second_p50`, `output_tokens_per_second_p90` | Decode speed after the first chunk, tokens per second |
| `end_to_end_500_ms_p50`, `end_to_end_500_ms_p90` | Per-request TTFT plus extrapolated time for 500 output tokens, milliseconds |
| `blended_usd_per_mtok` | Summary catalogue price: (3 × input + output) / 4, USD per million tokens, without cache discounts |
| `t`, `bucket_hours` | History bucket start in UTC and bucket duration in hours |

History contains the chart's rolling-window p50 values only, ending at the earlier of bucket end and generation time; p90 and price are summary-only. History success rate describes the bucket, not the rolling window.

## Methodology and updates

Measurements run every 8 hours from two US client regions: `us-east-1` (N. Virginia) and `us-west-2` (Oregon). Data is refreshed after each site update. Each run attempts four streaming requests per route across prose/code and approximately 1,000/8,000 input tokens, with a 512-token output cap, no retries and no warm-up calls. Percentiles pool successful requests over the trailing {{ summary.window_hours }} hours and require at least eight successful and eight usable samples per metric. Failures remain in the success-rate denominator. Prices are dated catalogue declarations rather than billing receipts. See the [methodology](https://benchmarks.redrouter.org/methodology/) for formulas, limitations and reproduction details.

## Licence and attribution

Results are licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Attribute as: RedRouter Abliterated Benchmarks, CC BY 4.0. Include the methodology version and observation period when reusing results.

```bibtex
@misc{redrouter_abliterated_benchmarks,
  author = {RedRouter},
  title = {Abliterated Model API Benchmarks},
  year = { {{- summary.generated_at[:4] -}} },
  url = {https://benchmarks.redrouter.org},
  note = {Methodology {{ summary.methodology_version }}; generated {{ summary.generated_at }}}
}
```
