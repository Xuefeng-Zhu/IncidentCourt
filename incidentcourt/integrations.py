"""Real Meterless H-MEM reference bridge and authenticated Nightshift adapter.

Provider requests are opt-in; no fallback, response replay, or implicit cloud spend.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import uuid
import time
from datetime import datetime, timezone
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REFERENCE_REVISION = "1742dec56a66e58eef9dda2d2b865823758ca8cd"
_ID = re.compile(r"^[A-Za-z0-9_:-]{1,120}$")
_MEMORY_LOCK = threading.RLock()
_HOSTED_PROVIDERS = frozenset({"nvidia", "openai", "anthropic", "google", "openrouter", "opencode-go", "xai"})
_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/+\-]{1,199}$")


class IntegrationError(RuntimeError):
    """Sanitized integration failure safe to display without credentials."""


def _identifier(value: str, field: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError(f"Invalid {field}")
    return value


class HMemStore:
    """Calls upstream HMEM; Python does not reimplement its retrieval algorithm.

    Separate persistent store from telemetry. Only reviewed findings from nonholdout
    incidents are eligible. Recall is historical context, never current evidence.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()

    def _run(self, action: str, payload: dict | None = None) -> Any:
        node = shutil.which("node")
        if not node:
            raise IntegrationError("H-MEM requires Node.js 24 or later")
        request = {"action": action, "directory": str(self.path), "payload": payload or {}}
        with _MEMORY_LOCK:
            try:
                result = subprocess.run(
                    [node, "--disable-warning=ExperimentalWarning", "--experimental-transform-types",
                     str(PROJECT_ROOT / "scripts/hmem_bridge.ts")],
                    input=json.dumps(request), text=True, capture_output=True, timeout=20,
                    cwd=PROJECT_ROOT, check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise IntegrationError("H-MEM reference process unavailable") from None
            if result.returncode:
                raise IntegrationError("H-MEM reference failed; check Node.js 24 and store integrity")
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                raise IntegrationError("H-MEM returned an invalid response") from None

    def remember_verified(self, incident_id: str, content: str, evidence_ids: list[str],
                          services: list[str], verifier: str, *, split: str = "development",
                          confidence: float = 0.8) -> dict:
        """Caller must verify cited evidence against that earlier incident first.

        No labels, evaluator files, original case paths, or injection metadata belong
        in content. Holdout runs cannot be promoted by this application interface.
        """
        _identifier(incident_id, "incident id")
        if split not in {"development", "history"}:
            raise ValueError("Only nonholdout investigations may enter historical memory")
        if not isinstance(content, str) or not 10 <= len(content.strip()) <= 4000:
            raise ValueError("Memory needs a bounded verified finding")
        if not isinstance(verifier, str) or not verifier.strip():
            raise ValueError("An explicit verifier is required")
        if not evidence_ids or len(evidence_ids) > 50:
            raise ValueError("Verified evidence references are required")
        if not 0 <= confidence <= 1:
            raise ValueError("Confidence must be in [0, 1]")
        for item in evidence_ids:
            _identifier(item, "evidence reference")
        for service in services:
            _identifier(service, "service")
        return self._run("remember", {"incident_id": incident_id, "content": content.strip(),
            "evidence_ids": evidence_ids, "services": services[:10], "verifier": verifier[:120],
            "split": split, "confidence": confidence})

    def retrieve(self, query: str, exclude_incident_id: str, services=(), limit: int = 5) -> dict:
        _identifier(exclude_incident_id, "excluded incident id")
        if not isinstance(query, str) or not query.strip() or len(query) > 4000:
            raise ValueError("A bounded retrieval query is required")
        if not 1 <= limit <= 5:
            raise ValueError("Memory retrieval limit must be between 1 and 5")
        for service in services:
            _identifier(service, "retrieval service")
        return self._run("retrieve", {"query": query, "exclude_incident_id": exclude_incident_id,
                                      "services": list(services)[:10], "limit": limit})

    def status(self) -> dict:
        try:
            return {"state": "working", **self._run("status")}
        except IntegrationError as exc:
            return {"state": "unavailable", "engine": "Meterless H-MEM reference", "reason": str(exc)}

    def audit(self) -> list[dict]:
        return self._run("audit")


def normalize_nightshift_url(value: str) -> str:
    """Normalize documented paste formats without accepting credentials in URLs."""
    value = value.strip()
    if not value:
        value = "http://127.0.0.1:8088/v1"
    if "://" not in value:
        value = ("http://" if value.startswith(("localhost", "127.0.0.1", "[::1]")) else "https://") + value
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Nightshift URL must be an HTTP(S) origin without embedded credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("Nightshift URL cannot contain a query or fragment")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Remote Nightshift connections require HTTPS")
    path = parsed.path.rstrip("/")
    for suffix in ("/v1/chat/completions", "/v1/models", "/healthz", "/v1"):
        if path.endswith(suffix):
            path = path[:-len(suffix)]
            break
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path + "/v1", "", ""))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class NightshiftClient:
    """Official Meterless Runtime OpenAI-compatible provider contract.

    list_models/probe are read-only discovery. complete requires explicit local
    NIGHTSHIFT_ALLOW_INFERENCE=1; routing can use user-configured cloud providers.
    """

    def __init__(self, base_url: str = "http://127.0.0.1:8088/v1", token: str = "",
                 model: str = "meterless/auto", allow_inference: bool = False,
                 output_token_limit: int | None = None, receipt_directory: str | Path | None = None,
                 hosted_only: bool = False, enable_thinking: bool | None = None):
        self.base_url = normalize_nightshift_url(base_url)
        self._token = token.strip()
        self.model = model
        self.allow_inference = allow_inference
        if output_token_limit is not None and not 1 <= output_token_limit <= 4096:
            raise ValueError("Nightshift output token limit must be between 1 and 4096")
        self.output_token_limit = output_token_limit
        self.hosted_only = hosted_only
        if enable_thinking is not None and not isinstance(enable_thinking, bool):
            raise ValueError("NVIDIA thinking control must be true or false")
        self.enable_thinking = enable_thinking
        self.receipt_directory = Path(receipt_directory) if receipt_directory is not None else PROJECT_ROOT / "artifacts/private/nightshift-calls"
        self._opener = urllib.request.build_opener(_NoRedirect)
        if not isinstance(model, str) or not _MODEL_ID.fullmatch(model) or (not model.startswith("meterless/") and model.split("/",1)[0] not in _HOSTED_PROVIDERS):
            raise ValueError("Nightshift model must be a Meterless routing id or explicit supported hosted-provider model")

    @classmethod
    def from_env(cls) -> "NightshiftClient":
        token = os.environ.get("NIGHTSHIFT_API_KEY") or os.environ.get("METERLESS_RUNTIME_TOKEN", "")
        token_file = os.environ.get("NIGHTSHIFT_TOKEN_FILE", "")
        if not token and token_file:
            try:
                token = Path(token_file).expanduser().read_text().strip()
            except OSError:
                token = ""
        return cls(os.environ.get("NIGHTSHIFT_BASE_URL") or os.environ.get("METERLESS_BASE_URL", "http://127.0.0.1:8088/v1"), token,
                   os.environ.get("NIGHTSHIFT_MODEL") or os.environ.get("METERLESS_MODEL", "meterless/auto"),
                   os.environ.get("NIGHTSHIFT_ALLOW_INFERENCE") == "1",
                   output_token_limit=int(os.environ["NIGHTSHIFT_MAX_OUTPUT_TOKENS"]) if os.environ.get("NIGHTSHIFT_MAX_OUTPUT_TOKENS") else None,
                   hosted_only=os.environ.get("NIGHTSHIFT_HOSTED_ONLY") == "1",
                   enable_thinking={"0":False,"1":True}.get(os.environ.get("NIGHTSHIFT_ENABLE_THINKING", "")))

    @property
    def configured(self) -> bool:
        return bool(self._token and self._token != "rt-local")

    def status(self, probe: bool = False) -> dict:
        result = {"provider": "nightshift", "state": "configured_unverified" if self.configured else "not_configured",
                  "requested_model": self.model, "inference_enabled": self.allow_inference,
                  "live_inference_verified": False, "hosted_only": self.hosted_only,
                  "explicit_hosted_model": self.model.split("/",1)[0] in _HOSTED_PROVIDERS,
                  "enable_thinking": self.enable_thinking}
        if probe:
            return {**result, **self.probe()}
        return result

    def _request(self, endpoint: str, payload: dict | None = None, *, timeout: float = 5) -> dict:
        if not self.configured:
            raise IntegrationError("Nightshift requires an existing runtime bearer token")
        data = json.dumps(payload).encode() if payload is not None else None
        headers = {"Authorization": "Bearer " + self._token, "meterless-app": "incidentcourt",
                   "Accept": "application/json"}
        if payload is not None:
            headers.update({"Content-Type": "application/json", "meterless-scope": "user"})
        request = urllib.request.Request(self.base_url + endpoint, data=data, headers=headers)
        try:
            with self._opener.open(request, timeout=timeout) as response:
                raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise IntegrationError("Nightshift response exceeded limit")
                return json.loads(raw)
        except urllib.error.HTTPError as exc:
            code = exc.code
            error_code = ""
            try:
                failure = json.loads(exc.read(16_000))
                candidate = failure.get("error", {}).get("code", "") if isinstance(failure, dict) else ""
                if isinstance(candidate, str) and re.fullmatch(r"[a-z0-9_]{1,80}", candidate):
                    error_code = candidate
            except (ValueError, OSError, AttributeError):
                pass
            finally:
                exc.close()
            if code in (401, 403):
                raise IntegrationError("Nightshift unauthorized") from None
            detail = f" ({error_code})" if error_code else ""
            raise IntegrationError(f"Nightshift HTTP {code}{detail}") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise IntegrationError("Nightshift offline or timed out") from None
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise IntegrationError("Nightshift returned invalid JSON") from None

    def list_models(self) -> list[str]:
        response = self._request("/models")
        rows = response.get("data", [])
        if not isinstance(rows, list):
            raise IntegrationError("Nightshift model discovery returned an invalid shape")
        return [row["id"] for row in rows if isinstance(row, dict)
                and isinstance(row.get("id"), str) and (row["id"].startswith("meterless/")
                    or row["id"].split("/",1)[0] in _HOSTED_PROVIDERS)]

    def probe(self) -> dict:
        if not self.configured:
            return {"state": "not_configured", "models": []}
        try:
            models = self.list_models()
            return {"state": "online" if models else "offline", "models": models,
                    "live_inference_verified": False}
        except IntegrationError as exc:
            return {"state": "unauthorized" if str(exc) == "Nightshift unauthorized" else "offline",
                    "models": [], "reason": str(exc), "live_inference_verified": False}

    @staticmethod
    def _response_proof(response: dict) -> dict:
        """Allowlisted model output and routing evidence, never runtime config."""
        if not isinstance(response, dict):
            return {"shape": type(response).__name__}
        source = response.get("meterless", {})
        receipt = {}
        if isinstance(source, dict):
            for key in ("schema", "receipt_id", "decision_id", "requested_model", "provider_model",
                        "routed_model", "usage", "cost", "cache", "privacy", "from_cache", "served"):
                if key in source:
                    receipt[key] = source[key]
            route = source.get("winning_route")
            if isinstance(route, dict):
                receipt["winning_route"] = {key: route[key] for key in
                    ("model", "provider", "lane", "backend") if key in route}
        try:
            content = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            content = None
        return {"content": content, "served_model": response.get("model", "unreported"),
                "usage": response.get("usage", {}), "runtime_receipt": receipt}

    def _save_call(self, call_id: str, receipt: dict) -> str:
        self.receipt_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.receipt_directory / (call_id + ".json")
        # Exclusive creation prevents overwriting an earlier failed provider call.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as target:
            json.dump(receipt, target, indent=2, allow_nan=False)
            target.write("\n")
        try:
            return str(path.relative_to(PROJECT_ROOT))
        except ValueError:
            return str(path)

    def complete(self, messages: list[dict], max_tokens: int = 1200) -> dict:
        if not 1 <= max_tokens <= 4096:
            raise ValueError("max_tokens must be between 1 and 4096")
        if not messages or len(json.dumps(messages)) > 100_000:
            raise ValueError("A bounded messages list is required")
        if self.output_token_limit is not None:
            max_tokens = min(max_tokens, self.output_token_limit)
        call_id = uuid.uuid4().hex[:20]
        request = {"model": self.model, "messages": messages, "max_tokens": max_tokens,
                   "temperature": 0.1, "stream": False}
        if self.enable_thinking is not None:
            if not self.model.startswith(("nvidia/nemotron-3-", "nvidia/nemotron-3.")):
                raise IntegrationError("Explicit thinking control requires a pinned NVIDIA Nemotron 3 or 3.5 model")
            request["chat_template_kwargs"] = {"enable_thinking": self.enable_thinking}
        call = {"call_id": call_id, "created_at": datetime.now(timezone.utc).isoformat(),
                "provider": "nightshift", "request": request, "dispatched": False}
        start = time.monotonic()
        try:
            if not self.allow_inference:
                raise IntegrationError("Nightshift inference is disabled; explicitly enable authorized runtime usage")
            if self.hosted_only:
                expected_provider = self.model.split("/",1)[0]
                if expected_provider not in _HOSTED_PROVIDERS:
                    raise IntegrationError("Hosted-only Nightshift requires an explicit hosted provider/model; automatic routing is disabled")
                if self.model not in self.list_models():
                    raise IntegrationError("The pinned hosted model is not advertised by this Nightshift runtime; no generation was sent")
            call["dispatched"] = True
            response = self._request("/chat/completions", request, timeout=180)
            proof = self._response_proof(response)
            call["response"] = proof
            content = proof.get("content")
            if self.hosted_only:
                route = proof["runtime_receipt"].get("winning_route", {})
                if route.get("provider") != expected_provider or route.get("model") != self.model or proof.get("served_model") != self.model:
                    raise IntegrationError("Nightshift response did not match the pinned hosted provider/model; no result was accepted")
            if not isinstance(content, str) or not content.strip():
                raise IntegrationError("Nightshift did not return plain text")
            call.update(status="completed", elapsed_seconds=round(time.monotonic()-start, 3))
            receipt_path = self._save_call(call_id, call)
            return {**proof, "provider": "nightshift", "requested_model": self.model,
                    "call_id": call_id, "call_receipt": receipt_path,
                    "live_inference_verified": True}
        except IntegrationError as exc:
            call.update(status="failed", elapsed_seconds=round(time.monotonic()-start, 3), error=str(exc))
            receipt_path = self._save_call(call_id, call)
            exc.call_id = call_id
            exc.call_receipt = receipt_path
            raise
