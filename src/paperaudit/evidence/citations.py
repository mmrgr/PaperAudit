"""引用检查。零 LLM，且比 LLM 更可靠。

依据：CiteAudit (arXiv:2602.23452) —— 250 万篇论文中发现约 14.69 万条
AI 生成的虚假引用；NeurIPS 2025 的 100 条幻觉引用中 66% 完全捏造。
因此引用类问题必须归确定性层，绝不能交给模型判断。
"""

from __future__ import annotations

from paperaudit.ingest.docx_reader import _split_mark
from paperaudit.models import DocumentIR, Finding, IssueType, Severity


def check(doc: DocumentIR) -> list[Finding]:
    findings: list[Finding] = []
    findings.extend(_numbering(doc))
    findings.extend(_unused_and_missing(doc))
    findings.extend(_first_appearance_order(doc))
    return findings


def _mk(ftype, sev, rationale, blocks, quote="", refs=None, conf=1.0) -> Finding:
    return Finding(
        id="",
        issue_type=ftype,
        severity=sev,
        confidence=conf,
        block_ids=blocks or [],
        verbatim_quote=quote,
        rationale=rationale,
        evidence_refs=refs or [],
        checklist_id="pre-submission/citations",
        source="deterministic",
    )


def _numbering(doc: DocumentIR) -> list[Finding]:
    """参考文献条目编号必须 1..N 连续、无重复、无断号。"""
    out: list[Finding] = []
    entries = [e for e in doc.citations if e.index is not None]
    if not entries:
        return out

    idxs = [e.index for e in entries]
    dup = sorted({i for i in idxs if idxs.count(i) > 1})
    if dup:
        out.append(
            _mk(
                IssueType.CITATION_NUMBERING,
                Severity.MAJOR,
                f"参考文献条目编号重复：{dup}。同一编号对应多条文献，正文引用无法唯一定位。",
                [e.block_id for e in entries if e.index in dup],
            )
        )

    if idxs:
        expected = list(range(1, max(idxs) + 1))
        missing = [n for n in expected if n not in set(idxs)]
        if missing:
            out.append(
                _mk(
                    IssueType.CITATION_NUMBERING,
                    Severity.MAJOR,
                    f"参考文献编号断号，缺失：{missing}（共 {max(idxs)} 条，应为 1..{max(idxs)} 连续）。",
                    [entries[0].block_id],
                )
            )
    return out


def _unused_and_missing(doc: DocumentIR) -> list[Finding]:
    """列而未引 / 引而未录。"""
    out: list[Finding] = []
    entries = {e.index: e for e in doc.citations if e.index is not None}
    if not entries:
        return out

    cited: set[int] = set()
    for m in doc.citation_marks:
        cited.update(_split_mark(m.key))

    listed = set(entries.keys())

    unused = sorted(listed - cited)
    if unused:
        out.append(
            _mk(
                IssueType.CITATION_UNUSED,
                Severity.MINOR,
                f"列而未引：参考文献表中 {len(unused)} 条从未在正文出现 —— {_fmt(unused)}。"
                "投稿前需确认是补引还是删除。",
                [entries[i].block_id for i in unused],
                refs=[f"[{i}]" for i in unused],
            )
        )

    missing = sorted(cited - listed)
    if missing:
        out.append(
            _mk(
                IssueType.CITATION_MISSING,
                Severity.MAJOR,
                f"引而未录：正文引用了 {len(missing)} 个在参考文献表中不存在的编号 —— {_fmt(missing)}。",
                [m.block_id for m in doc.citation_marks if set(_split_mark(m.key)) & set(missing)],
                refs=[f"[{i}]" for i in missing],
            )
        )
    return out


def _first_appearance_order(doc: DocumentIR) -> list[Finding]:
    """顺序编码制：编号应按首次出现顺序递增。

    注意：同一编号在后文重复出现是**正常**的，只检查每个编号的首次出现位置。
    """
    entries = {e.index: e for e in doc.citations if e.index is not None}
    if len(entries) < 3:
        return []

    order = [b.id for b in doc.blocks]
    pos = {bid: i for i, bid in enumerate(order)}

    first_seen: dict[int, int] = {}
    for m in doc.citation_marks:
        for n in _split_mark(m.key):
            if n in entries and n not in first_seen:
                first_seen[n] = pos.get(m.block_id, 10**9)

    seq = sorted(first_seen.items(), key=lambda kv: kv[1])
    nums = [n for n, _ in seq]
    if nums == sorted(nums):
        return []

    offenders = [n for i, n in enumerate(nums) if i > 0 and n < nums[i - 1]]
    return [
        _mk(
            IssueType.CITATION_NUMBERING,
            Severity.MINOR,
            f"顺序编码制下编号未按首次出现顺序排列，乱序编号：{_fmt(sorted(offenders))}。"
            "（同一编号在后文重复出现属正常，此处仅指首次出现顺序。）",
            [entries[n].block_id for n in offenders if n in entries],
        )
    ]


def _fmt(nums: list[int], limit: int = 20) -> str:
    shown = nums[:limit]
    s = ", ".join(str(n) for n in shown)
    if len(nums) > limit:
        s += f" …（共 {len(nums)} 个）"
    return s
