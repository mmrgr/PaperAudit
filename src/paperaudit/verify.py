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

from paperaudit.checklist import load as load_checklist
from paperaudit.collaboration import append_trace
from paperaudit.ingest import read_docx
from paperaudit.models import Finding, IssueType, Severity
from paperaudit.reporting import gate_finding, to_markdown
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
    ir = read_docx(source) if source and Path(source).exists() else None

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
        f = Finding(
            id=f"L{i:03d}",
            issue_type=_map_type(r),
            severity=_SEV.get(str(r.get("severity", "minor")).lower(), Severity.MINOR),
            confidence=float(r.get("confidence", 0.8) or 0.8),
            block_ids=list(r.get("block_ids") or []),
            verbatim_quote=str(r.get("verbatim_quote") or ""),
            rationale=str(r.get("rationale") or ""),
            checklist_id=str(r.get("checklist_id") or ""),
            reviewer_id=str(r.get("reviewer_id") or r.get("agent_role") or ""),
            source="llm",
            needs_author_decision=True,
            suggested_fix=str(r.get("suggested_fix") or ""),
            owner_skill=str(r.get("owner_skill") or "paper_audit"),
        )
        findings.append(f)

    _progress(progress, "gate", "正在执行引用证据门禁", 42, candidate_findings=len(findings))

    if ir is not None:
        for f in findings:
            gate_finding(ir, f, allow_empty=False)

    # 与确定性结果合并（id 在下方统一重编号）
    for d in manifest.get("deterministic_findings", []):
        findings.append(_from_dict(d))

    findings = _dedupe(findings)
    _progress(progress, "dedupe", "正在去重并排序审查意见", 68, findings=len(findings))
    findings.sort(key=lambda x: (_sev_rank(x.severity), -x.confidence))
    for i, f in enumerate(findings, 1):
        f.id = f"F{i:03d}"

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
        collaboration=collaboration,
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
    }


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
    cid = str(r.get("checklist_id") or "").upper()
    mapping = {
        "C": IssueType.CITATION_MISMATCH,
        "X": IssueType.NUMERIC_INCONSISTENCY,
        "D": IssueType.NUMERIC_INCONSISTENCY,
        "F": IssueType.CROSSREF_BROKEN,
        "S": IssueType.STRUCTURE_ISSUE,
        "A": IssueType.ARGUMENT_GAP,
        "M": IssueType.METHOD_GAP,
        "L": IssueType.CLARITY,
    }
    return mapping.get(cid[:1], IssueType.OTHER)


def _sev_rank(s: Severity) -> int:
    return {"major": 0, "minor": 1, "nit": 2}.get(s.value, 3)


def _from_dict(d: dict) -> Finding:
    return Finding(
        id=str(d.get("id", "")),
        issue_type=IssueType(str(d.get("issue_type", "other"))),
        severity=_SEV.get(str(d.get("severity", "minor")), Severity.MINOR),
        confidence=float(d.get("confidence", 1.0)),
        block_ids=list(d.get("block_ids") or []),
        verbatim_quote=str(d.get("verbatim_quote") or ""),
        rationale=str(d.get("rationale") or ""),
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
        gate_passed=bool(d.get("verdict", "confirmed") != "unverifiable"),
        needs_author_decision=bool(d.get("needs_author_decision", False)),
    )


def _plain_md(findings, confirmed, rejected) -> str:
    lines = ["# PaperAudit 审查结果", "", f"共 {len(findings)} 项，确认 {len(confirmed)} 项。", ""]
    for f in confirmed:
        lines += [f"## {f.id} · {f.issue_type.value}", "", f"- **位置**：{', '.join(f.block_ids)}", f"- **依据**：{f.rationale}", ""]
    if rejected:
        lines += ["## 未通过证据门禁", ""]
        for f in rejected:
            lines.append(f"- {f.id}：{f.rationale[:100]}（{f.gate_reason}）")
    return "\n".join(lines)
