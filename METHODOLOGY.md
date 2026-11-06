# Methodology 1.0.0

Effective 2026-11-05. This protocol measures customer-observed API performance for explicitly named routes. A route is a gateway, model identity, modification and, where available, pinned supplier. Different modifications and undated aliases are distinct products, not controlled experiments on hosting. Published precision is a provider declaration; it is not independently verified.

## Workload and schedule

Each scheduled run attempts four requests per enabled route: approximately 1,000 and 8,000 input tokens, crossed with prose and code. Fixed original prompts are in [`prompts/1.0.0/`](prompts/1.0.0/). Sizes are approximate character-based design targets; provider-reported prompt counts establish actual size. V1 has no local tokenizer. Prose requests a 1,600-word handbook; code requests a complete 200-line module. Both encourage output exceeding the 512-token limit without artificial minimum-token settings.

Every request prepends a new 128-bit nonce, uses temperature 0.5 and `max_tokens: 512`, requests streaming, and asks for usage with `stream_options.include_usage` where supported. No retries, warm-up calls, successful-sample replacement or adaptive endpoint selection. `length` is an expected successful finish at this output cap. Reasoning is disabled where documented; mandatory reasoning uses the lowest documented effort where supported and remains part of reported completion usage. Unknown controls are omitted. Hiding reasoning text is not disabling computation. Route request settings are explicit catalogue data, so mandatory-reasoning routes must not be interpreted as equivalent non-reasoning workloads.

The intended cadence is 00:00, 03:00, …, 21:00 UTC, seven days a week, independently from measurement regions `us-east-1` and `us-west-2`. These are client locations, not claims about server location. Each region uses ordinary internet egress. No live observations are claimed by the demo.

For each run, seed a pseudorandom generator, shuffle requests within each gateway and shuffle gateway order on each pass. Take one request from each active gateway per pass, with uniform start jitter of 0–2 seconds before every request. V1 executes serially, so concurrency never exceeds one per provider within a runner process; all NanoGPT pins share one gateway queue. Preserve the seed in every record. Do not overlap runs in the same region. Two-region runs are independent and can overlap.

Use httpx directly with transport retries disabled. The request deadline is 180 seconds from sending. Headers and first response bytes must arrive within 60 seconds of sending; subsequent byte stalls also time out after 60 seconds. Heartbeats do not extend the total deadline. Redirects are retained as client errors. Ambient HTTP proxy settings are ignored to keep the measured egress explicit.

## Per-request definitions

All timing comes from the client monotonic clock, in integer nanoseconds. UTC wall time labels observations. A content delta includes non-empty answer content, reasoning content or tool-call arguments; role announcements, heartbeats, finish markers and usage-only events do not start or extend content timing.

| Field | Definition |
| --- | --- |
| `ttft_ms` | `(first_delta_ns - sent_ns) / 1e6` |
| `output_tokens_per_second` | `(completion_tokens - first_chunk_tokens) / ((last_delta_ns - first_delta_ns) / 1e9)` |
| `end_to_end_500_ms` | `ttft_ms + 500 / output_tokens_per_second * 1000`, computed per request before aggregation |
| Actual final-token latency | `(last_delta_ns - sent_ns) / 1e6`, derivable from raw records |
| Actual stream completion | `(completed_ns - sent_ns) / 1e6`, includes trailing usage/transport time |
| Observed success rate | Successful requests / attempted requests, including every failure |
| Blended USD/M tokens | `(3 × input_usd_per_mtok + output_usd_per_mtok) / 4`; no cache assumption in the headline blend |

Completion and reasoning counts come only from provider usage. Reported completion already includes reasoning where the provider bills it that way; do not add reasoning again. A first-delta usage event can establish `first_chunk_tokens`; when it is not reported, the first chunk counts as one token, the LLMPerf convention. Speed needs at least two content deltas. Raw records keep `content_delta_count`, so coarse chunking is visible. When final completion usage is absent, speed and time-to-500 are also null. TTFT and success remain usable. Nonpositive decode duration, a single coalesced content time, or no tokens after the first chunk cannot establish speed. Network buffering can distort timing; measured deltas represent client receipt, not model execution.

Time-to-500 is a constant-rate extrapolation, including for short completions; it is not observed 500-token answer latency. The decode-speed formula, workload, accounts and aggregation use the versioned definitions in this document.

Per-request `cost_usd` applies the raw record's price snapshot: uncached prompt tokens × input rate, cached prompt tokens × cache rate, plus completion tokens × output rate, divided by one million. Unknown cached usage is treated as zero for a conservative estimate; an unknown cache price uses the ordinary input rate. `cost_estimated` flags these assumptions. Missing prompt or completion usage makes cost null. Catalogue cost is not a billing receipt: fees, cache writes, promotions, dynamic supplier prices and charges for failed streams may differ. Failed requests keep any reported usage and cost. Summary prices are the current catalogue snapshot, with source and check date.

## Errors and retention

A success has a 2xx SSE response, at least one non-empty content delta and a terminal `stop`, `length`, `tool_calls` or `function_call` finish. A finish marker followed by clean EOF can succeed without `[DONE]`. Malformed or truncated SSE, invalid JSON, unsupported finish reasons and stream error events fail. Partial output followed by transport failure is a failure, with timing evidence retained. Usage-only or role-only streams are empty output.

| `error_class` | Classification |
| --- | --- |
| `timeout` | Total deadline, first-byte deadline or byte-stall timeout |
| `rate_limited` | HTTP 429 |
| `server_error` | HTTP 5xx |
| `client_error` | Other non-2xx responses, including redirects and account/configuration errors |
| `network` | Transport failure other than timeout |
| `empty_output` | A clean stream without a non-empty content delta |
| `protocol` | Invalid content type, SSE, JSON or completion structure |

Numeric HTTP-equivalent codes in SSE error envelopes use the same HTTP classification. Other stream errors are protocol errors. Failures are retained in the denominator regardless of suspected cause. Account misconfiguration therefore remains visible and must be corrected operationally, not erased from published observations. No generated text, error-response body or credential is retained. Allowed response headers are `content-type`, `retry-after`, `x-ratelimit-limit-requests` and `x-ratelimit-remaining-requests`. Raw JSONL archives retain all completed observations; aggregation does not trim them to the headline window.

## Statistics and status

The summary window is `(now − 72 hours, now]`, assigned by request completion time. Future observations are excluded. Pool successful requests equally across the four strata. The `all` scope pools both regions equally per observed request; unequal sample counts can change its composition. Regional metrics remain separate. Do not average precomputed regional percentiles or per-request speeds to form a percentile.

For p50 and p90, sort usable observations and linearly interpolate at index `(n − 1) × q`. Round displayed distributions to six decimal places. A metric is null unless the route/scope has at least eight successful requests **and** eight usable values for that metric. Failures affect success rate and status, never latency percentiles. There is no outlier removal. No confidence interval or global rank is implied by these descriptive statistics.

Status is evaluated per scope in this order; the summary route status uses `all`, and CSV includes regional statuses:

1. **stale:** observations exist, but the latest completed request is older than 12 hours, including completed failures.
2. **insufficient_data:** fewer than eight attempted requests in the 72-hour window, including a route never observed.
3. **failing:** observed success rate below 0.80.
4. **degraded:** success rate from 0.80 up to 0.95, or a rate at least 0.95 without a success in the last six hours.
5. **ok:** success rate at least 0.95 and a success within the last six hours.

History files retain UTC three-hour buckets for the supplied archive. Counts describe requests completed in the bucket. Each point's p50 is calculated over the trailing 72 hours ending at the earlier of bucket end and `now`, with the same eight-value minimum. Points are timestamped at bucket start. Missing buckets are gaps rather than fabricated zero-latency observations. This preserves meaningful trends despite only four requests per route/region/run.

## Fairness and limits

Use ordinary paid accounts, public endpoints and the same settings for every route except documented API compatibility and reasoning controls. No provider-supplied benchmark keys, preferred endpoints, negotiated priority, pre-arranged capacity or free tiers. Disclose any unavoidable account-tier difference. Hard-pin suppliers and disable fallback wherever supported. A gateway that internally reroutes without a public pin remains a gateway observation; upstream labels are declarations, not proof of hardware independence. Do not merge aliases whose dated checkpoint binding is uncertain.

The suite does not measure task correctness, refusal rate, tool-call quality, model capability, deployed weight hashes, actual quantization, saturated throughput, concurrency scaling, long-context limits or worldwide uptime. These require different workloads, references or infrastructure; latency cannot establish them. A transport-level successful answer can still be useless. Four synthetic requests every three hours cannot certify an SLA or rare-event reliability. Cache busting reduces shared-prefix reuse but cannot guarantee no provider-side cache hits; retain reported cache usage.

## Versions, corrections and provider response

Changes to prompts, timing formulas, output limits, reasoning policy, sampling weights or status rules require a methodology version change. Keep old raw observations associated with their original version; aggregation rejects mixed versions. Additive routes and dated price corrections use catalogue commits without relabelling old raw price snapshots. Harness releases are recorded separately. Reproduction requires the source revision, locked dependencies, catalogue snapshot, region, seed and original raw archive.

Providers and contributors can open a [correction issue](https://github.com/redrouter/benchmarks/issues) or email `support@redrouter.org` with route ID, observation interval, methodology version and reproducible evidence. Never send credentials. Separate errors in this harness from changes to a service. Retain the original observation, identify corrected periods, and explain material corrections in the relevant issue and commit. Provider responses can be linked alongside the affected evidence under the same factual standard; a response does not silently replace measurements. No provider has a privileged suppression or approval right.

## Design precedents

The implementation adopts request-level evidence and explicit workload/concurrency settings from [LLMPerf](https://github.com/ray-project/llmperf); explicit provider/model settings from [K2 Vendor Verifier](https://github.com/MoonshotAI/K2-Vendor-Verifier); configuration as data and repeatable result generation from [InferenceX, formerly InferenceMAX](https://github.com/SemiAnalysisAI/InferenceX); runnable commands and reproducible environments from [SWE-bench](https://github.com/SWE-bench/SWE-bench); visible workload/settings and cost accounting from [Aider](https://aider.chat/docs/benchmarks.html); and version-bound definitions and reproducible artifacts from [BFCL](https://gorilla.cs.berkeley.edu/leaderboard.html). These are structural conventions, not reused tasks or copied prose. Unlike task benchmarks that retain answers for scoring, this performance suite stores no generated text.
