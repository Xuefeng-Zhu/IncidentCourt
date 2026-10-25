# IncidentCourt

An incident investigation desk for **Open Agent Hackathon Track 02 — The Agent That Can Explain Why**. It compares metrics, logs and distributed traces, challenges a leading service hypothesis, and returns a timeline with supporting evidence, counterevidence, alternatives and uncertainty.

**The complete command-line live flow passed on October 25, 2026:** real Zetaris SQL → Nightshift → hosted NVIDIA Nemotron 3.5 Lightning, with official Meterless H-MEM consumption. Two actual investigations passed all 11 verification layers. The run used 45 remote SELECTs and six distinct uncached hosted responses; historical recall caused two extra current-incident queries without storing the holdout. This establishes the tested behavior and provenance on sampled RCAEval telemetry. The saved live record was also verified in the browser through citation rows, query trail and matching JSON export; browser-triggered generation was not repeated. Accuracy, production readiness and deployment are not established.

## Run the offline sample

Requires **Python 3.12+** with SQLite and **Node.js 24+**. No pip or npm packages are needed for application sample execution. The bundled 736,817-byte fixture contains actual measured telemetry, not generated incidents or replayed reports.

```sh
cd /Users/frank/mygit/IncidentCourt
python3.12 run.py --sample
```

Open <http://127.0.0.1:8317>. Choose an incident and run an investigation. Evidence references open their source rows; Query trail shows executed SQL and critic decisions. Export record saves the computed report as JSON.

The prepared `.venv/bin/python` is Python 3.12.13 with working SQLite. To create an environment with uv:

```sh
uv venv --python 3.12 .venv
.venv/bin/python run.py --sample
```

The CLI accepts JSON and prints a report to stdout; execution summaries go to stderr and `logs/`:

```sh
.venv/bin/python run.py --sample --input input_examples/example_1.json
curl -s http://127.0.0.1:8317/run \
  -H 'Content-Type: application/json' \
  -d '{"incident_id":"IC-001","model":"local"}'
```

`SAMPLE_MODE=true` also selects offline mode. A small SQLite file is restored from the committed fixture without network access. It never calls Zetaris or Nightshift. Sample, full local and live modes have distinct receipts and separate sample memory stores.

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

To regenerate the larger local subset:

```sh
uv pip install --python .venv/bin/python -r requirements-data.txt
.venv/bin/python scripts/import_rcaeval.py
.venv/bin/python -m scripts.prepare_sample
```

See [data provenance](docs/DATA.md) and the manifests under `data/public/`.

## Sponsor connections

| Component | Verified layer | Remaining layer |
| --- | --- | --- |
| Meterless H-MEM | Official reference recalled history and triggered two actual Zetaris queries; holdout memory unchanged | Full conformance and production engine behavior not claimed |
| Zetaris | Four native NDP tables, 44,627 real sampled rows, 45 SELECTs with actual query receipts | Broader datasets, production scale and deployment not tested |
| Nightshift | Six distinct uncached planner/critic/explanation responses across two real investigations | Saved live record browser citation/query/export checks passed; new generation disabled after the approved budget |
| NVIDIA | Every response named the exact approved `nvidia/nemotron-3.5-lightning-30b-a3b` route through Nightshift | Runtime cost reports do not independently prove account billing |

Copy `.env.example` to ignored `.env.local` using existing settings. `.venv/bin/python -m scripts.check_integrations` performs read-only discovery and SELECT checks; no inference or resources are created. See [Zetaris data path](docs/ZETARIS.md) and [Meterless/Nightshift details](docs/INTEGRATIONS.md).

The approved six gateway generation calls have been consumed. Persistent `NIGHTSHIFT_ALLOW_INFERENCE=0` disables further generation until new usage is explicitly authorized. The completed run reported zero actual cost for each response and 890 output tokens in total; these are runtime reports, not an invoice. Nightshift's approved worst-case bound was 18 upstream attempts; individual internal retries are not exposed.

For a separately authorized live run, set `SAMPLE_MODE=false`, `INCIDENTCOURT_DATA_MODE=zetaris`, existing Zetaris settings, and Nightshift settings. The current project policy is hosted NVIDIA Nemotron through Nightshift, using an exact advertised `nvidia/...` model ID and `NIGHTSHIFT_HOSTED_ONLY=1`; automatic/local substitutions are rejected. `NIGHTSHIFT_ALLOW_INFERENCE=1` explicitly enables authorized usage of the runtime's providers. `METERLESS_BASE_URL`, `METERLESS_RUNTIME_TOKEN` and `METERLESS_MODEL` are supported aliases. Nightshift is the only integrated LLM provider. An NVIDIA key belongs in Nightshift, not this app.

## Verification

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m scripts.verify_memory_flow
npm ci --prefix vendor/meterless-hmem --ignore-scripts
npm test --prefix vendor/meterless-hmem
```

Tests cover offline restore, units/nulls, query boundaries, missing telemetry, hypothesis revision, H-MEM behavior/exclusion, provider contracts, citation rejection, redirects and HTTP validation. Synthetic test fixtures are labeled and separate from dataset runs. Upstream H-MEM smoke tests cover ten cases; no full conformance claim is made.

The completed live receipt is `artifacts/private/live-flow-20261025-nvidia-01/receipt.json`. History `IC-001` (`3c24d86ae0a5`) produced 14 remote queries and 21 evidence records; target `IC-003` (`99f570d82daa`) produced 16 remote queries and 33 evidence records. All citations resolved. One same-service historical match caused target queries `Q03` and `Q09`; memory membership stayed at two records. See [the live verification record and limits](docs/LIVE_VERIFICATION.md). Private receipts retain real data and provider outputs; they are not committed demo assets.

The label-blind real-data memory check runs cold/history/warm investigations and preserves passing or failed receipts under ignored `artifacts/private/`. Reports are under `artifacts/runs/`; agent/query JSONL traces are under `logs/`. Example outputs never act as canned API responses.

Docker packaging is provided, but **has not been built or run here** because the Docker daemon is unavailable:

```sh
docker build -t incidentcourt .
docker run --rm -p 127.0.0.1:8317:8317 incidentcourt
```

The container defaults to sample mode. Remote adapters require HTTPS. This is a local investigation tool with loopback Host checks, no general SQL API and no remediation actions.

## Event and remaining delivery work

The [organizer event page](https://hackathon.genai.works/event/open-agent-hackathon-2026) and signed-in [resources](https://hackathon.genai.works/resources) were rechecked October 25, 2026. Submissions close **October 27 at 23:45 UTC (4:45 p.m. Pacific)**. Zetaris, a working Meterless Engine feature and Nightshift as primary AI provider are required; Cursor is optional. Public source/README, a 1–4 minute real-running demo, slides and sponsor-use explanation remain delivery work. Nothing has been published or submitted.
