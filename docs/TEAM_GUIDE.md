# Team guide: data and agent development

This page explains our repo layout and proposes implementation priorities. It is
for contributors, not a runtime prompt or a statement that these improvements
already exist. Keep implemented behavior in the code and its tests; update or
remove the recommendations as work lands.

## What is already here?

| Location | Contents | Use |
|---|---|---|
| `data/data/*.csv` | Procurement tables: suppliers, parts, orders, invoices, quality, forecasts, quotes, contracts and indices | Original tabular data; schemas in `data/datapackage.json` |
| `data/world.sqlite` | The same 24 tables as SQLite | Local joins and analysis without API or model calls |
| `data/facts.json` | Facts keyed by the IDs the arena checks | Ground negotiation claims in an existing ID and its correct values |
| `data/contracts/` | Framework contract text | Clauses and existing commitments |
| `data/correspondence/` | Supplier letters, emails, meeting and call notes | Motives, relationship history and negotiation levers |
| `example_inputs/sim/` | Saved local simulator API responses and quote PDFs | Learn the API shape; these are S01–S03 examples, not hosted practice or hidden final cases |
| `starter-kit/arena_kit/sim_cases/` | Local simulator case definitions | Offline plumbing and decision tests |
| `brief/` | Official task, API and submission documentation | Rules and API contract |

The procurement world is **as of 2026-09-30**. Company data is synthetic; market
series are real open data frozen for the challenge. Read
[the data README](../data/README.md), [known issues](../data/KNOWN_ISSUES.md) and
[attribution](../data/ATTRIBUTION.md) before deriving claims.

Important pitfalls: prices can be per 1, 100 or 1,000 units; vendor IDs can have
aliases; delivery performance depends on the reference date; should-cost is an
estimate. Do not treat a derived conclusion as a fact verified by the arena.

## Do we need more data in Git?

The anonymous `GET https://arena.tacto.ai/v1/data` lists available releases.
On **2026-10-10**, the public base ZIP was checked against this repo: all CSV,
JSON and text files matched; all 24 SQLite tables contained identical rows.
**Do not commit another copy of the base ZIP or extract a second `package/`.**
This is a dated check, not a promise that future downloads stay identical.

The official Sunday release is announced for **2026-10-11 at 08:00 Europe/Berlin**.
Check the index at download time; only use the release URL when it is public.
Review and add its new facts/documents under `data/release-sunday/`, preserving
the base package and source attribution. `DataPackage` discovers that directory
automatically. Check for fact-ID collisions before accepting the release.

```bash
# Public, read-only; no login, negotiation or model call.
curl -fSL https://arena.tacto.ai/v1/data
```

Hosted practice and final case files come from the authenticated Cases API.
The public base package and simulator examples do not contain those cases.
Keep credentials out of Git. Additional live web market data is lower priority
than official releases: the arena validates its own fact IDs.

## Proposed implementation priorities

1. **Retrieve supplier context.** Extend `LLMAgent.prepare()` beyond direct case
   references: collect relevant supplier/part rows, notes and clauses. Cache a
   compact dossier with source IDs, original values and uncertainties. Infer
   motives from evidence rather than hard-coding behavior by supplier name.
2. **Optimize packages in code.** Use `cost.py` to compare valid alternatives over
   price, payment, volume, duration, freight and other allowed fields. Send two
   or three meaningful trades. For `stabilise`/`secure_supply`, account for
   uncertainty in supplier economics: the relationship cost is zero when no
   supplier estimate is passed.
3. **Make every closing path safe.** Validate standard terms from the quote,
   must-haves and the buyer's alternative before accepting. Apply this to normal
   decisions, deadline closure and model-error fallback; the starter's naive
   fallback does not provide the same acceptance guards as `LLMAgent.guard()`.
4. **Check RFQ deadlines.** `--parallel` runs cases concurrently, while `play()`
   sends to one supplier at a time within a case. Test delayed replies from
   multiple suppliers so one thread cannot starve another. Preserve safe retries
   and prevent competing acceptance actions if scheduling is changed.
5. **Evaluate locally first.** Simulator and mocked-model tests validate plumbing,
   not real supplier strength. After explicit permission for service/model use,
   compare practice scores, cost breakdowns, claim results and latency.

Use the Python kit for the final runner. n8n is an optional import/reporting/demo
layer if already available; rebuilding cost and wave handling there is lower
priority. This is a project recommendation, not a limitation of n8n.

No paid service/model calls without explicit approval, even if keys or credits
are present. Before the final, use the current
[submission checklist](../brief/submission.md). Entire checkpoint integration is
required by the [event guide](https://tum-ai.notion.site/EHL-Finale-All-you-need-to-know-3eb7306bfd62808993e9ec18c2d64834).
Check the actual checkpoint link when committing, rather than storing machine
hook status in this document.

Do not feed live events, screens, leaderboards or results to the agent during
the final. Current rules: [brief](../brief/brief.md) and [API](../brief/api.md).
For changing operational details, consult the [Arena FAQ](https://arena.tacto.ai/faq)
and organizers instead of copying them into another permanent checklist.
