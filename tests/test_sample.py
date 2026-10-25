"""Offline sample tests using committed real sampled telemetry, never answer keys."""
from contextlib import closing
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from incidentcourt.sample import ROOT, SampleError, ensure_sample_db
from incidentcourt.store import Store


class SampleTests(unittest.TestCase):
    def test_fresh_restore_is_offline_queryable_and_keeps_missing_values(self):
        manifest=json.loads((ROOT/'data/public/sample-manifest.json').read_text())
        with tempfile.TemporaryDirectory() as directory, patch('urllib.request.urlopen',side_effect=AssertionError('Network forbidden')):
            path=Path(directory)/'sample.sqlite3'
            self.assertEqual(ensure_sample_db(path),path.resolve())
            size=path.stat().st_size
            self.assertGreater(size,0)
            # Restoring again is idempotent and does not rewrite an existing matching sample.
            modified=path.stat().st_mtime_ns
            ensure_sample_db(path)
            self.assertEqual(path.stat().st_mtime_ns,modified)
            store=Store(path=path,mode='local')
            self.assertEqual([r['id'] for r in store.incidents()],['IC-001','IC-002','IC-003'])
            with closing(sqlite3.connect(path)) as con:
                self.assertEqual(con.execute('PRAGMA integrity_check').fetchone()[0],'ok')
                self.assertEqual(con.execute('PRAGMA foreign_key_check').fetchall(),[])
                self.assertGreater(con.execute('SELECT COUNT(*) FROM metrics WHERE value IS NULL').fetchone()[0],0)
                self.assertEqual(con.execute('SELECT COUNT(*) FROM logs WHERE severity IS NOT NULL').fetchone()[0],0)
                for incident in manifest['incidents']:
                    for table in ('metrics','logs','traces'):
                        row=con.execute(f'SELECT COUNT(*),MIN(ts),MAX(ts) FROM {table} WHERE incident_id=?',(incident['id'],)).fetchone()
                        self.assertEqual(row,(incident['rows'][table],incident['coverage'][table]['first_ts'],incident['coverage'][table]['last_ts']))
                self.assertEqual(con.execute('SELECT fixture_sha256 FROM sample_metadata').fetchone()[0],manifest['sha256'])
                self.assertEqual(len(con.execute("SELECT name FROM sqlite_master WHERE type='index' AND name IN ('metric_window','log_window','trace_window','trace_link','trace_parent')").fetchall()),5)

    def test_fixture_is_bounded_and_has_only_public_telemetry_fields(self):
        compressed=(ROOT/'data/sample.json.gz').read_bytes()
        manifest=json.loads((ROOT/'data/public/sample-manifest.json').read_text())
        self.assertLess(len(compressed),1_000_000)
        self.assertEqual(hashlib.sha256(compressed).hexdigest(),manifest['sha256'])
        raw=gzip.decompress(compressed)
        self.assertEqual(len(raw),manifest['expanded_bytes'])
        self.assertNotIn(b'root_cause_service',raw)
        self.assertNotIn(b'inject_time',raw)
        self.assertNotIn(b're2ob_',raw)
        fixture=json.loads(raw)
        self.assertEqual(set(fixture['tables']),{'incidents','metrics','logs','traces'})
        for incident in manifest['incidents']:
            self.assertLessEqual(incident['rows']['logs'],500)
            self.assertLessEqual(incident['rows']['traces'],5000)

    def test_unmarked_database_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'existing.sqlite3'
            with closing(sqlite3.connect(path)) as con:
                con.execute('CREATE TABLE existing(value)');con.commit()
            original=path.read_bytes()
            with self.assertRaises(SampleError): ensure_sample_db(path)
            self.assertEqual(path.read_bytes(),original)

    def test_corrupt_fixture_fails_before_database_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'data/public').mkdir(parents=True)
            manifest=json.loads((ROOT/'data/public/sample-manifest.json').read_text())
            (root/'data/public/sample-manifest.json').write_text(json.dumps(manifest))
            (root/'data/sample.json.gz').write_bytes(b'corrupt fixture')
            path=root/'fresh.sqlite3'
            with patch('incidentcourt.sample.ROOT',root),self.assertRaises(SampleError):
                ensure_sample_db(path)
            self.assertFalse(path.exists())


if __name__=='__main__': unittest.main()
