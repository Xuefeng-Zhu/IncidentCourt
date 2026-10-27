# IncidentCourt

A personal workspace for investigating incidents and reviewing evidence, built for **Open Agent Hackathon Track 02 — The Agent That Can Explain Why**. It compares metrics, logs and distributed traces, challenges a leading service hypothesis, and returns a timeline with supporting evidence, counterevidence, alternatives and uncertainty.

**The complete command-line live flow passed on October 25, 2026:** real Zetaris SQL → Nightshift → hosted NVIDIA Nemotron 3.5 Lightning, with official Meterless H-MEM consumption. Two actual investigations passed all 11 verification layers. The run used 45 remote SELECTs and six distinct uncached hosted responses; historical recall caused two extra current-incident queries without storing the holdout. This establishes the tested behavior and provenance on sampled RCAEval telemetry. The saved live record was also verified in the browser through citation rows, query trail and matching JSON export; browser-triggered generation was not repeated. Accuracy, production readiness and deployment are not established.

## Run the offline sample

Requires **Python 3.12+** with SQLite and **Node.js 24+**. No pip or npm installation, credentials or network access are needed to run the sample. The bundled fixture contains measured telemetry from controlled experiments; it is not a set of generated incidents or replayed reports.

```sh
git clone https://github.com/Xuefeng-Zhu/IncidentCourt.git
cd IncidentCourt
python3.12 run.py --sample
```

Open <http://127.0.0.1:8317>, select an incident and choose **New investigation**. You can leave and reopen the run while it executes. Evidence references open their source rows, **Query trail** shows executed SQL and critic decisions, and **Visuals** explores saved observations. Export JSON preserves the original computed report. Stop the server with `Ctrl+C`.

If Python 3.12 is not the default, one option is to create an environment with [uv](https://docs.astral.sh/uv/):

```sh
uv venv --python 3.12 .venv
.venv/bin/python run.py --sample
```

To run an investigation directly from a JSON input, use the CLI. The report goes to stdout; execution summaries go to stderr and `logs/`:

```sh
python3.12 run.py --sample --input input_examples/example_1.json > report.json
```

With the sample server running, the synchronous HTTP endpoint accepts the same input:

```sh
curl -s http://127.0.0.1:8317/run \
  -H 'Content-Type: application/json' \
  -d '{"incident_id":"IC-001","model":"local"}'
```

`SAMPLE_MODE=true` also selects offline mode. A small SQLite file is restored from the committed fixture without network access. It never calls Zetaris or Nightshift. Sample, full local and live modes have distinct receipts and separate sample memory stores.

## Documentation

| Guide | Contents |
| --- | --- |
| [Data and provenance](docs/DATA.md) | RCAEval source, sampling, normalization and leakage boundaries |
| [Integrations](docs/INTEGRATIONS.md) | H-MEM and Nightshift configuration and verification |
| [Zetaris data path](docs/ZETARIS.md) | Table preparation, registration and connection details |
| [Live verification](docs/LIVE_VERIFICATION.md) | Dated execution evidence and its limits |
| [Implementation overview](docs/IMPLEMENTATION.md) | Components, execution flow and workspace APIs |
| [Handoff](docs/HANDOFF.md) | Project status and development context |

## Persistent workspace

- **Incidents:** search and filter the current source's case queue by investigation/review status. Cached case metadata remains available when telemetry cannot be refreshed.
- **Investigations:** reopen completed, failed, and interrupted work using stable URLs. One background run executes at a time, sharing the legacy API's lock; idempotency keys prevent duplicate dispatch. Actual stages and query counts persist. Unfinished runs become Interrupted on server restart and are never automatically resubmitted.
- **Review:** save private notes and mark individual runs reviewed. Notes survive reloads and restarts, never modify generated reports, and never enter telemetry SQL, model prompts, or H-MEM. Concurrent stale review saves return a conflict rather than overwriting newer notes.
- **Visual evidence:** open **Visuals** from a completed report to compare baseline and later metric/span means, inspect telemetry coverage, and explore observed service dependencies. Focus on a service or switch the coverage display between the full, baseline, and later windows; selections are retained in the report URL. Display ranges do not recompute the saved means. Every plotted comparison and dependency links back to evidence and executed SQL. Values and sample counts remain visible without hover; the dependency list also works on narrow screens.
- **Settings:** inspect the source/model and perform explicit metadata-only connection checks. The main workflow has no model selector or credential form.

Visuals read the report's saved rows without requesting telemetry or inference. Each comparison has its own zero-inclusive scale; unrelated metrics are not placed on one shared numeric axis. Metric units are labeled as source units when the record does not declare a unit, while trace durations use milliseconds. Coverage bars show first/last observation bounds, not continuous availability. New reports retain the exact baseline and later-window boundaries with their incident-query reference; older reports show only the recorded telemetry extent. The dependency map shows observed parent/child spans and does not establish causation. Original JSON exports remain unchanged when browsing or filtering.

Private data is stored in ignored `data/workspace.sqlite3` (including its SQLite sidecars). Run one application-server process per workspace database. The source identity excludes credentials and partitions sample, local, and Zetaris history. Stop the server before copying the database for a simple filesystem backup.

When a valid private verification receipt and its reports are explicitly configured, the live workspace can bootstrap those two hash-verified historical reports as unreviewed history. Those private files are not included in a public clone. The workspace does not scan private directories or index unrelated legacy test outputs. Manually imported JSON snapshots persist in the library, remain visibly unverified/unlinked, and cannot change case-queue status. Original reports remain immutable and exports retain their existing JSON format.

New case/history APIs are `/api/cases`, `/api/cases/{id}`, `/api/investigations`, and `/api/investigations/{id}`. POST starts an investigation with `incident_id` and `idempotency_key`; `/api/investigations/{id}/review` saves `note`, `reviewed`, and the current `version`. `/api/investigations/import` accepts a complete report up to 5 MiB. `/api/cases/refresh` and `/api/connections/check` refresh metadata without inference. Existing CLI and synchronous `/run` and `/api/investigate` contracts remain available.

The background UI was exercised end to end using isolated offline sample SQL/rules, including navigation/reload during a gated QA run and eventual completion. This does not constitute a new hosted-provider verification. The existing hosted allowance remains exhausted and inference stays disabled.

## Investigation behavior

1. **Planner** compares the first and last thirds of the observation window. Only metrics with enough non-null samples are compared; baseline-relative changes form hypotheses.
2. **H-MEM recall** searches earlier verified observations for up to three distinct observed services, using the unchanged upstream threshold. Current-incident and holdout memories are filtered before ranking. Retrieved context triggers fresh measurement and log queries.
3. **Evidence Critic** challenges metric-only explanations. **Investigator** compares span durations, parent/child dependencies and bounded later-window log excerpts. Resource and timing checks can revise the service ranking. Stable or decreasing metrics remain visible as counterevidence.
4. **Report** preserves query IDs, returned rows, coverage, observed threshold crossings, alternatives and limitations. Only measured development facts with saved evidence receipts enter memory; a guessed diagnosis is not promoted as a verified fact.

Local mode uses transparent deterministic heuristics. Nightshift mode makes separate structured planner, critic and explanation requests. Its planner selects an observed service and its critic selects an actual follow-up query target. Unknown evidence IDs or malformed decisions fail explicitly. Generated prose has reference validation, not automated proof of every causal claim, and remains separately labeled for review. Failed provider calls do not silently become local successes.

## Data and leakage boundaries

[RCAEval](https://huggingface.co/datasets/phamquiluan/RCAEval), by Pham et al., provides controlled fault-injection experiments. The imported subset is **3 RE2-OB cases**, pinned to revision `afeacb11bcc94dadfd1c8f483ee4377b2b8b614e`: **21,225,701 compressed source bytes**, below a 50 MiB hard cap. The full collection was not downloaded. The MIT license is retained in `LICENSES/RCAEval-MIT.txt`.

The committed sample contains 28,126 metrics, 1,498 logs and 15,000 spans, sampled across three anonymous incidents. Sampling can miss brief events and change rankings. Original paths, labels, injection times and answer keys remain ignored/private. The runtime exposes only `incidents`, `metrics`, `logs` and `traces`; no evaluation tables, file tools or shell are available. IC-001 is development; IC-002 and IC-003 are held out from memory writes. Labels were not used to tune the investigator.

Nulls remain missing. Trace times are normalized to epoch seconds and durations to milliseconds. Missing severity/status is not converted into an error. IC-001 traces end about 170 seconds before its metrics. Correlation or missing spans do not prove a cause or recovery. No deployment history or support tickets were supplied or synthesized.

To regenerate the larger local subset, create `.venv` as shown above first. The following commands install optional data dependencies and download the bounded source subset:

```sh
uv pip install --python .venv/bin/python -r requirements-data.txt
.venv/bin/python scripts/import_rcaeval.py
.venv/bin/python -m scripts.prepare_sample
```

See [data provenance](docs/DATA.md) and the manifests under `data/public/`.

## Sponsor connections

In the hosted investigation path, each component has a distinct role:

1. **Zetaris** serves the incident, metric, log and trace tables. The investigator issues bounded SELECT queries and records their results as evidence.
2. **Meterless H-MEM** recalls eligible findings from earlier investigations. Recalled service names prompt fresh Zetaris queries; historical memory is a lead, not current evidence.
3. **Nightshift** is the gateway for the planner, critic and explanation requests. The planner chooses an observed service and the critic can direct a follow-up query.
4. **NVIDIA Nemotron** supplies the hosted model responses through Nightshift. The historical verification used `nvidia/nemotron-3.5-lightning-30b-a3b`; local sample mode uses deterministic rules instead of this model.

The table describes the October 25, 2026 verification, not a guarantee that those services or credentials are currently available:

| Component | Verified layer | Remaining layer |
| --- | --- | --- |
| Meterless H-MEM | Official reference recalled history and triggered two actual Zetaris queries; holdout memory unchanged | Full conformance and production engine behavior not claimed |
| Zetaris | Four native NDP tables, 44,627 real sampled rows, 45 SELECTs with actual query receipts | Broader datasets, production scale and deployment not tested |
| Nightshift | Six distinct uncached planner/critic/explanation responses across two real investigations | Saved live record browser citation/query/export checks passed; new generation disabled after the approved budget |
| NVIDIA | Every response named the exact approved `nvidia/nemotron-3.5-lightning-30b-a3b` route through Nightshift | Runtime cost reports do not independently prove account billing |

For optional integrations, configure an existing Zetaris instance and Nightshift gateway. Start with a local settings file, preserving it if one already exists:

```sh
test -e .env.local || cp .env.example .env.local
```

Set `SAMPLE_MODE=false`, `INCIDENTCOURT_DATA_MODE=zetaris`, the Zetaris connection and registered table namespace, and the existing Nightshift gateway token and advertised model ID. The H-MEM reference is already vendored and uses Node.js; it does not require a separate hosted memory service. Configure the NVIDIA provider key in Nightshift, not in this repository. Environment variables take precedence over `.env.local` values; keep credentials out of Git.

```sh
python3.12 -m scripts.check_integrations
```

This preflight performs read-only discovery and SELECT checks; it does not send inference requests or create resources. Configuring a connection or passing preflight does not itself verify an end-to-end hosted investigation. For registration and live-run prerequisites, see [Zetaris data path](docs/ZETARIS.md) and [Meterless/Nightshift details](docs/INTEGRATIONS.md).

The six gateway generation calls authorized for the October 25 verification were consumed. Those responses reported zero estimated cost and 890 output tokens in total; the runtime figures are not an invoice. The receipts do not establish the number or route of every internal model call, including any cross-check or tool loop.

For a separately authorized live run, set `SAMPLE_MODE=false`, `INCIDENTCOURT_DATA_MODE=zetaris`, existing Zetaris settings, and Nightshift settings. The historical verification used an exact advertised NVIDIA Nemotron model ID through Nightshift with `NIGHTSHIFT_HOSTED_ONLY=1`. The application checks the selected and returned route, which does not establish whether Nightshift uses auxiliary models internally. Set `NIGHTSHIFT_ALLOW_INFERENCE=1` only when provider usage is authorized. `METERLESS_BASE_URL`, `METERLESS_RUNTIME_TOKEN` and `METERLESS_MODEL` are supported aliases. Nightshift is the integrated LLM gateway; an NVIDIA key belongs in Nightshift, not this app.

## Verification

```sh
python3.12 -m unittest discover -s tests -v
npm ci --prefix vendor/meterless-hmem --ignore-scripts
npm test --prefix vendor/meterless-hmem
```

Tests cover offline restore, units/nulls, query boundaries, missing telemetry, hypothesis revision, H-MEM behavior/exclusion, provider contracts, citation rejection, redirects and HTTP validation. Synthetic test fixtures are labeled and separate from dataset runs. Upstream H-MEM smoke tests cover ten cases; no full conformance claim is made.

The completed live receipt is `artifacts/private/live-flow-20261025-nvidia-01/receipt.json`. History `IC-001` (`3c24d86ae0a5`) produced 14 remote queries and 21 evidence records; target `IC-003` (`99f570d82daa`) produced 16 remote queries and 33 evidence records. All citations resolved. One same-service historical match caused target queries `Q03` and `Q09`; memory membership stayed at two records. See [the live verification record and limits](docs/LIVE_VERIFICATION.md). Private receipts retain real data and provider outputs; they are not committed demo assets.

After importing the larger local dataset, `python3.12 -m scripts.verify_memory_flow` runs the label-blind real-data memory check. It runs cold/history/warm investigations and preserves passing or failed receipts under ignored `artifacts/private/`. Reports are under `artifacts/runs/`; agent/query JSONL traces are under `logs/`. Example outputs never act as canned API responses.

Docker packaging is provided, but **has not been built or run here** because the Docker daemon is unavailable:

```sh
docker build -t incidentcourt .
docker run --rm -p 127.0.0.1:8317:8317 incidentcourt
```

The container defaults to sample mode. Remote adapters require HTTPS. This is a local investigation tool with loopback Host checks, no general SQL API and no remediation actions.

## Event and remaining delivery work

The [organizer event page](https://hackathon.genai.works/event/open-agent-hackathon-2026) and signed-in [resources](https://hackathon.genai.works/resources) were rechecked October 25, 2026. Submissions close **October 27 at 23:45 UTC (4:45 p.m. Pacific)**. Zetaris, a working Meterless Engine feature and Nightshift as primary AI provider are required; Cursor is optional. A 1–4 minute real-running demo, slides and sponsor-use explanation are among the requested delivery materials. Check the organizer for current submission requirements.
