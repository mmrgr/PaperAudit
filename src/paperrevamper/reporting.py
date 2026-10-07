"""报告与轨迹输出。"""

from __future__ import annotations

import json
import re
from pathlib import Path

from paperrevamper.models import DocumentIR, Finding, Severity, Verdict

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
        # Keep word boundaries intact.  Removing all whitespace would let a
        # fabricated ``samplesize was10`` quote match ``sample size was 10``.
        return re.sub(r"\s+", " ", s or "").strip()

    # A detector failure is a diagnostic, never an empty-quote finding.  Keep
    # this guard here because deterministic callers intentionally use
    # ``allow_empty=True`` for legitimate absence checks; without the guard a
    # detector exception could be promoted to a confirmed finding on rerun.
    if finding.gate_reason == "detector-error":
        finding.gate_passed = False
        finding.verdict = Verdict.UNVERIFIABLE
        return finding

    if not finding.verbatim_quote:
        finding.gate_passed = allow_empty
        finding.gate_reason = "no-quote" if allow_empty else "missing-quote"
        if not allow_empty:
            finding.verdict = Verdict.UNVERIFIABLE
        return finding

    if finding.block_ids:
        blocks = [doc.block_by_id(bid) for bid in finding.block_ids]
        if any(block is None for block in blocks):
            finding.gate_passed = False
            finding.gate_reason = "block-not-found"
            finding.verdict = Verdict.UNVERIFIABLE
            finding.confidence = min(finding.confidence, 0.3)
            return finding
        candidate_blocks = list(blocks)
    else:
        candidate_blocks = list(doc.blocks)
    hay_n = [norm(block.text) for block in candidate_blocks]

    # Disjoint snippets are allowed only when a deterministic detector opts
    # into that mode explicitly.  Model findings must match one contiguous
    # normalized span in the cited blocks.
    if str(getattr(finding, "quote_mode", "contiguous")) == "disjoint_parts":
        parts = [p for p in _SPLIT_QUOTE.split(finding.verbatim_quote) if p.strip()]
        block_texts = hay_n
        hits = sum(1 for p in parts if any(norm(p) in text for text in block_texts))
        if parts and hits == len(parts):
            finding.gate_passed = True
            finding.gate_reason = f"all-parts-match {hits}/{len(parts)}"
            return finding
        finding.gate_passed = False
        finding.gate_reason = f"partial-match {hits}/{len(parts)}"
        finding.verdict = Verdict.UNVERIFIABLE
        finding.confidence = min(finding.confidence, 0.3)
        return finding
    if any(norm(finding.verbatim_quote) in block_text for block_text in hay_n):
        finding.gate_passed = True
        finding.gate_reason = "exact-match"
        return finding

    finding.gate_passed = False
    finding.gate_reason = "quote-not-found"
    finding.verdict = Verdict.UNVERIFIABLE
    finding.confidence = min(finding.confidence, 0.3)
    return finding


def _assign_ids(findings: list[Finding]) -> None:
    # ``verify`` assigns the presentation IDs once after aggregation.  Do not
    # renumber them while rendering the report: decisions and graph relations
    # already refer to that run's display IDs, while the stable ``uid`` carries
    # identity across later re-runs.
    if findings and all(str(f.id or "").startswith("F") for f in findings):
        return
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
        "privacy": "匿名与隐私",
    }
    recorded = doc.metadata.get("detectors_run") if isinstance(doc.metadata, dict) else None
    if isinstance(recorded, list):
        ran = set(str(name) for name in recorded) & set(checks)
    else:
        # Backward-compatible fallback for callers that construct findings
        # manually instead of running evidence.run_all first.
        ran = set()
        for finding in findings:
            key = (finding.checklist_id or "").split("/")[-1]
            if key in checks:
                ran.add(key)

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
        "# PaperRevamper 审查报告",
        "",
        f"**文件**：`{Path(doc.source_path).name}`  ",
        f"**文档指纹**：`{doc.source_hash}`  ",
        f"**段落/表格块数**：{len(doc.blocks)}  ",
        f"**检出问题**：{len(confirmed)} 项（另有 {len(unver)} 项未通过证据门禁）",
        "",
        f"> {_provenance_line(doc, findings)}",
        "> 标注「需你确认」的条目为启发式判断，可能存在误报。",
        "",
    ]
    warnings = list((doc.metadata or {}).get("warnings", []) or [])
    if (doc.metadata or {}).get("scan_likely") and "pdf_text_unavailable_scan_likely" not in warnings:
        warnings.append("pdf_text_unavailable_scan_likely")
    if warnings:
        lines += ["## 解析警告", "", "- " + "；".join(str(item) for item in warnings), ""]

    if not confirmed:
        lines.append("未发现问题。")
    else:
        for sev in (Severity.MAJOR, Severity.MINOR, Severity.NIT):
            group = [f for f in confirmed if f.severity is sev]
            if not group:
                continue
            lines += [f"## {_SEV_CN[sev]}（{len(group)}）", ""]
            for f in group:
                locations: list[str] = []
                for block_id in f.block_ids[:5]:
                    block = doc.block_by_id(block_id)
                    if block is not None and block.page is not None:
                        locations.append(f"{block_id} · p.{block.page}")
                    else:
                        locations.append(block_id)
                loc = "、".join(locations) if locations else "—"
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
                if f.support > 1 or f.reviewer_ids:
                    reviewers = ", ".join(f.reviewer_ids) or "deterministic"
                    lines.append(f"- **独立支持**：{f.support}；来源：{reviewers}")
                if f.related:
                    lines.append(f"- **关联意见**：{', '.join(f.related[:12])}")
                lines.append(f"- **检测方式**：{f.source} · 置信度 {f.confidence:.2f}")
                lines.append("")

    if unver:
        lines += ["## 未通过证据门禁", ""]
        lines.append("以下条目无法在原文中定位到所引内容，仅作记录，不计入确认问题：")
        lines.append("")
        for f in unver:
            lines.append(f"- {f.id} · {f.issue_type.value}：{f.rationale[:120]}")

    return "\n".join(lines)


def _provenance_line(doc: DocumentIR, findings: list[Finding]) -> str:
    """Describe the actual finding sources without making a false claim.

    ``verify`` merges deterministic and host-LLM findings before rendering the
    report.  The old fixed sentence therefore became incorrect as soon as a
    semantic reviewer contributed a finding.
    """
    sources = {str(f.source or "").strip().lower() for f in findings}
    sources.discard("")
    deterministic = "deterministic" in sources or bool(
        isinstance(doc.metadata, dict) and doc.metadata.get("detectors_run")
    )
    llm = bool(sources & {"llm", "language_model", "host_agent"})
    if deterministic and llm:
        return "本报告包含确定性检查与语言模型审查结果；所有条目均经过证据门禁。"
    if llm:
        return "本报告包含语言模型审查结果；所有条目均经过证据门禁。"
    if deterministic:
        return "本报告由确定性检查生成，未调用语言模型。"
    return "本报告未标注审查来源；条目仍须以证据门禁结果为准。"


def to_json(doc: DocumentIR, findings: list[Finding]) -> str:
    _assign_ids(findings)
    return json.dumps(
        {
            "document": {
                "path": doc.source_path,
                "hash": doc.source_hash,
                "format": (doc.metadata or {}).get("format", "docx"),
                "blocks": len(doc.blocks),
                "citations": len(doc.citations),
                "citation_marks": len(doc.citation_marks),
                "figures": len(doc.figures),
                "block_anchors": {
                    block.id: {
                        "page": block.page,
                        "bbox": list(block.bbox) if block.bbox is not None else None,
                    }
                    for block in doc.blocks
                    if block.page is not None or block.bbox is not None
                },
                "metadata": doc.metadata or {},
            },
            "coverage": coverage(doc, findings),
            "findings": [f.to_dict() for f in findings],
        },
        ensure_ascii=False,
        indent=2,
    )
