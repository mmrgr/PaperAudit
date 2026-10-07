"""Config-driven venue profiles and deterministic submission checks.

Venue profiles are deliberately metadata-only.  PaperRevamper never claims that a
small built-in profile is an official author guideline; users can point the
registry at a reviewed, local profile and keep that profile's provenance in
the prepared run.  The checks here cover only explicit requirements such as
required section headings, leaving scope and acceptance decisions to the
author.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

from paperrevamper.models import Finding, IssueType, Severity, stable_id
from paperrevamper.resources import resource_path

REGISTRY = resource_path("venues", "registry.yaml")


@dataclass(frozen=True)
class VenueProfile:
    id: str
    name: str
    checklist: str = "academic"
    aliases: tuple[str, ...] = ()
    required_sections: tuple[str, ...] = ()
    stop_criteria: tuple[str, ...] = ()
    scope: str = "advisory"
    status: str = "user-supplied"
    source_url: str = ""

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        # Stop criteria are declarative until a corresponding executable
        # checker exists.  Keep that boundary explicit in every run artifact.
        value["stop_criteria_status"] = "advisory"
        value["stop_criteria_evaluated"] = False
        return value


def list_venues(registry_path: str | Path | None = None) -> list[VenueProfile]:
    """Load valid profiles from a YAML registry, ignoring malformed rows."""

    registry = Path(registry_path) if registry_path else REGISTRY
    try:
        payload = yaml.safe_load(registry.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeError, yaml.YAMLError):
        return []
    if isinstance(payload, dict) and isinstance(payload.get("venues"), list):
        rows = payload["venues"]
    elif isinstance(payload, dict) and payload.get("id"):
        # A local one-profile YAML is convenient for a lab or a venue owner.
        rows = [payload]
    else:
        rows = []
    out: list[VenueProfile] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        identifier = str(row.get("id", "")).strip()
        name = str(row.get("name", identifier)).strip()
        if not identifier or not name:
            continue
        out.append(
            VenueProfile(
                id=identifier,
                name=name,
                checklist=str(row.get("checklist", "academic")).strip() or "academic",
                aliases=_strings(row.get("aliases")),
                required_sections=_strings(row.get("required_sections")),
                stop_criteria=_strings(row.get("stop_criteria")),
                scope=str(row.get("scope", "advisory")).strip() or "advisory",
                status=str(row.get("status", "user-supplied")).strip() or "user-supplied",
                source_url=str(row.get("source_url", "")).strip(),
            )
        )
    return out


def resolve_venue(identifier: str | Path, registry_path: str | Path | None = None) -> VenueProfile | None:
    """Resolve a profile id, alias, or a single profile YAML file."""

    candidate = Path(identifier)
    if candidate.exists():
        profiles = list_venues(candidate)
        return profiles[0] if profiles else None
    needle = str(identifier).strip().casefold()
    if not needle:
        return None
    for profile in list_venues(registry_path):
        names = {profile.id.casefold(), profile.name.casefold(), *(a.casefold() for a in profile.aliases)}
        if needle in names:
            return profile
    return None


def audit_required_sections(ir: Any, profile: VenueProfile) -> list[Finding]:
    """Return high-confidence findings for explicitly missing section names."""

    if not profile.required_sections:
        return []
    headings = [str(block.text).strip() for block in getattr(ir, "blocks", []) if getattr(block, "heading_level", 0)]
    normalized = [_norm(value) for value in headings]
    findings: list[Finding] = []
    for required in profile.required_sections:
        needle = _norm(required)
        if not needle or any(needle in heading or heading in needle for heading in normalized):
            continue
        item_id = f"venue:{profile.id}:section:{_slug(required)}"
        findings.append(
            Finding(
                id=f"V-{stable_id(profile.id, required)}",
                issue_type=IssueType.STRUCTURE_ISSUE,
                severity=Severity.MAJOR,
                confidence=0.98,
                checklist_id=item_id,
                rationale=f"目标投稿配置 {profile.name} 明确要求章节“{required}”，当前文档标题中未发现可匹配的章节。",
                source="deterministic",
                owner_skill="paper_audit",
                gate_passed=True,
                gate_reason="venue-profile-required-section",
            )
        )
    return findings


def _strings(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def _norm(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").casefold()).strip().rstrip(":：")


def _slug(value: str) -> str:
    token = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", _norm(value)).strip("-")
    return token[:80] or "required"


__all__ = ["REGISTRY", "VenueProfile", "audit_required_sections", "list_venues", "resolve_venue"]
