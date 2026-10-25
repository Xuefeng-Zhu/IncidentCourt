# Telemetry provenance and import

IncidentCourt uses a deliberately bounded subset of the author's [RCAEval dataset](https://huggingface.co/datasets/phamquiluan/RCAEval) at revision `afeacb11bcc94dadfd1c8f483ee4377b2b8b614e`. These are controlled fault-injection experiments in Online Boutique (RE2-OB), not production incident reports. The source index describes an injected service/fault; it is not a complete causal explanation.

Run from the repository root:

```sh
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements-data.txt
.venv/bin/python scripts/import_rcaeval.py
```

On this machine the verified environment is Python 3.12.13. The optional importer dependency is pinned in `requirements-data.txt`; the application does not need Arrow to query the resulting SQLite database. If a pre-existing `.venv` works, keep using it.

The importer fetches the 29,500-byte case index and at most three complete RE2-OB cases, ordered by log-plus-trace row count with distinct injected services. It inspects each selected directory's actual file metadata, verifies advertised lengths and available LFS SHA-256 hashes, and enforces a 50 MiB ceiling across source files, including cached files. It never uses `snapshot_download`, recursive telemetry fetches, or the full collection. `--max-bytes` can lower the ceiling and `--cases` can lower the case count. The application database is larger than the compressed Parquet subset because metrics are normalized and indexes are materialized.

`data/public/manifest.json` records revision, anonymous IDs, source-file hashes and sizes, source and normalized row counts, missing metric counts, and the database hash. Original case paths, injected services/faults and injection timestamps are kept only in ignored `data/private/answer-key.json`; raw Parquet is kept only in ignored `data/raw/`. Private answer files use owner-only permissions. `data/telemetry.sqlite3` contains no answer table, source directory, fault label or injection boundary. Agent tools must restrict reads to its telemetry tables; the importer and private files are not agent tools.

`IC-001` is a development incident. `IC-002` and `IC-003` are held out for later evaluation. Do not read their answer-key entries while tuning ranking or constructing historical memory. An evaluation of three selected cases would be a small diagnostic check, not a benchmark-wide performance claim. Memory must exclude the current incident and its answer.

## Verified source schema and normalization

| Signal | Observed source fields | Normalization |
| --- | --- | --- |
| Metrics | `time` int64; float64 columns named `{service}_{metric}` | `time` is Unix seconds; service and metric are separated at the last underscore. Preserve source NULLs and exact zero values separately. Nonfinite numeric values become SQL NULL. No filling, resampling, or interpolation. |
| Logs | `timestamp` int64, `container_name`, `message` strings | Unix seconds; message preserved. Source has no severity field, so `severity` is NULL. A text match on failure terms is a derived heuristic, not a supplied severity label. |
| Traces | `startTime` int64, `startTimeMillis` int64, `duration` int64, `serviceName`, `spanID`, `parentSpanID`, `traceID`, `statusCode` | `startTime` microseconds divided by 1,000,000 gives Unix seconds. Duration microseconds divided by 1,000 gives milliseconds. IDs remain strings, preserving leading zeros. Source status code and missing values are preserved without inventing errors. |

Trace duration treatment follows the author's [trace RCA implementation](https://github.com/phamquiluan/RCAEval/blob/main/RCAEval/e2e/tracerca.py), which divides source duration by 1,000 for the duration statistic. Metric latency percentiles retain the source units (seconds), and metric memory retains source bytes; do not compare raw magnitudes across metric types.

The observed trace service `frontendservice` is normalized to `frontend` to align it with metric/log service names. No other service alias is inferred. Raw originals retain their original service names. Parent spans can be absent from the captured window; missing linkage is uncertainty, not proof of a root span. Null status codes are unknown, not success. A lack of nonzero source status codes does not prove there was no incident.

Each normalized row has a stable source-reference ID: `IC-NNN-m-<source-row>-<source-column>`, `IC-NNN-l-<source-row>`, or `IC-NNN-t-<source-row>`. These resolve to raw Parquet row positions using the private provenance mapping; public citations need only the anonymous row ID. Incident start/end are observed metric coverage, not injected-fault boundaries. Normalized SQL tables are `incidents`, `metrics`, `logs`, and `traces` with indexes for incident/service/time queries and parent/span joins.

No deployment histories, support tickets, or synthetic telemetry are included. Any later synthetic scenario must have a separate source label and cannot count as RCAEval proof. Telemetry correlation and parent/child timing constrain hypotheses but do not establish intervention-level causation.

## Attribution and license

Pham, Luan; Zhang, Hongyu; Ha, Huong; Salim, Flora; Zhang, Xiuzhen. *RCAEval: A Benchmark for Root Cause Analysis of Microservice Systems with Telemetry Data.* Companion Proceedings of the ACM Web Conference 2025, pp. 777–780. [Author repository](https://github.com/phamquiluan/RCAEval), [paper](https://arxiv.org/abs/2412.17015).

The pinned Hugging Face dataset card declares MIT. The following notice is retained from the [author repository license](https://github.com/phamquiluan/RCAEval/blob/main/LICENSE):

MIT License

Copyright (c) 2024 Luan Pham

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## Local import receipt

The verified import contains 21,225,701 source bytes (20.24 MiB) and a 356,225,024-byte normalized database. SQLite integrity and foreign-key checks passed.

| Anonymous incident | Split | Metric rows | Log rows | Span rows | Missing metric values |
| --- | --- | ---: | ---: | ---: | ---: |
| IC-001 | development | 68,820 | 110,067 | 199,960 | 1,974 |
| IC-002 | holdout | 103,752 | 117,601 | 254,889 | 916 |
| IC-003 | holdout | 106,634 | 131,836 | 286,200 | 1,214 |

Coverage differs between signals. In IC-001, the last captured span starts roughly 170 seconds before metric coverage ends; logs end roughly 37 seconds before metrics. The public manifest records each signal's observed coverage. Do not treat missing late spans as evidence of recovery or compare windows without checking observation counts.

## Offline sample mode

The committed `data/sample.json.gz` contains real, sampled rows from the three imported incidents. Its compressed size is 736,817 bytes; it expands to 7,343,741 bytes of JSON and restores to a 9,355,264-byte indexed SQLite database. `incidentcourt.sample.ensure_sample_db(path)` needs only Python's standard library, verifies the committed fixture hash, and creates the database atomically. It refuses to overwrite an unrelated, unmarked database. No download, provider request, PyArrow import, or secret is required for restoration.

`SAMPLE_MODE=true` selects this offline application path. The source application data path through live Zetaris remains a separate integration check. Sample mode contains 28,126 metrics, 1,498 logs and 15,000 spans, with original source IDs and values. It samples every tenth distinct metric timestamp (plus boundaries), up to 500 logs per incident spread across time and text-match strata, and up to 5,000 spans per incident as complete captured trace groups distributed across twelve temporal strata. Missing source parents remain missing.

Every selected fixture row was compared with the full normalized source and matched exactly. Trace-group counts matched the full captured source for 296/357/390 selected groups respectively; every signal retained its original first and last timestamp. This preserves the observed timeline boundaries, not every intervening event. Sampling changes metric distributions and may omit rare failures, so conclusions can differ from full-subset runs. `data/public/sample-manifest.json` records exact counts, sampling rules, hashes, coverage and limitations. `scripts/prepare_sample.py` can reproduce the fixture from an existing full import without network access.
