"""结构检查：章节编号连续性与必需章节。

清单可外部导入（PLAN_v2 §4.4）——检测什么由清单决定，不由代码写死。
"""

from __future__ import annotations

import re

from paperrevamper.models import Block, DocumentIR, Finding, IssueType, Severity

# 章节编号：1 / 1.1 / 1.1.1 / 第一章
_NUM_RE = re.compile(r"^(\d+(?:[.\-]\d+)*)[\s、.]+")
_CN_NUM_RE = re.compile(r"^第([一二三四五六七八九十]+)[章节部分]")

_CN_DIGIT = {c: i + 1 for i, c in enumerate("一二三四五六七八九十")}

# 通用学术文档必需章节（可被测清单覆盖）
_REQUIRED = [
    (r"研究背景|背景|引言|绪论|Introduction", "研究背景/引言"),
    (r"研究现状|文献综述|Related Work|国内外", "研究现状/文献综述"),
    (r"研究内容|研究方法|主要内容|方案|Method", "研究内容与方法"),
    (r"参考文献|References", "参考文献"),
]


def check(doc: DocumentIR) -> list[Finding]:
    out: list[Finding] = []
    out.extend(_numbering(doc))
    out.extend(_required_sections(doc))
    return out


def _numbering(doc: DocumentIR) -> list[Finding]:
    """同级标题编号应连续（1,2,3…或 1.1,1.2…）。"""
    out: list[Finding] = []
    heads = [b for b in doc.blocks if b.heading_level > 0 and b.text.strip()]
    if len(heads) < 3:
        return out

    # 按层级分组
    by_level: dict[int, list[tuple[list[int], Block]]] = {}
    for b in heads:
        parsed = _parse_num(b.text.strip())
        if parsed:
            nums, level_hint = parsed
            lv = b.heading_level or level_hint
            by_level.setdefault(lv, []).append((nums, b))

    for lv, items in sorted(by_level.items()):
        # 按父编号分组检查
        groups: dict[tuple[int, ...], list[tuple[int, Block]]] = {}
        for nums, b in items:
            parent = tuple(nums[:-1]) if len(nums) > 1 else ()
            groups.setdefault(parent, []).append((nums[-1], b))

        for parent, seq in sorted(groups.items(), key=lambda kv: _key(kv[0])):
            if len(seq) < 2:
                continue
            nums = [n for n, _ in seq]
            if nums == list(range(nums[0], nums[0] + len(nums))):
                continue
            gaps = _find_gaps(nums)
            if not gaps:
                continue
            prefix = ".".join(str(p) for p in parent)
            label = f"{prefix}." if prefix else ""
            # quote 必须是真实原文，否则会被证据门禁拦截
            # （门禁要求 verbatim_quote 能在原文精确匹配）
            quote = "、".join(b.text.strip()[:24] for _, b in seq[:6])
            out.append(
                Finding(
                    id="",
                    issue_type=IssueType.STRUCTURE_ISSUE,
                    severity=Severity.MINOR,
                    confidence=0.9,
                    block_ids=[b.id for _, b in seq],
                    verbatim_quote=quote,
                    quote_mode="disjoint_parts",
                    rationale=f"{'第 ' + str(lv) + ' 级' if lv else ''}标题编号在 {label or '顶层'} "
                    f"下不连续，缺失 {gaps}（现有 {nums}）。"
                    "注意：编号在多处被引用，改动需全文同步。",
                    checklist_id="pre-submission/structure",
                    source="deterministic",
                )
            )
    return out


def _required_sections(doc: DocumentIR) -> list[Finding]:
    heads = [b.text.strip() for b in doc.blocks if b.heading_level > 0]
    if not heads:
        return []
    joined = "\n".join(heads)
    missing = [name for pat, name in _REQUIRED if not re.search(pat, joined, re.I)]
    if not missing:
        return []
    return [
        Finding(
            id="",
            issue_type=IssueType.STRUCTURE_ISSUE,
            severity=Severity.MAJOR,
            confidence=0.7,
            block_ids=[b.id for b in doc.blocks if b.heading_level == 1][:1],
            # These are absent sections, so there is no source substring to
            # quote. Keep the names as structured evidence instead of a
            # comma-separated pseudo-quote that could pass on one token.
            verbatim_quote="",
            evidence_refs=missing,
            rationale=f"缺少常见必需章节：{'、'.join(missing)}。"
            "（按通用学术文档清单判断；若目标模板不同请导入对应清单。）",
            checklist_id="pre-submission/structure",
            source="deterministic",
            needs_author_decision=True,
        )
    ]


def _parse_num(text: str) -> tuple[list[int], int] | None:
    m = _NUM_RE.match(text)
    if m:
        nums = [int(x) for x in m.group(1).replace("-", ".").split(".") if x.isdigit()]
        return nums, len(nums)
    m = _CN_NUM_RE.match(text)
    if m:
        return [_CN_DIGIT.get(m.group(1), 0)], 1
    return None


def _find_gaps(nums: list[int]) -> list[int]:
    if not nums:
        return []
    full = set(range(nums[0], max(nums) + 1))
    return sorted(full - set(nums))


def _key(parent: tuple[int, ...]) -> tuple:
    return (len(parent), parent)
