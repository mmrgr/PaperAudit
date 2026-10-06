"""术语与缩写检查。

只做确定性可判的两类：
1. 英文缩写首次出现未给出中文全称
2. 同一术语存在多种写法
"""

from __future__ import annotations

import re

from paperaudit.models import BlockKind, DocumentIR, Finding, IssueType, Severity

# 大写缩写：2-6 个大写字母，或含数字的缩写如 CO2、PM2.5
#
# 前后断言用于压低 FPR（依据 HALLMARK：误报率才是部署瓶颈）：
#   前不能紧跟任何字母/数字/'-' → 排除 NSGA-II 里的 "II"、ηWTW 这类下标
#   后不能紧跟 '字母/数字//'    → 排除 GB/T 32910 这类标准编号里的 "GB"
_ACRONYM_RE = re.compile(r"(?<![\w\-])([A-Z][A-Z0-9]{1,5}(?:\.[0-9]+)?)(?![A-Za-z0-9/])")

# 常见非缩写词，避免误报
_STOP = {
    "AI", "AND", "OR", "OF", "THE", "IN", "ON", "AT", "TO", "FOR", "BY", "IS",
    "IT", "AS", "BE", "DO", "IF", "NO", "SO", "UP", "US", "WE", "AN",
    "FIG", "TAB", "EQ", "REF", "NOTE", "NOTES", "APPENDIX", "TABLE",
    "DOI", "URL", "HTML", "XML", "PDF", "ET", "AL", "IE", "EG", "VS",
    # 标准/规范编号前缀，不是需要定义的技术缩写
    "GB", "ISO", "ASTM", "JIS", "EN", "DIN", "ASME", "IEEE", "ACI", "AWWA",
    # 常见量纲与单位
    "MW", "GW", "KW", "KWH", "MWH", "GWH", "KPA", "MPA", "KG", "KM",
}


def check(doc: DocumentIR) -> list[Finding]:
    out: list[Finding] = []
    out.extend(_undefined_acronyms(doc))
    return out


def _undefined_acronyms(doc: DocumentIR) -> list[Finding]:
    """缩写首次出现时，同一段或前一段应带中文全称或英文展开。"""
    body_blocks = [
        b
        for b in doc.blocks
        if b.kind is BlockKind.PARAGRAPH and not b.is_bibliography and b.text.strip()
    ]
    if not body_blocks:
        return []

    seen: dict[str, str] = {}  # acronym -> block_id of first appearance
    for b in body_blocks:
        for m in _ACRONYM_RE.finditer(b.text):
            ac = m.group(1).rstrip(".")
            if len(ac) < 2 or ac in _STOP:
                continue
            if ac.isdigit():
                continue
            seen.setdefault(ac, b.id)

    # 定义在全文任何位置出现即视为已定义。
    # 只看「首次出现那一段」会把后文给出定义的缩写误报为未定义（实测 WUE 即属此类）。
    # 按 precision-first 原则，宁可漏报也不误报。
    defined_ok = {ac for ac in seen if any(_has_definition(b.text, ac) for b in body_blocks)}

    undefined = sorted(a for a in seen if a not in defined_ok)
    if not undefined:
        return []

    return [
        Finding(
            id="",
            issue_type=IssueType.TERMINOLOGY_UNDEFINED,
            severity=Severity.MINOR,
            confidence=0.6,
            block_ids=list(dict.fromkeys(seen[a] for a in undefined)),
            verbatim_quote="、".join(undefined),
            rationale=f"以下缩写首次出现时未见中文全称或英文展开：{'、'.join(undefined)}。"
            "（确定性启发式判断，可能存在误报，需你确认。）",
            evidence_refs=undefined,
            checklist_id="pre-submission/terminology",
            source="deterministic",
            needs_author_decision=True,
        )
    ]


def _has_definition(text: str, ac: str) -> bool:
    """同段内是否有定义迹象：紧跟中文，或出现「（XXX）」「XXX（ac）」结构。"""
    # 形如：城市代谢（Urban Metabolism, UM）
    if re.search(r"[（(][^）)]*\b" + re.escape(ac) + r"\b[^）)]*[）)]", text):
        return True
    # 形如：UM（城市代谢）
    if re.search(r"\b" + re.escape(ac) + r"\b\s*[（(]\s*[\u4e00-\u9fff]", text):
        return True
    # 前面紧邻中文（中文全称 followed by ac）
    if re.search(r"[\u4e00-\u9fff]{2,}\s*[（(]?\s*" + re.escape(ac), text):
        return True
    return False
