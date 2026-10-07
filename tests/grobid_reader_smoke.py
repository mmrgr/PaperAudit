"""Offline smoke test for the optional GROBID TEI adapter."""

from __future__ import annotations

from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from paperrevamper.ingest.grobid_reader import _tei_to_ir, read_grobid_tei
from paperrevamper.evidence.privacy import check as privacy_check


def main() -> int:
    tei = """
    <TEI xmlns="http://www.tei-c.org/ns/1.0">
      <teiHeader><fileDesc><titleStmt><author><persName>Jane Doe</persName></author></titleStmt></fileDesc></teiHeader>
      <text>
        <body>
          <div>
            <head n="1" coords="1,10,20,100,20">Introduction</head>
            <p coords="1,10,50,200,30">Prior work [1] reports 92.4% accuracy.</p>
            <figure coords="1,10,90,200,80"><head>Figure 1</head><figDesc>Overview</figDesc></figure>
          </div>
        </body>
        <back><div type="references"><listBibl>
          <biblStruct xml:id="b0"><analytic><title>Prior work</title></analytic><date when="2024"/></biblStruct>
        </listBibl></div></back>
      </text>
    </TEI>
    """
    with TemporaryDirectory() as directory:
        source = Path(directory) / "paper.pdf"
        source.write_bytes(b"%PDF offline fixture")
        document = _tei_to_ir(source, ET.fromstring(tei))
        tei_path = Path(directory) / "paper.tei.xml"
        tei_path.write_text(tei, encoding="utf-8")
        saved_document = read_grobid_tei(tei_path)
        privacy_findings = privacy_check(saved_document)
    assert document.metadata["parser"] == "grobid-tei"
    assert document.blocks[0].page == 1
    assert document.blocks[1].bbox == (10.0, 50.0, 210.0, 80.0)
    assert len(document.citations) == 1
    assert len(document.citation_marks) == 1
    assert any(item.kind == "figure" for item in document.figures)
    assert saved_document.metadata["format"] == "grobid-tei"
    assert any("GROBID TEI" in finding.rationale for finding in privacy_findings)
    print({"status": "ok", "blocks": len(document.blocks), "citations": len(document.citations)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
