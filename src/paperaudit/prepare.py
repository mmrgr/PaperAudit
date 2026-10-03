"""prepare：为宿主 agent（WorkBuddy / Codex）生成多角色审查包。

架构要点：宿主本身就是 LLM，PaperAudit 不需要自己调 API；宿主按协作计划运行独立角色。
它负责宿主不擅长的事：解析、定位、清单、门禁、改写执行、回归。
宿主负责它擅长的事：按角色和清单做语义审查，再由裁决角色汇总。

产物：
  manifest.json        概览 + 清单 + 回填 schema
  outline.md           章节树
  deterministic.md     确定性预检结果
  findings.template.json
  collaboration.plan.json / collaboration.md
  trace.jsonl            协作过程事件（不含原文）
  context/sec_XX.md    按章节切分的原文（分批读取，省 token 且可并行）
"""

from __future__ import annotations

import json
import hashlib
import re
from pathlib import Path
from typing import Callable

from paperaudit.checklist import detect_doc_type, load as load_checklist
from paperaudit.collaboration import append_trace, validate_workflow, write_plan
from paperaudit.evidence import run_all
from paperaudit.ingest import read_docx
from paperaudit.models import Block, DocumentIR, Finding
from paperaudit.reporting import apply_gate, to_markdown

# 单批上下文的字数上限，超过则按二级标题再切
CHUNK_LIMIT = 3500


def prepare(
    path: str | Path,
    out_dir: str | Path,
    checklist: str | None = None,
    workflow: dict | None = None,
    progress: Callable[..., None] | None = None,
) -> Path:
    src = Path(path)
    if not src.exists():
        raise FileNotFoundError(src)

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    _progress(progress, "parse", "正在解析论文", 10)
    ir = read_docx(src)
    _progress(progress, "parse_complete", "论文解析完成", 24)
    doc_type = detect_doc_type(ir)
    cl = load_checklist(checklist)
    items = cl.items_for(doc_type)

    # 确定性预检
    _progress(progress, "deterministic", "正在执行确定性检查", 34)
    findings = apply_gate(ir, run_all(ir))
    _progress(progress, "deterministic_complete", "确定性检查完成", 48, findings=len(findings))

    (out / "outline.md").write_text(_outline(ir), encoding="utf-8")
    (out / "deterministic.md").write_text(
        _deterministic(ir, findings), encoding="utf-8"
    )
    (out / "findings.template.json").write_text(
        json.dumps(_template(items), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _progress(progress, "context", "正在切分章节上下文", 62)
    _write_chunks(ir, out / "context")
    _progress(progress, "context_complete", "章节上下文已生成", 70)

    manifest = {
        "source": str(src),
        "hash": ir.source_hash,
        "doc_type": doc_type,
        "stats": {
            "blocks": len(ir.blocks),
            "words": _word_count(ir),
            "headings": sum(1 for b in ir.blocks if b.heading_level > 0),
            "tables": sum(1 for b in ir.blocks if b.rows),
            "figures": len(ir.figures),
            "citations": len(ir.citations),
        },
        "checklist": {
            "name": cl.name,
            "version": cl.version,
            "item_count": len(items),
            "groups": [
                {"id": g.id, "name": g.name, "items": [i.id for i in g.items]}
                for g in cl.groups
            ],
        },
        "deterministic_findings": [f.to_dict() for f in findings if f.gate_passed],
        "files": {
            "outline": "outline.md",
            "deterministic": "deterministic.md",
            "template": "findings.template.json",
            "context_dir": "context/",
            "collaboration_plan": "collaboration.plan.json",
            "collaboration_summary": "collaboration.md",
            "trace": "trace.jsonl",
        },
        "next_step": (
            "读 outline.md 了解结构 → 读 context/sec_*.md 分批审查 "
            "→ 按 findings.template.json 回填 → 运行 paperaudit verify <dir>"
        ),
    }
    _progress(progress, "workflow", "正在生成多 Agent 协作计划", 80)
    plan = write_plan(out, doc_type, manifest["checklist"], validate_workflow(workflow))
    manifest["collaboration"] = {
        "mode": "host-coordinated multi-agent",
        "review_roles": [role["id"] for role in plan.get("roles", [])],
        "agent_orders": {role["id"]: role["order"] for role in plan.get("roles", [])},
        "plan": "collaboration.plan.json",
        "workflow": plan.get("workflow", {}),
        "workflow_hash": hashlib.sha256(json.dumps(plan.get("workflow", {}), ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16],
        "customized": workflow is not None,
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _progress(progress, "manifest", "正在写入审查包清单", 92)
    append_trace(
        out,
        "prepare_complete",
        source_hash=ir.source_hash,
        doc_type=doc_type,
        review_roles=manifest["collaboration"]["review_roles"],
        deterministic_findings=len(manifest["deterministic_findings"]),
    )
    _progress(progress, "complete", "审查包生成完成", 100)
    return out


def _progress(
    callback: Callable[..., None] | None,
    stage: str,
    message: str,
    percent: int,
    **payload,
) -> None:
    if callback is not None:
        callback(stage, message, percent, **payload)


def _word_count(ir: DocumentIR) -> int:
    text = ir.full_text()
    cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    return cjk + len(re.findall(r"[A-Za-z]+", text))


def _outline(ir: DocumentIR) -> str:
    lines = ["# 文档结构", ""]
    for b in ir.blocks:
        if b.heading_level > 0 and b.text.strip():
            indent = "  " * max(0, b.heading_level - 1)
            lines.append(f"{indent}- `{b.id}` {b.text.strip()}")
    return "\n".join(lines) + "\n"


def _deterministic(ir: DocumentIR, findings: list[Finding]) -> str:
    return to_markdown(ir, findings)


def _template(items) -> dict:
    return {
        "_说明": "由宿主 agent 回填。verbatim_quote 必须是原文中能精确匹配的子串，否则会被证据门禁拦截。",
        "findings": [
            {
                "checklist_id": it.id,
                "severity": it.sev,
                "block_ids": [],
                "verbatim_quote": "",
                "rationale": "",
                "suggested_fix": "",
                "confidence": 0.8,
                "reviewer_id": "",
                "owner_skill": "paper_audit",
                "needs_author_decision": True,
            }
            for it in items[:3]
        ],
        "_item_count": len(items),
    }


def _write_chunks(ir: DocumentIR, ctx_dir: Path) -> None:
    """按一级/二级标题切分原文，便于宿主分批读取（省 token、可并行）。"""
    ctx_dir.mkdir(parents=True, exist_ok=True)
    for f in ctx_dir.glob("*.md"):
        f.unlink()

    sections: list[tuple[str, list[Block]]] = []
    cur_title = "front"
    cur: list[Block] = []
    for b in ir.blocks:
        if b.heading_level and b.heading_level <= 2 and b.text.strip():
            if cur:
                sections.append((cur_title, cur))
            cur_title = re.sub(r"[^\w\u4e00-\u9fff]+", "_", b.text.strip())[:40]
            cur = [b]
        else:
            cur.append(b)
    if cur:
        sections.append((cur_title, cur))

    for i, (title, blocks) in enumerate(sections, 1):
        buf: list[str] = [f"# {title}", ""]
        size = 0
        for b in blocks:
            if b.is_bibliography:
                continue
            tag = f"[`{b.id}`] "
            text = b.text.strip()
            if not text:
                continue
            if b.rows:
                text = "\n".join(" | ".join(r) for r in b.rows)
            buf.append(tag + text)
            buf.append("")
            size += len(text)
        (ctx_dir / f"sec_{i:02d}.md").write_text("\n".join(buf), encoding="utf-8")
