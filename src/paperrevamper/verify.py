"""verify：对宿主回填的意见做证据门禁、去重与排序。

门禁规则（docs/PLAN_v2.md §四）：
  verbatim_quote 必须能在原文精确子串匹配（仅允许空白归一化，不允许模糊匹配）。
  匹配失败 → 降级 unverifiable，不计入确认问题。

这是最便宜也最有效的幻觉过滤器 —— 宿主 LLM 编造的「原文引用」
在这一步会被直接拦下，不会进入最终报告。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from paperrevamper.checklist import load as load_checklist
from paperrevamper.collaboration import append_trace
from paperrevamper.ingest import read_document
from paperrevamper.models import (
    Block,
    BlockKind,
    CitationEntry,
    CitationMark,
    DocumentIR,
    FigureRef,
    Finding,
    IssueType,
    NumericEntity,
    Severity,
    Verdict,
)
from paperrevamper.protocol.finding_graph import aggregate_findings, build_graph
from paperrevamper.reporting import gate_finding, to_markdown
_SEV = {"major": Severity.MAJOR, "minor": Severity.MINOR, "nit": Severity.NIT}


def verify(
    run_dir: str | Path,
    checklist: str | None = None,
    progress: Callable[..., None] | None = None,
) -> dict:
    run = Path(run_dir)
    manifest_p = run / "manifest.json"
    if not manifest_p.exists():
        raise FileNotFoundError(f"找不到 manifest.json：{run}。请先运行 prepare。")
    manifest = json.loads(manifest_p.read_text(encoding="utf-8"))
    _progress(progress, "load", "正在加载审查包和 Agent 输出", 10)

    source = manifest.get("source")
    ir, source_state = _load_verification_ir(run, manifest)

    load_checklist(checklist)  # 校验清单存在且可解析；审查结果以 findings 中的 checklist_id 为准。
    raw = _load_raw(run)
    input_inventory = _collaboration_input_inventory(run)
    if input_inventory:
        append_trace(
            run,
            "review_inputs_loaded",
            files=input_inventory["files"],
            roles=input_inventory["roles"],
            adjudicated=input_inventory["adjudicated"],
            candidate_findings=len(raw),
        )

    findings: list[Finding] = []
    for i, r in enumerate(raw, 1):
        schema_error = _validate_raw_finding(r)
        if schema_error:
            findings.append(
                Finding(
                    id=f"L{i:03d}",
                    issue_type=IssueType.OTHER,
                    severity=Severity.NIT,
                    confidence=1.0,
                    rationale=f"Agent finding schema 无效：{schema_error}",
                    reviewer_id=str(r.get("reviewer_id") or r.get("agent_role") or "") if isinstance(r, dict) else "",
                    source="llm",
                    verdict=Verdict.UNVERIFIABLE,
                    gate_passed=False,
                    gate_reason="schema-invalid",
                )
            )
            continue
        f = Finding(
            id=f"L{i:03d}",
            issue_type=_map_type(r),
            severity=_SEV.get(str(r.get("severity", "minor")).lower(), Severity.MINOR),
            confidence=float(r.get("confidence", 0.8) or 0.8),
            block_ids=list(r.get("block_ids") or []),
            verbatim_quote=str(r.get("verbatim_quote") or ""),
            rationale=str(r.get("rationale") or ""),
            evidence_refs=[str(value) for value in (r.get("evidence_refs") or [])],
            checklist_id=str(r.get("checklist_id") or ""),
            reviewer_id=str(r.get("reviewer_id") or r.get("agent_role") or ""),
            reviewer_ids=[str(value) for value in (r.get("reviewer_ids") or [])],
            page_anchors=[str(value) for value in (r.get("page_anchors") or [])],
            char_ranges=[tuple(value) for value in (r.get("char_ranges") or [])],
            source="llm",
            needs_author_decision=True,
            suggested_fix=str(r.get("suggested_fix") or ""),
            owner_skill=str(r.get("owner_skill") or "paper_audit"),
            quote_mode="contiguous",
        )
        findings.append(f)

    _progress(progress, "gate", "正在执行引用证据门禁", 42, candidate_findings=len(findings))

    if ir is not None:
        for f in findings:
            gate_finding(ir, f, allow_empty=False)

    # 与确定性结果合并（id 在下方统一重编号）
    for d in manifest.get("deterministic_findings", []):
        findings.append(_from_dict(d))

    # Collapse only precision-first graph duplicates.  Same issue type alone
    # never merges findings; support/related metadata stays visible for the
    # author and the raw role files remain the audit trail.
    finding_graph = build_graph(findings, min_confidence=0.55)
    findings = aggregate_findings(findings, finding_graph)
    graph_summary = _graph_summary(finding_graph)
    _progress(
        progress,
        "dedupe",
        "正在执行证据关系图聚合与排序",
        68,
        findings=len(findings),
        graph_edges=graph_summary["edges"],
    )
    findings.sort(key=lambda x: (_sev_rank(x.severity), -x.confidence))
    old_ids = {id(finding): str(finding.id or "") for finding in findings}
    for i, f in enumerate(findings, 1):
        f.id = f"F{i:03d}"
    old_to_new = {old_ids[id(finding)]: finding.id for finding in findings if old_ids[id(finding)]}
    for finding in findings:
        # Collapsed duplicate members may no longer have an emitted node.  Do
        # not leave dangling related IDs in the public report.
        finding.related = [
            old_to_new[related]
            for related in (finding.related or [])
            if related in old_to_new and old_to_new[related] != finding.id
        ]

    confirmed = [f for f in findings if f.gate_passed]
    rejected = [f for f in findings if not f.gate_passed]

    report = to_markdown(ir, findings) if ir is not None else _plain_md(findings, confirmed, rejected)
    collaboration = _collaboration_summary(run)
    if collaboration:
        report += "\n\n" + _collaboration_markdown(collaboration)
    (run / "review.md").write_text(report, encoding="utf-8")
    (run / "findings.json").write_text(
        json.dumps(
            {
                "source": source,
                "source_hash": str(manifest.get("hash") or (ir.source_hash if ir is not None else "")),
                "confirmed": [f.to_dict() | {"suggested_fix": getattr(f, "suggested_fix", "")} for f in confirmed],
                "rejected": [f.to_dict() for f in rejected],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    _progress(progress, "report", "正在生成审查报告", 90, confirmed=len(confirmed), rejected=len(rejected))
    append_trace(
        run,
        "verify_complete",
        total=len(findings),
        confirmed=len(confirmed),
        rejected=len(rejected),
        source_state=source_state,
        collaboration=collaboration,
        finding_graph=graph_summary,
    )

    return {
        "status": "ok",
        "run_dir": str(run),
        "total": len(findings),
        "confirmed": len(confirmed),
        "rejected": len(rejected),
        "by_severity": {
            s: sum(1 for f in confirmed if f.severity.value == s) for s in ("major", "minor", "nit")
        },
        "review": str(run / "review.md"),
        "findings": str(run / "findings.json"),
        "collaboration": collaboration,
        "finding_graph": graph_summary,
        "source_state": source_state,
    }


def _load_verification_ir(run: Path, manifest: dict) -> tuple[DocumentIR | None, str]:
    """Load current DOCX only when it matches the prepared hash.

    If the source was moved, deleted, or changed after ``prepare``, use the
    immutable JSON IR snapshot.  This keeps evidence gating reproducible and
    makes a run portable across machines.
    """
    source = manifest.get("source")
    expected_hash = str(manifest.get("hash") or "")
    if source and Path(source).exists():
        parser_options = manifest.get("parser_options", {}) if isinstance(manifest, dict) else {}
        try:
            current = read_document(
                source,
                pdf_parser=str(parser_options.get("pdf_parser", "native")),
                grobid_endpoint=str(parser_options.get("grobid_endpoint") or "") or None,
            )
        except (RuntimeError, ValueError):
            snapshot = _load_ir_snapshot(run / str(manifest.get("files", {}).get("ir_snapshot", "ir_snapshot.json")))
            if snapshot is not None:
                return snapshot, "snapshot-parser-unavailable"
            raise
        if not expected_hash or current.source_hash == expected_hash:
            return current, "source"
        snapshot = _load_ir_snapshot(run / str(manifest.get("files", {}).get("ir_snapshot", "ir_snapshot.json")))
        if snapshot is not None:
            return snapshot, "snapshot-source-hash-mismatch"
        return current, "source-hash-mismatch-no-snapshot"
    snapshot = _load_ir_snapshot(run / str(manifest.get("files", {}).get("ir_snapshot", "ir_snapshot.json")))
    if snapshot is not None:
        return snapshot, "snapshot-source-missing"
    return None, "source-missing-no-snapshot"


def _load_ir_snapshot(path: Path) -> DocumentIR | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if int(data.get("schema_version", 0)) != 1:
            return None
        blocks = [
            Block(
                id=str(row["id"]),
                kind=BlockKind(str(row["kind"])),
                text=str(row.get("text", "")),
                section_path=[str(x) for x in row.get("section_path", [])],
                style_name=str(row.get("style_name", "")),
                heading_level=int(row.get("heading_level", 0)),
                is_bibliography=bool(row.get("is_bibliography", False)),
                rows=[[str(cell) for cell in cells] for cells in row.get("rows", [])],
                page=int(row["page"]) if row.get("page") is not None else None,
                bbox=tuple(float(value) for value in row["bbox"]) if row.get("bbox") else None,
            )
            for row in data.get("blocks", [])
        ]
        return DocumentIR(
            doc_id=str(data.get("doc_id", "snapshot")),
            source_path=str(data.get("source_path", "")),
            source_hash=str(data.get("source_hash", "")),
            blocks=blocks,
            figures=[FigureRef(**{key: str(row.get(key, "")) for key in ("kind", "label", "caption", "block_id")}) for row in data.get("figures", [])],
            citations=[CitationEntry(index=row.get("index"), key=str(row.get("key", "")), raw=str(row.get("raw", "")), block_id=str(row.get("block_id", ""))) for row in data.get("citations", [])],
            citation_marks=[CitationMark(raw=str(row.get("raw", "")), key=str(row.get("key", "")), block_id=str(row.get("block_id", "")), char_range=tuple(row.get("char_range", [0, 0]))) for row in data.get("citation_marks", [])],
            numerics=[NumericEntity(raw=str(row.get("raw", "")), value=float(row.get("value", 0)), unit=str(row.get("unit", "")), context=str(row.get("context", "")), block_id=str(row.get("block_id", "")), char_range=tuple(row.get("char_range", [0, 0]))) for row in data.get("numerics", [])],
            metadata=dict(data.get("metadata", {}) or {}),
        )
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None


def _validate_raw_finding(row: object) -> str:
    """Validate the small JSON boundary before coercing values into Finding.

    This is deliberately dependency-free rather than a full JSON-Schema
    runtime.  Invalid rows remain visible as rejected trace items and cannot
    become a confirmed issue through permissive ``str``/``float`` coercion.
    """
    if not isinstance(row, dict):
        return "必须是对象"
    checklist_id = row.get("checklist_id")
    if not isinstance(checklist_id, str) or not checklist_id.strip():
        return "checklist_id 必须是非空字符串"
    severity = row.get("severity", "minor")
    if not isinstance(severity, str) or severity.lower() not in _SEV:
        return "severity 必须是 major、minor 或 nit"
    confidence = row.get("confidence", 0.8)
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        return "confidence 必须是 0..1 数字"
    if not 0 <= float(confidence) <= 1:
        return "confidence 必须处于 0..1"
    for key in ("block_ids", "char_ranges", "reviewer_ids", "page_anchors", "evidence_refs"):
        if key in row and not isinstance(row[key], list):
            return f"{key} 必须是数组"
    if any(not isinstance(block, str) for block in (row.get("block_ids") or [])):
        return "block_ids 必须只包含字符串"
    for key in ("reviewer_ids", "page_anchors"):
        if any(not isinstance(value, str) for value in (row.get(key) or [])):
            return f"{key} 必须只包含字符串"
    if any(not isinstance(value, str) for value in (row.get("evidence_refs") or [])):
        return "evidence_refs 必须只包含字符串"
    for value in row.get("char_ranges") or []:
        if (
            not isinstance(value, (list, tuple))
            or len(value) != 2
            or any(isinstance(part, bool) or not isinstance(part, int) or part < 0 for part in value)
        ):
            return "char_ranges 必须是非负整数二元数组"
    for key in ("verbatim_quote", "rationale", "suggested_fix", "reviewer_id", "owner_skill"):
        if key in row and row[key] is not None and not isinstance(row[key], str):
            return f"{key} 必须是字符串"
    return ""


def _progress(callback: Callable[..., None] | None, stage: str, message: str, percent: int, **payload) -> None:
    if callback is not None:
        callback(stage, message, percent, **payload)


def _load_raw(run: Path) -> list[dict]:
    # Prefer a host-adjudicated file when present. Otherwise merge the
    # independent role outputs in stable filename order. The legacy single
    # findings.llm.json format remains supported.
    names: list[tuple[Path, str]] = []
    plan = _read_json(run / "collaboration.plan.json", {})
    configured_roles = plan.get("roles", []) if isinstance(plan, dict) else []
    adjudicated_name = "findings.adjudicated.json"
    adjudicated = run / adjudicated_name
    if adjudicated.exists():
        names = [(adjudicated, "skeptical_adjudicator")]
    else:
        configured_files = []
        for configured in configured_roles:
            candidate = run / str(configured.get("findings_file") or f"findings.{configured.get('id')}.json")
            if candidate.exists():
                configured_files.append((candidate, str(configured.get("id") or "host_agent")))
        role_files = sorted(p for p in run.glob("findings.*.json") if p.name not in {"findings.llm.json", "findings.template.json", adjudicated.name})
        names = configured_files or [(p, p.stem.removeprefix("findings.") or "host_agent") for p in role_files]
        legacy = run / "findings.llm.json"
        if legacy.exists():
            names.append((legacy, "host_agent"))

    out: list[dict] = []
    for path, role in names:
        data = json.loads(path.read_text(encoding="utf-8"))
        items = data.get("findings", []) if isinstance(data, dict) else data
        for item in items or []:
            if isinstance(item, dict):
                row = dict(item)
                row.setdefault("reviewer_id", role)
                out.append(row)
    return out


def _read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _collaboration_input_inventory(run: Path) -> dict:
    """Return auditable input provenance without copying manuscript text."""
    plan = _read_json(run / "collaboration.plan.json", {})
    configured_roles = plan.get("roles", []) if isinstance(plan, dict) else []
    adjudicated_name = "findings.adjudicated.json"
    adjudicated = run / adjudicated_name
    if adjudicated.exists():
        return {
            "files": [adjudicated.name],
            "roles": ["skeptical_adjudicator"],
            "adjudicated": True,
        }

    configured_files = []
    for role in configured_roles:
        candidate = run / str(role.get("findings_file") or f"findings.{role.get('id')}.json")
        if candidate.exists():
            configured_files.append((candidate, str(role.get("id") or "host_agent")))
    role_files = sorted(p for p in run.glob("findings.*.json") if p.name not in {"findings.llm.json", "findings.template.json", adjudicated.name})
    pairs = configured_files or [(p, p.stem.removeprefix("findings.") or "host_agent") for p in role_files]
    files = [p.name for p, _ in pairs]
    roles = [role for _, role in pairs]
    legacy = run / "findings.llm.json"
    if legacy.exists():
        files.append(legacy.name)
        roles.append("host_agent")
    return {"files": files, "roles": roles, "adjudicated": False}


def _dedupe(findings: list[Finding]) -> list[Finding]:
    """Remove only exact duplicate observations.

    The key is intentionally conservative: issue type, grounded quote, and
    target blocks must all match. Related findings with different evidence
    remain visible for manual review.
    """
    unique: dict[tuple, Finding] = {}
    for finding in findings:
        key = (
            finding.issue_type.value,
            " ".join(finding.verbatim_quote.split()),
            tuple(sorted(set(finding.block_ids))),
        )
        current = unique.get(key)
        if current is None:
            unique[key] = finding
            continue
        # Prefer deterministic evidence and then the higher-confidence item.
        rank = (current.source != "deterministic", -current.confidence)
        candidate_rank = (finding.source != "deterministic", -finding.confidence)
        if candidate_rank < rank:
            unique[key] = finding
    return list(unique.values())


def _graph_summary(graph) -> dict:
    relations: dict[str, int] = {}
    for edge in graph.edges:
        relations[edge.relation] = relations.get(edge.relation, 0) + 1
    return {
        "nodes": len(graph.nodes),
        "edges": len(graph.edges),
        "relations": dict(sorted(relations.items())),
        "duplicate_clusters": [list(cluster) for cluster in graph.duplicate_clusters],
    }


def _collaboration_summary(run: Path) -> dict:
    plan_path = run / "collaboration.plan.json"
    if not plan_path.exists():
        return {}
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    roles = []
    for role in plan.get("roles", []):
        role_id = role.get("id", "")
        path = run / str(role.get("findings_file") or f"findings.{role_id}.json")
        status = "completed" if path.exists() else "pending"
        count = 0
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            count = len(data.get("findings", [])) if isinstance(data, dict) else len(data)
        roles.append({"id": role_id, "label": role.get("label", ""), "order": int(role.get("order", 1)), "status": status, "findings": count})
    return {
        "mode": "host-coordinated multi-agent",
        "roles": roles,
        "plan": "collaboration.plan.json",
    }


def _collaboration_markdown(summary: dict) -> str:
    lines = [
        "## 多 Agent 协作记录",
        "",
        f"- **模式**：{summary['mode']}",
        "- **顺序**：解析与确定性检查 → 按顺序号依次执行（同序并行）→ 代码门禁与报告",
        "",
        "| 顺序 | 角色 | 状态 | 候选意见 |",
        "|---:|---|---|---:|",
    ]
    for role in summary["roles"]:
        lines.append(f"| {role['order']} | `{role['id']}` | {role['status']} | {role['findings']} |")
    lines += ["", "> 角色之间独立发现；代码负责证据门禁和保守去重。缺失角色结果不会伪装成已完成。"]
    return "\n".join(lines)


def _map_type(r: dict) -> IssueType:
    # Checklist groups contain several materially different checks.  Mapping
    # only by the first letter turned, for example, terminology consistency
    # (X03) into a numeric issue and every citation item into a metadata
    # mismatch.  Keep the mapping explicit so adding a checklist item cannot
    # silently change its meaning.
    cid = str(r.get("checklist_id") or "").strip().upper().split("/")[-1]
    exact = {
        # Structure and argument
        "S01": IssueType.STRUCTURE_ISSUE,
        "S02": IssueType.STRUCTURE_ISSUE,
        "S03": IssueType.STRUCTURE_ISSUE,
        "S04": IssueType.STRUCTURE_ISSUE,
        "A01": IssueType.ARGUMENT_GAP,
        "A02": IssueType.ARGUMENT_GAP,
        "A03": IssueType.ARGUMENT_GAP,
        "A04": IssueType.OVERCLAIM,
        "A05": IssueType.ARGUMENT_GAP,
        "A06": IssueType.OVERCLAIM,
        # Methods and data
        "M01": IssueType.REPRODUCIBILITY_GAP,
        "M02": IssueType.REPRODUCIBILITY_GAP,
        "M03": IssueType.METHOD_GAP,
        "M04": IssueType.METHOD_GAP,
        "M05": IssueType.METHOD_GAP,
        "M06": IssueType.METHOD_GAP,
        "D01": IssueType.STATS_INCONSISTENCY,
        "D02": IssueType.NUMERIC_INCONSISTENCY,
        "D03": IssueType.NUMERIC_INCONSISTENCY,
        "D04": IssueType.NUMERIC_INCONSISTENCY,
        "D05": IssueType.STATS_INCONSISTENCY,
        # Citation checks: each type reflects the question being checked.
        "C01": IssueType.CITATION_MISMATCH,
        "C02": IssueType.CITATION_UNSUPPORTED,
        "C03": IssueType.CITATION_MISSING,
        "C04": IssueType.CITATION_NUMBERING,
        # There is no dedicated enum for age/self-citation policy.  OTHER is
        # safer than claiming a metadata mismatch that was never checked.
        "C05": IssueType.OTHER,
        "C06": IssueType.CITATION_MISSING,
        # Figures, language, and cross-chapter consistency
        "F01": IssueType.CROSSREF_BROKEN,
        "F02": IssueType.CLARITY,
        "F03": IssueType.CLARITY,
        "F04": IssueType.CLARITY,
        "L01": IssueType.TERMINOLOGY_INCONSISTENT,
        "L02": IssueType.TERMINOLOGY_UNDEFINED,
        "L03": IssueType.CLARITY,
        "L04": IssueType.CLARITY,
        "L05": IssueType.CLARITY,
        "X01": IssueType.NUMERIC_INCONSISTENCY,
        "X02": IssueType.ARGUMENT_GAP,
        "X03": IssueType.TERMINOLOGY_INCONSISTENT,
        "X04": IssueType.ARGUMENT_GAP,
    }
    if cid in exact:
        return exact[cid]

    # Preserve a conservative fallback for custom checklists that use the
    # built-in group prefixes but introduce their own item IDs.
    return {
        "C": IssueType.OTHER,
        "X": IssueType.OTHER,
        "D": IssueType.NUMERIC_INCONSISTENCY,
        "F": IssueType.CROSSREF_BROKEN,
        "S": IssueType.STRUCTURE_ISSUE,
        "A": IssueType.ARGUMENT_GAP,
        "M": IssueType.METHOD_GAP,
        "L": IssueType.CLARITY,
    }.get(cid[:1], IssueType.OTHER)


def _sev_rank(s: Severity) -> int:
    return {"major": 0, "minor": 1, "nit": 2}.get(s.value, 3)


def _from_dict(d: dict) -> Finding:
    try:
        verdict = Verdict(str(d.get("verdict", "confirmed")))
    except ValueError:
        verdict = Verdict.UNVERIFIABLE
    return Finding(
        id=str(d.get("id", "")),
        issue_type=IssueType(str(d.get("issue_type", "other"))),
        severity=_SEV.get(str(d.get("severity", "minor")), Severity.MINOR),
        confidence=float(d.get("confidence", 1.0)),
        block_ids=list(d.get("block_ids") or []),
        verbatim_quote=str(d.get("verbatim_quote") or ""),
        rationale=str(d.get("rationale") or ""),
        evidence_refs=[str(value) for value in (d.get("evidence_refs") or [])],
        checklist_id=str(d.get("checklist_id") or ""),
        source=str(d.get("source", "deterministic")),
        reviewer_id=str(d.get("reviewer_id", "")),
        suggested_fix=str(d.get("suggested_fix", "")),
        reviewer_ids=list(d.get("reviewer_ids") or []),
        page_anchors=list(d.get("page_anchors") or []),
        char_ranges=[
            tuple(x)
            for x in (d.get("char_ranges") or [])
            if isinstance(x, (list, tuple)) and len(x) == 2
        ],
        owner_skill=str(d.get("owner_skill", "paper_audit")),
        quote_mode=str(d.get("quote_mode", "contiguous") or "contiguous"),
        uid=str(d.get("uid", "") or ""),
        verdict=verdict,
        gate_passed=bool(d.get("gate_passed", verdict is not Verdict.UNVERIFIABLE)),
        gate_reason=str(d.get("gate_reason", "") or ""),
        needs_author_decision=bool(d.get("needs_author_decision", False)),
        support=max(1, int(d.get("support", 1) or 1)),
        related=[str(value) for value in (d.get("related") or [])],
        relation=str(d.get("relation", "") or ""),
    )


def _plain_md(findings, confirmed, rejected) -> str:
    lines = ["# PaperRevamper 审查结果", "", f"共 {len(findings)} 项，确认 {len(confirmed)} 项。", ""]
    for f in confirmed:
        lines += [f"## {f.id} · {f.issue_type.value}", "", f"- **位置**：{', '.join(f.block_ids)}", f"- **依据**：{f.rationale}", ""]
    if rejected:
        lines += ["## 未通过证据门禁", ""]
        for f in rejected:
            lines.append(f"- {f.id}：{f.rationale[:100]}（{f.gate_reason}）")
    return "\n".join(lines)
