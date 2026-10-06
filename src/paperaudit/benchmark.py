"""Deterministic benchmark runner for PaperAudit detector regressions.

The benchmark format is intentionally small and reviewable.  A corpus is a
JSON or YAML object with a ``cases`` list.  Each case either points to a local
DOCX/PDF/GROBID XML file with ``path`` or embeds a tiny ``document`` object
using the same fields as :class:`~paperaudit.models.DocumentIR`.  Gold labels
contain an issue type and may include severity and block anchors::

    {"id": "citation-gap", "document": {"blocks": [...]},
     "detectors": ["citations"],
     "expected": [{"issue_type": "citation_missing", "block_ids": ["p1"]}],
     "expected_absent": ["privacy_risk"]}

Matching is one-to-one.  An anchor supplied by a gold label must overlap a
predicted finding's block anchors; omitting anchors means that any finding of
the same issue type is eligible.  The runner reports precision, recall and F1
without claiming that a small corpus is a production quality estimate.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from paperaudit.evidence import DETECTORS, run_all
from paperaudit.ingest import read_document
from paperaudit.models import (
    Block,
    BlockKind,
    CitationEntry,
    CitationMark,
    DocumentIR,
    FigureRef,
    NumericEntity,
)
from paperaudit.reporting import apply_gate


@dataclass(frozen=True)
class BenchmarkCase:
    case_id: str
    expected: tuple[dict[str, Any], ...]
    expected_absent: tuple[str, ...] = ()
    path: Path | None = None
    document: dict[str, Any] | None = None
    detectors: tuple[str, ...] = ()
    skip: tuple[str, ...] = ()
    pdf_parser: str | None = None
    grobid_endpoint: str | None = None


def load_corpus(path: str | Path) -> tuple[dict[str, Any], list[BenchmarkCase]]:
    """Load and validate a benchmark corpus from JSON or YAML."""

    corpus_path = Path(path)
    payload = _load_payload(corpus_path)
    if not isinstance(payload, dict):
        raise ValueError("benchmark corpus must be an object with a cases list")
    rows = payload.get("cases")
    if not isinstance(rows, list):
        raise ValueError("benchmark corpus requires a cases list")
    cases: list[BenchmarkCase] = []
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise ValueError(f"benchmark case {index} must be an object")
        case_id = str(row.get("id") or f"case-{index}").strip()
        if not case_id:
            raise ValueError(f"benchmark case {index} has an empty id")
        expected = tuple(_normalize_expected(row.get("expected", []), case_id))
        expected_absent = _names(row.get("expected_absent"))
        case_pdf_parser = str(row["pdf_parser"]).casefold() if row.get("pdf_parser") else None
        if case_pdf_parser is not None and case_pdf_parser not in {"native", "grobid"}:
            raise ValueError(f"benchmark case {case_id} pdf_parser must be native or grobid")
        raw_path = row.get("path")
        case_path = None
        if raw_path:
            case_path = (corpus_path.parent / str(raw_path)).resolve()
        document = row.get("document")
        if case_path is None and not isinstance(document, dict):
            raise ValueError(f"benchmark case {case_id} needs path or embedded document")
        detectors = _names(row.get("detectors"))
        skip = _names(row.get("skip"))
        cases.append(
            BenchmarkCase(
                case_id=case_id,
                expected=tuple(expected),
                expected_absent=expected_absent,
                path=case_path,
                document=document if isinstance(document, dict) else None,
                detectors=detectors,
                skip=skip,
                pdf_parser=case_pdf_parser,
                grobid_endpoint=str(row["grobid_endpoint"]) if row.get("grobid_endpoint") else None,
            )
        )
    return payload, cases


def run_benchmark(
    corpus: str | Path | dict[str, Any],
    *,
    only: list[str] | None = None,
    skip: list[str] | None = None,
    pdf_parser: str = "native",
    grobid_endpoint: str | None = None,
) -> dict[str, Any]:
    """Run every corpus case and return a stable JSON-compatible result."""

    if isinstance(corpus, (str, Path)):
        metadata, cases = load_corpus(corpus)
        corpus_path = Path(corpus).resolve()
    elif isinstance(corpus, dict):
        metadata = corpus
        rows = corpus.get("cases", [])
        if not isinstance(rows, list):
            raise ValueError("benchmark corpus requires a cases list")
        # In-memory callers use a synthetic path solely for relative path
        # resolution; embedded documents do not depend on it.
        temp = Path.cwd() / "benchmark.json"
        cases = _cases_from_payload(rows, temp)
        corpus_path = temp
    else:
        raise TypeError("corpus must be a path or mapping")

    case_results: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for case in cases:
        try:
            doc = _case_document(case, default_pdf_parser=pdf_parser, default_grobid_endpoint=grobid_endpoint)
            selected = list(case.detectors) if case.detectors else only
            skipped = list(case.skip) if case.skip else skip
            unknown = sorted((set(selected or []) | set(skipped or [])) - set(DETECTORS))
            if unknown:
                raise ValueError(f"benchmark case {case.case_id} references unknown detector(s): {', '.join(unknown)}")
            findings = run_all(doc, only=selected, skip=skipped)
            apply_gate(doc, findings)
            # Benchmark only reportable findings. A detector may emit a
            # candidate that the evidence gate rejects; counting it as a true
            # positive would hide a grounding regression.
            predicted = [finding.to_dict() for finding in findings if finding.gate_passed]
            gate_rejected = len(findings) - len(predicted)
            metrics = _score(case.expected, predicted, case.expected_absent)
            case_results.append(
                {
                    "id": case.case_id,
                    "status": "ok",
                    "source": str(case.path) if case.path else "embedded",
                    "detectors_run": (doc.metadata or {}).get("detectors_run", []),
                    "gold": list(case.expected),
                    "predicted": predicted,
                    "gate_rejected": gate_rejected,
                    **metrics,
                }
            )
        except Exception as exc:
            errors.append({"id": case.case_id, "error": f"{type(exc).__name__}: {exc}"})
            case_results.append({"id": case.case_id, "status": "error", "error": str(exc)})

    aggregate = _aggregate(case_results)
    return {
        "version": "benchmark-v1",
        "status": "error" if errors else "ok",
        "corpus": {
            "path": str(corpus_path),
            "version": metadata.get("version", ""),
            "name": metadata.get("name", ""),
            "cases": len(cases),
        },
        "summary": aggregate,
        "cases": case_results,
        "errors": errors,
    }


def render_markdown(result: dict[str, Any]) -> str:
    """Render a compact benchmark report suitable for CI artifacts."""

    summary = result.get("summary", {})
    lines = [
        "# PaperAudit Benchmark",
        "",
        f"- Status: `{result.get('status', 'unknown')}`",
        f"- Cases: {summary.get('cases', 0)} (errors: {summary.get('errors', 0)})",
        f"- Micro: precision={summary.get('precision', 0):.3f}, recall={summary.get('recall', 0):.3f}, F1={summary.get('f1', 0):.3f}, FPR={_format_metric(summary.get('fpr'))}, gate-rejected={summary.get('gate_rejected', 0)}",
        "",
        "| Case | Gold | Predicted | TP | FP | FN | F1 | FPR |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for case in result.get("cases", []):
        if case.get("status") != "ok":
            lines.append(f"| {case.get('id', '')} | — | — | — | — | — | error | — |")
            continue
        lines.append(
            f"| {case.get('id', '')} | {len(case.get('gold', []))} | {len(case.get('predicted', []))} | "
            f"{case.get('tp', 0)} | {case.get('fp', 0)} | {case.get('fn', 0)} | {case.get('f1', 0):.3f} | {_format_metric(case.get('fpr'))} |"
        )
    if result.get("errors"):
        lines.extend(["", "## Errors", ""])
        lines.extend(f"- `{row['id']}`: {row['error']}" for row in result["errors"])
    return "\n".join(lines) + "\n"


def _load_payload(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise FileNotFoundError(path) from exc
    if path.suffix.casefold() in {".yaml", ".yml"}:
        try:
            import yaml
        except ImportError as exc:  # pragma: no cover - project dependency
            raise ValueError("YAML benchmark corpora require PyYAML") from exc
        return yaml.safe_load(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid benchmark JSON: {exc}") from exc


def _cases_from_payload(rows: list[Any], corpus_path: Path) -> list[BenchmarkCase]:
    # Reuse validation and path normalization without writing a temporary file.
    cases: list[BenchmarkCase] = []
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise ValueError(f"benchmark case {index} must be an object")
        case_id = str(row.get("id") or f"case-{index}")
        raw_path = row.get("path")
        case_path = (corpus_path.parent / str(raw_path)).resolve() if raw_path else None
        document = row.get("document")
        if case_path is None and not isinstance(document, dict):
            raise ValueError(f"benchmark case {case_id} needs path or embedded document")
        case_pdf_parser = str(row["pdf_parser"]).casefold() if row.get("pdf_parser") else None
        if case_pdf_parser is not None and case_pdf_parser not in {"native", "grobid"}:
            raise ValueError(f"benchmark case {case_id} pdf_parser must be native or grobid")
        cases.append(
            BenchmarkCase(
                case_id=case_id,
                expected=tuple(_normalize_expected(row.get("expected", []), case_id)),
                expected_absent=_names(row.get("expected_absent")),
                path=case_path,
                document=document if isinstance(document, dict) else None,
                detectors=_names(row.get("detectors")),
                skip=_names(row.get("skip")),
                pdf_parser=case_pdf_parser,
                grobid_endpoint=str(row["grobid_endpoint"]) if row.get("grobid_endpoint") else None,
            )
        )
    return cases


def _normalize_expected(value: Any, case_id: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError(f"benchmark case {case_id} expected must be a list")
    result: list[dict[str, Any]] = []
    for index, row in enumerate(value, 1):
        if isinstance(row, str):
            row = {"issue_type": row}
        if not isinstance(row, dict) or not str(row.get("issue_type", "")).strip():
            raise ValueError(f"benchmark case {case_id} expected[{index}] needs issue_type")
        item = {"issue_type": str(row["issue_type"]).strip()}
        if row.get("severity"):
            severity = str(row["severity"]).casefold()
            if severity not in {"major", "minor", "nit"}:
                raise ValueError(f"benchmark case {case_id} expected[{index}].severity 无效")
            item["severity"] = severity
        if row.get("block_ids"):
            if not isinstance(row["block_ids"], list):
                raise ValueError(f"benchmark case {case_id} expected[{index}].block_ids must be a list")
            item["block_ids"] = [str(block_id) for block_id in row["block_ids"] if str(block_id)]
        result.append(item)
    return result


def _names(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("detectors and skip must be lists")
    return tuple(str(item) for item in value if str(item).strip())


def _case_document(case: BenchmarkCase, *, default_pdf_parser: str, default_grobid_endpoint: str | None) -> DocumentIR:
    if case.document is not None:
        return _document_from_mapping(case.document, case.case_id)
    assert case.path is not None
    return read_document(
        case.path,
        pdf_parser=case.pdf_parser or default_pdf_parser,
        grobid_endpoint=case.grobid_endpoint or default_grobid_endpoint,
    )


def _document_from_mapping(value: dict[str, Any], case_id: str) -> DocumentIR:
    blocks: list[Block] = []
    for index, raw in enumerate(value.get("blocks", []), 1):
        if not isinstance(raw, dict):
            raise ValueError(f"benchmark case {case_id} block {index} must be an object")
        try:
            kind = BlockKind(str(raw.get("kind", "paragraph")))
        except ValueError as exc:
            raise ValueError(f"benchmark case {case_id} block {index} has invalid kind") from exc
        bbox = raw.get("bbox")
        blocks.append(
            Block(
                id=str(raw.get("id") or f"b{index}"),
                kind=kind,
                text=str(raw.get("text", "")),
                section_path=[str(x) for x in raw.get("section_path", []) or []],
                style_name=str(raw.get("style_name", "")),
                heading_level=int(raw.get("heading_level", 0) or 0),
                is_bibliography=bool(raw.get("is_bibliography", False)),
                rows=[[str(cell) for cell in row] for row in raw.get("rows", []) or []],
                page=int(raw["page"]) if raw.get("page") is not None else None,
                bbox=tuple(float(x) for x in bbox) if isinstance(bbox, list) and len(bbox) == 4 else None,
            )
        )
    citations = [
        CitationEntry(
            index=int(row["index"]) if row.get("index") is not None else None,
            key=str(row.get("key", "")),
            raw=str(row.get("raw", "")),
            block_id=str(row.get("block_id", "")),
        )
        for row in value.get("citations", []) or []
        if isinstance(row, dict)
    ]
    marks = [
        CitationMark(
            raw=str(row.get("raw", "")),
            key=str(row.get("key", "")),
            block_id=str(row.get("block_id", "")),
            char_range=(int((row.get("char_range") or [0, 0])[0]), int((row.get("char_range") or [0, 0])[1])),
        )
        for row in value.get("citation_marks", []) or []
        if isinstance(row, dict) and isinstance(row.get("char_range", [0, 0]), list) and len(row.get("char_range", [])) == 2
    ]
    figures = [FigureRef(str(row.get("kind", "figure")), str(row.get("label", "")), str(row.get("caption", "")), str(row.get("block_id", ""))) for row in value.get("figures", []) or [] if isinstance(row, dict)]
    numerics = [
        NumericEntity(str(row.get("raw", "")), float(row.get("value", 0)), str(row.get("unit", "raw")), str(row.get("context", "")), str(row.get("block_id", "")), tuple(int(x) for x in row.get("char_range", [0, 0])))
        for row in value.get("numerics", []) or []
        if isinstance(row, dict)
    ]
    source_path = str(value.get("source_path", f"<benchmark:{case_id}>"))
    source_hash = str(value.get("source_hash", ""))
    if not source_hash:
        source_hash = hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    return DocumentIR(
        doc_id=str(value.get("doc_id", case_id)),
        source_path=source_path,
        source_hash=source_hash,
        blocks=blocks,
        figures=figures,
        citations=citations,
        citation_marks=marks,
        numerics=numerics,
        metadata=dict(value.get("metadata", {}) or {}),
    )


def _score(gold: tuple[dict[str, Any], ...], predicted: list[dict[str, Any]], expected_absent: tuple[str, ...] = ()) -> dict[str, Any]:
    # Use maximum-cardinality bipartite matching. A greedy pass can consume a
    # gold label that is the only valid match for a later prediction and
    # understate recall. Visit constrained predictions first; within each
    # adjacency list, prefer anchored and higher-overlap labels.
    adjacency = {
        pred_index: sorted(
            (gold_index for gold_index, item in enumerate(gold) if _compatible(item, pred)),
            key=lambda gold_index: _match_specificity(gold[gold_index], pred),
            reverse=True,
        )
        for pred_index, pred in enumerate(predicted)
    }
    prediction_order = sorted(
        adjacency,
        key=lambda index: (
            len(adjacency[index]),
            -max((_match_specificity(gold[g], predicted[index])[1] for g in adjacency[index]), default=0),
            index,
        ),
    )
    gold_to_prediction: dict[int, int] = {}

    def assign(pred_index: int, visited: set[int]) -> bool:
        for gold_index in adjacency[pred_index]:
            if gold_index in visited:
                continue
            visited.add(gold_index)
            previous = gold_to_prediction.get(gold_index)
            if previous is None or assign(previous, visited):
                gold_to_prediction[gold_index] = pred_index
                return True
        return False

    for pred_index in prediction_order:
        assign(pred_index, set())

    matches = [(gold[g], predicted[p]) for g, p in sorted(gold_to_prediction.items())]
    matched_gold = set(gold_to_prediction)
    matched_predictions = set(gold_to_prediction.values())
    unmatched_gold = [item for index, item in enumerate(gold) if index not in matched_gold]
    unmatched_predicted = [item for index, item in enumerate(predicted) if index not in matched_predictions]
    tp = len(matches)
    fp = len(unmatched_predicted)
    fn = len(unmatched_gold)
    precision, recall, f1 = _prf(tp, fp, fn)
    labels = sorted({str(item.get("issue_type", "")) for item in gold} | {str(item.get("issue_type", "")) for item in predicted})
    by_issue_type: dict[str, dict[str, Any]] = {}
    for label in labels:
        label_tp = sum(1 for item, _ in matches if str(item.get("issue_type", "")) == label)
        label_fp = sum(1 for item in unmatched_predicted if str(item.get("issue_type", "")) == label)
        label_fn = sum(1 for item in unmatched_gold if str(item.get("issue_type", "")) == label)
        label_precision, label_recall, label_f1 = _prf(label_tp, label_fp, label_fn)
        by_issue_type[label] = {
            "gold": label_tp + label_fn,
            "predicted": label_tp + label_fp,
            "tp": label_tp,
            "fp": label_fp,
            "fn": label_fn,
            "precision": label_precision,
            "recall": label_recall,
            "f1": label_f1,
        }
    negative_types = set(expected_absent)
    negative_fp = sum(1 for item in predicted if str(item.get("issue_type", "")) in negative_types)
    negative_tn = max(0, len(negative_types) - negative_fp)
    fpr = negative_fp / (negative_fp + negative_tn) if negative_types else None
    return {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall, "f1": f1, "negative_fp": negative_fp, "negative_tn": negative_tn, "fpr": round(fpr, 4) if fpr is not None else None, "by_issue_type": by_issue_type}


def _compatible(gold: dict[str, Any], pred: dict[str, Any]) -> bool:
    if str(gold.get("issue_type", "")) != str(pred.get("issue_type", "")):
        return False
    if gold.get("severity") and str(gold["severity"]) != str(pred.get("severity", "")):
        return False
    expected_blocks = set(str(x) for x in gold.get("block_ids", []) or [])
    predicted_blocks = set(str(x) for x in pred.get("block_ids", []) or [])
    return not expected_blocks or bool(expected_blocks & predicted_blocks)


def _match_specificity(gold: dict[str, Any], pred: dict[str, Any]) -> tuple[int, int]:
    expected_blocks = set(str(x) for x in gold.get("block_ids", []) or [])
    predicted_blocks = set(str(x) for x in pred.get("block_ids", []) or [])
    return (1 if expected_blocks else 0, len(expected_blocks & predicted_blocks))


def _aggregate(case_results: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [row for row in case_results if row.get("status") == "ok"]
    tp = sum(int(row.get("tp", 0)) for row in valid)
    fp = sum(int(row.get("fp", 0)) for row in valid)
    fn = sum(int(row.get("fn", 0)) for row in valid)
    precision, recall, f1 = _prf(tp, fp, fn)
    negative_fp = sum(int(row.get("negative_fp", 0)) for row in valid)
    negative_tn = sum(int(row.get("negative_tn", 0)) for row in valid)
    gate_rejected = sum(int(row.get("gate_rejected", 0)) for row in valid)
    fpr = negative_fp / (negative_fp + negative_tn) if negative_fp + negative_tn else None
    by_issue_type: dict[str, dict[str, Any]] = {}
    for case in valid:
        for label, metrics in (case.get("by_issue_type") or {}).items():
            item = by_issue_type.setdefault(label, {"gold": 0, "predicted": 0, "tp": 0, "fp": 0, "fn": 0})
            for key in ("gold", "predicted", "tp", "fp", "fn"):
                item[key] += int(metrics.get(key, 0))
    for item in by_issue_type.values():
        item["precision"], item["recall"], item["f1"] = _prf(item["tp"], item["fp"], item["fn"])
    return {"cases": len(valid), "errors": len(case_results) - len(valid), "tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall, "f1": f1, "negative_fp": negative_fp, "negative_tn": negative_tn, "fpr": round(fpr, 4) if fpr is not None else None, "gate_rejected": gate_rejected, "by_issue_type": by_issue_type}


def _prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    precision = tp / (tp + fp) if tp + fp else (1.0 if fn == 0 else 0.0)
    recall = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return tuple(round(value, 4) for value in (precision, recall, f1))


def _format_metric(value: Any) -> str:
    return "n/a" if value is None else f"{float(value):.3f}"


__all__ = ["BenchmarkCase", "load_corpus", "run_benchmark", "render_markdown"]
