"""PaperAudit CLI。

工作流（宿主协作式 —— 宿主 agent 本身就是 LLM，本工具不调 API）：

    prepare   解析文档 → 审查包（清单 + 分章节上下文）
       ↓  宿主按清单做语义审查，回填 findings.llm.json
    verify    证据门禁 → 去重 → 排序 → 报告
       ↓  用户勾选
    apply     副本上改写 + 备份 + 回归复验（原文件不动）
    panel     启动本地可视化多 Agent 修改控制面板

多篇按传入顺序串行处理。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from paperaudit.evidence import run_all
from paperaudit.ingest import read_docx
from paperaudit.reporting import apply_gate, to_json, to_markdown


def _cmd_check(args) -> int:
    code = 0
    for raw in args.paths:
        p = Path(raw)
        if not p.exists():
            print(f"[错误] 文件不存在：{p}", file=sys.stderr)
            code = 2
            continue
        if p.suffix.lower() != ".docx":
            print(f"[跳过] 目前仅支持 .docx：{p}", file=sys.stderr)
            # A successful process exit must not imply that an unsupported
            # input was audited. Keep processing other paths, but expose the
            # unsupported input to CI and calling agents.
            code = max(code, 2)
            continue
        doc = read_docx(p)
        findings = apply_gate(doc, run_all(doc, only=args.only, skip=args.skip))
        if args.format == "json":
            print(to_json(doc, findings))
        else:
            if len(args.paths) > 1:
                print(f"\n{'=' * 70}\n### {p.name}\n{'=' * 70}\n")
            print(to_markdown(doc, findings))
        if args.out:
            out = Path(args.out)
            out.mkdir(parents=True, exist_ok=True)
            (out / f"{p.stem}.review.md").write_text(to_markdown(doc, findings), encoding="utf-8")
            (out / f"{p.stem}.findings.json").write_text(to_json(doc, findings), encoding="utf-8")
            print(f"[已写入] {out / (p.stem + '.review.md')}", file=sys.stderr)
    return code


def _cmd_prepare(args) -> int:
    from paperaudit.prepare import prepare
    from paperaudit.collaboration import load_workflow

    workflow = load_workflow(args.workflow) if args.workflow else None
    out = prepare(args.path, args.out, args.checklist, workflow)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(f"\n[审查包已生成] {out}", file=sys.stderr)
    print(f"[下一步] {manifest['next_step']}", file=sys.stderr)
    return 0


def _cmd_verify(args) -> int:
    from paperaudit.verify import verify

    result = verify(args.run_dir, args.checklist)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _cmd_apply(args) -> int:
    from paperaudit.revise import apply_revision

    ids = [x.strip() for x in args.findings.split(",") if x.strip()]
    result = apply_revision(
        args.path, args.run_dir, ids, out_path=args.out,
        backup=not args.no_backup, text=args.text,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "ok" else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="paperaudit",
        description="PaperAudit —— 投前自审的验证器（宿主协作式，本工具不调 LLM）",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="确定性检查，只出意见，不修改文件")
    c.add_argument("paths", nargs="+")
    c.add_argument("--format", choices=["markdown", "json"], default="markdown")
    c.add_argument("--out")
    c.add_argument("--only", nargs="*")
    c.add_argument("--skip", nargs="*")

    pr = sub.add_parser("prepare", help="生成审查包，供宿主 agent 做语义审查")
    pr.add_argument("path")
    pr.add_argument("--out", required=True)
    pr.add_argument("--checklist", help="外部清单 YAML（默认内置 academic.yaml）")
    pr.add_argument("--workflow", help="自定义多 Agent workflow JSON")

    v = sub.add_parser("verify", help="对回填意见做证据门禁、去重、排序")
    v.add_argument("run_dir")
    v.add_argument("--checklist")

    ap = sub.add_parser("apply", help="在副本上改写，原文件不动，自动备份")
    ap.add_argument("path")
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--findings", required=True, help="逗号分隔，如 F003,F007")
    ap.add_argument("--out")
    ap.add_argument("--text", help="直接给出改写后的段落文本（覆盖 suggested_fix）")
    ap.add_argument("--no-backup", action="store_true")

    pnl = sub.add_parser("panel", help="启动本地多 Agent 修改控制面板")
    pnl.add_argument("--run-root", default="out/panel-runs")
    pnl.add_argument("--host", default="127.0.0.1")
    pnl.add_argument("--port", type=int, default=8765)
    pnl.add_argument("--open", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "panel":
        from paperaudit.panel import main as panel_main

        panel_args = ["--run-root", args.run_root, "--host", args.host, "--port", str(args.port)]
        if args.open:
            panel_args.append("--open")
        return panel_main(panel_args)
    return {
        "check": _cmd_check,
        "prepare": _cmd_prepare,
        "verify": _cmd_verify,
        "apply": _cmd_apply,
    }[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
