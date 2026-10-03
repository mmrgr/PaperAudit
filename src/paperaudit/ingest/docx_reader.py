"""DOCX → DocumentIR。

实现约束（来自 ~/.workbuddy/skills/docx-section-revision/SKILL.md 的实战教训）：
1. 必须遍历 document.element.body 并按 tag 分流 p / tbl，
   Document.paragraphs 会跳过表格，导致编号与交叉引用检查错位。
2. 段落文本需跨 run 拼接（同一段常因加粗被切成多个 run）。
3. 锚点用 block_id（body 序）而非字符偏移 —— 偏移在插入后会整体位移。
4. 中文标点：文档里是 “ ”(U+201C/201D)、—(U+2014)，不是 ASCII。
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import docx
from docx.table import Table
from docx.text.paragraph import Paragraph

from paperaudit.models import (
    Block,
    BlockKind,
    CitationEntry,
    CitationMark,
    DocumentIR,
    FigureRef,
    NumericEntity,
)

_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

# 标题层级：兼容 "Heading 1" 与中文 "标题 1"
_HEADING_RE = re.compile(r"(?:Heading|标题)\s*(\d+)", re.I)

# 正文引用标记：[1] / [2,3] / [1-3] / [1—3] / [1–3]
_CITE_MARK_RE = re.compile(r"\[(\d[\d\s,;–—\-]*)\]")

# 参考文献条目：[1] xxx
_BIB_ENTRY_RE = re.compile(r"^\s*\[(\d+)\]\s*(.+)$")

# 题注：图3-1 / 表3-1 / Figure 1 / Table 1
_CAPTION_RE = re.compile(r"^\s*(图|表|Figure|Table|Fig\.?)\s*(\d+(?:[-.]\d+)*)\s*[.、:：]?\s*(.*)$", re.I)

# 数字实体：12.3% / 4.8 points / 0.05
_NUM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(%|个百分点|points|point|倍|倍率|%)?")

# 参考文献标题：允许编号前缀（"9 参考文献"、"第9章 参考文献"、"References"）
_BIB_HEADING = re.compile(
    r"^\s*(?:第[一二三四五六七八九十\d]+[章节部分])?\s*(?:\d+(?:[.\-]\d+)*)?[\s、.]*"
    r"(参考文献|References|Bibliography)\s*$",
    re.I,
)


def read_docx(path: str | Path) -> DocumentIR:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)

    document = docx.Document(str(path))
    body = document.element.body

    source_hash = hashlib.sha256(path.read_bytes()).hexdigest()[:16]

    blocks: list[Block] = []
    p_idx = 0
    t_idx = 0
    section_stack: list[str] = []
    in_biblio = False

    for el in body.iterchildren():
        tag = el.tag.split("}")[-1]

        if tag == "p":
            para = Paragraph(el, document)
            text = para.text  # 已跨 run 拼接
            style = para.style.name if para.style is not None else ""

            m = _HEADING_RE.search(style)
            is_heading = bool(m) and bool(text.strip())

            if is_heading:
                level = int(m.group(1))
                if _BIB_HEADING.match(text.strip()):
                    in_biblio = True
                elif level <= 3:
                    # 维护章节路径
                    section_stack = section_stack[: level - 1]
                    section_stack.append(text.strip())
                    in_biblio = False

            kind = BlockKind.HEADING if is_heading else BlockKind.PARAGRAPH
            p_idx += 1
            blocks.append(
                Block(
                    id=f"p_{p_idx:04d}",
                    kind=kind,
                    text=text,
                    section_path=list(section_stack),
                    style_name=style,
                    heading_level=int(m.group(1)) if is_heading else 0,
                    is_bibliography=in_biblio and not is_heading,
                )
            )

        elif tag == "tbl":
            table = Table(el, document)
            rows = []
            for row in table.rows:
                rows.append([c.text.strip() for c in row.cells])
            flat = " ".join(" ".join(r) for r in rows)
            t_idx += 1
            blocks.append(
                Block(
                    id=f"t_{t_idx:04d}",
                    kind=BlockKind.TABLE,
                    text=flat,
                    section_path=list(section_stack),
                    rows=rows,
                    is_bibliography=in_biblio,
                )
            )

    doc = DocumentIR(
        doc_id=path.stem,
        source_path=str(path),
        source_hash=source_hash,
        blocks=blocks,
    )
    _extract_citations(doc)
    _extract_figures(doc)
    _extract_numerics(doc)
    return doc


def _extract_citations(doc: DocumentIR) -> None:
    """分离「正文引用标记」与「参考文献条目」。

    必须先精确定位参考文献区，否则会把目录里的"参考文献"误当正文起点。
    """
    for b in doc.blocks:
        if b.is_bibliography:
            m = _BIB_ENTRY_RE.match(b.text.strip())
            if m:
                doc.citations.append(
                    CitationEntry(
                        index=int(m.group(1)),
                        key=f"[{m.group(1)}]",
                        raw=b.text.strip(),
                        block_id=b.id,
                    )
                )
            continue

        # 正文：扫描引用标记
        if b.kind in (BlockKind.HEADING, BlockKind.PARAGRAPH, BlockKind.TABLE):
            for m in _CITE_MARK_RE.finditer(b.text):
                if _is_year_like(m.group(1)):
                    continue  # [2026] 这类是年份，不是引用编号
                doc.citation_marks.append(
                    CitationMark(
                        raw=m.group(0),
                        key=m.group(1).strip(),
                        block_id=b.id,
                        char_range=(m.start(), m.end()),
                    )
                )


def _is_year_like(key: str) -> bool:
    """排除被方括号包裹的年份，如 [2026]、[2023]。

    这类标记常被误判为引用编号，会把大量文献错报成「引而未录」。
    """
    nums = re.findall(r"\d+", key)
    return len(nums) == 1 and len(nums[0]) == 4 and 1900 <= int(nums[0]) <= 2100


def _split_mark(key: str) -> list[int]:
    """把 '2,3' / '1-3' / '1—3' / '1 3' 展开成具体编号列表。

    区间必须展开，否则 12/13/14 会被误判为「未引用」。
    """
    nums: list[int] = []
    for part in re.split(r"[,;]", key):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"^(\d+)\s*[–—\-]\s*(\d+)$", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if a > b:
                a, b = b, a
            nums.extend(range(a, b + 1))
        else:
            for n in re.findall(r"\d+", part):
                nums.append(int(n))
    return nums


def _extract_figures(doc: DocumentIR) -> None:
    for b in doc.blocks:
        if b.kind not in (BlockKind.PARAGRAPH, BlockKind.CAPTION):
            continue
        m = _CAPTION_RE.match(b.text.strip())
        if not m:
            continue
        raw_kind = m.group(1).lower()
        kind = "figure" if raw_kind in ("图", "figure", "fig.") else "table"
        doc.figures.append(
            FigureRef(
                kind=kind,
                label=m.group(2),
                caption=b.text.strip(),
                block_id=b.id,
            )
        )


def _extract_numerics(doc: DocumentIR) -> None:
    for b in doc.blocks:
        if b.is_bibliography:
            continue
        for m in _NUM_RE.finditer(b.text):
            try:
                val = float(m.group(1))
            except ValueError:
                continue
            unit = (m.group(2) or "").strip()
            ctx = b.text[max(0, m.start() - 30) : m.end() + 30]
            doc.numerics.append(
                NumericEntity(
                    raw=m.group(0),
                    value=val,
                    unit=unit,
                    context=ctx,
                    block_id=b.id,
                    char_range=(m.start(), m.end()),
                )
            )
