#!/usr/bin/env python3
"""Bounded RCAEval import. The runtime never reads this program's private inputs."""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import sys
import urllib.error
import urllib.request

REPOSITORY = "phamquiluan/RCAEval"
REVISION = "afeacb11bcc94dadfd1c8f483ee4377b2b8b614e"
BASE = f"https://huggingface.co/datasets/{REPOSITORY}/resolve/{REVISION}"
API = f"https://huggingface.co/api/datasets/{REPOSITORY}/tree/{REVISION}"
HARD_CAP = 50 * 1024 * 1024
SCHEMA = """
CREATE TABLE incidents(id TEXT PRIMARY KEY,title TEXT NOT NULL,source TEXT NOT NULL,start_ts REAL NOT NULL,end_ts REAL NOT NULL,split TEXT NOT NULL);
CREATE TABLE metrics(id TEXT PRIMARY KEY,incident_id TEXT NOT NULL REFERENCES incidents(id),ts REAL NOT NULL,service TEXT NOT NULL,name TEXT NOT NULL,value REAL);
CREATE TABLE logs(id TEXT PRIMARY KEY,incident_id TEXT NOT NULL REFERENCES incidents(id),ts REAL NOT NULL,service TEXT,severity TEXT,message TEXT);
CREATE TABLE traces(id TEXT PRIMARY KEY,incident_id TEXT NOT NULL REFERENCES incidents(id),ts REAL NOT NULL,service TEXT,span_id TEXT,parent_id TEXT,trace_id TEXT,duration_ms REAL,status INTEGER);
CREATE INDEX metric_window ON metrics(incident_id,service,name,ts);
CREATE INDEX log_window ON logs(incident_id,service,ts);
CREATE INDEX trace_window ON traces(incident_id,service,ts);
CREATE INDEX trace_link ON traces(incident_id,trace_id,span_id);
CREATE INDEX trace_parent ON traces(incident_id,trace_id,parent_id);
"""


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


class Budget:
    """Enforce a cap across every source file, including already cached files."""
    def __init__(self, maximum: int):
        if not 0 < maximum <= HARD_CAP:
            raise ValueError("Download budget must be between 1 byte and 50 MiB")
        self.maximum, self.total = maximum, 0

    def fetch(self, url: str, path: Path, expected_size: int | None = None,
              expected_hash: str | None = None) -> dict:
        if expected_size and self.total + expected_size > self.maximum:
            raise ValueError("Selected telemetry exceeds the download cap")
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            partial = path.with_suffix(path.suffix + ".part")
            try:
                with urllib.request.urlopen(url, timeout=40) as response, partial.open("wb") as out:
                    announced = int(response.headers.get("Content-Length", "0"))
                    if announced and self.total + announced > self.maximum:
                        raise ValueError("Source file exceeds the remaining download cap")
                    size = 0
                    while chunk := response.read(1024 * 1024):
                        size += len(chunk)
                        if self.total + size > self.maximum:
                            raise ValueError("Source stream exceeds the remaining download cap")
                        out.write(chunk)
                partial.replace(path)
            finally:
                partial.unlink(missing_ok=True)
        size = path.stat().st_size
        if self.total + size > self.maximum:
            raise ValueError("Cached source files exceed the download cap")
        if expected_size is not None and size != expected_size:
            raise ValueError("Source file length failed verification")
        digest = sha256(path)
        if expected_hash and digest != expected_hash:
            raise ValueError("Source file hash failed verification")
        self.total += size
        return {"bytes": size, "sha256": digest}


def select_cases(rows: list[dict], count: int = 3) -> list[dict]:
    if not 1 <= count <= 3:
        raise ValueError("Import between one and three incidents")
    candidates = [r for r in rows if r["dataset"] == "RE2-OB" and r["has_logs"]
                  and r["has_traces"] and r["n_logs"] > 0 and r["n_traces"] > 0]
    candidates.sort(key=lambda r: (r["n_logs"] + r["n_traces"], r["case"]))
    chosen, services = [], set()
    for row in candidates:
        if row["root_cause_service"] in services:
            continue
        services.add(row["root_cause_service"])
        chosen.append(row)
        if len(chosen) == count:
            return chosen
    raise ValueError("Insufficient distinct RE2-OB incidents with all three signals")


def number(value):
    if value is None:
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def service(value):
    # This is an observed naming inconsistency between trace and metric/log sources.
    return "frontend" if value == "frontendservice" else value


def normalize_metrics(table, incident: str):
    columns = table.column_names
    if "time" not in columns:
        raise ValueError("Metric source lacks time")
    for row_idx, row in enumerate(table.to_pylist()):
        ts = number(row["time"])
        if ts is None:
            raise ValueError("Metric timestamp is missing or nonfinite")
        for col_idx, col in enumerate(columns):
            if col == "time":
                continue
            svc, sep, metric = col.rpartition("_")
            if not sep:
                raise ValueError("Metric column lacks service separator")
            yield (f"{incident}-m-{row_idx}-{col_idx}", incident, ts,
                   service(svc), metric, number(row[col]))


def normalize_logs(table, incident: str):
    required = {"timestamp", "container_name", "message"}
    if not required <= set(table.column_names):
        raise ValueError("Unexpected log schema")
    for idx, row in enumerate(table.to_pylist()):
        ts = number(row["timestamp"])
        if ts is None:
            raise ValueError("Log timestamp is missing or nonfinite")
        yield (f"{incident}-l-{idx}", incident, ts, service(row["container_name"]),
               None, row["message"])  # Severity is not supplied by the dataset.


def normalize_traces(table, incident: str):
    required = {"startTime", "duration", "serviceName", "spanID", "parentSpanID", "traceID", "statusCode"}
    if not required <= set(table.column_names):
        raise ValueError("Unexpected trace schema")
    for idx, row in enumerate(table.to_pylist()):
        ts = number(row["startTime"])
        if ts is None:
            raise ValueError("Span start timestamp is missing or nonfinite")
        duration = number(row["duration"])
        if duration is not None and duration < 0:
            raise ValueError("Span duration is negative")
        yield (f"{incident}-t-{idx}", incident, ts / 1_000_000,
               service(row["serviceName"]), row["spanID"], row["parentSpanID"], row["traceID"],
               duration / 1000 if duration is not None else None, row["statusCode"])


def json_write(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, allow_nan=False) + "\n")


def import_data(root: Path, count: int = 3, maximum: int = HARD_CAP):
    import pyarrow.parquet as pq
    raw, private = root / "data/raw", root / "data/private"
    private.mkdir(parents=True, exist_ok=True)
    os.chmod(private, 0o700)
    budget = Budget(maximum)
    index_receipt = budget.fetch(f"{BASE}/cases.parquet", raw / "cases.parquet", 29500,
        "c49a288920dbba2e8e724679a14636d5c7eb2b45426bba14007ef79a6c0ab1bb")
    chosen = select_cases(pq.read_table(raw / "cases.parquet").to_pylist(), count)
    public = {"repository": REPOSITORY, "revision": REVISION, "license": "MIT",
              "source_kind": "controlled fault-injection experiment", "synthetic_additions": False,
              "selection": "Smallest complete RE2-OB cases by log+trace row count, distinct injected services",
              "maximum_bytes": maximum, "incidents": []}
    answers = {"revision": REVISION, "index": index_receipt, "incidents": []}
    database = root / "data/telemetry.sqlite3"
    temp = database.with_suffix(".sqlite3.part")
    if temp.exists():
        temp.unlink()
    with closing(sqlite3.connect(temp)) as con:
        con.execute("PRAGMA foreign_keys = ON")
        con.executescript(SCHEMA)
        for ordinal, case in enumerate(chosen, 1):
            anonymous = f"IC-{ordinal:03d}"
            split = "development" if ordinal == 1 else "holdout"
            files = json.load(urllib.request.urlopen(f"{API}/{case['case']}", timeout=30))
            by_name = {f["path"].rsplit("/", 1)[-1]: f for f in files}
            entry = {"id": anonymous, "split": split, "files": {}, "rows": {}, "coverage": {}}
            raw_case = raw / case["case"]
            tables = {}
            for name in ("metrics", "logs", "traces"):
                f = by_name[f"{name}.parquet"]
                expected_hash = f.get("lfs", {}).get("oid")
                path = raw_case / f"{name}.parquet"
                receipt = budget.fetch(f"{BASE}/{case['case']}/{name}.parquet", path,
                                       f["size"], expected_hash)
                tables[name] = pq.read_table(path)
                entry["files"][name] = receipt | {"source_rows": tables[name].num_rows}
            # Only telemetry coverage is public; injection boundaries and labels stay private.
            times = tables["metrics"]["time"].to_pylist()
            con.execute("INSERT INTO incidents VALUES (?,?,?,?,?,?)", (
                anonymous, f"Online Boutique incident {ordinal:03d}", "RCAEval RE2-OB",
                min(times), max(times), split))
            con.executemany("INSERT INTO metrics VALUES (?,?,?,?,?,?)", normalize_metrics(tables["metrics"], anonymous))
            con.executemany("INSERT INTO logs VALUES (?,?,?,?,?,?)", normalize_logs(tables["logs"], anonymous))
            con.executemany("INSERT INTO traces VALUES (?,?,?,?,?,?,?,?,?)", normalize_traces(tables["traces"], anonymous))
            for table in ("metrics", "logs", "traces"):
                entry["rows"][table] = con.execute(f"SELECT COUNT(*) FROM {table} WHERE incident_id=?", (anonymous,)).fetchone()[0]
                start, end = con.execute(f"SELECT MIN(ts),MAX(ts) FROM {table} WHERE incident_id=?", (anonymous,)).fetchone()
                entry["coverage"][table] = {"start_ts": start, "end_ts": end}
            entry["missing_metric_values"] = con.execute("SELECT COUNT(*) FROM metrics WHERE incident_id=? AND value IS NULL", (anonymous,)).fetchone()[0]
            public["incidents"].append(entry)
            answers["incidents"].append({"id": anonymous, "source_case": case["case"],
                "source_directory": str(raw_case), "root_cause_service": case["root_cause_service"],
                "fault": case["fault"], "inject_time": case["inject_time"], "split": split})
            con.commit()
            print(f"Imported {anonymous}: {entry['rows']}", flush=True)
        # Guard that original case names and metadata never entered the query database.
        for case in chosen:
            for table in ("logs", "metrics", "traces"):
                column = "message" if table == "logs" else "service"
                if con.execute(f"SELECT 1 FROM {table} WHERE {column} LIKE ? LIMIT 1", (f"%{case['case']}%",)).fetchone():
                    raise ValueError("Answer-revealing source name entered normalized data")
        con.execute("ANALYZE")
        con.commit()
    temp.replace(database)
    public["total_source_bytes"] = budget.total
    public["database_sha256"] = sha256(database)
    json_write(private / "answer-key.json", answers)
    os.chmod(private / "answer-key.json", 0o600)
    json_write(root / "data/public/manifest.json", public)
    print(f"Verified {len(chosen)} incidents; {budget.total:,} source bytes; database {database.stat().st_size:,} bytes")
    return public


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--cases", type=int, default=3)
    parser.add_argument("--max-bytes", type=int, default=HARD_CAP)
    args = parser.parse_args()
    try:
        import_data(args.root.resolve(), args.cases, args.max_bytes)
    except (ValueError, urllib.error.URLError, OSError, ImportError) as exc:
        # Do not print remote URLs or raw paths that reveal case names.
        print(f"Import failed ({type(exc).__name__}); check local dependencies, cap and source availability.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
