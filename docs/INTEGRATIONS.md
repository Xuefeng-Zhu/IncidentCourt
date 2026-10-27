# Sponsor integrations

Verified October 25, 2026; service availability and configuration may have changed since. Private receipts referenced below are not included in a public clone. **The complete CLI sponsor path passed all 11 live verification layers**: actual Zetaris SQL, hosted NVIDIA Nemotron through Nightshift, and the official Meterless H-MEM reference. The private source of record is `artifacts/private/live-flow-20261025-nvidia-01/receipt.json`. Browser import of the actual saved live record, citation rows, query trail and matching JSON export are verified; browser-triggered generation was not repeated. The approved six gateway generation calls are consumed and persistent inference is disabled.

## Zetaris connectivity and native registration

At verification time, the compute cluster was active and the configured SQL UI origin was reachable. Actual authenticated `SELECT 1`, native NDP previews, all four table registrations and their counts succeeded. The verified remote tables contained 3 incidents, 28,126 metrics, 1,498 logs and 15,000 spans, totaling 44,627 genuine sampled rows. Two complete investigations then executed against them through the actual adapter.

The application uses its Zetaris SQL adapter with credentials only in ignored `.env.local`. No password, account identifier, bucket detail or credential is required in public documentation. No AWS access key was created and no IAM permission was changed. Private setup evidence is retained under `artifacts/private/zetaris-setup/` and `artifacts/private/zetaris-registration-*/`. The completed live verifier retained 45 actual remote SELECTs including preflight, with returned-row hashes and resolving citations; no SQLite fallback was accepted. See `docs/LIVE_VERIFICATION.md` for the full receipt summary, procedure and limits.

## Meterless H-MEM

IncidentCourt executes the **official deterministic H-MEM reference implementation**, not a renamed local search function. The vendored files are unchanged from [Meterless/Meterless](https://github.com/Meterless/Meterless/tree/1742dec56a66e58eef9dda2d2b865823758ca8cd/engines/hmem/reference), commit `1742dec56a66e58eef9dda2d2b865823758ca8cd`. `vendor/meterless-hmem/PROVENANCE.json` records individual SHA-256 hashes; its Apache 2.0 license is retained beside the source.

The Python `HMemStore` adapter invokes `scripts/hmem_bridge.ts` with Node.js 24. The bridge calls upstream `HMEM.add` and `HMEM.query`; provenance, tiered persistence, retrieval scoring, context formatting and the trust ledger come from that upstream implementation. The reference uses deterministic lexical hash embeddings; it does not use a neural embedding model. Upstream describes this as a minimal reference, not a production library.

Only evidence-cited findings from earlier development/history incidents can be saved through `remember_verified`. The application automatically records measured development aggregates after generating query receipts; this does not require a human review flag. The caller is responsible for checking each cited evidence ID against that earlier incident. The adapter is not an independent verifier of the factual narrative. Holdout findings are rejected. Recall filters out the currently investigated incident and any holdout records **before** the upstream ranker runs. An explicit service scope further restricts eligible memories to matching service tags. The upstream ranker retains its default threshold of 0.35 and recall is logged with the upstream trust ledger. Historical recall provides a lead for further checks; it does not become current-incident evidence or establish causation.

The application consumes recalled service tags by querying their current metric coverage and logs, recording the resulting SQL receipts. In Nightshift mode it also passes the returned context to the critic prompt. `tests/test_investigation.py` verifies the local behavior with declared synthetic telemetry: a verified earlier finding adds a reserve-service log query in a fresh incident, while the current evidence still controls the conclusion. This is real Engine execution and application behavior on a synthetic fixture, not a live Nightshift or RCAEval accuracy claim.

Verification completed:

- Upstream reference smoke suite: **10/10 tests passed** using `npm test --prefix vendor/meterless-hmem` after `npm ci --prefix vendor/meterless-hmem --ignore-scripts`.
- IncidentCourt integration suite: **21/21 tests passed**, including real reference persistence/recall, current-incident exclusion, holdout rejection, audit lineage and duplicate avoidance.
- Investigation behavior suite: **8/8 tests passed** on declared synthetic SQLite telemetry, including memory-triggered SQL, hypothesis revision, citation lineage, missing-data uncertainty and rejection of unsupported provider citations.
- All 16 vendored files matched recorded upstream SHA-256 hashes.

These are reference smoke and application contract checks. The separate complete upstream conformance runner has not been run; no full engine conformance claim is made. Dreaming, sleep maintenance and conflict resolution are not invoked by IncidentCourt's current application adapter.

Official contracts: [H-MEM spec](https://github.com/Meterless/Meterless/blob/1742dec56a66e58eef9dda2d2b865823758ca8cd/engines/hmem/AGENTS.md), [reference facade](https://github.com/Meterless/Meterless/blob/1742dec56a66e58eef9dda2d2b865823758ca8cd/engines/hmem/reference/src/index.ts), [reference retrieval](https://github.com/Meterless/Meterless/blob/1742dec56a66e58eef9dda2d2b865823758ca8cd/engines/hmem/reference/src/memoryRetrieval.ts).

## Earlier local real-data memory behavior receipt

`scripts/verify_memory_flow.py` verifies the flow using only anonymous public telemetry tables and fresh separate memory stores. It does not open evaluator labels, injection metadata or original case paths. The latest strict run used development incident `IC-001` as history and holdout incident `IC-003` as a fresh target:

| Check | Observed result |
| --- | --- |
| Cold target investigation | 14 SQL queries, no recalled history |
| Target after earlier measured findings were stored | 16 SQL queries, one historical match |
| Additional actual SQL | Current metric coverage and later log checks for a recalled service |
| Threshold | Upstream default 0.35, unchanged |
| Eligibility | Current incident/holdout excluded, explicit service tags matched before ranking |
| Holdout promotion | None; historical-memory count unchanged |
| Receipt checks | 8/8 passed |

The complete private receipt is `artifacts/private/memory-flow-f96164826298/receipt.json`; report IDs are `de2d7b210130` (cold), `4a476009ca50` (earlier development investigation), and `7d54b4363d46` (warm). This demonstrates local application behavior on real controlled-experiment telemetry. It is not an accuracy benchmark or live Zetaris/Nightshift proof. The earlier failed strict run is also retained at `artifacts/private/memory-flow-77cf389c9ba6/receipt.json`: spending all recall slots on repeated metric quantiles missed a relevant service. Distinct-service recall coverage fixed that issue without changing the threshold.

Reproduce locally with `.venv/bin/python3.12 scripts/verify_memory_flow.py`. Each run preserves independent memory stores and actual query reports under a fresh ignored private directory.

## Nightshift

**Current direction: hosted inference through Nightshift.** The user explicitly asked to stop using local models after the initial local proof. The local model selection and local-specific 150-token cap were removed, and `NIGHTSHIFT_HOSTED_ONLY=1` validates the requested and returned winning route. This does not establish that Nightshift made no auxiliary local calls. A human subsequently saved an NVIDIA key in Nightshift, and metadata-only discovery advertised the approved Lightning model. The final live verifier then completed six distinct uncached hosted responses, three for each of two investigations. No additional local inference is authorized or planned.

Nightshift remains the primary gateway at loopback `http://127.0.0.1:8088/v1`. The official free Mac M1+ application, version 0.92.3, is staged under ignored `work/runtime/`. Its native shell holds provider credentials; the project refers only to the app-generated runtime token file. The human-entered NVIDIA provider key stays in the native Nightshift credential store; it is not copied into this project or printed. The user selected NVIDIA Nemotron and authorized free credits only, zero billed dollars, at most six Nightshift gateway generation calls and a conservative allowance of at most 18 upstream provider attempts. The agent has not created an external key, account, paid resource or billing plan. The clarified allowance includes the runtime retry behavior described below. All six gateway calls were used by the completed live verifier. That historical allowance does not describe a future usage budget. After that run, `NIGHTSHIFT_ALLOW_INFERENCE=0` was restored. Check current configuration before any later run.

| Variable | Meaning |
| --- | --- |
| `NIGHTSHIFT_BASE_URL` | Existing Nightshift gateway endpoint |
| `NIGHTSHIFT_API_KEY` | Existing gateway bearer token, never a provider key |
| `NIGHTSHIFT_TOKEN_FILE` | Alternative path to the existing app-generated runtime token |
| `NIGHTSHIFT_MODEL` | Explicit advertised hosted registry ID, such as a configured `nvidia/...` model |
| `NIGHTSHIFT_HOSTED_ONLY` | `1` refuses automatic routing and local winning-route substitutions; auxiliary runtime activity is not established by the receipt |
| `NIGHTSHIFT_ALLOW_INFERENCE` | Explicit runtime usage gate; consult the active usage authorization and settings |
| `NIGHTSHIFT_MAX_OUTPUT_TOKENS` | Optional bounded output cap |
| `NIGHTSHIFT_ENABLE_THINKING` | `0` sends the documented NVIDIA Nemotron thinking-off control |

`METERLESS_BASE_URL`, `METERLESS_RUNTIME_TOKEN` and `METERLESS_MODEL` are aliases; explicit `NIGHTSHIFT_*` settings take precedence. Aliases do not enable inference.

### Exact hosted model pin

The actual shipped runtime supports a concrete model ID in the standard request `model` field. Its `js/engine/proxy.js` functions `forceModelFromRequest` and `pinFromRequest` pin an enabled registry ID even for an app with a bound mode. A known-provider ID that is unavailable is refused; it is never replaced by automatic routing. In `js/engine/orchestrator.js`, the pinned model's fallback pool contains only that model. The native Mac HTTP bridge uses the same contract in `js/web/web-daemon.js`.

IncidentCourt therefore checks the exact hosted ID against authenticated `GET /v1/models` before sending generation. It sends the chat request to Nightshift, then accepts a result only when the returned physical model and `winning_route.model` match the requested model and `winning_route.provider` matches its provider prefix. A WebLLM/local or other-model response is rejected and retained as a failed receipt. Automatic `meterless/*` routing cannot be dispatched while hosted-only enforcement is enabled. No answer repair or fallback is performed.

The standard [Meterless provider contract](https://github.com/Meterless/Meterless/blob/1742dec56a66e58eef9dda2d2b865823758ca8cd/docs/agent-ready/add-meterless-provider.md) and [hotspot guide](https://github.com/Meterless/Meterless/blob/1742dec56a66e58eef9dda2d2b865823758ca8cd/docs/products/nightshift/hotspot.md) describe gateway authentication and routing. The newer concrete-ID pin is verified directly from the unmodified official 0.92.3 application downloaded from [Meterless's Mac M1+ link](https://meterless.ai/Nightshift/M1%2B/). The installer SHA-256 and setup evidence are retained in `artifacts/private/nightshift-setup/installer-receipt.json`.

### Hosted NVIDIA preparation

The official app supports NVIDIA's hosted OpenAI-compatible API at `https://integrate.api.nvidia.com/v1`; its credential check uses metadata-only `GET /v1/models`. [NVIDIA API documentation](https://docs.api.nvidia.com/nim/reference/llm-apis) lists Nemotron models including `nvidia/nemotron-3.5-lightning-30b-a3b` and `nvidia/nemotron-3-super-120b-a12b`. The human configured the key through Nightshift's provider UI, and authenticated metadata discovery advertised the exact pin `nvidia/nemotron-3.5-lightning-30b-a3b`. The [Lightning page](https://build.nvidia.com/nvidia/nemotron-3.5-lightning-30b-a3b) was labeled with a free endpoint at the time of verification. Availability may change. Use that exact advertised Lightning ID for this verification. The runtime catalog describes hosted NVIDIA access as trial/credits; a zero catalog price is not proof of unlimited free access or a verified account allowance. The completed live run used that exact pin for all six responses; each reported the NVIDIA provider, cache miss and zero actual cost.

### Bounded structured requests

`NIGHTSHIFT_ENABLE_THINKING=0` sends `chat_template_kwargs: {"enable_thinking": false}` in the request to Nightshift. This is NVIDIA's documented direct-answer control in the [official Lightning model card](https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4#api-client). The shipped `proxy.js` passes these kwargs through, and `providers.js` merges caller kwargs after model/UI defaults. The explicit false therefore survives to NVIDIA. No `reasoning_budget` value is added when thinking is off, preserving the application's 600/1200 output budgets for answers. Fixture tests confirmed the wire fields before any hosted call was spent. The six later live request receipts also retained this explicit false setting.

The runtime forwards `response_format`, but the current model-specific hosted reference does not document JSON schema support. This preflight does not send an unverified schema option or repair generated answers; existing strict JSON and citation validation remains required.

The unmodified official runtime retries transient upstream 429/500/502/503/529 failures up to twice, in `providers.js:withRetry`. Its active call site does not expose a retry override, and the checked proxy/header/preference surfaces do not provide one. An explicitly pinned plain chat uses one model and one phase, but one gateway request can still produce up to three upstream attempts on these failures. The user explicitly approved at most six gateway generation calls with a conservative maximum of 18 upstream attempts, free credits only and zero billed dollars. Six gateway requests still cannot be represented as exactly six provider attempts; individual runtime retries are not observable from these gateway receipts. The IncidentCourt adapter adds no retry, answer repair or model fallback.

### Completed live provider and memory evidence

The private receipt `artifacts/private/live-flow-20261025-nvidia-01/receipt.json` reports all 11 layers passed. Development history `IC-001` produced run `3c24d86ae0a5` (14 remote queries, 21 evidence records); holdout `IC-003` produced `99f570d82daa` (16 remote queries, 33 evidence records). All current citations resolved. Six distinct uncached planner/critic/explanation responses named the exact approved `nvidia/nemotron-3.5-lightning-30b-a3b` model and provider `nvidia`.

Each response reported numeric `cost.actual_usd: 0`; the total reported output was 890 tokens. These are runtime cost estimates, not independent account billing or an invoice. Winning-route receipts do not establish whether Nightshift performed auxiliary model or tool calls. The approved conservative upstream-attempt bound was 18; actual individual upstream retries are not exposed. That historical allowance does not describe a future usage budget.

A fresh official H-MEM store held two development observations. The target recalled one earlier same-service finding, which triggered actual current Zetaris queries `Q03` (metric coverage) and `Q09` (later logs). The history store still held two records afterward, and the holdout was not promoted. This establishes actual Engine consumption in the live data/model path, rather than storage alone. It does not establish fault-localization accuracy, production readiness, deployment or submission. Browser viewing of the actual saved live record is verified, without new generation. At that verification point the application suite passed 101 tests.

### Provider evidence and historical local checks

Every attempted completion writes an immutable private mode-0600 receipt at `artifacts/private/nightshift-calls/<call_id>.json`. It retains bounded messages, actual response text, allowed compute receipt fields, elapsed time and sanitized failures; authentication headers, token values and runtime configuration are excluded. Returned `call_id` and `call_receipt` fields link planner, critic and explanation responses even if later validation fails. Contract tests verify secret exclusion, failed receipts, exact model pins and refusal of automatic/local substitutions. These HTTP fixtures do not establish hosted connectivity.

Before the user changed direction, authenticated gateway discovery and one real local nonce response succeeded. The historical physical model was `webllm/Llama-3.2-1B-Instruct-q4f16_1-MLC`, provider `webllm`, privacy `local-only`, actual cost zero and cache miss. All six nonce checks passed; the retained receipt is `artifacts/private/nightshift-setup/local-probe-ebbb8a40e7/receipt.json`. A subsequent full investigation using local SQLite completed actual uncached planner and critic calls but failed at the explanation with HTTP 502 `provider_error`. That partial failure is preserved at `artifacts/private/nightshift-setup/investigation-d05656a2f2/receipt.json`, with three per-call records and queries at failure. It is not a completed live investigation and does not establish Zetaris connectivity. No local rerun or answer repair followed.

## Event check — October 25, 2026

The [organizer event page](https://hackathon.genai.works/event/open-agent-hackathon-2026) confirms Track 02 and the submission deadline **October 27, 2026 at 23:45 UTC** (4:45 p.m. Pacific). It lists the build window as October 22–27, 2026. Public resource fetching redirects to sign-in. The existing signed-in resource hub was subsequently checked: [Technical Execution Requirements](https://hackathon.genai.works/resources?id=01a08ff1-54b1-7670-9441-a1e73ff43a6e) and the October 23 Official Rules resource confirm Zetaris, a working Meterless Engine feature and Nightshift as primary AI provider; Cursor is optional. The technical guide labels its general practices as recommendations and defers to the binding event page. Offline sample mode, examples and JSONL receipts were added accordingly.

The Meterless repository's community hackathon guide still shows October 15–20 and an older track/rubric list. Treat that as stale event guidance and use the organizer's current page for dates, tracks and scoring. Sponsor technical contracts above remain useful independently of that stale guide. Recheck authenticated resources and complete current requirements before claiming submission readiness.
