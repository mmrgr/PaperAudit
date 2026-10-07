"""GROBID TEI -> DocumentIR adapter.

GROBID is an optional service, not a package dependency.  This adapter keeps
the common PaperAudit IR while preserving page coordinates when the service
returns ``coords`` attributes.  Network access is opt-in through an explicit
parser selection; the native pdfplumber reader remains the default.
"""

from __future__ import annotations

import hashlib
import re
import uuid
import urllib.error
import urllib.request
from pathlib import Path
from xml.etree import ElementTree as ET

from paperaudit.models import Block, BlockKind, DocumentIR
from paperaudit.llm import _validate_endpoint

from .docx_reader import _extract_citations, _extract_figures, _extract_numerics

_TEI = "http://www.tei-c.org/ns/1.0"
def read_grobid(
    path: str | Path,
    *,
    endpoint: str = "http://127.0.0.1:8070/api/processFulltextDocument",
    timeout: float = 60.0,
) -> DocumentIR:
    """Send a PDF to GROBID and parse its TEI response.

    The endpoint must be supplied explicitly by callers who want a remote
    service.  Errors are surfaced as ``RuntimeError`` so CLI callers can
    return a non-success status rather than silently falling back to a weaker
    parser.
    """

    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(source)
    if source.suffix.casefold() != ".pdf":
        raise ValueError("GROBID 输入必须是 PDF")
    _validate_endpoint(endpoint)
    payload = _multipart_pdf(source)
    request = urllib.request.Request(
        endpoint,
        data=payload.body,
        method="POST",
        headers={
            "Content-Type": f"multipart/form-data; boundary={payload.boundary}",
            "Accept": "application/xml, text/xml, */*",
            "User-Agent": "PaperAudit/0.2",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=max(0.5, float(timeout))) as response:
            content = response.read()
    except (OSError, urllib.error.URLError) as exc:
        raise RuntimeError(f"GROBID 请求失败：{exc}") from exc
    if len(content) > 100 * 1024 * 1024:
        raise RuntimeError("GROBID 返回内容超过 100 MiB，已拒绝解析")
    if b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
        raise RuntimeError("GROBID 返回了不允许的 DTD/实体声明")
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise RuntimeError("GROBID 返回的内容不是有效 TEI XML") from exc

    return _tei_to_ir(source, root, source_format="pdf")


def read_grobid_tei(path: str | Path) -> DocumentIR:
    """Parse a saved GROBID TEI XML file without contacting a service."""

    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(source)
    content = source.read_bytes()
    if len(content) > 100 * 1024 * 1024:
        raise RuntimeError("TEI 文件超过 100 MiB，已拒绝解析")
    if b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
        raise RuntimeError("TEI 文件包含不允许的 DTD/实体声明")
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise RuntimeError("TEI 文件不是有效 XML") from exc
    if _local(root.tag) != "TEI":
        raise ValueError("输入 XML 不是 GROBID TEI")
    return _tei_to_ir(source, root, source_format="grobid-tei")


class _Multipart:
    def __init__(self, body: bytes, boundary: str):
        self.body = body
        self.boundary = boundary


def _multipart_pdf(path: Path) -> _Multipart:
    boundary = f"----PaperAudit{uuid.uuid4().hex}"
    data = path.read_bytes()
    header = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="input"; filename="paper.pdf"\r\n'
        "Content-Type: application/pdf\r\n\r\n"
    ).encode("ascii")
    options = (
        f"\r\n--{boundary}\r\n"
        'Content-Disposition: form-data; name="teiCoordinates"\r\n\r\n'
        "s,p,fig\r\n"
        f"--{boundary}--\r\n"
    ).encode("ascii")
    return _Multipart(header + data + options, boundary)


def _tei_to_ir(path: Path, root: ET.Element, *, source_format: str = "pdf") -> DocumentIR:
    blocks: list[Block] = []
    section_stack: list[str] = []
    paragraph_index = 0
    table_index = 0
    bibliography = False

    text_root = _first(root, "text")
    if text_root is None:
        raise RuntimeError("GROBID TEI 缺少 text 节点")

    def visit(node: ET.Element, *, in_back: bool = False) -> None:
        nonlocal paragraph_index, table_index, bibliography, section_stack
        name = _local(node.tag)
        if name == "div":
            div_type = str(node.attrib.get("type", "")).casefold()
            previous_bib = bibliography
            if in_back or div_type in {"references", "bibliography"}:
                bibliography = True
            for child in list(node):
                visit(child, in_back=in_back or bibliography)
            bibliography = previous_bib
            return
        if name == "head":
            text = _clean_text(node)
            if text:
                level = _level(node)
                section_stack = section_stack[: max(0, level - 1)]
                section_stack.append(text)
                paragraph_index += 1
                blocks.append(Block(
                    id=f"p_{paragraph_index:04d}", kind=BlockKind.HEADING,
                    text=text, section_path=list(section_stack),
                    style_name="GROBID heading", heading_level=level,
                    is_bibliography=bibliography, page=_page(node), bbox=_bbox(node),
                ))
            return
        if name == "p":
            text = _clean_text(node)
            if text:
                paragraph_index += 1
                blocks.append(Block(
                    id=f"p_{paragraph_index:04d}", kind=BlockKind.PARAGRAPH,
                    text=text, section_path=list(section_stack),
                    style_name="GROBID paragraph", is_bibliography=bibliography,
                    page=_page(node), bbox=_bbox(node),
                ))
            return
        if name in {"figure", "table"}:
            text = _clean_text(node)
            if not text:
                return
            if name == "table":
                table_index += 1
                rows = _table_rows(node)
                blocks.append(Block(
                    id=f"t_{table_index:04d}", kind=BlockKind.TABLE,
                    text=" ".join(" ".join(row) for row in rows) or text,
                    section_path=list(section_stack), rows=rows,
                    is_bibliography=bibliography, page=_page(node), bbox=_bbox(node),
                ))
            else:
                paragraph_index += 1
                blocks.append(Block(
                    id=f"p_{paragraph_index:04d}", kind=BlockKind.CAPTION,
                    text=text, section_path=list(section_stack),
                    style_name="GROBID caption", is_bibliography=bibliography,
                    page=_page(node), bbox=_bbox(node),
                ))
            return
        if name == "listBibl" or name == "back":
            for child in list(node):
                visit(child, in_back=True)
            return
        if name in {"biblStruct", "bibl"}:
            text = _clean_text(node)
            if text:
                index = len([block for block in blocks if block.is_bibliography]) + 1
                if not re.match(r"^\s*\[\d+\]", text):
                    text = f"[{index}] {text}"
                paragraph_index += 1
                blocks.append(Block(
                    id=f"p_{paragraph_index:04d}", kind=BlockKind.BIBLIO_ENTRY,
                    text=text, section_path=list(section_stack),
                    style_name="GROBID bibliography", is_bibliography=True,
                    page=_page(node), bbox=_bbox(node),
                ))
            return
        for child in list(node):
            visit(child, in_back=in_back)

    visit(text_root)
    source_hash = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    doc = DocumentIR(
        doc_id=path.stem,
        source_path=str(path),
        source_hash=source_hash,
        blocks=blocks,
        metadata={
            "format": source_format,
            "parser": "grobid-tei",
            "grobid": True,
            "pages": max((block.page or 0 for block in blocks), default=None),
        },
    )
    _extract_citations(doc)
    _extract_figures(doc)
    _extract_numerics(doc)
    return doc


def _first(root: ET.Element, name: str) -> ET.Element | None:
    return next((node for node in root.iter() if _local(node.tag) == name), None)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _clean_text(node: ET.Element) -> str:
    return " ".join(part.strip() for part in node.itertext() if part and part.strip())


def _level(node: ET.Element) -> int:
    value = node.attrib.get("n", "")
    match = re.match(r"\d+(?:\.\d+)*", value)
    return max(1, match.group(0).count(".") + 1) if match else 1


def _coords(node: ET.Element) -> list[float]:
    raw = str(node.attrib.get("coords", ""))
    for item in raw.split(";"):
        values = item.split(",")
        if len(values) >= 5:
            try:
                return [float(values[i]) for i in range(5)]
            except ValueError:
                continue
    return []


def _page(node: ET.Element) -> int | None:
    values = _coords(node)
    return int(values[0]) if values else None


def _bbox(node: ET.Element) -> tuple[float, float, float, float] | None:
    values = _coords(node)
    if len(values) < 5:
        return None
    _, x, y, width, height = values[:5]
    return (x, y, x + width, y + height)


def _table_rows(node: ET.Element) -> list[list[str]]:
    rows: list[list[str]] = []
    for row in node.iter():
        if _local(row.tag) != "row":
            continue
        cells = [_clean_text(cell) for cell in list(row) if _local(cell.tag) in {"cell", "entry"}]
        if cells:
            rows.append(cells)
    return rows


__all__ = ["read_grobid", "read_grobid_tei"]
