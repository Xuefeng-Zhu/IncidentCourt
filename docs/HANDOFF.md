# IncidentCourt — working handoff

## Repository and historical context

The [GitHub repository](https://github.com/Xuefeng-Zhu/IncidentCourt) is public. The persistent workspace and visual evidence work were merged into `main` at `793a842` on October 26, 2026. Fetch the remote and inspect the working tree before making changes. When creating a worktree for local app or authentication verification, copy the matching ignored `.env*` files from the main checkout without printing their values.

The setup and verification details below are a historical record from October 25, 2026, unless a different date is stated. Service availability, configuration, testing results and event requirements can change. Private artifacts referenced here are ignored and are not part of a public clone.

The requested complete live sponsor path has now passed through the command-line verifier: **Zetaris SQL → Nightshift → hosted NVIDIA Nemotron 3.5 Lightning + official H-MEM**. The receipt at `artifacts/private/live-flow-20261025-nvidia-01/receipt.json` reports all 11 layers passed. The actual saved live target record was imported in the browser, with citation row expansion, query-trail review and JSON export matching the original record. No browser-triggered generation was performed. No accuracy, production-readiness or deployment claim follows from this pass.

## Persistent product workspace

The UI now uses Incidents → case history → persistent investigation pages, with separate personal notes and per-run review state. Reports remain immutable. Settings contains source/model details and explicit metadata-only checks. JSON import is a secondary library action and never links unknown snapshots into the case queue.

`incidentcourt/workspace.py` owns the ignored, private SQLite registry, source partitioning, bounded report imports, review-version conflicts, and background jobs. New HTTP routes are documented in README. A single process/active job shares the existing execution lock. Real progress callbacks report actual phases and completed SQL counts; interrupted jobs never restart automatically. CLI and existing synchronous endpoints remain compatible.

The two existing hash-verified live reports bootstrap once as unreviewed history. Browser testing at that time verified notes/review persistence across page and server restart; temporary QA notes/review flags were restored afterward. Isolated offline QA exercised an actual sample investigation through the background API while navigating/reloading, then observed one completed unreviewed run. Imported snapshots persisted without changing queue membership. The workspace QA suite passed 110 tests at that time. Desktop/390px/320px browser checks passed with no document overflow or console warnings/errors. At the time of that check the real app was on port 8317 and the isolated offline QA server was stopped. No additional hosted inference was sent.

## Verified setup and remaining acceptance

### Zetaris

- At verification time the existing compute cluster was active. The application uses the SQL UI origin, not the Cloud Gateway billing/account page; configure the appropriate origin in the local environment.
- Authenticated **SELECT 1 succeeded** against the actual instance. Native NDP preview, creation and count checks succeeded, followed by two complete investigations through the actual remote tables.
- All four native NDP tables are registered and their counts verified: 3 incidents, 28,126 metrics, 1,498 logs and 15,000 trace spans, totaling 44,627 genuine sampled rows. The complete live verification executed 45 actual remote SELECTs, including preflight; all returned-row provenance and citations passed. There was no SQLite fallback.
- Existing settings and account credentials are kept only in ignored `.env.local`. Do not publish passwords, account/organization identifiers, storage details or credential values in documentation or screenshots. The setup has not created AWS access keys or changed IAM permissions.
- Private evidence includes `artifacts/private/zetaris-setup/select-one-response.json`, `ndp-table-listing.json`, and the registration receipts under `artifacts/private/zetaris-registration-*/`.
- Read `docs/ZETARIS.md` for the verified application contract and idempotent setup workflow. Existing namespaces/tables must be reconciled before any later mutation; do not recreate or overwrite the working data path.

### Nightshift and hosted NVIDIA

- The user explicitly said **do not use a local model**. The local LLM lane was stopped. Do not restart it, run local-model inference, or permit a local fallback.
- The official Nightshift Mac M1-M4 app, version 0.92.3, is staged at ignored `work/runtime/Nightshift M1-M4.app`. Its gateway listens on loopback `http://127.0.0.1:8088/v1`. The existing app-generated runtime token is referenced by `NIGHTSHIFT_TOKEN_FILE`; its value is not copied into the project or printed.
- A human saved the NVIDIA provider key in Nightshift's native credential store. Authenticated metadata-only discovery advertised **`nvidia/nemotron-3.5-lightning-30b-a3b`**, the exact approved hosted pin. No provider account/key creation or raw direct-to-NVIDIA inference is authorized for the agent.
- `NIGHTSHIFT_HOSTED_ONLY=1` requires an explicit advertised hosted ID and exact returned model/provider receipt. Automatic modes and local winning-route substitutions are refused; the receipt does not establish whether the runtime made auxiliary local calls. Nightshift remains the primary model gateway.
- `NIGHTSHIFT_ENABLE_THINKING=0` sends documented `chat_template_kwargs.enable_thinking=false`, preserving the answer budget for strict JSON outputs. No unverified JSON-schema option, response repair or answer replay is used.
- The user approved **free credits only, zero billed dollars, at most six Nightshift gateway generation calls and a conservative maximum of 18 upstream provider attempts**. Nightshift can internally retry transient failures up to twice; the app itself adds no retry. This allowance is for the two full live investigations, three calls each. **All six gateway calls under that historical allowance were used.** All six were distinct and uncached, reported the exact approved NVIDIA model/provider, and reported `actual_usd: 0`; their output total was 890 tokens. The runtime does not expose each upstream attempt, and reported cost is not an independent billing invoice. `NIGHTSHIFT_ALLOW_INFERENCE=0` was restored after that run. Metadata discovery and read-only SQL are separate from this generation allowance.
- Historical evidence only: one real local nonce probe passed before the user changed direction. A later local-SQLite/local-LLM full investigation completed actual planner and critic calls but failed at explanation with HTTP 502. Preserve those receipts; they are not the current plan, a completed live investigation, or Zetaris proof.

### Full live verification

`scripts/verify_live_flow.py` and `docs/LIVE_VERIFICATION.md` define the bounded check. Use real Zetaris Store mode, sample mode disabled, the exact NVIDIA Lightning pin through Nightshift, fresh H-MEM persistence, and the approved call/attempt limits. The completed sequence used development history `IC-001` (`3c24d86ae0a5`, 14 remote queries, 21 evidence records), then fresh holdout `IC-003` (`99f570d82daa`, 16 remote queries, 33 evidence records), with three genuine planner/critic/explanation responses each. All 11 verification layers passed. H-MEM retrieved one earlier match and caused current target queries `Q03` and `Q09`; historical-memory membership stayed at two records and the holdout was not stored. Every query must have backend `zetaris`; actual responses must be uncached and match the hosted model/provider pin. Historical memory must trigger new current-incident SQL, and the holdout must not be stored as history.

Model-quality caveat: the history critic selected `checkoutservice` (queried in `Q08`) but its reason described `cartservice` as an alternative/core dependency. The target critic's `frontend-external` selection and `Q09` follow-up were consistent. No output was repaired or replayed. The passed integration validates structured IDs, actual calls and query provenance; it does not establish narrative consistency, causal reasoning or accuracy. Keep the actual model outputs for review.

Preserve complete and failed call receipts under ignored `artifacts/private/`; no answer repair, silent SQLite fallback, synthetic success trace or provider substitution is allowed. A full pass must distinguish data connectivity, table registration, model inference, evidence citations, adaptive query behavior, and memory consumption. Current upstream retry counts are not individually visible from gateway responses; do not invent them or equate six gateway calls with exactly six provider attempts. Provider-reported zero cost does not independently establish account billing.

## Application and data

- Start the offline sample UI with `.venv/bin/python run.py --sample` at `http://127.0.0.1:8317`. This uses deterministic local investigation logic and clearly labeled sample data; it is separate from live acceptance. The prepared Python 3.12.13 environment has working SQLite; use it because the machine's Homebrew Python had a missing SQLite dylib.
- Three bounded actual RCAEval RE2-OB cases were imported: 21,225,701 compressed source bytes. A 736,817-byte committed anonymous telemetry fixture supports offline execution. Do not download the multi-GB collection again. Dataset provenance and third-party licensing are recorded in `docs/DATA.md`.
- Author sources: [RCAEval repository](https://github.com/phamquiluan/RCAEval) and [dataset](https://huggingface.co/datasets/phamquiluan/RCAEval). The selected material contains controlled fault-injection experiments, not production outages. Ground truth identifies the injected fault/service, not a complete causal narrative.
- Keep original case names, source paths, answer keys, fault labels and injection timestamps private and outside agent-accessible tables. Do not read held-out labels while tuning. Historical memory cannot contain the current target's answer.
- Metrics, logs and traces are normalized into anonymous `incidents`, `metrics`, `logs`, `traces` tables. Missing observations stay null. Preserve identifiers, timestamps, span linkage and query provenance. No deployment history or support tickets are supplied by RCAEval; no synthetic deployment/ticket examples have been added.

## Investigation behavior and completed local checks

Track 02, **The Agent That Can Explain Why**, remains the selected direction. IncidentCourt compares hypotheses across telemetry, lets an evidence critic trigger independent queries, and produces a cited timeline, supporting evidence, counterevidence, alternatives and uncertainty. Temporal correlation does not prove causation.

Read-only SQL templates implement metric comparison, trace/dependency checks and later-window log queries. Local mode is explicitly heuristic. Nightshift mode calls actual structured planner, critic and explanation stages and validates returned service/evidence IDs. The unchanged pinned official Meterless H-MEM reference executes through Node 24. It uses deterministic lexical hash embeddings, not a neural embedding service; full upstream conformance is not claimed.

Real-data cold/history/warm verification on `IC-001` → `IC-003` added two current SQL queries (14 → 16), with same-service-scoped recall and the unchanged upstream 0.35 threshold. Holdout/current incidents are excluded before ranking. Local sample browser citation/filter/SQL/export flows and widths 320/390 previously passed; three sample incidents ran from a fresh source copy with Python networking blocked. The upstream H-MEM smoke suite passed 10/10, and the current sponsor adapter suite passed 21/21. The earlier live-verification application suite passed **101 tests**. The saved live record also passed browser checks at 580px and 390px, with no horizontal overflow or console warnings/errors. Docker packaging exists; container execution is not verified.

## Event requirements and delivery boundaries

The event sources checked at that time were [the organizer event page](https://hackathon.genai.works/event/open-agent-hackathon-2026) and the authenticated [resource hub](https://hackathon.genai.works/resources). They were rechecked on October 25, 2026. The deadline is **October 27, 2026 at 23:45 UTC / 4:45 p.m. Pacific**. Recheck before final submission.

The application must use Zetaris for its real data layer, demonstrate at least one working Meterless Engine feature that affects behavior, and use Nightshift as the primary model provider. NVIDIA is optional for general entry; meaningful verified Nemotron use is needed for its award. Cursor is optional; older material treating it as mandatory is stale. Meterless's repository community guide also has outdated event dates/tracks; the current organizer page takes precedence.

Expected delivery materials include public source and README, a 1–4 minute real-running demo video, a slide deck and sponsor-use explanation. `metadata.json` is not required by the current organizer clarification. Disclose pre-existing code/data and synthetic examples; preserve third-party notices. These are requirements to prepare, not authorization to publish, deploy, accept terms, submit or post externally.

Repository publication occurred on October 26, 2026. This historical handoff does not itself authorize application deployment, final hackathon submission, external posting, new paid resources, AWS key creation or IAM changes. The bounded hosted allowance above describes the historical verification; it is not a current usage budget.
