"""Deterministic aggregation for a two-position, multi-model review panel.

The panel does not invent a scientific judgement.  It only combines explicit
binary records produced by an external judge.  A finding is ``confirmed`` or
``refuted`` only when at least two distinct judge models agree in both prompt
positions (claim-first and evidence-first).  Missing positions, abstentions,
or disagreements remain visible as ``contested``/``unverifiable``.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

POSITIONS = ("claim_first", "evidence_first")
VOTES = ("yes", "no", "cannot_assess")
VERDICTS = ("confirmed", "contested", "refuted", "unverifiable")


def _text(value: Any) -> str:
    return str(value or "").strip()


def validate_judgments(
    payload: Mapping[str, Any] | Iterable[Mapping[str, Any]],
    *,
    finding_ids: set[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Normalize panel rows and return ``(valid, errors)``.

    Errors are data rather than exceptions so a panel run can preserve all
    malformed rows in its audit artifact without allowing them to affect a
    verdict.
    """

    if isinstance(payload, Mapping):
        rows = payload.get("judgments", [])
    else:
        rows = payload
    if not isinstance(rows, list):
        return [], [{"index": None, "reason": "judgments must be an array"}]

    valid: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            errors.append({"index": index, "reason": "judgment must be an object"})
            continue
        finding_id = _text(row.get("finding_id"))
        model = _text(row.get("judge_model"))
        position = _text(row.get("position")).casefold()
        vote = _text(row.get("verdict")).casefold()
        if not finding_id or (finding_ids is not None and finding_id not in finding_ids):
            errors.append({"index": index, "finding_id": finding_id, "reason": "unknown or empty finding_id"})
            continue
        if not model:
            errors.append({"index": index, "finding_id": finding_id, "reason": "judge_model is required"})
            continue
        if position not in POSITIONS:
            errors.append({"index": index, "finding_id": finding_id, "reason": f"position must be one of {POSITIONS}"})
            continue
        if vote not in VOTES:
            errors.append({"index": index, "finding_id": finding_id, "reason": f"verdict must be one of {VOTES}"})
            continue
        key = (finding_id, model, position)
        if key in seen:
            errors.append({"index": index, "finding_id": finding_id, "reason": "duplicate model/position judgment"})
            continue
        seen.add(key)
        normalized = {
            "finding_id": finding_id,
            "judge_model": model,
            "position": position,
            "verdict": vote,
        }
        for key_name in ("extracted_claim", "evidence_summary", "reason"):
            if row.get(key_name) not in (None, ""):
                normalized[key_name] = _text(row.get(key_name))
        if isinstance(row.get("raw_json"), Mapping):
            normalized["raw_json"] = dict(row["raw_json"])
        valid.append(normalized)
    return valid, errors


def aggregate_panel(
    findings: Iterable[Mapping[str, Any]],
    judgments: Iterable[Mapping[str, Any]],
    *,
    required_models: int = 2,
) -> dict[str, Any]:
    """Aggregate explicit panel votes into four safe verdict states."""

    finding_rows = [dict(row) for row in findings if isinstance(row, Mapping)]
    by_id = {_text(row.get("id")): row for row in finding_rows if _text(row.get("id"))}
    valid, errors = validate_judgments(list(judgments), finding_ids=set(by_id))
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in valid:
        grouped[row["finding_id"]].append(row)

    records: list[dict[str, Any]] = []
    for finding_id, finding in by_id.items():
        rows = grouped.get(finding_id, [])
        models = sorted({row["judge_model"] for row in rows})
        positions = {position: [row for row in rows if row["position"] == position] for position in POSITIONS}
        model_positions = {
            model: {row["position"] for row in rows if row["judge_model"] == model}
            for model in models
        }
        missing = [
            f"{model}:{position}"
            for model, model_position_set in model_positions.items()
            for position in POSITIONS
            if position not in model_position_set
        ]
        reason = ""
        if len(models) < max(1, int(required_models)):
            verdict = "unverifiable"
            reason = "insufficient_independent_models"
        elif missing:
            verdict = "unverifiable"
            reason = "incomplete_position_exchange"
        else:
            votes = [row["verdict"] for row in rows]
            non_abstain = [vote for vote in votes if vote != "cannot_assess"]
            if not non_abstain:
                verdict = "unverifiable"
                reason = "panel_abstained"
            elif len(non_abstain) != len(votes):
                verdict = "contested"
                reason = "panel_contains_abstention"
            elif all(vote == "yes" for vote in votes):
                verdict = "confirmed"
                reason = "unanimous_yes_across_positions"
            elif all(vote == "no" for vote in votes):
                verdict = "refuted"
                reason = "unanimous_no_across_positions"
            else:
                verdict = "contested"
                reason = "position_or_model_disagreement"
        records.append(
            {
                "finding_id": finding_id,
                "finding_uid": _text(finding.get("uid")),
                "verdict": verdict,
                "reason": reason,
                "judge_models": models,
                "positions": {
                    position: [row["verdict"] for row in positions[position]]
                    for position in POSITIONS
                },
                "judgments": rows,
            }
        )

    counts = {verdict: sum(row["verdict"] == verdict for row in records) for verdict in VERDICTS}
    return {
        "schema_version": 1,
        "status": "ok" if not errors else "invalid_rows",
        "required_models": max(1, int(required_models)),
        "required_positions": list(POSITIONS),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": counts,
        "findings": records,
        "errors": errors,
    }


def adjudicate_run(
    run_dir: str | Path,
    judgments_path: str | Path,
    *,
    output_path: str | Path | None = None,
    required_models: int = 2,
) -> dict[str, Any]:
    """Adjudicate a verified run and persist a separate audit artifact."""

    run = Path(run_dir).resolve()
    findings_path = run / "findings.json"
    if not findings_path.exists():
        raise FileNotFoundError(f"找不到 findings.json：{run}。请先运行 verify。")
    try:
        findings_payload = json.loads(findings_path.read_text(encoding="utf-8"))
        judgment_payload = json.loads(Path(judgments_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取 panel JSON：{exc}") from exc
    if not isinstance(findings_payload, Mapping):
        raise ValueError("findings.json 必须是对象")
    findings = [
        item
        for key in ("confirmed", "rejected")
        for item in findings_payload.get(key, []) or []
        if isinstance(item, Mapping)
    ]
    judgments = judgment_payload.get("judgments", []) if isinstance(judgment_payload, Mapping) else judgment_payload
    result = aggregate_panel(findings, judgments or [], required_models=required_models)
    result["run_dir"] = str(run)
    result["source"] = findings_payload.get("source", "")
    result["source_hash"] = _text(findings_payload.get("source_hash"))
    result["judgments_source"] = str(Path(judgments_path).resolve())
    destination = Path(output_path).resolve() if output_path else run / "adjudication.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    result["output"] = str(destination)
    markdown_destination = destination.with_suffix(".md")
    markdown_destination.write_text(to_markdown(result), encoding="utf-8")
    result["markdown_output"] = str(markdown_destination)
    return result


def to_markdown(result: Mapping[str, Any]) -> str:
    """Render a compact, audit-friendly panel result."""

    summary = result.get("summary", {})
    lines = [
        "# PaperAudit Panel Adjudication",
        "",
        f"- Confirmed: {summary.get('confirmed', 0)}",
        f"- Contested: {summary.get('contested', 0)}",
        f"- Refuted: {summary.get('refuted', 0)}",
        f"- Unverifiable: {summary.get('unverifiable', 0)}",
        "",
        "| Finding | Verdict | Models | Position votes | Reason |",
        "|---|---|---|---|---|",
    ]
    for row in result.get("findings", []) or []:
        positions = row.get("positions", {}) or {}
        position_text = "; ".join(
            f"{position}={','.join(str(vote) for vote in positions.get(position, [])) or '—'}"
            for position in POSITIONS
        )
        lines.append(
            f"| {row.get('finding_id', '')} | `{row.get('verdict', '')}` | "
            f"{', '.join(row.get('judge_models', []) or []) or '—'} | {position_text} | {row.get('reason', '')} |"
        )
    if result.get("errors"):
        lines += ["", "## Invalid judgment rows", ""]
        lines.extend(f"- row {item.get('index')}: {item.get('reason', '')}" for item in result["errors"])
    return "\n".join(lines) + "\n"


__all__ = [
    "POSITIONS",
    "VOTES",
    "VERDICTS",
    "adjudicate_run",
    "aggregate_panel",
    "to_markdown",
    "validate_judgments",
]
