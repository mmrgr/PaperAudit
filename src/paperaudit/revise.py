"""修订执行：在副本上改，原文件不动，改前备份，改后回归。

铁律（用户明确要求）：
  1. 绝不修改原文件
  2. 改动一律输出到新文件
  3. 自动备份

实现要点（来自 ~/.workbuddy/skills/docx-section-revision/SKILL.md）：
  - 替换段落文本时删除 pPr 以外的全部子元素再追加 run，保留原 pPr（缩进/样式不丢）
  - 新 run 必须深拷贝原首 run 的完整 rPr，否则中文字体丢失（eastAsia）
"""

from __future__ import annotations

import copy
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Callable

import docx
from docx.text.paragraph import Paragraph

from paperaudit.evidence import run_all
from paperaudit.ingest import read_docx
from paperaudit.reporting import apply_gate

_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _index_paragraphs(document) -> dict[str, Paragraph]:
    """按 body 遍历序重建 block_id → 段落 的映射。

    必须与 ingest 的编号规则一致（p_{序号:04d}，只数 w:p）。
    """
    idx: dict[str, Paragraph] = {}
    n = 0
    for el in document.element.body.iterchildren():
        if el.tag.split("}")[-1] == "p":
            n += 1
            idx[f"p_{n:04d}"] = Paragraph(el, document)
    return idx


def apply_revision(
    source: str | Path,
    run_dir: str | Path,
    finding_ids: list[str],
    out_path: str | Path | None = None,
    backup: bool = True,
    text: str | None = None,
    progress: Callable[..., None] | None = None,
) -> dict:
    src = Path(source)
    run_dir = Path(run_dir)
    if not src.exists():
        raise FileNotFoundError(src)

    _progress(progress, "load", "正在加载已接受的修改意见", 10)

    findings = _load_findings(run_dir)
    fixable = [f.get("id") for f in findings if (f.get("suggested_fix") or "").strip()]

    if finding_ids == ["*"]:
        finding_ids = fixable  # 一键应用所有带改写内容的条目

    selected = [f for f in findings if f.get("id") in finding_ids]
    if not selected:
        return {
            "status": "error",
            "message": f"未找到指定条目：{finding_ids}",
            "fixable": fixable,
            "hint": "用 --findings '*' 应用所有带改写内容的条目，或用 --text 直接给出文本",
        }

    # 备份（在原文件同目录，不动原文件本身）
    backup_path = None
    if backup:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = src.with_suffix(f".bak-{ts}{src.suffix}")
        shutil.copy2(src, backup_path)
    _progress(progress, "backup", "正在创建原文备份和修改副本", 30)

    out = Path(out_path) if out_path else src.with_name(f"{src.stem}_revised{src.suffix}")
    shutil.copy2(src, out)

    document = docx.Document(str(out))
    paras = _index_paragraphs(document)

    applied, failed = [], []
    for f in selected:
        # --text 覆盖优先，便于用户直接口述改法而不必先写进 JSON
        new_text = (text or "").strip() or (f.get("suggested_fix") or "").strip()
        if not new_text:
            failed.append({"id": f.get("id"), "reason": "无改写内容（用 --text 指定或填 suggested_fix）"})
            continue
        targets = f.get("block_ids") or []
        if not targets:
            failed.append({"id": f.get("id"), "reason": "无目标位置（block_ids 为空）"})
            continue
        for bid in targets:
            p = paras.get(bid)
            if p is None:
                failed.append({"id": f.get("id"), "reason": f"定位失败：{bid}"})
                continue
            try:
                _replace_keep_style(p, new_text)
                applied.append({"id": f.get("id"), "block": bid})
            except Exception as exc:
                failed.append({"id": f.get("id"), "reason": f"{bid}: {exc}"})

    _progress(progress, "write", "正在写入修改后的论文副本", 72, applied=len(applied), failed=len(failed))

    document.save(str(out))

    # 回归复验：改完重跑确定性检查，确认没引入新问题
    before = _findings_keys(run_dir)
    after_ir = read_docx(out)
    after = apply_gate(after_ir, run_all(after_ir))
    regressions = [f.issue_type.value for f in after if f.gate_passed and f.issue_type.value not in before]
    _progress(progress, "regression", "正在执行修改后的回归复验", 92, regressions=len(regressions))

    result = {
        "status": "ok" if applied else "error",
        "output": str(out),
        "backup": str(backup_path) if backup_path else None,
        "applied": applied,
        "failed": failed,
        "regressions": regressions,
        "source_unchanged": str(src),
        "fixable": fixable,
    }
    if not applied:
        result["hint"] = "所选条目均无改写内容；可用 --text 直接给出文本，或 --findings '*'"
    return result


def _progress(callback: Callable[..., None] | None, stage: str, message: str, percent: int, **payload) -> None:
    if callback is not None:
        callback(stage, message, percent, **payload)


def _replace_keep_style(p: Paragraph, new_text: str) -> None:
    """替换段落文本，保留 pPr 与首 run 的完整 rPr。"""
    runs = p.runs
    rpr = copy.deepcopy(runs[0]._element.find(f"{_NS}rPr")) if runs else None

    for child in list(p._p):
        if child.tag != f"{_NS}pPr":
            p._p.remove(child)

    new_run = p.add_run(new_text)
    if rpr is not None:
        el = new_run._element
        for existing in list(el):
            if existing.tag == f"{_NS}rPr":
                el.remove(existing)
        el.insert(0, copy.deepcopy(rpr))


def _load_findings(run_dir: Path) -> list[dict]:
    """读取条目。优先级：verify 产物 > 宿主原始回填 > 确定性预检。"""
    # verify 产物：{"confirmed": [...], "rejected": [...]}
    p = run_dir / "findings.json"
    if p.exists():
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            if "confirmed" in data:
                return list(data["confirmed"])
            return data.get("findings", [])
        return data

    # 宿主原始回填
    p = run_dir / "findings.llm.json"
    if p.exists():
        data = json.loads(p.read_text(encoding="utf-8"))
        return data.get("findings", []) if isinstance(data, dict) else data

    p = run_dir / "manifest.json"
    if p.exists():
        data = json.loads(p.read_text(encoding="utf-8"))
        return data.get("deterministic_findings", [])
    return []


def _findings_keys(run_dir: Path) -> set[str]:
    return {f.get("issue_type") for f in _load_findings(run_dir)}
