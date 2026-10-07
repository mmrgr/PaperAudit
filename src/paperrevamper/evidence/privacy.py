"""Deterministic DOCX privacy and double-blind submission audit.

The check intentionally works on the OOXML package instead of the rendered
paragraph text.  Author names can survive in comments, revisions, properties,
headers, or relationships even when they are invisible in the manuscript.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from paperrevamper.models import DocumentIR, Finding, IssueType, Severity, stable_id

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_DC_NS = "http://purl.org/dc/elements/1.1/"
_CP_NS = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
_EP_NS = "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"

_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_IDENTITY_LABEL_RE = re.compile(
    r"(?:作者|姓名|单位|机构|邮箱|通讯作者|author|affiliation|institution|"
    r"corresponding\s+author|laboratory|lab)\s*[:：]",
    re.I,
)
_W_TAG = f"{{{_W_NS}}}"


def _xml(zf: zipfile.ZipFile, name: str) -> ET.Element | None:
    try:
        return ET.fromstring(zf.read(name))
    except (KeyError, ET.ParseError):
        return None


def _text(root: ET.Element | None) -> str:
    if root is None:
        return ""
    return " ".join(x for x in root.itertext() if x).strip()


def _finding(
    key: str,
    rationale: str,
    *,
    severity: Severity = Severity.MAJOR,
    quote: str = "",
    refs: list[str] | None = None,
) -> Finding:
    return Finding(
        id=f"privacy-{stable_id(key, rationale)}",
        issue_type=IssueType.PRIVACY_RISK,
        severity=severity,
        confidence=1.0,
        verbatim_quote=quote,
        rationale=rationale,
        evidence_refs=refs or [],
        source="deterministic",
        checklist_id="pre-submission/privacy",
        needs_author_decision=False,
    )


def _core_identities(root: ET.Element | None) -> list[str]:
    if root is None:
        return []
    values: list[str] = []
    for tag in (f"{{{_DC_NS}}}creator", f"{{{_CP_NS}}}lastModifiedBy"):
        value = (root.findtext(tag) or "").strip()
        # python-docx writes this implementation marker by default; it is not
        # an author identity and would make every generated fixture noisy.
        if value and value.casefold() not in {"python-docx", "microsoft office", "anonymous", "unknown"}:
            values.append(value)
    return values


def _metadata_findings(zf: zipfile.ZipFile) -> tuple[list[Finding], list[str]]:
    out: list[Finding] = []
    identities: list[str] = []
    core = _xml(zf, "docProps/core.xml")
    identities.extend(_core_identities(core))
    if identities:
        names = ", ".join(dict.fromkeys(identities))
        out.append(
            _finding(
                "core-properties",
                f"DOCX 核心属性仍包含作者身份信息：{names}",
                refs=["docProps/core.xml"],
            )
        )

    app = _xml(zf, "docProps/app.xml")
    company = (app.findtext(f"{{{_EP_NS}}}Company") if app is not None else "") or ""
    if company.strip():
        out.append(
            _finding(
                "company-property",
                f"DOCX 扩展属性包含机构信息：{company.strip()}",
                refs=["docProps/app.xml"],
            )
        )

    custom = _xml(zf, "docProps/custom.xml")
    if custom is not None:
        for prop in list(custom):
            value = _text(prop)
            if not value:
                continue
            name = prop.attrib.get("name", "custom property")
            out.append(
                _finding(
                    f"custom:{name}",
                    f"自定义属性 {name} 含有可能泄露身份或工作环境的信息：{value[:160]}",
                    severity=Severity.MINOR,
                    refs=["docProps/custom.xml"],
                )
            )
    return out, identities


def _comment_and_revision_findings(zf: zipfile.ZipFile) -> list[Finding]:
    out: list[Finding] = []
    comments = _xml(zf, "word/comments.xml")
    authors = []
    if comments is not None:
        for node in comments.findall(f"{_W_TAG}comment"):
            author = (node.attrib.get(f"{_W_TAG}author") or "").strip()
            if author:
                authors.append(author)
    if authors:
        out.append(
            _finding(
                "comments-authors",
                f"批注中仍有作者标识：{', '.join(dict.fromkeys(authors))}",
                refs=["word/comments.xml"],
            )
        )

    revision_authors: list[str] = []
    for name in zf.namelist():
        if not name.startswith("word/") or not name.endswith(".xml"):
            continue
        root = _xml(zf, name)
        if root is None:
            continue
        for node in root.iter():
            local = node.tag.rsplit("}", 1)[-1]
            if local not in {"ins", "del", "moveFrom", "moveTo", "rPrChange", "sectPrChange"}:
                continue
            author = (node.attrib.get(f"{_W_TAG}author") or "").strip()
            if author:
                revision_authors.append(author)
    if revision_authors:
        out.append(
            _finding(
                "tracked-changes",
                f"修订记录中仍有作者标识：{', '.join(dict.fromkeys(revision_authors))}",
                refs=["word/*.xml"],
            )
        )
    return out


def _hidden_text_findings(zf: zipfile.ZipFile) -> list[Finding]:
    out: list[Finding] = []
    for name in zf.namelist():
        if not name.startswith("word/") or not name.endswith(".xml"):
            continue
        root = _xml(zf, name)
        if root is None:
            continue
        hidden: list[str] = []
        for rpr in root.iter(f"{_W_TAG}rPr"):
            if rpr.find(f"{_W_TAG}vanish") is None and rpr.find(f"{_W_TAG}specVanish") is None:
                continue
            parent_text = "".join(rpr.getparent().itertext()) if hasattr(rpr, "getparent") else ""
            if parent_text.strip():
                hidden.append(parent_text.strip())
        # stdlib ElementTree has no parent pointers.  A second pass over runs
        # catches the common case without requiring lxml.
        for run in root.iter(f"{_W_TAG}r"):
            rpr = run.find(f"{_W_TAG}rPr")
            if rpr is not None and (
                rpr.find(f"{_W_TAG}vanish") is not None
                or rpr.find(f"{_W_TAG}specVanish") is not None
            ):
                value = "".join(run.itertext()).strip()
                if value:
                    hidden.append(value)
        if hidden:
            sample = " ".join(dict.fromkeys(hidden))[:160]
            out.append(
                _finding(
                    f"hidden:{name}",
                    f"文档包含隐藏文字，提交前应确认其不含身份信息：{sample}",
                    refs=[name],
                )
            )
    return out


def _header_footer_findings(zf: zipfile.ZipFile, identities: list[str]) -> list[Finding]:
    out: list[Finding] = []
    identity_tokens = [x.casefold() for x in identities if len(x.strip()) >= 2]
    for name in zf.namelist():
        if not (name.startswith("word/header") or name.startswith("word/footer")) or not name.endswith(".xml"):
            continue
        root = _xml(zf, name)
        value = _text(root)
        if not value:
            continue
        lower = value.casefold()
        if any(token in lower for token in identity_tokens) or _EMAIL_RE.search(value) or _IDENTITY_LABEL_RE.search(value):
            out.append(
                _finding(
                    f"header-footer:{name}",
                    f"页眉/页脚包含可能的身份信息：{value[:160]}",
                    refs=[name],
                )
            )
    return out


def _relationship_findings(zf: zipfile.ZipFile) -> list[Finding]:
    out: list[Finding] = []
    for name in zf.namelist():
        if not name.endswith(".rels"):
            continue
        root = _xml(zf, name)
        if root is None:
            continue
        for rel in root:
            target = rel.attrib.get("Target", "")
            mode = rel.attrib.get("TargetMode", "")
            lower = target.casefold()
            local_path = mode.casefold() == "external" and (
                lower.startswith(("file:", "\\\\", "/", "c:\\", "d:\\"))
                or "%5c" in lower
            )
            if local_path:
                out.append(
                    _finding(
                        f"relationship:{name}:{target}",
                        f"关系文件包含外部本地路径，可能泄露用户名或工作目录：{target}",
                        severity=Severity.MAJOR,
                        refs=[name],
                    )
                )
    return out


def _package_name_findings(zf: zipfile.ZipFile) -> list[Finding]:
    out: list[Finding] = []
    for name in zf.namelist():
        if not any(name.startswith(prefix) for prefix in ("word/media/", "word/embeddings/", "word/oleObject")):
            continue
        base = Path(name).name
        if _EMAIL_RE.search(base) or re.search(r"(?:author|姓名|作者|user|用户|[A-Z][a-z]+_[A-Z][a-z]+)", base, re.I):
            out.append(
                _finding(
                    f"embedded:{name}",
                    f"嵌入文件名可能包含身份信息：{base}",
                    severity=Severity.MINOR,
                    refs=[name],
                )
            )
    return out


def check(doc: DocumentIR) -> list[Finding]:
    """Return high-confidence double-blind/privacy findings for DOCX or PDF."""
    path = Path(doc.source_path)
    if str((doc.metadata or {}).get("format", "")).casefold() == "pdf" or path.suffix.casefold() == ".pdf":
        return _pdf_findings(doc)
    if str((doc.metadata or {}).get("format", "")).casefold() == "grobid-tei" or path.suffix.casefold() == ".xml":
        return _tei_findings(doc)
    if not path.exists() or path.suffix.casefold() != ".docx":
        return []
    out: list[Finding] = []
    with zipfile.ZipFile(path) as zf:
        findings, identities = _metadata_findings(zf)
        out.extend(findings)
        out.extend(_comment_and_revision_findings(zf))
        out.extend(_hidden_text_findings(zf))
        out.extend(_header_footer_findings(zf, identities))
        out.extend(_relationship_findings(zf))
        out.extend(_package_name_findings(zf))

    for block in doc.blocks:
        if not block.text:
            continue
        if _EMAIL_RE.search(block.text) or _IDENTITY_LABEL_RE.search(block.text):
            out.append(
                _finding(
                    f"body:{block.id}",
                    "正文中出现作者/机构/邮箱等自我识别信息，双盲投稿前请确认是否应匿名。",
                    quote=block.text[:240],
                    refs=[block.id],
                )
            )
    return out


def _tei_findings(doc: DocumentIR) -> list[Finding]:
    """Inspect saved GROBID TEI headers and visible identity phrases.

    GROBID XML commonly carries author names and affiliations in ``teiHeader``
    even when the rendered manuscript body is anonymized.  Treat only the
    header identity fields as metadata risks; ordinary bibliography authors
    in the body are not flagged by this adapter.
    """

    out: list[Finding] = []
    path = Path(doc.source_path)
    try:
        content = path.read_bytes()
        if len(content) > 100 * 1024 * 1024 or b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
            return out
        root = ET.fromstring(content)
    except (OSError, ET.ParseError):
        root = None
    if root is not None:
        header = next((node for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "teiHeader"), None)
        if header is not None:
            identity_values: list[str] = []
            for node in header.iter():
                local = node.tag.rsplit("}", 1)[-1]
                if local not in {"author", "email", "affiliation", "orgName", "institution"}:
                    continue
                value = " ".join(part.strip() for part in node.itertext() if part and part.strip())
                if value:
                    identity_values.append(value)
            if identity_values:
                values = "; ".join(dict.fromkeys(identity_values))[:300]
                out.append(
                    _finding(
                        "tei-header-identities",
                        f"GROBID TEI 头信息仍包含作者/机构身份：{values}",
                        refs=["teiHeader"],
                    )
                )
    for block in doc.blocks:
        if not block.text:
            continue
        if _EMAIL_RE.search(block.text) or _IDENTITY_LABEL_RE.search(block.text):
            out.append(
                _finding(
                    f"tei-body:{block.id}",
                    "GROBID TEI 正文中出现作者/机构/邮箱等自我识别信息，双盲投稿前请确认是否应匿名。",
                    quote=block.text[:240],
                    refs=[block.id],
                )
            )
    return out


def _pdf_findings(doc: DocumentIR) -> list[Finding]:
    """Inspect portable PDF metadata and visible identity phrases.

    PDF annotations, embedded files, and XMP variants are intentionally not
    guessed here; the parser records unsupported metadata in the IR so a
    future PDF privacy adapter can extend this without weakening DOCX rules.
    """

    out: list[Finding] = []
    metadata = (doc.metadata or {}).get("pdf_metadata", {})
    if isinstance(metadata, dict):
        for key in ("Author", "author", "Creator", "creator"):
            value = str(metadata.get(key, "") or "").strip()
            if value and value.casefold() not in {"reportlab", "pdftex", "latex", "anonymous", "unknown"}:
                out.append(
                    _finding(
                        f"pdf-metadata:{key}",
                        f"PDF 元数据 {key} 仍包含可能的身份信息：{value}",
                        refs=[f"pdf-metadata:{key}"],
                    )
                )
    for block in doc.blocks:
        if not block.text:
            continue
        if _EMAIL_RE.search(block.text) or _IDENTITY_LABEL_RE.search(block.text):
            out.append(
                _finding(
                    f"pdf-body:{block.id}",
                    "PDF 正文中出现作者/机构/邮箱等自我识别信息，双盲投稿前请确认是否应匿名。",
                    quote=block.text[:240],
                    refs=[block.id],
                )
            )
    return out
