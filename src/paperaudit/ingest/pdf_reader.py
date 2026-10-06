"""Native PDF -> DocumentIR ingestion.

This adapter keeps page and bounding-box anchors instead of projecting a PDF
through a temporary DOCX.  pdfplumber is optional so the core DOCX workflow
remains lightweight; callers get a clear install hint when PDF support is
requested without the extra dependency.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from paperaudit.models import Block, BlockKind, DocumentIR

from .docx_reader import (
    _BIB_HEADING,
    _extract_citations,
    _extract_figures,
    _extract_numerics,
)

_NUMBERED_HEADING = re.compile(r"^\s*\d+(?:\.\d+)*[.)]?\s+\S")
_CJK_HEADING = re.compile(
    r"^\s*(?:摘要|引言|绪论|背景|方法|结果|讨论|结论|局限性|致谢|参考文献|附录|"
    r"abstract|introduction|background|method(?:s)?|results?|discussion|conclusion|"
    r"limitations?|acknowledg(?:e)?ments?|references|bibliography)\s*[:：]?\s*$",
    re.I,
)


def read_pdf(path: str | Path) -> DocumentIR:
    """Parse a PDF into page-aware blocks and conservative tables.

    The parser intentionally does not claim perfect layout reconstruction.
    It preserves enough geometry for evidence links and leaves unsupported
    semantics visible in DocumentIR.metadata rather than silently converting
    the file to another format.
    """

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise RuntimeError(
            "原生 PDF 解析需要 pdfplumber；请安装 paperaudit[pdf]。"
        ) from exc

    source_hash = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    blocks: list[Block] = []
    page_count = 0
    pages_with_text = 0
    pages_without_text = 0
    text_chars = 0
    p_index = 0
    t_index = 0
    section_stack: list[str] = []
    in_bibliography = False
    pdf_metadata: dict[str, str] = {}

    with pdfplumber.open(str(path)) as pdf:
        page_count = len(pdf.pages)
        for key, value in (pdf.metadata or {}).items():
            clean_key = str(key).lstrip("/")
            if value is not None and str(value).strip():
                pdf_metadata[clean_key] = str(value).strip()

        for page_no, page in enumerate(pdf.pages, 1):
            table_boxes, table_rows = _extract_tables(page)
            lines = _extract_lines(page)
            page_text = " ".join(str(line.get("text", "")) for line in lines).strip()
            if page_text:
                pages_with_text += 1
                text_chars += len(page_text)
            else:
                pages_without_text += 1
            page_blocks: list[tuple[float, Block]] = []

            # Lines covered by a detected table are represented by the table
            # block below, avoiding duplicate numeric/citation facts.
            text_lines = [line for line in lines if not _line_in_tables(line, table_boxes)]
            grouped = _group_lines(text_lines)
            for text, bbox in grouped:
                text = text.strip()
                if not text:
                    continue
                heading, level = _heading_info(text)
                if heading:
                    if _BIB_HEADING.match(text):
                        in_bibliography = True
                    elif level <= 3:
                        section_stack = section_stack[: max(0, level - 1)]
                        section_stack.append(text)
                        in_bibliography = False
                p_index += 1
                block = Block(
                    id=f"p_{p_index:04d}",
                    kind=BlockKind.HEADING if heading else BlockKind.PARAGRAPH,
                    text=text,
                    section_path=list(section_stack),
                    style_name="PDF heading" if heading else "PDF paragraph",
                    heading_level=level if heading else 0,
                    is_bibliography=in_bibliography and not heading,
                    page=page_no,
                    bbox=bbox,
                )
                page_blocks.append((bbox[1], block))

            for bbox, rows in zip(table_boxes, table_rows):
                normalized_rows = [[str(cell or "").strip() for cell in row] for row in rows]
                flat = " ".join(" ".join(row) for row in normalized_rows).strip()
                if not flat:
                    continue
                t_index += 1
                block = Block(
                    id=f"t_{t_index:04d}",
                    kind=BlockKind.TABLE,
                    text=flat,
                    section_path=list(section_stack),
                    rows=normalized_rows,
                    is_bibliography=in_bibliography,
                    page=page_no,
                    bbox=bbox,
                )
                page_blocks.append((bbox[1], block))

            blocks.extend(block for _, block in sorted(page_blocks, key=lambda item: item[0]))

    doc = DocumentIR(
        doc_id=path.stem,
        source_path=str(path),
        source_hash=source_hash,
        blocks=blocks,
        metadata={
            "format": "pdf",
            "parser": "pdfplumber-native",
            "pages": page_count,
            "pages_with_text": pages_with_text,
            "pages_without_text": pages_without_text,
            "text_chars": text_chars,
            # An empty text layer is a correctness boundary: running the
            # deterministic checks on it would otherwise look like a clean
            # manuscript while silently skipping all substantive evidence.
            "scan_likely": bool(page_count and pages_with_text == 0),
            "pdf_metadata": pdf_metadata,
        },
    )
    _extract_citations(doc)
    _extract_figures(doc)
    _extract_numerics(doc)
    return doc


def _extract_lines(page: Any) -> list[dict[str, Any]]:
    try:
        lines = page.extract_text_lines() or []
    except (AttributeError, TypeError, ValueError):
        lines = []
    out: list[dict[str, Any]] = []
    for line in lines:
        if not isinstance(line, dict):
            continue
        text = str(line.get("text", "") or "").strip()
        if not text:
            continue
        try:
            x0 = float(line.get("x0", 0.0))
            x1 = float(line.get("x1", x0))
            top = float(line.get("top", 0.0))
            bottom = float(line.get("bottom", top))
        except (TypeError, ValueError):
            continue
        out.append({"text": text, "x0": x0, "x1": x1, "top": top, "bottom": bottom})
    return sorted(out, key=lambda item: (item["top"], item["x0"]))


def _extract_tables(page: Any) -> tuple[list[tuple[float, float, float, float]], list[list[list[str]]]]:
    boxes: list[tuple[float, float, float, float]] = []
    rows: list[list[list[str]]] = []
    try:
        tables = page.find_tables() or []
    except (AttributeError, TypeError, ValueError):
        tables = []
    for table in tables:
        try:
            bbox = tuple(float(value) for value in table.bbox)
            extracted = table.extract() or []
        except (AttributeError, TypeError, ValueError):
            continue
        if len(bbox) != 4 or not extracted:
            continue
        boxes.append(bbox)  # type: ignore[arg-type]
        rows.append(extracted)
    return boxes, rows


def _line_in_tables(line: dict[str, Any], boxes: list[tuple[float, float, float, float]]) -> bool:
    center_x = (float(line["x0"]) + float(line["x1"])) / 2
    center_y = (float(line["top"]) + float(line["bottom"])) / 2
    return any(x0 <= center_x <= x1 and top <= center_y <= bottom for x0, top, x1, bottom in boxes)


def _group_lines(lines: list[dict[str, Any]]) -> list[tuple[str, tuple[float, float, float, float]]]:
    grouped: list[tuple[str, tuple[float, float, float, float]]] = []
    current: list[dict[str, Any]] = []
    for line in lines:
        if not current:
            current = [line]
            continue
        previous = current[-1]
        gap = float(line["top"]) - float(previous["bottom"])
        height = max(1.0, float(previous["bottom"]) - float(previous["top"]))
        indent_shift = abs(float(line["x0"]) - float(previous["x0"]))
        # A large vertical gap or a fresh heading starts a new block. Normal
        # wrapped lines stay in one block, retaining one evidence quote.
        if (
            gap > max(12.0, height * 1.8)
            or _heading_info(str(previous["text"]))[0]
            or _heading_info(str(line["text"]))[0]
            or indent_shift > 36.0
        ):
            grouped.append(_finalize_lines(current))
            current = [line]
        else:
            current.append(line)
    if current:
        grouped.append(_finalize_lines(current))
    return grouped


def _finalize_lines(lines: list[dict[str, Any]]) -> tuple[str, tuple[float, float, float, float]]:
    text = " ".join(str(line["text"]).strip() for line in lines).strip()
    bbox = (
        min(float(line["x0"]) for line in lines),
        min(float(line["top"]) for line in lines),
        max(float(line["x1"]) for line in lines),
        max(float(line["bottom"]) for line in lines),
    )
    return text, bbox


def _heading_info(text: str) -> tuple[bool, int]:
    clean = str(text or "").strip()
    if not clean:
        return False, 0
    if _BIB_HEADING.match(clean) or _CJK_HEADING.match(clean):
        return True, 1
    numbered = _NUMBERED_HEADING.match(clean)
    if numbered and len(clean) <= 180:
        prefix = clean.split(None, 1)[0].rstrip(".)")
        return True, max(1, prefix.count(".") + 1)
    # Short all-caps lines are common English section headings in PDFs.
    if len(clean) <= 90 and re.fullmatch(r"[A-Z][A-Z0-9\s:,&\-]{3,}", clean):
        return True, 1
    return False, 0


__all__ = ["read_pdf"]
