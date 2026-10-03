"""数字一致性检查。

设计上刻意保守：只报「同一指标短语在文中出现多个不同数值」，
并标记 needs_author_decision。因为不同情境下同一术语取不同值是正常的，
误报的代价高于漏报（依据 HALLMARK：FPR 才是部署瓶颈）。
"""

from __future__ import annotations

import re
from collections import defaultdict

from paperaudit.models import BlockKind, DocumentIR, Finding, IssueType, Severity

# 「…率为 12.3%」「…达 4.8%」「…约 30 million」
_PATTERN = re.compile(
    r"([\u4e00-\u9fffA-Za-z]{2,10}?(?:率|量|数|值|比|占比|比例|规模|容量|效率|强度|消耗|排放))"
    r"[^0-9]{0,6}(\d+(?:\.\d+)?)\s*(%|个百分点|倍|亿|万)"
)


def check(doc: DocumentIR) -> list[Finding]:
    # Keep the matched source text alongside each value. The evidence gate
    # requires a real substring from the document; joining values such as
    # ``12% / 15%`` fabricates a quote and makes an otherwise useful finding
    # unverifiable.
    values: dict[str, dict[str, list[tuple[str, str]]]] = defaultdict(
        lambda: defaultdict(list)
    )

    for b in doc.blocks:
        if b.kind is not BlockKind.PARAGRAPH or b.is_bibliography:
            continue
        for m in _PATTERN.finditer(b.text):
            term = m.group(1)
            val = f"{m.group(2)}{m.group(3)}"
            values[term][val].append((b.id, m.group(0)))

    out: list[Finding] = []
    for term, variants in values.items():
        if len(variants) < 2:
            continue
        # 只在取值明确冲突时报（>1 种写法）
        shown = " / ".join(sorted(variants))
        blocks = [bid for matches in variants.values() for bid, _ in matches]
        # Anchor the finding to one actual occurrence. The rationale and
        # evidence_refs still describe all conflicting values.
        quote = next(
            (quote for matches in variants.values() for _, quote in matches),
            "",
        )
        out.append(
            Finding(
                id="",
                issue_type=IssueType.NUMERIC_INCONSISTENCY,
                severity=Severity.MINOR,
                confidence=0.5,
                block_ids=blocks[:8],
                verbatim_quote=quote,
                rationale=f"「{term}」在文中出现多个不同取值：{shown}。"
                "可能是不同情境下的正常差异，也可能是抄写错误，需你确认。",
                evidence_refs=[term],
                checklist_id="pre-submission/consistency",
                source="deterministic",
                needs_author_decision=True,
            )
        )
    return out
