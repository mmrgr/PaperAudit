"""确定性扫描入口。"""

from . import citations, crossref, numbers, structure, terminology

DETECTORS = {
    "structure": structure.check,
    "citations": citations.check,
    "crossref": crossref.check,
    "terminology": terminology.check,
    "numbers": numbers.check,
}


def run_all(doc, only: list[str] | None = None, skip: list[str] | None = None):
    """运行确定性检查。零 LLM 调用。"""
    from paperaudit.models import Finding, IssueType, Severity

    skip = set(skip or [])
    names = only or list(DETECTORS)
    out: list[Finding] = []
    for name in names:
        if name in skip:
            continue
        fn = DETECTORS.get(name)
        if fn is None:
            continue
        try:
            out.extend(fn(doc))
        except Exception as exc:
            out.append(
                Finding(
                    id=f"F-ERR-{name}",
                    issue_type=IssueType.OTHER,
                    severity=Severity.NIT,
                    confidence=1.0,
                    rationale=f"检测器 {name} 执行失败：{exc}",
                    source="deterministic",
                    gate_passed=False,
                    gate_reason="detector-error",
                )
            )
    return out
