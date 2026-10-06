"""审查清单加载。

清单是一等公民（docs/PLAN_v2.md §4.4）：
瓶颈在「定位错误」而非「修复」，而清单正是把定位外部化的手段。
支持外部导入（会议/期刊的 author instructions）。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from paperaudit.resources import resource_path

BUILTIN = resource_path("checklists", "academic.yaml")
REGISTRY = resource_path("checklists", "registry.yaml")


@dataclass(frozen=True)
class ChecklistDescriptor:
    """Metadata for one selectable checklist pack.

    Content remains in the referenced YAML file.  Keeping the registry
    separate lets callers discover packs without loading every question into
    the review prompt.
    """

    id: str
    path: str
    name: str
    aliases: tuple[str, ...] = ()
    scopes: tuple[str, ...] = ("all",)
    keywords: tuple[str, ...] = ()
    status: str = "builtin"
    study_design: str = ""
    source_url: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Item:
    id: str
    q: str
    sev: str = "minor"
    judge: str = ""
    scope: str = "all"


@dataclass
class Group:
    id: str
    name: str
    items: list[Item] = field(default_factory=list)


@dataclass
class Checklist:
    name: str
    version: str
    groups: list[Group] = field(default_factory=list)

    def all_items(self) -> list[Item]:
        return [it for g in self.groups for it in g.items]

    def items_for(self, doc_type: str = "all") -> list[Item]:
        """按文档类型过滤。doc_type 为 paper / proposal / all。"""
        if doc_type == "all":
            return [it for it in self.all_items() if it.scope == "all"]
        return [it for it in self.all_items() if it.scope in (doc_type, "all")]

    def item_by_id(self, item_id: str) -> Item | None:
        for it in self.all_items():
            if it.id == item_id:
                return it
        return None


def load(path: str | Path | None = None) -> Checklist:
    p = _resolve_checklist(path)
    if not p.exists():
        raise FileNotFoundError(f"清单文件不存在：{p}")
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    groups = []
    for g in data.get("groups", []) or []:
        items = [
            Item(
                id=str(i.get("id", "")),
                q=str(i.get("q", "")).strip(),
                sev=str(i.get("sev", "minor")),
                judge=str(i.get("judge", "")).strip(),
                scope=str(i.get("scope", "all")),
            )
            for i in (g.get("items") or [])
        ]
        groups.append(Group(id=str(g.get("id", "")), name=str(g.get("name", "")), items=items))
    return Checklist(
        name=str(data.get("name", "未命名清单")),
        version=str(data.get("version", "0")),
        groups=groups,
    )


def list_checklists(registry_path: str | Path | None = None) -> list[ChecklistDescriptor]:
    """List checklist packs declared by the registry.

    A malformed registry entry is ignored rather than making the default
    academic checklist unusable.  The returned paths are absolute when the
    registry is a local file, which makes them safe to pass to ``load``.
    """

    registry = Path(registry_path) if registry_path else REGISTRY
    try:
        data = yaml.safe_load(registry.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return []
    out: list[ChecklistDescriptor] = []
    for row in data.get("checklists", []) or []:
        if not isinstance(row, dict):
            continue
        identifier = str(row.get("id", "")).strip()
        path = str(row.get("path", "")).strip()
        if not identifier or not path:
            continue
        resolved = Path(path)
        if not resolved.is_absolute():
            resolved = registry.parent / resolved
        out.append(
            ChecklistDescriptor(
                id=identifier,
                path=str(resolved),
                name=str(row.get("name", identifier)).strip() or identifier,
                aliases=tuple(str(x).strip() for x in (row.get("aliases") or []) if str(x).strip()),
                scopes=tuple(str(x).strip() for x in (row.get("scopes") or ["all"]) if str(x).strip()) or ("all",),
                keywords=tuple(str(x).strip() for x in (row.get("keywords") or []) if str(x).strip()),
                status=str(row.get("status", "builtin")).strip() or "builtin",
                study_design=str(row.get("study_design", "")).strip(),
                source_url=str(row.get("source_url", "")).strip(),
            )
        )
    return out


def resolve_checklist(identifier: str | Path, registry_path: str | Path | None = None) -> Path | None:
    """Resolve a registry id/alias or return an existing direct path."""

    candidate = Path(identifier)
    if candidate.exists():
        return candidate
    needle = str(identifier).strip().casefold()
    if not needle:
        return None
    for descriptor in list_checklists(registry_path):
        names = {
            descriptor.id.casefold(),
            descriptor.name.casefold(),
            Path(descriptor.path).name.casefold(),
            Path(descriptor.path).stem.casefold(),
            *(alias.casefold() for alias in descriptor.aliases),
        }
        if needle in names:
            return Path(descriptor.path)
    return None


def recommend_checklists(ir: Any = None, text: str = "", registry_path: str | Path | None = None) -> list[ChecklistDescriptor]:
    """Rank registry packs for a document without making a hard selection.

    Recommendations are advisory.  ``prepare --checklist`` remains explicit;
    callers can show the ranked list and let the author choose a venue or
    study-design pack.
    """

    descriptors = list_checklists(registry_path)
    if not descriptors:
        return []
    doc_type = detect_doc_type(ir) if ir is not None else "all"
    haystack = str(text or "").casefold()
    if ir is not None:
        # Recommendation is advisory, so use the bounded manuscript text in
        # addition to headings.  Study-design terms often occur in the title
        # or abstract without a recognized Heading style.
        block_text = " ".join(str(block.text) for block in getattr(ir, "blocks", []))[:200_000]
        haystack = (haystack + " " + block_text).casefold()
    scored: list[tuple[int, ChecklistDescriptor]] = []
    for descriptor in descriptors:
        score = 0
        scopes = {scope.casefold() for scope in descriptor.scopes}
        if doc_type.casefold() != "all" and doc_type.casefold() in scopes:
            score += 4
        elif "all" in scopes:
            score += 2
        score += sum(1 for keyword in descriptor.keywords if keyword.casefold() in haystack)
        scored.append((score, descriptor))
    return [descriptor for _, descriptor in sorted(scored, key=lambda item: (-item[0], item[1].id))]


def _resolve_checklist(path: str | Path | None) -> Path:
    if path is None:
        return BUILTIN
    resolved = resolve_checklist(path)
    return resolved if resolved is not None else Path(path)


def detect_doc_type(ir) -> str:
    """从章节标题判断文档类型：论文 / 开题报告。"""
    heads = " ".join(b.text for b in ir.blocks if b.heading_level > 0)
    if any(k in heads for k in ("开题", "进度安排", "预期达到", "已完成的学术研究", "研究过程中可能遇到")):
        return "proposal"
    if any(k in heads for k in ("摘要", "Abstract", "实验结果", "结论", "Discussion")):
        return "paper"
    return "all"
