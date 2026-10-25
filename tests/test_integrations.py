"""Contract and real local H-MEM tests; HTTP fixtures do not prove live Nightshift."""
import json
import tempfile
import threading
import unittest
import os
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from incidentcourt.integrations import HMemStore, IntegrationError, NightshiftClient, normalize_nightshift_url

class MemoryTest(unittest.TestCase):
    def test_real_engine_persistence_recall_exclusion_and_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            memory = HMemStore(directory)
            finding = "Redis latency and server errors require trace checks before a database diagnosis."
            a = memory.remember_verified("INC-001", finding, ["trace-1", "metric-2"], ["redis"], "reviewed-evidence")
            result = HMemStore(directory).retrieve(finding, "INC-002")
            self.assertEqual(result["memories"][0]["memory"]["id"], a["id"])
            self.assertIn(finding, result["context"])
            self.assertEqual(result["trace"]["engine"], "Meterless H-MEM reference")
            self.assertEqual(memory.retrieve(finding, "INC-001")["memories"], [])
            self.assertEqual(memory.status()["ledger"], {"create": 1, "read": 1})
            self.assertEqual(memory.audit()[0]["newState"]["provenance"]["refs"], ["INC-001:trace-1", "INC-001:metric-2"])
    def test_holdout_and_unverified_findings_cannot_enter_memory(self):
        with tempfile.TemporaryDirectory() as directory:
            memory = HMemStore(directory)
            with self.assertRaises(ValueError):
                memory.remember_verified("INC-099", "An unsupported finding", ["trace-1"], [], "reviewer", split="holdout")
            with self.assertRaises(ValueError):
                memory.remember_verified("INC-001", "An unsupported finding", [], [], "reviewer")
            with self.assertRaises(ValueError):
                memory.remember_verified("INC-001", "An unsupported finding", ["trace-1"], [], "")
    def test_explicit_service_scope_is_applied_before_official_ranker(self):
        with tempfile.TemporaryDirectory() as directory:
            memory = HMemStore(directory)
            finding = "resource latency incident baseline dependency checkout cpu mean increased"
            memory.remember_verified("IC-001", finding, ["metric-1"], ["checkout"], "reviewer")
            excluded = memory.retrieve(finding, "IC-002", services=("currency",))
            self.assertEqual(excluded["memories"], [])
            self.assertEqual(excluded["trace"]["eligibleCount"], 0)
            recalled = memory.retrieve(finding, "IC-002", services=("checkout",))
            self.assertEqual(len(recalled["memories"]), 1)
            self.assertEqual(recalled["trace"]["eligibleServiceScope"], ["checkout"])

    def test_duplicate_findings_are_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            memory = HMemStore(directory)
            for _ in range(2):
                memory.remember_verified("INC-001", "Redis service latency changed.", ["metric-1"], ["redis"], "reviewer")
            self.assertEqual(memory.status()["memories"], 1)

class RuntimeHandler(BaseHTTPRequestHandler):
    requests = []
    auth_code = 200
    def log_message(self, *args): pass
    def do_GET(self):
        type(self).requests.append({"path": self.path, "headers": dict(self.headers)})
        self.send_response(type(self).auth_code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"data": [{"id": "meterless/auto"}, {"id": "meterless/future-route"}, {"id": "other"}]}).encode())
    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).requests.append({"path":self.path,"headers":dict(self.headers),"payload":payload})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"model":"fixture-only", "choices":[{"message":{"content":"Fixture diagnosis."}}],"usage":{"total_tokens":5}, "meterless":{"schema":"meterless.compute_receipt.v1", "routed_model":"fixture-only", "winning_route":{"model":"fixture-only", "provider":"fixture", "secret_key":"must-not-be-returned"}, "from_cache":False, "unrelated_secret":"must-not-be-returned"}}).encode())

class ProviderContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), RuntimeHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join()
    def setUp(self):
        RuntimeHandler.requests = []; RuntimeHandler.auth_code = 200
        self.receipts = tempfile.TemporaryDirectory()
        self.addCleanup(self.receipts.cleanup)
        self.client = NightshiftClient(f"http://127.0.0.1:{self.server.server_port}", "fixture-token", receipt_directory=self.receipts.name)
    def test_discovery_uses_contract_and_keeps_unknown_meterless_modes(self):
        result = self.client.probe()
        self.assertEqual(result["state"], "online")
        self.assertEqual(result["models"], ["meterless/auto", "meterless/future-route"])
        self.assertFalse(result["live_inference_verified"])
        request = RuntimeHandler.requests[-1]
        self.assertEqual(request["path"], "/v1/models")
        self.assertEqual(request["headers"]["Authorization"], "Bearer fixture-token")
        self.assertEqual(request["headers"]["Meterless-App"], "incidentcourt")
    def test_unauthorized_is_distinct_from_offline(self):
        RuntimeHandler.auth_code = 401
        self.assertEqual(self.client.probe()["state"], "unauthorized")
    def test_no_inference_without_optin(self):
        with self.assertRaises(IntegrationError):
            self.client.complete([{"role":"user","content":"Investigate."}])
        self.assertEqual(RuntimeHandler.requests, [])
    def test_plain_text_contract_with_fixture_not_live_provider(self):
        self.client.allow_inference = True
        result = self.client.complete([{"role":"user","content":"Investigate."}])
        self.assertEqual(result["content"], "Fixture diagnosis.")
        self.assertEqual(result["served_model"], "fixture-only")
        self.assertEqual(result["runtime_receipt"]["schema"], "meterless.compute_receipt.v1")
        self.assertEqual(result["runtime_receipt"]["winning_route"], {"model":"fixture-only", "provider":"fixture"})
        self.assertNotIn("unrelated_secret", result["runtime_receipt"])
        self.assertFalse(result["runtime_receipt"]["from_cache"])
        request = RuntimeHandler.requests[-1]
        self.assertEqual(request["headers"]["Meterless-Scope"], "user")
        self.assertEqual(request["payload"]["model"], "meterless/auto")
        self.assertEqual(request["payload"]["messages"][0]["content"], "Investigate.")
    def test_private_call_receipts_link_output_and_exclude_auth(self):
        self.client.allow_inference = True
        self.client.output_token_limit = 100
        result = self.client.complete([{"role":"user","content":"Bounded public fixture"}], max_tokens=600)
        receipt_path = Path(result["call_receipt"])
        receipt = json.loads(receipt_path.read_text())
        self.assertEqual(receipt["call_id"], result["call_id"])
        self.assertEqual(receipt["response"]["content"], result["content"])
        self.assertEqual(receipt["request"]["max_tokens"], 100)
        self.assertEqual(receipt_path.stat().st_mode & 0o777, 0o600)
        self.assertNotIn("fixture-token", receipt_path.read_text())
        self.assertNotIn("Authorization", receipt_path.read_text())
        self.assertNotIn("must-not-be-returned", receipt_path.read_text())
    def test_failed_call_has_immutable_sanitized_receipt(self):
        with self.assertRaises(IntegrationError) as captured:
            self.client.complete([{"role":"user","content":"Bounded public fixture"}])
        receipt = json.loads(Path(captured.exception.call_receipt).read_text())
        self.assertEqual(receipt["status"], "failed")
        self.assertFalse(receipt["dispatched"])
        self.assertNotIn("fixture-token", json.dumps(receipt))
        self.assertEqual(len(list(Path(self.receipts.name).glob("*.json"))), 1)

    def test_nvidia_thinking_false_is_sent_explicitly_through_gateway(self):
        self.client.allow_inference = True
        self.client.hosted_only = True
        self.client.enable_thinking = False
        self.client.model = "nvidia/nemotron-3.5-lightning-30b-a3b"
        response = {"model":self.client.model,"choices":[{"message":{"content":"Fixture only."}}],
                    "meterless":{"winning_route":{"model":self.client.model,"provider":"nvidia"}}}
        with patch.object(self.client, "_request", side_effect=[{"data":[{"id":self.client.model}]},response]) as request:
            result = self.client.complete([{"role":"user","content":"Public fixture JSON"}], max_tokens=600)
            body = request.call_args_list[1].args[1]
            self.assertEqual(body["chat_template_kwargs"], {"enable_thinking":False})
            self.assertEqual(body["max_tokens"], 600)
            self.assertNotIn("reasoning_budget", body)
            self.assertNotIn("response_format", body)
            receipt = json.loads(Path(result["call_receipt"]).read_text())
            self.assertIs(receipt["request"]["chat_template_kwargs"]["enable_thinking"], False)

    def test_hosted_only_refuses_automatic_routing_before_any_request(self):
        self.client.allow_inference = True
        self.client.hosted_only = True
        with patch.object(self.client, "_request") as request:
            with self.assertRaisesRegex(IntegrationError, "automatic routing is disabled"):
                self.client.complete([{"role":"user","content":"Public fixture"}])
            request.assert_not_called()

    def test_hosted_only_unknown_model_does_not_dispatch_generation(self):
        self.client.allow_inference = True
        self.client.hosted_only = True
        self.client.model = "nvidia/fixture-approved-model"
        with patch.object(self.client, "_request", return_value={"data":[{"id":"meterless/auto"}]}) as request:
            with self.assertRaisesRegex(IntegrationError, "no generation was sent") as captured:
                self.client.complete([{"role":"user","content":"Public fixture"}])
            self.assertEqual(request.call_count, 1)
            self.assertEqual(request.call_args.args, ("/models",))
            self.assertFalse(json.loads(Path(captured.exception.call_receipt).read_text())["dispatched"])

    def test_explicit_hosted_pin_uses_gateway_and_requires_exact_route_receipt(self):
        self.client.allow_inference = True
        self.client.hosted_only = True
        self.client.model = "nvidia/fixture-approved-model"
        response = {"model":self.client.model,"choices":[{"message":{"content":"Fixture only."}}],
                    "meterless":{"winning_route":{"model":self.client.model,"provider":"nvidia"},"from_cache":False}}
        with patch.object(self.client, "_request", side_effect=[{"data":[{"id":self.client.model}]},response]) as request:
            result = self.client.complete([{"role":"user","content":"Public fixture"}])
            self.assertEqual(result["served_model"], self.client.model)
            self.assertEqual(request.call_args_list[1].args[0], "/chat/completions")
            self.assertEqual(request.call_args_list[1].args[1]["model"], self.client.model)
            self.assertEqual(self.client.base_url, f"http://127.0.0.1:{self.server.server_port}/v1")

    def test_hosted_pin_rejects_local_or_other_model_response_without_retry(self):
        self.client.allow_inference = True
        self.client.hosted_only = True
        self.client.model = "nvidia/fixture-approved-model"
        for provider, model in [("webllm","webllm/fixture-local-model"),("nvidia","nvidia/fixture-other-model")]:
            with self.subTest(provider=provider):
                response = {"model":model,"choices":[{"message":{"content":"Must not be accepted."}}],
                            "meterless":{"winning_route":{"model":model,"provider":provider}}}
                with patch.object(self.client, "_request", side_effect=[{"data":[{"id":self.client.model}]},response]) as request:
                    with self.assertRaisesRegex(IntegrationError, "no result was accepted") as captured:
                        self.client.complete([{"role":"user","content":"Public fixture"}])
                    self.assertEqual(request.call_count, 2)
                    receipt = json.loads(Path(captured.exception.call_receipt).read_text())
                    self.assertEqual(receipt["status"], "failed")
                    self.assertEqual(receipt["response"]["served_model"], model)

    def test_missing_and_placeholder_auth_fail_closed(self):
        self.assertEqual(NightshiftClient(token="rt-local").probe()["state"], "not_configured")
        self.assertEqual(NightshiftClient(token="").probe()["state"], "not_configured")

class EnvironmentAliasTest(unittest.TestCase):
    def test_meterless_environment_aliases_are_supported(self):
        with patch.dict(os.environ, {"METERLESS_BASE_URL":"localhost:8090/v1/models", "METERLESS_RUNTIME_TOKEN":"fixture-alias-token", "METERLESS_MODEL":"meterless/local"}, clear=True):
            client = NightshiftClient.from_env()
            self.assertEqual(client.base_url, "http://localhost:8090/v1")
            self.assertTrue(client.configured)
            self.assertEqual(client.model, "meterless/local")
            self.assertFalse(client.allow_inference)
    def test_explicit_thinking_env_false_survives_config_loading(self):
        with patch.dict(os.environ, {"NIGHTSHIFT_MODEL":"nvidia/nemotron-3.5-lightning-30b-a3b", "NIGHTSHIFT_ENABLE_THINKING":"0", "NIGHTSHIFT_HOSTED_ONLY":"1"}, clear=True):
            client = NightshiftClient.from_env()
            self.assertIs(client.enable_thinking, False)
            self.assertTrue(client.hosted_only)
            self.assertFalse(client.allow_inference)

    def test_nightshift_configuration_takes_precedence(self):
        with patch.dict(os.environ, {"METERLESS_BASE_URL":"localhost:8090", "METERLESS_RUNTIME_TOKEN":"fixture-alias-token", "METERLESS_MODEL":"meterless/local", "NIGHTSHIFT_BASE_URL":"localhost:8088", "NIGHTSHIFT_API_KEY":"fixture-primary-token", "NIGHTSHIFT_MODEL":"meterless/deep"}, clear=True):
            client = NightshiftClient.from_env()
            self.assertEqual(client.base_url, "http://localhost:8088/v1")
            self.assertEqual(client.model, "meterless/deep")
            self.assertTrue(client.configured)

class NormalizeURLTest(unittest.TestCase):
    def test_pasted_forms(self):
        for suffix in ("", "/", "/v1", "/v1/models", "/v1/chat/completions", "/healthz"):
            self.assertEqual(normalize_nightshift_url("localhost:8088"+suffix), "http://localhost:8088/v1")
    def test_secrets_and_remote_plaintext_rejected(self):
        for value in ("https://secret@example.com", "https://example.com?key=secret", "http://example.com"):
            with self.assertRaises(ValueError): normalize_nightshift_url(value)

if __name__ == "__main__": unittest.main()
