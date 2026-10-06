"""确定性扫描入口。"""

from . import citations, crossref, numbers, numeric_facts, privacy, structure, terminology

DETECTORS = {
    "structure": structure.check,
    "citations": citations.check,
    "crossref": crossref.check,
    "terminology": terminology.check,
    # Keep the public detector name stable while using the richer fact graph.
    # ``numbers`` remains importable for callers that rely on the legacy
    # conservative metric scanner.
    "numbers": numeric_facts.check,
    "privacy": privacy.check,
}


def run_all(doc, only: list[str] | None = None, skip: list[str] | None = None):
    """运行确定性检查。零 LLM 调用。"""
    from paperaudit.models import Finding, IssueType, Severity, Verdict

    skip = set(skip or [])
    names = only or list(DETECTORS)
    out: list[Finding] = []
    ran: list[str] = []
    for name in names:
        if name in skip:
            continue
        fn = DETECTORS.get(name)
        if fn is None:
            continue
        try:
            out.extend(fn(doc))
            ran.append(name)
        except Exception as exc:
            out.append(
                Finding(
                    id=f"F-ERR-{name}",
                    issue_type=IssueType.OTHER,
                    severity=Severity.NIT,
                    confidence=1.0,
                    rationale=f"检测器 {name} 执行失败：{exc}",
                    source="deterministic",
                    verdict=Verdict.UNVERIFIABLE,
                    gate_passed=False,
                    gate_reason="detector-error",
                )
            )
    # Keep the execution plan on the IR so reports can show real coverage when
    # callers use --only/--skip instead of assuming every detector ran.
    if hasattr(doc, "metadata"):
        doc.metadata["detectors_run"] = sorted(set(ran))
        doc.metadata["detectors_requested"] = [str(name) for name in names if name not in skip]
    return out
