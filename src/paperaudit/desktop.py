"""Double-click launcher for the PaperAudit desktop bundle."""

from __future__ import annotations

import argparse
import os
import socket
from pathlib import Path

from paperaudit.panel import main as panel_main


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _default_run_root() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(local_app_data) / "PaperAudit" / "runs"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PaperAudit 论文审查与多 Agent 工作流桌面版")
    parser.add_argument("--run-root", default=str(_default_run_root()))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-open", action="store_true", help="只启动服务，不自动打开浏览器")
    parser.add_argument("--allow-remote", action="store_true", help="允许绑定非本机地址；必须同时提供 --auth-token")
    parser.add_argument("--auth-token", help="保护面板 API 的共享令牌；远程绑定时必填")
    args = parser.parse_args(argv)
    port = args.port or _free_port()
    panel_args = ["--run-root", args.run_root, "--host", args.host, "--port", str(port)]
    if args.allow_remote:
        panel_args.append("--allow-remote")
    if args.auth_token:
        panel_args.extend(["--auth-token", args.auth_token])
    if not args.no_open:
        panel_args.append("--open")
    return panel_main(panel_args)


if __name__ == "__main__":
    raise SystemExit(main())
