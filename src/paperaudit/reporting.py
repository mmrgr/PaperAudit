"""报告与轨迹输出。"""

from __future__ import annotations

import json
import re
from pathlib import Path

from paperaudit.models import DocumentIR, Finding, Severity, Verdict

# 清单型 quote 的分隔符：中文顿号 / 英文逗号
_SPLIT_QUOTE = re.compile(r"[、,]\s*")

_SEV_ORDER = {Severity.MAJOR: 0, Severity.MINOR: 1, Severity.NIT: 2}
_SEV_CN = {Severity.MAJOR: "重要", Severity.MINOR: "次要", Severity.NIT: "细节"}


def apply_gate(doc: DocumentIR, findings: list[Finding]) -> list[Finding]:
    """证据门禁：verbatim_quote 必须能在原文精确匹配。

    规则（PLAN_v2 §四）：允许空白归一化，不允许模糊匹配。
    匹配失败降级为 unverifiable —— 不计入 confirmed，但保留在轨迹中。
    """
    for f in findings:
        gate_finding(doc, f, allow_empty=True)
    return findings


def gate_finding(doc: DocumentIR, finding: Finding, *, allow_empty: bool = True) -> Finding:
    """Apply the evidence gate to one finding.

    ``allow_empty`` is used for deterministic absence checks (for example a
    missing required section). LLM supplied findings must provide a quote.
    A supplied block id is authoritative: an unknown id fails the gate
    instead of silently falling back to a document-wide match.
    """
    def norm(s: str) -> str:
        return re.sub(r"\s+", "", s or "")

    if not finding.verbatim_quote:
        finding.gate_passed = allow_empty
        finding.gate_reason = "no-quote" if allow_empty else "missing-quote"
        if not allow_empty:
            finding.verdict = Verdict.UNVERIFIABLE
        return finding

    if finding.block_ids:
        blocks = [doc.block_by_id(bid) for bid in finding.block_ids]
        if not any(blocks):
            finding.gate_passed = False
            finding.gate_reason = "block-not-found"
            finding.verdict = Verdict.UNVERIFIABLE
            finding.confidence = min(finding.confidence, 0.3)
            return finding
        hay = "\n".join(b.text for b in blocks if b is not None)
    else:
        hay = doc.full_text()
    hay_n = norm(hay)

    # 清单型 quote（"A、B、C"）逐项判定：允许部分误报项，
    # 但只要能定位到其中任一项，该 finding 就算可追溯。
    parts = [p for p in _SPLIT_QUOTE.split(finding.verbatim_quote) if p.strip()]
    if len(parts) > 1:
        hits = sum(1 for p in parts if norm(p) in hay_n)
        if hits:
            finding.gate_passed = True
            finding.gate_reason = f"partial-match {hits}/{len(parts)}"
            finding.confidence = round(finding.confidence * (hits / len(parts)), 2)
            return finding
    elif norm(finding.verbatim_quote) in hay_n:
        finding.gate_passed = True
        finding.gate_reason = "exact-match"
        return finding

    finding.gate_passed = False
    finding.gate_reason = "quote-not-found"
    finding.verdict = Verdict.UNVERIFIABLE
    finding.confidence = min(finding.confidence, 0.3)
    return finding


def _assign_ids(findings: list[Finding]) -> None:
    confirmed = [f for f in findings if f.verdict is not Verdict.UNVERIFIABLE]
    confirmed.sort(key=lambda f: (_SEV_ORDER.get(f.severity, 3), -f.confidence))
    for i, f in enumerate(confirmed, 1):
        f.id = f"F{i:03d}"
    for i, f in enumerate(
        [x for x in findings if x.verdict is Verdict.UNVERIFIABLE], len(confirmed) + 1
    ):
        f.id = f"F{i:03d}"


def coverage(doc: DocumentIR, findings: list[Finding]) -> dict:
    """清单覆盖率 —— 报告必须包含（PLAN_v2 §四：瓶颈在定位，覆盖率要可审计）。"""
    checks = {
        "structure": "结构完整性",
        "citations": "引用规范性",
        "crossref": "图表交叉引用",
        "terminology": "术语与缩写",
        "numbers": "数字一致性",
    }
    ran = set(checks)

    def _check_of(finding: Finding) -> str:
        cid = finding.checklist_id or ""
        return cid.split("/")[-1] if "/" in cid else "other"

    by_check: dict[str, int] = {}
    for f in findings:
        key = _check_of(f)
        by_check[key] = by_check.get(key, 0) + 1

    return {
        "checks_run": sorted(ran),
        "checks_total": len(checks),
        "coverage": round(len(ran) / len(checks), 3),
        "labels": checks,
        "findings_by_check": by_check,
    }


def to_markdown(doc: DocumentIR, findings: list[Finding]) -> str:
    _assign_ids(findings)
    confirmed = [f for f in findings if f.gate_passed]
    unver = [f for f in findings if not f.gate_passed]

    lines = [
        "# PaperAudit 审查报告",
        "",
        f"**文件**：`{Path(doc.source_path).name}`  ",
        f"**文档指纹**：`{doc.source_hash}`  ",
        f"**段落/表格块数**：{len(doc.blocks)}  ",
        f"**检出问题**：{len(confirmed)} 项（另有 {len(unver)} 项未通过证据门禁）",
        "",
        "> 本报告全部由确定性检查生成，未调用任何语言模型。",
        "> 标注「需你确认」的条目为启发式判断，可能存在误报。",
        "",
    ]

    if not confirmed:
        lines.append("未发现问题。")
    else:
        for sev in (Severity.MAJOR, Severity.MINOR, Severity.NIT):
            group = [f for f in confirmed if f.severity is sev]
            if not group:
                continue
            lines += [f"## {_SEV_CN[sev]}（{len(group)}）", ""]
            for f in group:
                loc = "、".join(f.block_ids[:5]) if f.block_ids else "—"
                flag = " ⚠ 需你确认" if f.needs_author_decision else ""
                lines += [
                    f"### {f.id} · {f.issue_type.value}{flag}",
                    "",
                    f"- **位置**：`{loc}`",
                    f"- **判定依据**：{f.rationale}",
                ]
                if f.verbatim_quote:
                    lines.append(f"- **涉及内容**：{f.verbatim_quote[:200]}")
                if f.evidence_refs:
                    lines.append(f"- **证据**：{', '.join(str(x) for x in f.evidence_refs[:15])}")
                lines.append(f"- **检测方式**：{f.source} · 置信度 {f.confidence:.2f}")
                lines.append("")

    if unver:
        lines += ["## 未通过证据门禁", ""]
        lines.append("以下条目无法在原文中定位到所引内容，仅作记录，不计入确认问题：")
        lines.append("")
        for f in unver:
            lines.append(f"- {f.id} · {f.issue_type.value}：{f.rationale[:120]}")

    return "\n".join(lines)


def to_json(doc: DocumentIR, findings: list[Finding]) -> str:
    _assign_ids(findings)
    return json.dumps(
        {
            "document": {
                "path": doc.source_path,
                "hash": doc.source_hash,
                "blocks": len(doc.blocks),
                "citations": len(doc.citations),
                "citation_marks": len(doc.citation_marks),
                "figures": len(doc.figures),
            },
            "coverage": coverage(doc, findings),
            "findings": [f.to_dict() for f in findings],
        },
        ensure_ascii=False,
        indent=2,
    )
