"""Dependency-free local control panel for multi-agent PaperAudit runs."""

from __future__ import annotations

import argparse
import json
import secrets
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from paperaudit.collaboration import append_trace, default_workflow, load_workflow, validate_workflow
from paperaudit.ingest import read_docx
from paperaudit.jobs import JOBS
from paperaudit.llm import chat as llm_chat, load_config as load_llm_config, public_config, save_config as save_llm_config, select_profile
from paperaudit.panel_state import append_decision, build_state, read_json
from paperaudit.prepare import prepare
from paperaudit.revise import apply_revision
from paperaudit.resources import resource_path
from paperaudit.verify import verify

_SOURCE_UI_FILE = Path(__file__).resolve().parents[2] / "ui" / "control-panel.html"
_PACKAGE_UI_FILE = Path(__file__).resolve().parent / "static" / "control-panel.html"
UI_FILE = _SOURCE_UI_FILE if _SOURCE_UI_FILE.exists() else resource_path("paperaudit", "static", "control-panel.html")


def _json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")


class PanelServer(ThreadingHTTPServer):
    def __init__(self, address, run_root: Path):
        self.run_root = run_root.resolve()
        self.run_root.mkdir(parents=True, exist_ok=True)
        self.config_path = self.run_root.parent / "config.json"
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


class PanelHandler(BaseHTTPRequestHandler):
    server: PanelServer

    def log_message(self, fmt: str, *args) -> None:  # noqa: A002
        print(f"[paperaudit-panel] {fmt % args}")

    def _send(self, status: int, body: bytes, content_type: str = "application/json; charset=utf-8") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, message: str) -> None:
        self._send(status, _json({"status": "error", "message": message}))

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 2_000_000:
            raise ValueError("请求体过大")
        value = json.loads(self.rfile.read(length).decode("utf-8") if length else "{}")
        if not isinstance(value, dict):
            raise ValueError("请求必须是 JSON 对象")
        return value

    def _prepare_inputs(self, data: dict) -> tuple[Path, Path, str | None, dict | None]:
        source = Path(str(data.get("source", ""))).expanduser().resolve()
        if not source.exists() or source.suffix.lower() != ".docx":
            raise ValueError("prepare 当前需要已有 DOCX 文件")
        requested = str(data.get("out_dir", "")).strip()
        if requested:
            out = self.server.safe_output(requested)
        else:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            out = self.server.run_root / f"{source.stem}-{stamp}-{secrets.token_hex(2)}"
        workflow = data.get("workflow")
        if data.get("workflow_path"):
            workflow = load_workflow(self.server.workflow_path(str(data["workflow_path"])))
        return source, out, data.get("checklist") or None, validate_workflow(workflow) if workflow else None

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
        confirmed = {str(f.get("id")): f for f in state["findings"]["confirmed"]}
        decisions = state["decisions"]
        invalid = [fid for fid in ids if fid not in confirmed or decisions.get(str(fid), {}).get("decision") != "accept"]
        if invalid:
            raise ValueError(f"以下意见未经过作者 accept：{invalid}")

    def _run_from_query(self, parsed) -> Path:
        query = parse_qs(parsed.query)
        raw = query.get("run_dir", [""])[0]
        return self.server.safe_run(raw)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            if parsed.path in {"/", "/index.html"}:
                body = UI_FILE.read_bytes()
                self._send(200, body, "text/html; charset=utf-8")
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
                from paperaudit.collaboration import ROLES, SKILLS

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
            data = self._body()
            if parsed.path == "/api/prepare":
                source = Path(str(data.get("source", ""))).expanduser().resolve()
                if not source.exists() or source.suffix.lower() != ".docx":
                    raise ValueError("prepare 当前需要已有 DOCX 文件")
                requested = str(data.get("out_dir", "")).strip()
                if requested:
                    out = self.server.safe_output(requested)
                else:
                    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                    out = self.server.run_root / f"{source.stem}-{stamp}-{secrets.token_hex(2)}"
                workflow = data.get("workflow")
                if data.get("workflow_path"):
                    workflow = load_workflow(self.server.workflow_path(str(data["workflow_path"])))
                result = prepare(source, out, data.get("checklist") or None, validate_workflow(workflow) if workflow else None)
                append_trace(result, "ui_prepare_requested", source=str(source), panel=True)
                append_trace(result, "ui_prepare_complete", panel=True)
                self._send(200, _json(build_state(result)))
                return
            if parsed.path == "/api/prepare/start":
                source, out, checklist, workflow = self._prepare_inputs(data)
                job_id = JOBS.start(
                    "prepare",
                    out,
                    lambda progress: prepare(source, out, checklist, workflow, progress=progress),
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
                config = save_llm_config(self.server.config_path, data)
                self._send(200, _json({"status": "ok", "config": public_config(config), "path": str(self.server.config_path)}))
                return
            if parsed.path == "/api/llm/test":
                config = load_llm_config(self.server.config_path)
                profile = select_profile(config, str(data.get("profile_id", "")) or None)
                result = llm_chat(profile, [{"role": "user", "content": str(data.get("message") or "请只回复 OK，确认模型连接正常。")[:2000]}], timeout=30)
                self._send(200, _json({"status": "ok", "profile": profile.get("id"), "result": result}))
                return
            if parsed.path == "/api/llm/chat":
                config = load_llm_config(self.server.config_path)
                profile = select_profile(config, str(data.get("profile_id", "")) or None)
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
                job_id = JOBS.start("verify", run, lambda progress: verify(run, data.get("checklist") or None, progress=progress))
                self._send(202, _json({"status": "accepted", "job_id": job_id, "run_dir": str(run)}))
                return
            if parsed.path == "/api/decisions":
                run = self.server.safe_run(str(data.get("run_dir", "")))
                manifest = read_json(run / "manifest.json", {})
                source_hash = str(data.get("source_hash", ""))
                if source_hash != str(manifest.get("hash", "")):
                    self._error(409, "论文源文件 hash 已变化，请重新 prepare")
                    return
                finding_id = str(data.get("finding_id", ""))
                decision = str(data.get("decision", ""))
                findings = read_json(run / "findings.json", {})
                confirmed = {str(item.get("id")) for item in findings.get("confirmed", [])} if isinstance(findings, dict) else set()
                rejected = {str(item.get("id")) for item in findings.get("rejected", [])} if isinstance(findings, dict) else set()
                if finding_id not in confirmed and finding_id not in rejected:
                    raise ValueError("找不到该审查意见")
                if decision == "accept" and finding_id not in confirmed:
                    raise ValueError("未通过证据门禁的意见不能 accept")
                row = append_decision(run, finding_id, decision, str(data.get("reason", "")), source_hash)
                append_trace(run, "ui_decision_recorded", finding_id=finding_id, decision=decision, panel=True)
                self._send(200, _json({"decision": row, "state": build_state(run)}))
                return
            if parsed.path == "/api/apply":
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
                    self._error(409, "论文源文件已变化，请重新 prepare")
                    return
                ids = data.get("finding_ids", [])
                if not isinstance(ids, list) or not ids:
                    raise ValueError("finding_ids 必须是非空列表")
                confirmed = {str(f.get("id")): f for f in state["findings"]["confirmed"]}
                decisions = state["decisions"]
                invalid = [fid for fid in ids if fid not in confirmed or decisions.get(str(fid), {}).get("decision") != "accept"]
                if invalid:
                    raise ValueError(f"以下意见未经过作者 accept：{invalid}")
                output = data.get("out")
                out_path = self.server.safe_output(str(output)) if output else None
                append_trace(run, "ui_apply_requested", finding_ids=ids, panel=True)
                result = apply_revision(source, run, [str(fid) for fid in ids], out_path=out_path, text=data.get("text") or None)
                append_trace(run, "apply_complete", status=result.get("status"), regressions=result.get("regressions", []), panel=True)
                self._send(200, _json({"result": result, "state": build_state(run)}))
                return
            if parsed.path == "/api/apply/start":
                self._validate_apply(data)
                run = self.server.safe_run(str(data.get("run_dir", "")))
                source = Path(str(data.get("source", ""))).expanduser().resolve()
                ids = [str(fid) for fid in data.get("finding_ids", [])]
                output = data.get("out")
                out_path = self.server.safe_output(str(output)) if output else None
                job_id = JOBS.start(
                    "apply",
                    run,
                    lambda progress: apply_revision(source, run, ids, out_path=out_path, text=data.get("text") or None, progress=progress),
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
    parser = argparse.ArgumentParser(description="启动 PaperAudit 多 Agent 修改控制面板")
    parser.add_argument("--run-root", default="out/panel-runs", help="审查运行目录根路径")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true", help="启动后打开默认浏览器")
    args = parser.parse_args(argv)
    server = PanelServer((args.host, args.port), Path(args.run_root))
    url = f"http://{args.host}:{args.port}/"
    print(f"PaperAudit 控制面板：{url}")
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
