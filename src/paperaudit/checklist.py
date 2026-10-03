"""审查清单加载。

清单是一等公民（docs/PLAN_v2.md §4.4）：
瓶颈在「定位错误」而非「修复」，而清单正是把定位外部化的手段。
支持外部导入（会议/期刊的 author instructions）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from paperaudit.resources import resource_path

BUILTIN = resource_path("checklists", "academic.yaml")


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
    p = Path(path) if path else BUILTIN
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


def detect_doc_type(ir) -> str:
    """从章节标题判断文档类型：论文 / 开题报告。"""
    heads = " ".join(b.text for b in ir.blocks if b.heading_level > 0)
    if any(k in heads for k in ("开题", "进度安排", "预期达到", "已完成的学术研究", "研究过程中可能遇到")):
        return "proposal"
    if any(k in heads for k in ("摘要", "Abstract", "实验结果", "结论", "Discussion")):
        return "paper"
    return "all"
