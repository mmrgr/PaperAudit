"""交叉引用检查：正文提到「图3-1」「表2」是否真有对应题注。

注意：编号在多处被引用（定义处 + 正文多处），改一处定义必须同步全部引用，
本检测器只负责「引用是否成立」，不负责改写。
"""

from __future__ import annotations

import re

from paperaudit.models import BlockKind, DocumentIR, Finding, IssueType, Severity

# 正文中的图/表引用：图3-1、表2、Figure 1、Table 2
_REF_RE = re.compile(r"(图|表|Figure|Table|Fig\.)[\s ]*(\d+(?:[-.]\d+)*)", re.I)


def check(doc: DocumentIR) -> list[Finding]:
    labels = _known_labels(doc)

    broken: dict[str, list[tuple[str, str]]] = {}
    for b in doc.blocks:
        if b.kind not in (BlockKind.PARAGRAPH, BlockKind.HEADING, BlockKind.TABLE):
            continue
        if b.is_bibliography:
            continue
        # A caption is a definition, rather than a body reference.  In
        # particular, an empty caption ("图1" with no description) must not
        # make the corresponding body reference appear valid.
        if _is_caption_only(b.text):
            continue
        for m in _REF_RE.finditer(b.text):
            raw_kind = m.group(1).lower()
            kind = "figure" if raw_kind in ("图", "figure", "fig.") else "table"
            label = m.group(2).replace(".", "-")
            key = f"{kind}:{label}"
            if key not in labels:
                # Keep the exact source spelling (Figure/Table versus 图/表)
                # so the evidence gate can verify the quote in multilingual
                # manuscripts.
                broken.setdefault(key, []).append((b.id, m.group(0)))

    if not broken:
        return []

    items = sorted(broken.items())
    detail = "、".join(f"{'图' if k.split(':')[0] == 'figure' else '表'}{k.split(':')[1]}" for k, _ in items)
    return [
        Finding(
            id="",
            issue_type=IssueType.CROSSREF_BROKEN,
            severity=Severity.MAJOR,
            confidence=0.9,
            block_ids=[bid for _, matches in items for bid, _ in matches],
            verbatim_quote="、".join(dict.fromkeys(raw for _, matches in items for _, raw in matches)),
            rationale=f"正文引用了 {len(items)} 个不存在对应题注的图表编号：{detail}。",
            evidence_refs=[k for k, _ in items],
            checklist_id="pre-submission/consistency",
            source="deterministic",
        )
    ]


def _known_labels(doc: DocumentIR) -> set[str]:
    """收集文档中已定义的图表编号。

    题注形如「图3-1 …」「表3-2 …」；也接受表格上方的独立题注段。
    """
    known: set[str] = set()
    # Keep the structured figure inventory authoritative when a caller builds
    # a DocumentIR without retaining the original caption blocks.
    for figure in doc.figures:
        caption = (figure.caption or "").strip()
        if _caption_has_description(caption):
            known.add(f"{figure.kind}:{figure.label.replace('.', '-')}")

    for b in doc.blocks:
        if b.kind is BlockKind.TABLE:
            continue
        text = b.text.strip()
        m = re.match(r"^\s*(图|表|Figure|Table|Fig\.)[\s ]*(\d+(?:[-.]\d+)*)", text, re.I)
        if m:
            # A bare label is not a usable caption.  Treating it as a
            # definition was the source of a false negative for documents
            # whose figure caption consisted only of "图1".
            if not _caption_has_description(text):
                continue
            raw = m.group(1).lower()
            kind = "figure" if raw in ("图", "figure", "fig.") else "table"
            known.add(f"{kind}:{m.group(2).replace('.', '-')}")
    return known


def _caption_has_description(text: str) -> bool:
    """Return whether *text* contains a label followed by caption prose."""
    m = re.match(
        r"^\s*(图|表|Figure|Table|Fig\.)[\s ]*(\d+(?:[-.]\d+)*)\s*[.、:：]?\s*(.*)$",
        text or "",
        re.I,
    )
    return bool(m and m.group(3).strip())


def _is_caption_only(text: str) -> bool:
    """Identify a paragraph that is only a caption label (possibly empty)."""
    return bool(
        re.match(
            r"^\s*(图|表|Figure|Table|Fig\.)[\s ]*\d+(?:[-.]\d+)*\s*[.、:：]?\s*$",
            text or "",
            re.I,
        )
    )
