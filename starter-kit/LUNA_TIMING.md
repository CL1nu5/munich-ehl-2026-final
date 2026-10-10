# Luna timing audit — 2026-10-10

Read-only analysis of existing journals and current harness code. No new model
requests or Arena sessions were made. Measurements describe the old recorded
agent, not a hosted benchmark of the recently revised agent or five-run batch.

## Recorded practice runs

Model latency is the duration recorded in `events.kind = model`; it begins
after acquiring model capacity and therefore excludes queue waiting. The p95
below uses nearest-rank, `ceil(0.95 * n)`, over all attempts, including aborted
ones. Incomplete calls are censored at the client's timeout.

| Run | Calls | Completed / unknown | Median | p95 | Maximum | Model timeout |
|---|---:|---:|---:|---:|---:|---:|
| `20261010T180504Z-efccf31f` | 45 | 43 / 2 | 17.59 s | 21.59 s | 25.04 s | 25 s |
| `20261010T184935Z-e5c18603` | 50 | 49 / 1 | 17.10 s | 29.35 s | 35.00 s | 35 s |

The latest run had no observed Arena timeouts and no harness decision fallbacks.
The one incomplete model request occurred in P05 during negotiation, at
35.004 seconds. The learner continued with its deterministic response. Model
usage for that request is unknown; this does not mean that it was unbilled.
The longest successful model call took 30.68 seconds.

The 53 complete agent decisions had a median duration of 16.41 seconds, p95
29.38 seconds and maximum 35.08 seconds. Some decisions did not use a model.
The complete run lasted approximately 14.46 minutes with two cases in parallel.
Individual case wall times, from opening request to final snapshot, ranged
from 90.69 to 242.46 seconds; their median was 171.19 seconds.

Matching model events to immediately following decisions and closing operations
identifies nine former pre-acceptance reflection calls, totalling 182.78 model
seconds. This is an inference from event timing, not an explicit phase field,
and the sum is not a wall-clock saving because cases ran concurrently. The new
architecture moves reflection after the confirmed result.

## Current request and time budgets

The model configurations use `gpt-6-luna` directly at
`https://api.openai.com/v1/chat/completions`, with `reasoning_effort = medium`,
`max_completion_tokens = 16000`, HTTP timeout 35 seconds and decision budget
45 seconds. Calls are non-streaming. The 16,000-token setting is a ceiling;
it does not request a fixed-length answer.

The learner currently chooses one combined reasoning/writing call per normal
decision: the two-call path requires a configured call allowance of at most
14 seconds and enough time for both calls. A 35-second timeout does not meet
that condition, despite `two_call = true` in TOML.

The Arena allows 60 seconds per supplier thread after its reply. Supplier
processing time does not count against that response window. The engine
subtracts a five-second transport reserve, caps the decision at 45 seconds,
and begins closing 90 seconds before a final wave's cap. The worker's model
deadline includes queue waiting and leaves a further two seconds to return.
These guards provide fallback behaviour, not a network-latency guarantee.
See [the protocol](../brief/api.md) and [budget.py](agents/_learner/budget.py).

## Fast mode

Official documentation supports Fast mode for GPT-6 Luna. Request it through
`service_tier: "fast"`; `"priority"` is the compatible alternative. It works
with Chat Completions as well as Responses, so changing API endpoint is not
required. It may also be selected at project level. The model documentation
lists Fast at twice the applicable Standard token rates. Account/project
eligibility was not tested. Speedups are not a fixed per-request guarantee.

Following this audit, Fast mode was enabled in `learner_openai.toml` and
`learner_final.toml`. The harness validates and forwards `[model].service_tier`
and journals both the requested tier and the tier returned by the API. Reports
count the returned tiers, including provider downgrades and missing metadata.
The old logs did not capture this field and cannot establish their serving tier.
Account eligibility and real Fast-mode latency remain untested; implementation
checks use mocked HTTP responses only.

Sources checked on 2026-10-10:

- [OpenAI Fast mode](https://developers.openai.com/api/docs/guides/fast-mode)
- [GPT-6 Luna model and pricing](https://developers.openai.com/api/docs/models/gpt-6-luna)
- [Reasoning effort](https://developers.openai.com/api/docs/guides/reasoning)

## Further improvements and evidence limits

The 49 known usages contain 133,730 input tokens and 80,117 completion tokens.
Of the latter, 66,881 (83.48%) are reasoning tokens. Visible output averages
about 270 tokens per completed call. No cache-hit tokens were reported;
132,594 input tokens were reported as cache writes. Reducing the output ceiling
alone will not necessarily make an already short completion faster and can
truncate reasoning or JSON.

Follow-up status:

1. Implemented: expose and forward `service_tier`, enable Fast in both Luna
   configs, and retain `medium` reasoning.
2. Requested and returned tiers are now recorded. Further telemetry should
   add queue duration, `finish_reason`, provider request ID and error class.
   HTTP duration is already recorded. The current `unknown`
   status is too coarse, and the model-call table marks usage done before the
   returned content is fully consumed by the agent.
3. Evaluate `low` reasoning separately on reserved examples before changing
   the default. It targets the dominant token component, with a possible
   decision-quality tradeoff.
4. Keep 35/45-second limits until new latency data supports lowering them.

Five runs with two sessions each share ten model slots. This bounds concurrency
and protects the decision deadline, but it is not a provider RPM/TPM limiter.
Ten continuously busy slots at the recorded mean latency imply roughly 32.4
requests per minute before accounting for supplier waiting. Actual account
limits and new prompt sizes are unknown. Fast and Standard share the same
provider rate limits; Fast mode does not increase the account quota. The
five-run integration tests use mocked model calls and cannot establish live
provider latency under load.
