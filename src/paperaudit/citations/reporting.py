"""Stable JSON and Markdown projections for citation audits."""

from __future__ import annotations

import json
from typing import Any

from .parser import audit_document


def to_json(value: Any, *, indent: int = 2) -> str:
    """Serialize an audit result, or audit a compatible document first."""

    result = value if isinstance(value, dict) and "citations" in value else audit_document(value)
    return json.dumps(result, ensure_ascii=False, indent=indent, sort_keys=True)


def report(value: Any) -> str:
    """Render a concise human-readable citation integrity report."""

    result = value if isinstance(value, dict) and "citations" in value else audit_document(value)
    summary = result.get("summary", {})
    statuses = summary.get("metadata_status", {})
    lines = [
        "# Citation Integrity v1",
        "",
        f"- References: {summary.get('total', 0)}",
        f"- Uncited bibliography entries: {summary.get('uncited', 0)}",
        f"- Metadata: {', '.join(f'{key}={value}' for key, value in sorted(statuses.items())) or 'none'}",
        f"- Claim support: {', '.join(f'{key}={value}' for key, value in sorted((summary.get('support_status') or {}).items())) or 'uncertain'}",
        "",
        "| Reference | Style | Identifier | Metadata | Publication | Support | Cited at |",
        "|---|---|---|---|---|---|---:|",
    ]
    for item in result.get("citations", []):
        raw = str(item.get("raw", "")).replace("|", "\\|").replace("\n", " ")
        support_values: list[str] = []
        provenance: list[str] = []
        for site in item.get("citation_sites", []) or []:
            status = str(site.get("support_status", "uncertain"))
            try:
                support_values.append(f"{status} ({float(site.get('support_confidence', 0.0)):.2f})")
            except (TypeError, ValueError):
                support_values.append(status)
            for evidence in site.get("evidence_provenance", []) or []:
                if not isinstance(evidence, dict):
                    continue
                source = str(evidence.get("source", "unknown"))
                locator = str(evidence.get("locator", "")).strip()
                provenance.append(f"{source}@{locator}" if locator else source)
        evidence_text = ", ".join(dict.fromkeys(provenance)) or "—"
        support_text = ", ".join(dict.fromkeys(support_values)) or "—"
        lines.append(
            f"| {raw} | {item.get('style', '')} | {item.get('identifier', '')} | "
            f"{item.get('metadata_status', '')} | {item.get('publication_status', '')} | "
            f"{support_text} | {len(item.get('citation_sites', []) or [])} |"
        )
        if evidence_text != "—":
            escaped_evidence_text = evidence_text.replace("|", "\\|")
            lines.append(f"| ↳ evidence |  |  |  |  |  | {escaped_evidence_text} |")
    return "\n".join(lines) + "\n"


to_markdown = report
