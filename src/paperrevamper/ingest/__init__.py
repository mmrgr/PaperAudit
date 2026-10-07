"""Document ingestion entry points."""

from .docx_reader import read_docx
from .grobid_reader import read_grobid, read_grobid_tei
from .pdf_reader import read_pdf


def read_document(path, *, pdf_parser: str = "native", grobid_endpoint: str | None = None):
    """Read a supported manuscript format into the common DocumentIR."""

    from pathlib import Path

    suffix = Path(path).suffix.casefold()
    if suffix == ".docx":
        return read_docx(path)
    if suffix == ".pdf":
        if str(pdf_parser).casefold() == "grobid":
            return read_grobid(path, endpoint=grobid_endpoint or "http://127.0.0.1:8070/api/processFulltextDocument")
        return read_pdf(path)
    if suffix == ".xml":
        return read_grobid_tei(path)
    raise ValueError(f"目前仅支持 .docx、.pdf 或 GROBID .xml：{path}")

__all__ = ["read_docx", "read_pdf", "read_grobid", "read_grobid_tei", "read_document"]
