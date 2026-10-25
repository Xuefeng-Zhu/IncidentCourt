"""Fixture-only configuration and private SQL-diagnostic boundary checks.

All credentials, JWTs, hosts, errors and HTTP responses below are synthetic.
Each parser/HTTP method invocation uses patched temporary configuration and
synthetic environment values. No network connection is opened.
"""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from incidentcourt import config
from incidentcourt.store import DataError, Zetaris


class ProjectConfigurationTests(unittest.TestCase):
    def test_json_quoted_credentials_roundtrip_special_characters_without_shell(self):
        secret = 'fixture-only:both\'"quotes\\slashes$() `literal`;=!#\t雪\nsecond-line'
        with tempfile.TemporaryDirectory() as directory, patch.object(config, 'ROOT', Path(directory)), patch.dict(os.environ, {}, clear=True):
            path = Path(directory) / '.env.local'
            path.write_text('ZETARIS_PASSWORD=' + json.dumps(secret, ensure_ascii=False) + '\n'
                            'ZETARIS_API_KEY=' + json.dumps('fixture=token#with!punctuation') + '\n'
                            "LITERAL_VALUE='$literal\\path\"quoted'\n")
            config.load_env()
            self.assertEqual(os.environ['ZETARIS_PASSWORD'], secret)
            self.assertEqual(os.environ['ZETARIS_API_KEY'], 'fixture=token#with!punctuation')
            self.assertEqual(os.environ['LITERAL_VALUE'], '$literal\\path"quoted')

    def test_existing_environment_takes_precedence_and_file_values_fill_missing_keys(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(config, 'ROOT', Path(directory)), patch.dict(os.environ, {'ZETARIS_PASSWORD': 'fixture-caller-value'}, clear=True):
            (Path(directory) / '.env.local').write_text('ZETARIS_PASSWORD="fixture-file-value"\nNEW_SETTING="from-file"\n')
            config.load_env()
            self.assertEqual(os.environ['ZETARIS_PASSWORD'], 'fixture-caller-value')
            self.assertEqual(os.environ['NEW_SETTING'], 'from-file')

    def test_malformed_json_quote_raises_only_sanitized_error_and_prints_nothing(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(config, 'ROOT', Path(directory)), patch.dict(os.environ, {}, clear=True):
            bad_value = 'fixture-secret-never-echoed'
            (Path(directory) / '.env.local').write_text('ZETARIS_PASSWORD="' + bad_value + '\n')
            stdout, stderr = io.StringIO(), io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr), self.assertRaises(ValueError) as error:
                config.load_env()
            self.assertEqual(str(error.exception), 'Invalid quoted project configuration value.')
            self.assertNotIn(bad_value, str(error.exception))
            self.assertNotIn('ZETARIS_PASSWORD', os.environ)
            self.assertEqual(stdout.getvalue(), '')
            self.assertEqual(stderr.getvalue(), '')


class SQLDiagnosticTests(unittest.TestCase):
    token = 'fixture-known-api-token-123456'
    password = 'fixture-known-password-987654'
    jwt = 'eyJhbGciOiJub25lIn0.eyJzdWIiOiJmaXh0dXJlIn0.c2lnbmF0dXJldGVzdA'
    header_secret = 'fixture-authorization-header-only'
    cookie_secret = 'fixture-cookie-session-only'

    def environment(self, **changes):
        return {'ZETARIS_BASE_URL': 'https://fixture.invalid', 'ZETARIS_ORG_ID': '1',
                'ZETARIS_API_KEY': self.token, 'ZETARIS_PASSWORD': self.password, **changes}

    @staticmethod
    def fixture_error(body, code=400):
        encoded = json.dumps(body).encode() if isinstance(body, (dict, list)) else body
        return HTTPError('https://fixture.invalid/fixture-path', code, 'Fixture-only response',
                         {'Authorization': 'Bearer fixture-http-error-header'}, io.BytesIO(encoded))

    def test_sql_http400_retains_useful_message_and_redacts_credentials_and_auth_headers(self):
        body = {'message': 'Unsupported filestore connector option near PATH. ' + self.password,
                'details': {'reason': 'Fixture syntax diagnostic ' + self.token,
                            'trace': 'Bearer ' + self.header_secret, 'jwt': self.jwt,
                            'apiKey': self.token, 'credentials': {'password': self.password}},
                'headers': {'Authorization': 'Bearer ' + self.header_secret, 'Cookie': self.cookie_secret},
                'aws_reference': 'AKIAABCDEFGHIJKLMNOP'}
        with tempfile.TemporaryDirectory() as directory, patch('incidentcourt.store.ROOT', Path(directory)), patch.dict(os.environ, self.environment(), clear=True):
            client = Zetaris()
            client.opener = Mock()
            client.opener.open.side_effect = self.fixture_error(body)
            with self.assertRaises(DataError) as error:
                client.query('SELECT 1 AS connected')
            self.assertIn('HTTP 400', str(error.exception))
            self.assertIn('sanitized diagnostic', str(error.exception))
            files = list((Path(directory) / 'artifacts/private/zetaris-errors').glob('*.json'))
            self.assertEqual(len(files), 1)
            private_text = files[0].read_text()
            diagnostic = json.loads(private_text)
            self.assertEqual(diagnostic['http_status'], 400)
            self.assertIn('Unsupported filestore connector option near PATH', private_text)
            self.assertIn('Fixture syntax diagnostic', private_text)
            for secret in (self.token, self.password, self.jwt, self.header_secret, self.cookie_secret,
                           'fixture-http-error-header', 'AKIAABCDEFGHIJKLMNOP'):
                with self.subTest(secret_kind=secret[:12]):
                    self.assertNotIn(secret, private_text)
                    self.assertNotIn(secret, str(error.exception))
            self.assertEqual(stat.S_IMODE(files[0].stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(files[0].parent.stat().st_mode), 0o700)
            self.assertNotIn('Unsupported filestore', str(error.exception))

    def test_login_failure_never_saves_username_password_payload_or_response(self):
        with tempfile.TemporaryDirectory() as directory, patch('incidentcourt.store.ROOT', Path(directory)), patch.dict(os.environ, self.environment(ZETARIS_API_KEY='', ZETARIS_USERNAME='fixture-private-user'), clear=True):
            client = Zetaris()
            client.opener = Mock()
            client.opener.open.side_effect = self.fixture_error({'message': 'Bad login fixture-private-user ' + self.password,
                                                                'token': self.token}, 401)
            with self.assertRaises(DataError) as error:
                client.query('SELECT 1 AS connected')
            self.assertIn('HTTP 401', str(error.exception))
            self.assertNotIn(self.password, str(error.exception))
            self.assertNotIn('fixture-private-user', str(error.exception))
            self.assertEqual(list(Path(directory).rglob('*.json')), [])
            request = client.opener.open.call_args.args[0]
            self.assertEqual(request.full_url, 'https://fixture.invalid/api/auth/login')

    def test_other_error_paths_and_nonjson_sql_errors_do_not_save_credential_bodies(self):
        for path, body in [('/api/proxy/not-a-sql-query', {'message': self.password, 'token': self.token}),
                           ('/api/proxy/sql-editor/sqls/run-query', ('raw-error ' + self.password).encode())]:
            with self.subTest(path=path), tempfile.TemporaryDirectory() as directory, patch('incidentcourt.store.ROOT', Path(directory)), patch.dict(os.environ, self.environment(), clear=True):
                client = Zetaris()
                client.opener = Mock()
                client.opener.open.side_effect = self.fixture_error(body)
                with self.assertRaises(DataError) as error:
                    client.request(path, {'fixture': 'read-only diagnostic request'})
                self.assertNotIn(self.token, str(error.exception))
                self.assertNotIn(self.password, str(error.exception))
                self.assertEqual(list(Path(directory).rglob('*.json')), [])


if __name__ == '__main__':
    unittest.main()
