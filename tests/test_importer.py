"""Importer checks independent of network and optional Arrow runtime."""
import importlib.util
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("importer", Path(__file__).resolve().parents[1] / "scripts/import_rcaeval.py")
importer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(importer)


class Table:
    def __init__(self, rows):
        self.rows = rows
        self.column_names = list(rows[0])
    def to_pylist(self):
        return self.rows


class ImporterTests(unittest.TestCase):
    def test_metrics_preserve_missing_not_zero_and_ids_resolve(self):
        table = Table([{"time": 1700000000, "cartservice_cpu": None, "frontend_mem": 0.0}])
        rows = list(importer.normalize_metrics(table, "IC-001"))
        self.assertIsNone(rows[0][-1])
        self.assertEqual(rows[1][-1], 0)
        self.assertEqual(rows[0][3:5], ("cartservice", "cpu"))
        self.assertEqual(len({r[0] for r in rows}), 2)
        with sqlite3.connect(":memory:") as con:
            con.executescript(importer.SCHEMA)
            con.executemany("INSERT INTO metrics VALUES (?,?,?,?,?,?)", rows)
            self.assertEqual(con.execute("SELECT COUNT(value),COUNT(*) FROM metrics").fetchone(), (1, 2))

    def test_span_units_parent_link_ids_and_missing_status_survive(self):
        rows = [{"startTime": 1700000000123456, "duration": 1500, "serviceName": "frontendservice", "spanID": "000123", "parentSpanID": "000012", "traceID": "000001", "statusCode": None}]
        span = list(importer.normalize_traces(Table(rows), "IC-001"))[0]
        self.assertAlmostEqual(span[2], 1700000000.123456)
        self.assertEqual(span[3:7], ("frontend", "000123", "000012", "000001"))
        self.assertEqual(span[7:], (1.5, None))
        rows[0]["duration"] = -1
        with self.assertRaises(ValueError):
            list(importer.normalize_traces(Table(rows), "IC-001"))

    def test_absent_log_severity_does_not_become_error(self):
        rows = [{"timestamp": 1700000000, "container_name": "frontend", "message": "request failed"}]
        log = list(importer.normalize_logs(Table(rows), "IC-001"))[0]
        self.assertEqual(log[4:], (None, "request failed"))

    def test_selection_requires_all_signals_and_distinct_services(self):
        def row(case, service, count, has_logs=True):
            return dict(case=case, root_cause_service=service, n_logs=count, n_traces=count, has_logs=has_logs, has_traces=True, dataset="RE2-OB")
        rows = [row("a", "service-a", 2), row("b", "service-a", 1), row("c", "service-b", 3), row("d", "service-c", 4), row("e", "service-d", 0, False)]
        self.assertEqual([r["case"] for r in importer.select_cases(rows)], ["b", "c", "d"])
        with self.assertRaises(ValueError):
            importer.select_cases(rows, 4)

    def test_budget_caps_cached_and_streamed_files_and_checks_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / "file"
            p.write_bytes(b"public")
            with self.assertRaises(ValueError):
                importer.Budget(5).fetch("unused", p)
            with self.assertRaises(ValueError):
                importer.Budget(10).fetch("unused", p, expected_hash="incorrect")
            receipt = importer.Budget(10).fetch("unused", p, expected_size=6)
            self.assertEqual(receipt["bytes"], 6)
            class Response:
                headers = {"Content-Length": "11"}
                def __enter__(self): return self
                def __exit__(self, *_): return None
            with patch("urllib.request.urlopen", return_value=Response()):
                with self.assertRaises(ValueError):
                    importer.Budget(10).fetch("unused", p.with_name("stream"))
            self.assertFalse(p.with_name("stream.part").exists())
            with self.assertRaises(ValueError):
                importer.Budget(importer.HARD_CAP + 1)


if __name__ == "__main__":
    unittest.main()
