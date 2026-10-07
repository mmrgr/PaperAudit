"""Dependency-free local control panel for multi-agent PaperRevamper runs."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import re
import secrets
import sys
import webbrowser
from datetime import datetime
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from paperrevamper.collaboration import append_trace, default_workflow, load_workflow, validate_workflow
from paperrevamper.ingest import read_docx
from paperrevamper.jobs import JOBS
from paperrevamper.llm import _validate_endpoint, chat as llm_chat, load_config as load_llm_config, public_config, save_config as save_llm_config, select_profile
from paperrevamper.panel_state import append_decision, build_state, read_json
from paperrevamper.panel_runner import run_panel
from paperrevamper.prepare import prepare
from paperrevamper.protocol.adjudication import adjudicate_run
from paperrevamper.revise import apply_revision, build_revision_plan
from paperrevamper.resources import resource_path
from paperrevamper.verify import verify
from paperrevamper.runner import run_review

_SOURCE_UI_FILE = Path(__file__).resolve().parents[2] / "ui" / "control-panel.html"
_PACKAGE_UI_FILE = Path(__file__).resolve().parent / "static" / "control-panel.html"
UI_FILE = _SOURCE_UI_FILE if _SOURCE_UI_FILE.exists() else resource_path("paperrevamper", "static", "control-panel.html")


def _json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")


class PanelServer(ThreadingHTTPServer):
    def __init__(self, address, run_root: Path, auth_token: str | None = None):
        self.run_root = run_root.resolve()
        self.run_root.mkdir(parents=True, exist_ok=True)
        current_config = self.run_root.parent / "config.json"
        legacy_config = (
            self.run_root.parent.parent / "PaperAudit" / "config.json"
            if self.run_root.parent.name == "PaperRevamper"
            else None
        )
        if current_config.exists() or legacy_config is None or not legacy_config.exists():
            self.config_path = current_config
        else:
            self.config_path = legacy_config
        self.auth_token = str(auth_token or "")
        try:
            self.remote_mode = not ipaddress.ip_address(str(address[0])).is_loopback
        except ValueError:
            self.remote_mode = str(address[0]).casefold() not in {"localhost", "127.0.0.1", "::1"}
        JOBS.register_root(self.run_root)
        super().__init__(address, PanelHandler)

    def safe_run(self, raw: str) -> Path:
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = self.run_root / candidate
        path = candidate.resolve()
        if path != self.run_root and self.run_root not in path.parents:
            raise ValueError("运行目录必须位于 --run-root 内")
        if not path.exists():
            raise FileNotFoundError(path)
        return path

    def safe_output(self, raw: str) -> Path:
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = self.run_root / candidate
        path = candidate.resolve()
        if path != self.run_root and self.run_root not in path.parents:
            raise ValueError("面板输出路径必须位于 --run-root 内")
        return path

    def workflow_path(self, name: str) -> Path:
        safe = Path(name).name
        if not safe or safe in {".", ".."} or not safe.endswith(".json"):
            safe = f"{safe or 'workflow'}.json"
        path = (self.run_root / "workflows" / safe).resolve()
        if self.run_root not in path.parents:
            raise ValueError("workflow 必须保存于 --run-root 内")
        return path

    def validate_endpoint(self, value: str) -> None:
        _validate_endpoint(value, allow_loopback=not self.remote_mode)


class PanelHandler(BaseHTTPRequestHandler):
    server: PanelServer

    def log_message(self, fmt: str, *args) -> None:  # noqa: A002
        message = fmt % args
        message = re.sub(r"([?&]token=)[^&\s]+", r"\1<redacted>", message, flags=re.IGNORECASE)
        print(f"[paperrevamper-panel] {message}")

    def _send(self, status: int, body: bytes, content_type: str = "application/json; charset=utf-8", *, extra_headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, message: str) -> None:
        self._send(status, _json({"status": "error", "message": message}))

    def _authorized(self) -> bool:
        expected = self.server.auth_token
        if not expected:
            return True
        parsed = urlparse(self.path)
        supplied = self.headers.get("X-PaperRevamper-Token", "")
        if not supplied:
            supplied = self.headers.get("X-PaperAudit-Token", "")
        if not supplied:
            supplied = parse_qs(parsed.query).get("token", [""])[0]
        if not supplied:
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
            morsel = cookie.get("paperrevamper_token")
            if morsel is None:
                morsel = cookie.get("paperaudit_token")
            supplied = morsel.value if morsel else ""
        return bool(supplied) and secrets.compare_digest(supplied, expected)

    def _require_auth(self) -> bool:
        if self._authorized():
            return True
        self._send(401, _json({"status": "error", "message": "需要有效的 X-PaperRevamper-Token"}))
        return False

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 2_000_000:
            raise ValueError("请求体过大")
        value = json.loads(self.rfile.read(length).decode("utf-8") if length else "{}")
        if not isinstance(value, dict):
            raise ValueError("请求必须是 JSON 对象")
        return value

    def _prepare_inputs(self, data: dict) -> tuple[Path, Path, str | None, dict | None, str, str | None, str | None, str | None]:
        source = Path(str(data.get("source", ""))).expanduser().resolve()
        if not source.exists() or source.suffix.lower() not in {".docx", ".pdf", ".xml"}:
            raise ValueError("prepare 当前需要已有 DOCX、PDF 或 GROBID XML 文件")
        requested = str(data.get("out_dir", "")).strip()
        if requested:
            out = self.server.safe_output(requested)
        else:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            out = self.server.run_root / f"{source.stem}-{stamp}-{secrets.token_hex(2)}"
        workflow = data.get("workflow")
        if data.get("workflow_path"):
            workflow = load_workflow(self.server.workflow_path(str(data["workflow_path"])))
        pdf_parser = str(data.get("pdf_parser") or "native").casefold()
        if pdf_parser not in {"native", "grobid"}:
            raise ValueError("pdf_parser 必须是 native 或 grobid")
        grobid_endpoint = str(data.get("grobid_endpoint") or "").strip() or None
        if grobid_endpoint:
            self.server.validate_endpoint(grobid_endpoint)
        venue = str(data.get("venue") or "").strip() or None
        venue_registry = str(data.get("venue_registry") or "").strip() or None
        return source, out, data.get("checklist") or None, validate_workflow(workflow) if workflow else None, pdf_parser, grobid_endpoint, venue, venue_registry

    def _validated_direct_profile(self, profile_id: str | None) -> dict:
        config = load_llm_config(self.server.config_path)
        profile = select_profile(config, profile_id)
        if str(profile.get("base_url") or "").strip():
            self.server.validate_endpoint(str(profile["base_url"]))
        if profile.get("enabled") is False:
            raise ValueError(f"模型 profile 已停用：{profile.get('id', '')}")
        if str(profile.get("protocol", "")).casefold() == "host_agent":
            raise ValueError("host_agent 由宿主执行，不能作为直接模型任务")
        return profile

    def _validate_apply(self, data: dict) -> None:
        if data.get("confirm") is not True:
            raise ValueError("修改操作需要 confirm=true")
        run = self.server.safe_run(str(data.get("run_dir", "")))
        source = Path(str(data.get("source", ""))).expanduser().resolve()
        if not source.exists() or source.suffix.lower() != ".docx":
            raise ValueError("apply 当前需要已有 DOCX 源文件")
        state = build_state(run)
        expected_hash = str(data.get("source_hash", ""))
        current_hash = read_docx(source).source_hash
        if expected_hash != current_hash or expected_hash != str(state.get("source_hash", "")):
            raise ValueError("论文源文件已变化，请重新 prepare")
        ids = data.get("finding_ids", [])
        if not isinstance(ids, list) or not ids:
            raise ValueError("finding_ids 必须是非空列表")
        ids = self._canonical_finding_ids(state, ids)
        confirmed = {str(f.get("id")): f for f in state["findings"]["confirmed"]}
        confirmed.update({str(f.get("uid")): f for f in state["findings"]["confirmed"] if f.get("uid")})
        decisions = state["decisions"]
        invalid = []
        for raw_id in ids:
            fid = str(raw_id)
            finding = confirmed.get(fid)
            if finding is None or (
                decisions.get(str(finding.get("uid") or ""), {}).get("decision")
                if finding.get("uid")
                else decisions.get(str(finding.get("id") or ""), {}).get("decision")
            ) != "accept":
                invalid.append(raw_id)
        if invalid:
            raise ValueError(f"以下意见未经过作者 accept：{invalid}")
        plan = read_json(run / "revision.plan.json", {})
        if not isinstance(plan, dict) or plan.get("status") not in {"ready", "partial"}:
            raise ValueError("请先生成可应用的 revision.plan.json，并在面板预览修订内容")
        full_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        if plan.get("source_hash") != full_hash or plan.get("source_hash_matches") is False:
            raise ValueError("修订计划的源文件 hash 已变化，请重新生成预览")
        planned_ids = {
            str(finding_id)
            for proposal in plan.get("proposals", [])
            if isinstance(proposal, dict)
            for finding_id in proposal.get("finding_ids", [])
        }
        if not set(str(fid) for fid in ids) <= planned_ids:
            raise ValueError("有已接受意见不在当前修订计划中，请重新预览")

    @staticmethod
    def _canonical_finding_ids(state: dict, ids: list[object]) -> list[str]:
        mapping = {
            str(item.get("id")): str(item.get("id"))
            for item in state.get("findings", {}).get("confirmed", [])
            if item.get("id")
        }
        mapping.update(
            {
                str(item.get("uid")): str(item.get("id"))
                for item in state.get("findings", {}).get("confirmed", [])
                if item.get("uid") and item.get("id")
            }
        )
        return [mapping.get(str(value), str(value)) for value in ids]

    def _resume_job(self, job_id: str) -> dict:
        record = JOBS.get(job_id)
        if record is None:
            raise FileNotFoundError(f"找不到任务：{job_id}")
        payload = dict(record.get("payload") or {})
        kind = str(record.get("kind", ""))
        if kind == "prepare":
            source, out, checklist, workflow, pdf_parser, grobid_endpoint, venue, venue_registry = self._prepare_inputs(payload)
            def work(progress):
                return prepare(source, out, checklist, workflow, progress=progress, pdf_parser=pdf_parser, grobid_endpoint=grobid_endpoint, venue=venue, venue_registry=venue_registry)
        elif kind == "verify":
            run = self.server.safe_run(str(payload.get("run_dir") or record.get("run_dir", "")))
            checklist = payload.get("checklist") or None
            def work(progress):
                return verify(run, checklist, progress=progress)
        elif kind == "review":
            run = self.server.safe_run(str(payload.get("run_dir") or record.get("run_dir", "")))
            profile_id = str(payload.get("profile_id") or "").strip() or None
            profile = self._validated_direct_profile(profile_id)
            profile_id = str(profile.get("id"))
            timeout = max(1, min(1800, int(payload.get("timeout", 120) or 120)))
            verify_after = payload.get("verify_after") is not False
            def work(progress):
                return run_review(
                    run,
                    config_path=self.server.config_path,
                    profile_id=profile_id,
                    timeout=timeout,
                    verify_after=verify_after,
                    progress=progress,
                )
        elif kind == "panel":
            run = self.server.safe_run(str(payload.get("run_dir") or record.get("run_dir", "")))
            profiles = payload.get("profiles")
            if not isinstance(profiles, list) or len(set(str(item) for item in profiles)) < 2:
                raise ValueError("Panel 恢复任务需要至少两个不同的模型 profile")
            profiles = [str(self._validated_direct_profile(str(item)).get("id")) for item in profiles]
            if len(set(profiles)) > 8:
                raise ValueError("Panel 一次最多支持 8 个 judge model profile")
            required_models = max(1, min(8, int(payload.get("required_models", 2) or 2)))
            if required_models > len(set(profiles)):
                raise ValueError("required_models 不能大于所选的 judge model profile 数")
            timeout = max(1, min(1800, int(payload.get("timeout", 120) or 120)))
            def work(progress):
                return run_panel(
                    run,
                    profile_ids=[str(item) for item in profiles],
                    config_path=self.server.config_path,
                    timeout=timeout,
                    required_models=required_models,
                    progress=progress,
                )
        elif kind == "apply":
            payload["confirm"] = True
            self._validate_apply(payload)
            run = self.server.safe_run(str(payload.get("run_dir") or record.get("run_dir", "")))
            source = Path(str(payload.get("source", ""))).expanduser().resolve()
            ids = [str(value) for value in payload.get("finding_ids", [])]
            out_path = self.server.safe_output(str(payload["out"])) if payload.get("out") else None
            def work(progress):
                return apply_revision(
                    source,
                    run,
                    ids,
                    out_path=out_path,
                    text=payload.get("text") or None,
                    progress=progress,
                )
        else:
            raise ValueError(f"任务类型不支持恢复：{kind}")
        resumed = JOBS.resume(job_id, work)
        if resumed is None:
            raise ValueError("任务无法恢复")
        if resumed.get("status") != "queued":
            raise ValueError(f"任务当前状态不能恢复：{resumed.get('status')}")
        return resumed

    def _run_from_query(self, parsed) -> Path:
        query = parse_qs(parsed.query)
        raw = query.get("run_dir", [""])[0]
        return self.server.safe_run(raw)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            if not self._require_auth():
                return
            if parsed.path in {"/", "/index.html"}:
                body = UI_FILE.read_bytes()
                headers: dict[str, str] = {}
                if self.server.auth_token and parse_qs(parsed.query).get("token", [""])[0]:
                    supplied = parse_qs(parsed.query).get("token", [""])[0]
                    if secrets.compare_digest(supplied, self.server.auth_token):
                        headers["Set-Cookie"] = f"paperrevamper_token={quote(supplied, safe='')}; Path=/; HttpOnly; SameSite=Strict"
                self._send(200, body, "text/html; charset=utf-8", extra_headers=headers)
                return
            if parsed.path == "/api/health":
                self._send(200, _json({"status": "ok", "run_root": str(self.server.run_root), "config_path": str(self.server.config_path)}))
                return
            if parsed.path == "/api/job":
                job_id = parse_qs(parsed.query).get("job_id", [""])[0]
                job = JOBS.get(job_id)
                if job is None:
                    raise FileNotFoundError(f"找不到任务：{job_id}")
                run = Path(job["run_dir"])
                if run.exists():
                    job["state"] = build_state(run)
                self._send(200, _json(job))
                return
            if parsed.path == "/api/jobs":
                self._send(200, _json({"jobs": JOBS.list_jobs(self.server.run_root)}))
                return
            if parsed.path == "/api/llm/config":
                config = load_llm_config(self.server.config_path)
                self._send(200, _json({"config": public_config(config), "path": str(self.server.config_path)}))
                return
            if parsed.path == "/api/runs":
                runs = []
                for path in sorted(self.server.run_root.iterdir()):
                    if path.is_dir() and (path / "manifest.json").exists():
                        state = build_state(path)
                        runs.append({"run_dir": str(path), "source": state["source"], "source_hash": state["source_hash"], "trace_cursor": state["trace_cursor"]})
                self._send(200, _json({"runs": runs}))
                return
            if parsed.path == "/api/workflow":
                folder = self.server.run_root / "workflows"
                saved = []
                if folder.exists():
                    saved = [p.name for p in sorted(folder.glob("*.json"))]
                active = read_json(folder / "default.json", default_workflow())
                self._send(200, _json({"workflow": validate_workflow(active), "saved": saved}))
                return
            if parsed.path == "/api/workflow/catalog":
                from paperrevamper.collaboration import ROLES, SKILLS

                self._send(200, _json({"roles": ROLES, "skills": SKILLS, "task_kinds": ["deterministic", "host_agent", "host_agent_plus_code", "user_confirmed_only"]}))
                return
            if parsed.path == "/api/state":
                self._send(200, _json(build_state(self._run_from_query(parsed))))
                return
            if parsed.path == "/api/artifact":
                run = self._run_from_query(parsed)
                name = parse_qs(parsed.query).get("name", [""])[0]
                if not name or ".." in Path(name).parts or Path(name).is_absolute():
                    raise ValueError("非法 artifact 名称")
                path = (run / name).resolve()
                if run not in path.parents or not path.exists() or not path.is_file():
                    raise FileNotFoundError(path)
                self._send(200, path.read_bytes(), "text/plain; charset=utf-8")
                return
            self._error(404, "未找到接口")
        except FileNotFoundError as exc:
            self._error(404, str(exc))
        except ValueError as exc:
            self._error(400, str(exc))
        except Exception as exc:  # pragma: no cover
            self._error(500, str(exc))

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            if not self._require_auth():
                return
            data = self._body()
            if parsed.path == "/api/job/cancel":
                job_id = str(data.get("job_id", ""))
                result = JOBS.cancel(job_id)
                if result is None:
                    raise FileNotFoundError(f"找不到任务：{job_id}")
                self._send(200, _json(result))
                return
            if parsed.path == "/api/job/resume":
                job_id = str(data.get("job_id", ""))
                result = self._resume_job(job_id)
                self._send(202, _json(result))
                return
            if parsed.path == "/api/prepare":
                source = Path(str(data.get("source", ""))).expanduser().resolve()
                if not source.exists() or source.suffix.lower() not in {".docx", ".pdf", ".xml"}:
                    raise ValueError("prepare 当前需要已有 DOCX、PDF 或 GROBID XML 文件")
                requested = str(data.get("out_dir", "")).strip()
                if requested:
                    out = self.server.safe_output(requested)
                else:
                    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                    out = self.server.run_root / f"{source.stem}-{stamp}-{secrets.token_hex(2)}"
                workflow = data.get("workflow")
                if data.get("workflow_path"):
                    workflow = load_workflow(self.server.workflow_path(str(data["workflow_path"])))
                pdf_parser = str(data.get("pdf_parser") or "native").casefold()
                if pdf_parser not in {"native", "grobid"}:
                    raise ValueError("pdf_parser 必须是 native 或 grobid")
                grobid_endpoint = str(data.get("grobid_endpoint") or "").strip() or None
                if grobid_endpoint:
                    self.server.validate_endpoint(grobid_endpoint)
                result = prepare(source, out, data.get("checklist") or None, validate_workflow(workflow) if workflow else None, pdf_parser=pdf_parser, grobid_endpoint=grobid_endpoint, venue=str(data.get("venue") or "").strip() or None, venue_registry=str(data.get("venue_registry") or "").strip() or None)
                append_trace(result, "ui_prepare_requested", source=str(source), panel=True)
                append_trace(result, "ui_prepare_complete", panel=True)
                self._send(200, _json(build_state(result)))
                return
            if parsed.path == "/api/prepare/start":
                source, out, checklist, workflow, pdf_parser, grobid_endpoint, venue, venue_registry = self._prepare_inputs(data)
                job_id = JOBS.start(
                    "prepare",
                    out,
                    lambda progress: prepare(source, out, checklist, workflow, progress=progress, pdf_parser=pdf_parser, grobid_endpoint=grobid_endpoint, venue=venue, venue_registry=venue_registry),
                    payload={
                        "source": str(source),
                        "out_dir": str(out),
                        "checklist": checklist,
                        "workflow": workflow,
                        "pdf_parser": pdf_parser,
                        "grobid_endpoint": grobid_endpoint,
                        "venue": venue,
                        "venue_registry": venue_registry,
                    },
                )
                self._send(202, _json({"status": "accepted", "job_id": job_id, "run_dir": str(out)}))
                return
            if parsed.path == "/api/workflow/validate":
                workflow = validate_workflow(data.get("workflow"))
                self._send(200, _json({"status": "ok", "workflow": workflow}))
                return
            if parsed.path == "/api/workflow/save":
                workflow = validate_workflow(data.get("workflow"))
                path = self.server.workflow_path(str(data.get("name", "default.json")))
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(workflow, ensure_ascii=False, indent=2), encoding="utf-8")
                self._send(200, _json({"status": "ok", "path": str(path), "workflow": workflow}))
                return
            if parsed.path == "/api/workflow/delete":
                path = self.server.workflow_path(str(data.get("name", "")))
                if path.name == "default.json":
                    raise ValueError("不能删除 default workflow")
                if path.exists():
                    path.unlink()
                self._send(200, _json({"status": "ok", "deleted": path.name}))
                return
            if parsed.path == "/api/llm/config":
                raw_profiles = data.get("profiles", [])
                for profile in raw_profiles if isinstance(raw_profiles, list) else []:
                    if isinstance(profile, dict) and str(profile.get("base_url") or "").strip():
                        self.server.validate_endpoint(str(profile["base_url"]))
                config = save_llm_config(self.server.config_path, data)
                self._send(200, _json({"status": "ok", "config": public_config(config), "path": str(self.server.config_path)}))
                return
            if parsed.path == "/api/llm/test":
                config = load_llm_config(self.server.config_path)
                profile = select_profile(config, str(data.get("profile_id", "")) or None)
                if str(profile.get("base_url") or "").strip():
                    self.server.validate_endpoint(str(profile["base_url"]))
                result = llm_chat(profile, [{"role": "user", "content": str(data.get("message") or "请只回复 OK，确认模型连接正常。")[:2000]}], timeout=30)
                self._send(200, _json({"status": "ok", "profile": profile.get("id"), "result": result}))
                return
            if parsed.path == "/api/llm/chat":
                config = load_llm_config(self.server.config_path)
                profile = select_profile(config, str(data.get("profile_id", "")) or None)
                if str(profile.get("base_url") or "").strip():
                    self.server.validate_endpoint(str(profile["base_url"]))
                messages = data.get("messages")
                if not isinstance(messages, list) or not messages:
                    raise ValueError("messages 必须是非空列表")
                result = llm_chat(profile, [item for item in messages if isinstance(item, dict)], timeout=120)
                self._send(200, _json({"status": "ok", "profile": profile.get("id"), "result": result}))
                return
            if parsed.path == "/api/verify":
                run = self.server.safe_run(str(data.get("run_dir", "")))
                result = verify(run, data.get("checklist") or None)
                append_trace(run, "ui_verify_requested", panel=True)
                self._send(200, _json({"result": result, "state": build_state(run)}))
                return
            if parsed.path == "/api/verify/start":
                run = self.server.safe_run(str(data.get("run_dir", "")))
                job_id = JOBS.start(
                    "verify",
                    run,
                    lambda progress: verify(run, data.get("checklist") or None, progress=progress),
                    payload={"run_dir": str(run), "checklist": data.get("checklist") or None},
                )
                self._send(202, _json({"status": "accepted", "job_id": job_id, "run_dir": str(run)}))
                return
            if parsed.path == "/api/review/start":
                run = self.server.safe_run(str(data.get("run_dir", "")))
                config = load_llm_config(self.server.config_path)
                profile_id = str(data.get("profile_id") or "").strip() or None
                profile = select_profile(config, profile_id)
                if str(profile.get("base_url") or "").strip():
                    self.server.validate_endpoint(str(profile["base_url"]))
                if profile.get("enabled") is False:
                    raise ValueError(f"模型 profile 已停用：{profile.get('id', '')}")
                if str(profile.get("protocol", "")).casefold() == "host_agent":
                    raise ValueError("host_agent 由宿主执行，不能作为直接模型任务")
                timeout = max(1, min(1800, int(data.get("timeout", 120) or 120)))
                verify_after = data.get("verify_after") is not False
                job_id = JOBS.start(
                    "review",
                    run,
                    lambda progress: run_review(
                        run,
                        config_path=self.server.config_path,
                        profile_id=str(profile.get("id")),
                        timeout=timeout,
                        verify_after=verify_after,
                        progress=progress,
                    ),
                    payload={
                        "run_dir": str(run),
                        "profile_id": str(profile.get("id")),
                        "timeout": timeout,
                        "verify_after": verify_after,
                    },
                )
                self._send(202, _json({"status": "accepted", "job_id": job_id, "run_dir": str(run)}))
                return
            if parsed.path == "/api/panel/start":
                run = self.server.safe_run(str(data.get("run_dir", "")))
                raw_profiles = data.get("profiles")
                profiles = [str(value).strip() for value in raw_profiles if str(value).strip()] if isinstance(raw_profiles, list) else []
                if len(set(profiles)) < 2:
                    raise ValueError("Panel 至少需要两个不同的模型 profile")
                if len(set(profiles)) > 8:
                    raise ValueError("Panel 一次最多支持 8 个 judge model profile")
                config = load_llm_config(self.server.config_path)
                for profile_id in profiles:
                    profile = select_profile(config, profile_id)
                    if str(profile.get("base_url") or "").strip():
                        self.server.validate_endpoint(str(profile["base_url"]))
                    if profile.get("enabled") is False:
                        raise ValueError(f"模型 profile 已停用：{profile_id}")
                    if str(profile.get("protocol", "")).casefold() == "host_agent":
                        raise ValueError(f"Panel judge 不能使用 host_agent：{profile_id}")
                timeout = max(1, min(1800, int(data.get("timeout", 120) or 120)))
                required_models = max(1, min(8, int(data.get("required_models", 2) or 2)))
                if required_models > len(set(profiles)):
                    raise ValueError("required_models 不能大于所选的 judge model profile 数")
                job_id = JOBS.start(
                    "panel",
                    run,
                    lambda progress: run_panel(
                        run,
                        profile_ids=profiles,
                        config_path=self.server.config_path,
                        timeout=timeout,
                        required_models=required_models,
                        resume=data.get("fresh") is not True,
                        progress=progress,
                    ),
                    payload={
                        "run_dir": str(run),
                        "profiles": profiles,
                        "timeout": timeout,
                        "required_models": required_models,
                    },
                )
                self._send(202, _json({"status": "accepted", "job_id": job_id, "run_dir": str(run)}))
                return
            if parsed.path == "/api/adjudicate":
                run = self.server.safe_run(str(data.get("run_dir", "")))
                judgments_raw = str(data.get("judgments") or "panel.judgments.json")
                judgments_candidate = Path(judgments_raw).expanduser()
                if not judgments_candidate.is_absolute():
                    judgments_candidate = run / judgments_candidate
                judgments = judgments_candidate.resolve()
                if judgments != run and run not in judgments.parents:
                    raise ValueError("panel judgments 必须位于审查包目录内")
                if not judgments.exists():
                    raise FileNotFoundError(judgments)
                required_models = max(1, min(8, int(data.get("required_models", 2) or 2)))
                result = adjudicate_run(run, judgments, required_models=required_models)
                append_trace(
                    run,
                    "panel_adjudication_complete",
                    status=result.get("status"),
                    summary=result.get("summary", {}),
                    panel=True,
                )
                self._send(200, _json({"result": result, "state": build_state(run)}))
                return
            if parsed.path == "/api/decisions":
                run = self.server.safe_run(str(data.get("run_dir", "")))
                manifest = read_json(run / "manifest.json", {})
                source_hash = str(data.get("source_hash", ""))
                if source_hash != str(manifest.get("hash", "")):
                    self._error(409, "论文源文件 hash 已变化，请重新 prepare")
                    return
                requested_id = str(data.get("finding_id", ""))
                requested_uid = str(data.get("finding_uid", ""))
                decision = str(data.get("decision", ""))
                findings = read_json(run / "findings.json", {})
                all_findings = [
                    item
                    for key in ("confirmed", "rejected")
                    for item in (findings.get(key, []) if isinstance(findings, dict) else [])
                    if isinstance(item, dict)
                ]
                finding = next(
                    (
                        item for item in all_findings
                        if (requested_uid and str(item.get("uid") or "") == requested_uid)
                        or (not requested_uid and str(item.get("id") or "") == requested_id)
                    ),
                    None,
                )
                if finding is None:
                    raise ValueError("找不到该审查意见")
                finding_id = str(finding.get("id") or requested_id)
                finding_uid = str(finding.get("uid") or "")
                confirmed = {str(item.get("id")) for item in (findings.get("confirmed", []) if isinstance(findings, dict) else [])}
                if decision == "accept" and finding_id not in confirmed:
                    raise ValueError("未通过证据门禁的意见不能 accept")
                adjudication = read_json(run / "adjudication.json", {})
                if not isinstance(adjudication, dict) or str(adjudication.get("source_hash", "")) != str(manifest.get("hash", "")):
                    adjudication = {}
                panel_row = next(
                    (
                        row for row in (adjudication.get("findings", []) if isinstance(adjudication, dict) else [])
                        if isinstance(row, dict) and (
                            str(row.get("finding_uid") or "") == finding_uid
                            or (not finding_uid and str(row.get("finding_id")) == finding_id)
                        )
                    ),
                    None,
                )
                if decision == "accept" and panel_row and panel_row.get("verdict") in {"refuted", "unverifiable"}:
                    raise ValueError("panel 裁决为 refuted/unverifiable 的意见不能直接 accept")
                row = append_decision(run, finding_id, decision, str(data.get("reason", "")), source_hash, finding_uid=finding_uid)
                append_trace(run, "ui_decision_recorded", finding_id=finding_id, finding_uid=finding_uid, decision=decision, panel=True)
                self._send(200, _json({"decision": row, "state": build_state(run)}))
                return
            if parsed.path == "/api/apply":
                if data.get("confirm") is not True:
                    raise ValueError("修改操作需要 confirm=true")
                self._validate_apply(data)
                run = self.server.safe_run(str(data.get("run_dir", "")))
                source = Path(str(data.get("source", ""))).expanduser().resolve()
                if not source.exists() or source.suffix.lower() != ".docx":
                    raise ValueError("apply 当前需要已有 DOCX 源文件")
                state = build_state(run)
                expected_hash = str(data.get("source_hash", ""))
                current_hash = read_docx(source).source_hash
                if expected_hash != current_hash or expected_hash != str(state.get("source_hash", "")):
                    self._error(409, "论文源文件已变化，请重新 prepare")
                    return
                ids = data.get("finding_ids", [])
                if not isinstance(ids, list) or not ids:
                    raise ValueError("finding_ids 必须是非空列表")
                ids = self._canonical_finding_ids(state, ids)
                confirmed = {str(f.get("id")): f for f in state["findings"]["confirmed"]}
                confirmed.update({str(f.get("uid")): f for f in state["findings"]["confirmed"] if f.get("uid")})
                decisions = state["decisions"]
                invalid = []
                for raw_id in ids:
                    finding = confirmed.get(str(raw_id))
                    decision = (
                        decisions.get(str(finding.get("uid") or ""), {}).get("decision")
                        if finding is not None and finding.get("uid")
                        else decisions.get(str(finding.get("id") or ""), {}).get("decision") if finding is not None else None
                    )
                    if finding is None or decision != "accept":
                        invalid.append(raw_id)
                if invalid:
                    raise ValueError(f"以下意见未经过作者 accept：{invalid}")
                output = data.get("out")
                out_path = self.server.safe_output(str(output)) if output else None
                append_trace(run, "ui_apply_requested", finding_ids=ids, panel=True)
                result = apply_revision(source, run, [str(fid) for fid in ids], out_path=out_path, text=data.get("text") or None)
                append_trace(run, "apply_complete", status=result.get("status"), regressions=result.get("regressions", []), panel=True)
                self._send(200, _json({"result": result, "state": build_state(run)}))
                return
            if parsed.path == "/api/revision/plan":
                run = self.server.safe_run(str(data.get("run_dir", "")))
                source = Path(str(data.get("source", ""))).expanduser().resolve()
                raw_ids = data.get("finding_ids", [])
                if isinstance(raw_ids, str):
                    ids = [value.strip() for value in raw_ids.split(",") if value.strip()]
                elif isinstance(raw_ids, list):
                    ids = [str(value).strip() for value in raw_ids if str(value).strip()]
                else:
                    ids = []
                if not ids:
                    raise ValueError("finding_ids 必须是非空列表或逗号分隔字符串")
                result = build_revision_plan(source, run, ids, text=data.get("text") or None)
                append_trace(run, "revision_plan_created", status=result.get("status"), proposals=len(result.get("proposals", [])), failed=len(result.get("failed", [])), panel=True)
                self._send(200, _json({"result": result, "state": build_state(run)}))
                return
            if parsed.path == "/api/apply/start":
                self._validate_apply(data)
                run = self.server.safe_run(str(data.get("run_dir", "")))
                source = Path(str(data.get("source", ""))).expanduser().resolve()
                state = build_state(run)
                ids = self._canonical_finding_ids(state, [str(fid) for fid in data.get("finding_ids", [])])
                output = data.get("out")
                out_path = self.server.safe_output(str(output)) if output else None
                job_id = JOBS.start(
                    "apply",
                    run,
                    lambda progress: apply_revision(source, run, ids, out_path=out_path, text=data.get("text") or None, progress=progress),
                    payload={
                        "source": str(source),
                        "run_dir": str(run),
                        "finding_ids": ids,
                        "source_hash": data.get("source_hash", ""),
                        "out": str(out_path) if out_path else None,
                        "text": data.get("text") or None,
                    },
                )
                self._send(202, _json({"status": "accepted", "job_id": job_id, "run_dir": str(run)}))
                return
            self._error(404, "未找到接口")
        except FileNotFoundError as exc:
            self._error(404, str(exc))
        except ValueError as exc:
            self._error(400, str(exc))
        except Exception as exc:  # pragma: no cover
            self._error(500, str(exc))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="启动 PaperRevamper 多 Agent 修改控制面板")
    parser.add_argument("--run-root", default="out/panel-runs", help="审查运行目录根路径")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true", help="启动后打开默认浏览器")
    parser.add_argument("--allow-remote", action="store_true", help="允许绑定非本机地址；必须同时提供 --auth-token")
    parser.add_argument("--auth-token", help="保护面板 API 的共享令牌；远程绑定时必填")
    args = parser.parse_args(argv)
    loopback = args.host.casefold() in {"127.0.0.1", "localhost", "::1"}
    if not loopback and not args.allow_remote:
        print("拒绝绑定远程地址：请明确传入 --allow-remote，并设置 --auth-token。", file=sys.stderr)
        return 2
    if not loopback and not args.auth_token:
        print("远程面板必须设置 --auth-token。", file=sys.stderr)
        return 2
    server = PanelServer((args.host, args.port), Path(args.run_root), auth_token=args.auth_token)
    token_query = f"?token={quote(args.auth_token)}" if args.auth_token else ""
    url = f"http://{args.host}:{args.port}/{token_query}"
    print(f"PaperRevamper 控制面板：{url}")
    print(f"运行目录根路径：{server.run_root}")
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
