"""Focused Stage A regressions for identity, evidence, and endpoint policy."""

from __future__ import annotations

import json
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from paperrevamper import llm  # noqa: E402
from paperrevamper.llm import _validate_endpoint  # noqa: E402
from paperrevamper.models import Block, BlockKind, DocumentIR, Finding, IssueType, Severity  # noqa: E402
from paperrevamper.panel import PanelHandler  # noqa: E402
from paperrevamper import panel as panel_module  # noqa: E402
from paperrevamper.panel_state import append_decision, build_state  # noqa: E402
from paperrevamper.reporting import gate_finding  # noqa: E402
from paperrevamper.runner import _MAX_ARTIFACT_BYTES, _read_artifacts  # noqa: E402


def main() -> None:
    first = Finding(
        id="F001",
        issue_type=IssueType.CLARITY,
        severity=Severity.MINOR,
        confidence=0.8,
        block_ids=["p_1"],
        verbatim_quote="A clear sentence.",
        checklist_id="academic/clarity",
    )
    second = Finding(
        id="F999",
        issue_type=IssueType.CLARITY,
        severity=Severity.MINOR,
        confidence=0.8,
        block_ids=["p_1"],
        verbatim_quote="A  clear   sentence.",
        checklist_id="academic/clarity",
    )
    assert first.uid == second.uid and first.uid.startswith("fd_")

    document = DocumentIR(
        "smoke",
        "paper.docx",
        "hash-new",
        blocks=[
            Block("p_1", BlockKind.PARAGRAPH, "sample size was 10"),
            Block("p_2", BlockKind.PARAGRAPH, "A clear sentence."),
        ],
    )
    mixed_blocks = Finding(
        id="F002",
        issue_type=IssueType.CLARITY,
        severity=Severity.MINOR,
        confidence=0.8,
        block_ids=["p_1", "missing"],
        verbatim_quote="sample size was 10",
    )
    assert not gate_finding(document, mixed_blocks).gate_passed
    fabricated = Finding(
        id="F003",
        issue_type=IssueType.NUMERIC_INCONSISTENCY,
        severity=Severity.MAJOR,
        confidence=0.8,
        block_ids=["p_1"],
        verbatim_quote="sample size was10",
    )
    assert not gate_finding(document, fabricated).gate_passed

    with tempfile.TemporaryDirectory(prefix="paperrevamper-stage-a-") as temp:
        run = Path(temp)
        (run / "manifest.json").write_text(json.dumps({"hash": "hash-new"}), encoding="utf-8")
        (run / "findings.json").write_text(
            json.dumps({"confirmed": [first.to_dict()], "rejected": []}), encoding="utf-8"
        )
        append_decision(run, first.id, "accept", "current", "hash-new", finding_uid=first.uid)
        append_decision(run, first.id, "reject", "stale", "hash-old", finding_uid=first.uid)
        state = build_state(run)
        assert state["decisions"][first.uid]["decision"] == "accept"
        oversized = run / "oversized.md"
        oversized.write_text("x" * (_MAX_ARTIFACT_BYTES + 100), encoding="utf-8")
        context_meta: dict[str, object] = {}
        _read_artifacts(run, ["oversized.md"], metadata=context_meta)
        assert context_meta["truncated"] is True
        assert "oversized.md" in context_meta["truncated_files"]

    try:
        _validate_endpoint("http://169.254.169.254")
    except ValueError:
        pass
    else:
        raise AssertionError("link-local endpoint must be rejected")
    with patch.object(llm.socket, "getaddrinfo", return_value=[(None, None, None, None, ("93.184.216.34", 80))]):
        try:
            _validate_endpoint("http://example.com")
        except ValueError:
            pass
        else:
            raise AssertionError("public HTTP endpoint must be rejected")
    _validate_endpoint("http://127.0.0.1:8000")
    _assert_provider_token_fields()
    _assert_redirect_is_blocked()
    _assert_remote_resume_revalidates_endpoint()
    print("stage A regression smoke checks passed")


def _assert_provider_token_fields() -> None:
    calls: list[tuple[str, dict]] = []
    original = llm._request

    def fake_request(url, headers, payload, timeout):
        calls.append((url, payload))
        if "generateContent" in url:
            return {"candidates": [{"content": {"parts": [{"text": "OK"}]}}]}
        if "/v1/messages" in url:
            return {"content": [{"type": "text", "text": "OK"}]}
        return {"choices": [{"message": {"content": "OK"}}]}

    llm._request = fake_request
    try:
        base = {"api_key": "k", "model": "m", "max_output_tokens": 1234}
        llm.chat({**base, "protocol": "openai_compatible", "base_url": "http://127.0.0.1", "output_token_field": "auto"}, [{"role": "user", "content": "x"}])
        assert calls[-1][1]["max_tokens"] == 1234
        llm.chat({**base, "protocol": "openai_compatible", "base_url": "http://127.0.0.1", "output_token_field": "max_completion_tokens"}, [{"role": "user", "content": "x"}])
        assert calls[-1][1]["max_completion_tokens"] == 1234
        llm.chat({**base, "protocol": "openai_compatible", "base_url": "http://127.0.0.1", "output_token_field": "none"}, [{"role": "user", "content": "x"}])
        assert "max_tokens" not in calls[-1][1] and "max_completion_tokens" not in calls[-1][1]
        llm.chat({**base, "protocol": "gemini", "base_url": "http://127.0.0.1", "output_token_field": "auto"}, [{"role": "user", "content": "x"}])
        assert calls[-1][1]["generationConfig"]["maxOutputTokens"] == 1234
        llm.chat({**base, "protocol": "anthropic", "base_url": "http://127.0.0.1", "output_token_field": "none"}, [{"role": "user", "content": "x"}])
        assert calls[-1][1]["max_tokens"] == 1234
    finally:
        llm._request = original


def _assert_redirect_is_blocked() -> None:
    target_hits = 0

    class RedirectHandler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            nonlocal target_hits
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/target")
                self.end_headers()
                return
            target_hits += 1
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok": true}')

        def log_message(self, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        try:
            llm._request(f"http://127.0.0.1:{server.server_port}/redirect", {}, {}, 2)
        except RuntimeError as exc:
            assert "HTTP 302" in str(exc)
        else:
            raise AssertionError("provider redirect must not be followed")
        assert target_hits == 0
    finally:
        server.shutdown()
        thread.join(timeout=2)


def _assert_remote_resume_revalidates_endpoint() -> None:
    with tempfile.TemporaryDirectory(prefix="paperrevamper-resume-policy-") as temp:
        config_path = Path(temp) / "config.json"
        config_path.write_text(
            json.dumps({"active_profile": "custom", "profiles": [{"id": "custom", "protocol": "openai_compatible", "base_url": "http://127.0.0.1", "model": "m", "enabled": True}]}),
            encoding="utf-8",
        )

        class FakeJobs:
            def get(self, job_id):
                return {"job_id": job_id, "kind": "review", "run_dir": str(Path(temp) / "run"), "payload": {"profile_id": "custom"}}

        fake_server = SimpleNamespace(
            config_path=config_path,
            safe_run=lambda value: Path(value),
            validate_endpoint=lambda value: _validate_endpoint(value, allow_loopback=False),
        )
        handler = object.__new__(PanelHandler)
        handler.server = fake_server
        with patch.object(panel_module, "JOBS", FakeJobs()):
            try:
                handler._resume_job("job")
            except ValueError as exc:
                assert "loopback" in str(exc)
            else:
                raise AssertionError("remote resume must revalidate provider endpoint")


if __name__ == "__main__":
    main()
