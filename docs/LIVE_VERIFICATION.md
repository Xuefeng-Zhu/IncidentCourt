# Live investigation verification

`scripts/verify_live_flow.py` checks a real application path through **Zetaris SQL → Nightshift hosted inference → official Meterless H-MEM**. It performs read-only SQL, two actual investigations, and up to six gateway generation calls, with an approved conservative allowance of 18 possible upstream attempts. It does not provision resources, import/register remote tables, create credentials, deploy, or submit anything.

## Completed live run — October 25, 2026

**PASS: all 11 layers passed** against the real existing Zetaris workspace and Nightshift gateway with hosted NVIDIA Nemotron. The private source of record is `artifacts/private/live-flow-20261025-nvidia-01/receipt.json`; this result is distinct from the verifier's synthetic fixture tests.

| Verified layer | Actual result |
| --- | --- |
| Remote telemetry | 3 incidents + 28,126 metrics + 1,498 logs + 15,000 spans = 44,627 rows |
| SQL provenance | 45 actual Zetaris SELECTs including preflight; returned rows hashed |
| Development history | `IC-001`, run `3c24d86ae0a5`, 14 queries, 21 evidence records |
| Holdout target | `IC-003`, run `99f570d82daa`, 16 queries, 33 evidence records |
| Nightshift gateway | Six distinct uncached planner/critic/explanation responses |
| Model/provider pin | Every response reported `nvidia/nemotron-3.5-lightning-30b-a3b` / `nvidia` |
| Current citations | All references resolved to actual executed query receipts |
| H-MEM consumption | One earlier match caused target SQL `Q03` and `Q09` |
| Holdout isolation | Two historical memories before and after; target not stored |
| Reported output/cost | 890 output tokens total; every runtime `actual_usd` was zero |

A concrete model-quality limitation remains: the history critic selected `checkoutservice`, which the application actually queried in `Q08`, while its reason discussed `cartservice` as the alternative/core dependency. Those fields were inconsistent. The target critic selected `frontend-external` and its `Q09` follow-up was consistent. No answer repair or replay occurred. Service/citation validation and executed-query provenance passed; they do not validate narrative consistency or causal reasoning.

The cost figures are runtime estimates, not an invoice or independent verification of account billing. Winning-route receipts do not establish whether Nightshift performed auxiliary model or tool calls. Six gateway calls were used under the approved conservative bound of 18 upstream provider attempts; individual internal retry counts are not exposed and remain unknown. No answer repair, local substitution or cached response was accepted.

The six-call allowance for that historical verification was exhausted and `NIGHTSHIFT_ALLOW_INFERENCE=0` was restored. That historical allowance does not define a future usage budget. The actual target report was imported in the browser without generation. Citation E01 expanded into its original returned metric row; the query trail showed all 16 Zetaris queries and H-MEM follow-ups Q03/Q09; exported JSON matched the original report exactly. At 580px and 390px there was no horizontal overflow, and the browser logged no warnings/errors. The hosted engine remained selected with Run disabled. At that verification point the application suite passed 101 tests. This verifies saved-record browsing; it does not claim browser-triggered generation. This CLI pass does not establish benchmark accuracy, production readiness, deployment, hackathon submission or browser execution. Earlier local-model receipts remain historical evidence only; this verifier refuses local inference.

## Before a separately authorized future run

The historical verifier used the existing free hosted NVIDIA endpoint with the bounded gateway/upstream call counts below. `--allow-hosted-inference` records an explicit execution opt-in; it is not itself user authorization or permission to spend credits.

Configure the existing services in ignored `.env.local`:

- `SAMPLE_MODE=false` and `INCIDENTCOURT_DATA_MODE=zetaris`. Remote selection is mandatory; there is no SQLite fallback.
- Existing Zetaris SQL UI origin, organization, account/token, engine where required, and table prefix. The four anonymous telemetry tables must already be queryable.
- Existing Nightshift gateway authentication and an **exact advertised hosted registry model ID**, such as a configured `nvidia/...` model. Automatic `meterless/*` modes are refused.
- `NIGHTSHIFT_HOSTED_ONLY=1` and `NIGHTSHIFT_ALLOW_INFERENCE=1` only after the user authorizes the hosted route and usage. The real adapter checks that the exact model is advertised and refuses substitutions.

No provider key belongs in command arguments, this document, Git, screenshots, or exported demo records. The gateway may run on loopback while its actual selected model is hosted.

## Reproducing the bounded flow after new authorization

The command below documents the tested configuration. Do not rerun it under the already consumed allowance. From the repository root, after new explicit usage authorization and fresh read-only endpoint/model checks:

```sh
.venv/bin/python scripts/verify_live_flow.py \
  --allow-hosted-inference \
  --approved-route nvidia/nemotron-3.5-lightning-30b-a3b \
  --expected-provider nvidia \
  --expected-model nvidia/nemotron-3.5-lightning-30b-a3b \
  --max-calls 6 \
  --max-upstream-attempts 18 \
  --max-output-tokens 600 \
  --expected-sample
```

The shown Lightning ID is the exact model used by the completed run. Any later model change needs a separately approved advertised ID. `--expected-provider` and `--expected-model` are optional additional pins; the approved route itself is required and must match the configured hosted model. The adapter and verifier both reject WebLLM/local execution and model substitutions.

Use `--expected-sample` only if Zetaris contains the committed sampled telemetry. It compares all four total counts and each incident's metrics/logs/traces counts with `data/public/sample-manifest.json`. This establishes count agreement, not full row-by-row content equivalence or benchmark accuracy.

The verifier budget is at most six **Nightshift gateway generation calls**: planner, evidence critic, and explanation for history `IC-001`, then the same three calls for holdout `IC-003`. `--max-calls` cannot exceed six; a smaller ceiling intentionally prevents full acceptance. Nightshift's existing `withRetry` behavior can make up to two retries after a transient upstream 429/5xx, for at most **three upstream provider attempts per gateway call**. The historical verification used a conservative bound of **18 upstream attempts** for the full flow. `--max-upstream-attempts` defaults to 18, cannot exceed 18, and must cover three times the selected gateway-call ceiling. This script reserves that worst-case allowance before dispatch; it cannot observe each internal upstream retry from the gateway response, so actual upstream-attempt count remains unknown. Each request receives a fresh verification context nonce and the selected output-token ceiling. With the defaults, each gateway request has at most 600 output tokens and the six accepted responses total at most 3,600 output tokens. Internal retries can attempt additional upstream generation within the separately approved allowance; 3,600 is not a guarantee about all internally attempted tokens. Metadata discovery and read-only SQL are separate from this generation count. Input tokens and provider prices also affect credit usage; this is a request/token bound, not a dollar-denominated billing limiter. Use only the existing approved free NVIDIA endpoint under the user's $0-billed allowance; these receipts alone do not prove account billing.

The verifier performs no retries or answer repairs and accepts no local winning-route substitution or cached answer. The receipt does not establish whether the runtime made auxiliary local calls. Nightshift's own bounded transient-error retries are distinct from script retries and are covered by the explicit upstream-attempt allowance. Every actual response must report `runtime_receipt.cost.actual_usd` as numeric zero. The budget proxy checks the returned cost immediately after the actual call, before any subsequent dispatch, and the retained call receipt must independently agree. A missing or nonzero cost stops dispatch permanently for that verification and preserves the partial receipts. This stops future calls; it cannot undo a completed provider request or independently prove account billing.

## What a pass establishes

The private receipt records separate acceptance layers:

1. The configured Store mode is Zetaris and model mode is Nightshift with a pinned hosted winning route; auxiliary model use is not attested by the receipt.
2. `SELECT 1` and all four application table counts succeed through the actual SQL adapter.
3. The NVIDIA Nemotron request receipts retain `chat_template_kwargs.enable_thinking=false` when that model family is selected. If requested, remote counts agree exactly with the public sample manifest.
4. Both investigations use Zetaris for every query and retain the actual returned rows, SQL, counts, and row hashes.
5. Current evidence references resolve, and each evidence record points to an executed query.
6. Planner and critic affect the investigation before the explanation. Each report retains exactly three distinct actual calls with physical hosted model/provider, uncached compute receipt proof, and exactly $0 reported actual provider cost.
7. A fresh official H-MEM store records measured findings from development history only. The holdout retrieves those findings, the official ledger records reads excluding the current incident, and recalled context triggers actual SQL against the current incident.
8. The holdout creates no historical memories and does not change historical-memory membership.

The verifier does not open evaluator labels, original case paths, injection metadata, or answer keys. Its result is a behavior and provenance check, not a root-cause accuracy score, production readiness assessment, browser proof, deployment proof, or hackathon submission receipt.

## Durable success and failure evidence

Each execution creates a new ignored directory under `artifacts/private/live-flow-<id>/`; an existing directory is never overwritten. A custom new directory can be supplied with `--output`.

- `receipt.json`: overall status, each layer's state, attempted SQL with returned rows/hashes, provider call inventory, and report references.
- `history-report.json` / `target-report.json`: completed reports, when available.
- `provider-calls/history/*.json` / `provider-calls/target/*.json`: exclusive private receipts written by the **actual** Nightshift adapter. They retain bounded prompt/output and allowlisted execution proof, without authentication headers or tokens.
- `memory/`: fresh official H-MEM persistence and its ledger.
- `artifacts/runs/` and `logs/`: the application's full reports and query/reasoning traces, redirected into this verification directory.

The scoped dependency binding uses a pass-through budget proxy around the real configured client. It does not replace HTTP, the model response, SQL, or H-MEM with a fake implementation. Fixture substitution exists only in the verifier test file.

A failure retains already passed layers, attempted/completed SQL, completed or failed model receipts, and any completed earlier report. Later layers remain pending. A timeout, invalid JSON/citation, unknown response envelope, route mismatch, missing memory effect, or incomplete final explanation cannot become a pass. Inspect that concrete failure before considering another separately authorized attempt.

Test the verifier without live services:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests -p 'test_live_verification.py' -v
```

These tests use clearly labeled synthetic inputs and never invoke live SQL or model generation.
