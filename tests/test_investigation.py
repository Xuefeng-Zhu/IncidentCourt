"""Declared synthetic behavior tests, not RCAEval fault-localization accuracy.

A frontend latency symptom competes with a downstream checkout CPU increase.
No injection metadata or real answer labels are supplied to the investigation.
"""
import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from incidentcourt.integrations import HMemStore
from incidentcourt.investigate import investigate
from incidentcourt.store import Store, DataError

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("synthetic_schema_source", ROOT / "scripts/import_rcaeval.py")
importer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(importer)

class SyntheticInvestigationTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.path = self.root / "telemetry.sqlite3"
        self.memory = HMemStore(self.root / "memory")
        self.receipt_root = patch("incidentcourt.investigate.ROOT", self.root)
        self.receipt_root.start()
        self.addCleanup(self.receipt_root.stop)
        with sqlite3.connect(self.path) as con:
            con.executescript(importer.SCHEMA)
            con.execute("INSERT INTO incidents VALUES (?,?,?,?,?,?)", ("IC-001", "Declared synthetic test", "synthetic unit-test fixture", 0, 90, "development"))
            metrics, spans = [], []
            for index, ts in enumerate([1, 2, 3, 4, 5, 61, 62, 63, 64, 65]):
                late = ts >= 60
                metrics += [
                    (f"m-front-{index}", "IC-001", ts, "frontend", "latency", 100 if late else 1),
                    (f"m-cpu-{index}", "IC-001", ts, "checkout", "cpu", 30 if late else 10),
                    (f"m-memory-{index}", "IC-001", ts, "checkout", "memory", 20),
                    (f"m-reserve-{index}", "IC-001", ts, "reserve", "queue_depth", 1)]
                spans += [
                    (f"t-front-{index}", "IC-001", ts, "frontend", f"parent-{index}", None, f"trace-{index}", 2000 if late else 100, 0),
                    (f"t-checkout-{index}", "IC-001", ts+.01, "checkout", f"child-{index}", f"parent-{index}", f"trace-{index}", 300 if late else 20, 0)]
            con.executemany("INSERT INTO metrics VALUES (?,?,?,?,?,?)", metrics)
            con.executemany("INSERT INTO traces VALUES (?,?,?,?,?,?,?,?,?)", spans)
            con.executemany("INSERT INTO logs VALUES (?,?,?,?,?,?)", [
                ("l-front", "IC-001", 62, "frontend", None, "request timeout waiting for checkout"),
                ("l-checkout", "IC-001", 61, "checkout", None, "resource unavailable: worker queue failure"),
                ("l-reserve", "IC-001", 64, "reserve", None, "an earlier timeout counter was reset")])
    def tearDown(self):
        self.directory.cleanup()
    def run_investigation(self, **kwargs):
        return investigate("IC-001", store=Store(path=self.path, mode="local"), memory=self.memory, **kwargs)

    def test_critique_drives_dependency_queries_and_revises_symptom_hypothesis(self):
        report = self.run_investigation()
        self.assertEqual(report["initial_service"], "frontend")
        self.assertEqual(report["likely_service"], "checkout")
        steps = report["steps"]
        critic = next(i for i, s in enumerate(steps) if s["action"] == "Request independent signals")
        dependencies = next(i for i, s in enumerate(steps) if s["action"] == "Inspect dependencies")
        revised = next(i for i, s in enumerate(steps) if s["action"] == "Revise hypothesis")
        self.assertLess(critic, dependencies)
        self.assertLess(dependencies, revised)
        self.assertIn("Critic follow-up: inspect observed parent/child dependencies", [q["purpose"] for q in report["queries"]])
        graph = next(e for e in report["evidence"] if e["service"] == "service graph")
        self.assertEqual(graph["rows"][0]["caller"], "frontend")
        self.assertEqual(graph["rows"][0]["callee"], "checkout")
        self.assertTrue(report["counterevidence_ids"])
        self.assertTrue(report["alternatives"])
        self.assertIn("not a proven root cause", steps[revised]["detail"])

    def test_real_hmem_recall_adds_a_current_evidence_query(self):
        cold = self.run_investigation()
        self.assertFalse(any("reserve" in q["purpose"] for q in cold["queries"]))
        self.memory.remember_verified("IC-090", "resource latency incident baseline dependency frontend latency checkout cpu reserve queue_depth: Earlier reserve observations require fresh log checks.", ["historical-metric-1"], ["checkout", "reserve"], "synthetic earlier-evidence fixture", split="history")
        warm = self.run_investigation()
        self.assertTrue(any(m["memory"]["missionId"] == "IC-090" for m in warm["memory"]["matches"]))
        added = [q for q in warm["queries"] if q["purpose"] == "Memory suggestion rechecked: reserve"]
        self.assertEqual(len(added), 1)
        self.assertIn("incident_id='IC-001'", added[0]["sql"])
        self.assertIn("service='reserve'", added[0]["sql"])
        self.assertEqual(added[0]["row_count"], 1)
        self.assertTrue(any(s["action"] == "Recheck retrieved memory" for s in warm["steps"]))
        self.assertEqual(warm["likely_service"], cold["likely_service"])
        self.assertEqual(warm["memory"]["trace"]["engine"], "Meterless H-MEM reference")

    def test_current_and_holdout_never_recalled_even_when_matching_exactly(self):
        query = "resource latency incident baseline dependency frontend latency checkout cpu"
        self.memory.remember_verified("IC-001", query, ["m-front-0"], ["reserve"], "synthetic fixture")
        prior = self.memory.remember_verified("IC-098", query, ["metric-history"], ["reserve"], "synthetic fixture")
        # Simulate a legacy store containing holdout data; current writes reject it.
        state_file = self.root / "memory" / "state.json"
        state = json.loads(state_file.read_text())
        for record in state["records"]:
            if record["id"] == prior["id"]:
                record["tags"] = ["split:holdout" if t == "split:development" else t for t in record["tags"]]
        state_file.write_text(json.dumps(state))
        report = self.run_investigation()
        self.assertEqual(report["memory"]["matches"], [])
        self.assertFalse(any(q["purpose"].startswith("Memory suggestion") for q in report["queries"]))
        with sqlite3.connect(self.path) as con: con.execute("UPDATE incidents SET split='holdout'")
        before = self.memory.status()["memories"]
        self.run_investigation()
        self.assertEqual(self.memory.status()["memories"], before)

    def test_every_evidence_citation_resolves_to_a_real_query_receipt(self):
        report = self.run_investigation()
        query_ids = {q["id"] for q in report["queries"]}
        evidence_ids = {e["id"] for e in report["evidence"]}
        self.assertEqual(len(evidence_ids), len(report["evidence"]))
        for evidence in report["evidence"]: self.assertIn(evidence["query_id"], query_ids)
        for item in report["steps"] + report["timeline"] + report["alternatives"]:
            self.assertTrue(set(item["evidence_ids"]).issubset(evidence_ids))
        self.assertTrue(set(report["counterevidence_ids"]).issubset(evidence_ids))
        self.assertTrue(all(q["backend"] == "local" for q in report["queries"]))
        self.assertTrue(all(q["sql"].lstrip().startswith("SELECT ") for q in report["queries"]))
        self.assertEqual(report["model"]["mode"], "local")

    def test_absent_telemetry_is_uncertain(self):
        with sqlite3.connect(self.path) as con:
            con.execute("DELETE FROM metrics"); con.execute("DELETE FROM traces"); con.execute("DELETE FROM logs")
        report = self.run_investigation()
        self.assertIsNone(report["likely_service"])
        self.assertEqual(report["confidence"], "limited")
        self.assertTrue(any("Insufficient non-null" in l for l in report["limitations"]))
        self.assertTrue(any("No traces" in l for l in report["limitations"]))
        self.assertIn("does not support", report["summary"])
        self.assertEqual(self.memory.status()["memories"], 0)

    def test_missing_logs_are_visible_and_do_not_become_a_health_claim(self):
        with sqlite3.connect(self.path) as con: con.execute("DELETE FROM logs")
        report = self.run_investigation()
        log_evidence = [e for e in report["evidence"] if e["kind"] == "logs" and e["service"] != "coverage"]
        self.assertTrue(log_evidence)
        self.assertTrue(all(e["rows"] == [] for e in log_evidence))
        self.assertTrue(all("does not prove" in e["summary"] for e in log_evidence))
        self.assertTrue(any("No logs observations" in l for l in report["limitations"]))
        self.assertEqual(next(c for c in report["coverage"] if c["kind"] == "logs")["observations"], 0)

    def test_unapproved_tables_are_not_accessible_to_the_query_layer(self):
        with sqlite3.connect(self.path) as con:
            con.execute("CREATE TABLE private_answers(value TEXT)")
            con.execute("INSERT INTO private_answers VALUES ('not agent input')")
        with self.assertRaises(DataError): Store(path=self.path, mode="local").query("Disallowed source", "SELECT value FROM private_answers")

    def test_provider_answer_with_unknown_citation_is_rejected(self):
        class FixtureProvider:
            def complete(self, messages, **kwargs):
                prompt = json.loads(messages[-1]["content"])
                if "services" in prompt:
                    return {"content": json.dumps({"service":prompt["services"][0], "reason":"Declared controller fixture", "evidence_ids":[prompt["evidence"][0]["id"]]})}
                return {"content": json.dumps({"summary":"Unsupported diagnosis", "evidence_ids":["E999"]})}
        with patch("incidentcourt.investigate.NightshiftClient.from_env", return_value=FixtureProvider()):
            with self.assertRaisesRegex(DataError, "unsupported citations"): self.run_investigation(model="nightshift")
        self.assertEqual(self.memory.status()["memories"], 0)

if __name__ == "__main__": unittest.main()
