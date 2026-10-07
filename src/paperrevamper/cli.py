"""PaperRevamper CLI。

工作流（默认宿主协作式；显式选择模型 profile 时也可由本工具执行角色）：

    prepare   解析文档 → 审查包（清单 + 分章节上下文）
       ↓  宿主按清单做语义审查，回填 findings.llm.json
    verify    证据门禁 → 去重 → 排序 → 报告
       ↓  用户勾选
    apply     副本上改写 + 备份 + 回归复验（原文件不动）
    panel     启动本地可视化多 Agent 修改控制面板
    run-review  使用显式模型 profile 执行 prepared review tasks → verify
    adjudicate  聚合双位置、多模型 panel judgment → 四态裁决
    run-panel   显式调用两个或多个模型执行双位置 panel → 四态裁决
    list-venues 列出可用投稿配置；prepare --venue 启用显式章节门禁

多篇按传入顺序串行处理。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from paperrevamper.evidence import run_all
from paperrevamper.ingest import read_document
from paperrevamper.reporting import apply_gate, to_json, to_markdown


def _cmd_check(args) -> int:
    code = 0
    for raw in args.paths:
        p = Path(raw)
        if not p.exists():
            print(f"[错误] 文件不存在：{p}", file=sys.stderr)
            code = 2
            continue
        if p.suffix.lower() not in {".docx", ".pdf", ".xml"}:
            print(f"[跳过] 目前仅支持 .docx、.pdf 或 GROBID .xml：{p}", file=sys.stderr)
            # A successful process exit must not imply that an unsupported
            # input was audited. Keep processing other paths, but expose the
            # unsupported input to CI and calling agents.
            code = max(code, 2)
            continue
        try:
            doc = read_document(p, pdf_parser=args.pdf_parser, grobid_endpoint=args.grobid_endpoint)
        except (RuntimeError, ValueError) as exc:
            print(f"[错误] {exc}", file=sys.stderr)
            code = max(code, 2)
            continue
        if (doc.metadata or {}).get("scan_likely"):
            print(
                f"[警告] {p} 没有可提取的文本层，疑似扫描件；本次检查结果不应视为完整审查。",
                file=sys.stderr,
            )
            code = max(code, 2)
        if (doc.metadata or {}).get("scan_likely"):
            findings = []
        else:
            findings = apply_gate(doc, run_all(doc, only=args.only, skip=args.skip))
        if args.format == "json":
            print(to_json(doc, findings))
        else:
            if len(args.paths) > 1:
                print(f"\n{'=' * 70}\n### {p.name}\n{'=' * 70}\n")
            print(to_markdown(doc, findings))
        if args.out:
            out = Path(args.out)
            out.mkdir(parents=True, exist_ok=True)
            (out / f"{p.stem}.review.md").write_text(to_markdown(doc, findings), encoding="utf-8")
            (out / f"{p.stem}.findings.json").write_text(to_json(doc, findings), encoding="utf-8")
            print(f"[已写入] {out / (p.stem + '.review.md')}", file=sys.stderr)
    return code


def _cmd_prepare(args) -> int:
    from paperrevamper.prepare import prepare
    from paperrevamper.collaboration import load_workflow

    workflow = load_workflow(args.workflow) if args.workflow else None
    out = prepare(
        args.path,
        args.out,
        args.checklist,
        workflow,
        pdf_parser=args.pdf_parser,
        grobid_endpoint=args.grobid_endpoint,
        venue=args.venue,
        venue_registry=args.venue_registry,
    )
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(f"\n[审查包已生成] {out}", file=sys.stderr)
    print(f"[下一步] {manifest['next_step']}", file=sys.stderr)
    return 0


def _cmd_verify(args) -> int:
    from paperrevamper.verify import verify

    result = verify(args.run_dir, args.checklist)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _cmd_list_checklists(args) -> int:
    from paperrevamper.checklist import list_checklists, recommend_checklists

    if args.recommend:
        from paperrevamper.ingest import read_document

        try:
            document = read_document(args.recommend, pdf_parser=args.pdf_parser, grobid_endpoint=args.grobid_endpoint)
        except (RuntimeError, ValueError) as exc:
            print(f"[错误] {exc}", file=sys.stderr)
            return 2
        descriptors = recommend_checklists(document, registry_path=args.registry)
    else:
        descriptors = list_checklists(args.registry)
    rows = [descriptor.to_dict() for descriptor in descriptors]
    if args.format == "json":
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        if not rows:
            print("未发现可用清单。")
        for row in rows:
            aliases = ", ".join(row["aliases"]) or "—"
            design = f"\t研究设计: {row['study_design']}" if row.get("study_design") else ""
            print(f"{row['id']}\t{row['name']}\t适用: {', '.join(row['scopes'])}\t别名: {aliases}{design}")
    return 0


def _cmd_list_venues(args) -> int:
    from paperrevamper.venue import list_venues

    profiles = list_venues(args.registry)
    rows = [profile.to_dict() for profile in profiles]
    if args.format == "json":
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    elif not rows:
        print("未发现可用投稿配置。")
    else:
        for row in rows:
            aliases = ", ".join(row["aliases"]) or "—"
            req = ", ".join(row["required_sections"]) or "—"
            print(f"{row['id']}\t{row['name']}\t别名: {aliases}\t必需章节: {req}\t状态: {row['status']}")
    return 0


def _cmd_citation_integrity(args) -> int:
    """Run the opt-in scholarly metadata audit.

    Ordinary ``check`` remains offline and deterministic.  Network access is
    only enabled when the caller explicitly supplies ``--online``.
    """
    from paperrevamper.citations.cache import JsonCache
    from paperrevamper.citations.evidence_fetcher import JsonEvidenceFetcher, LocalEvidenceFetcher
    from paperrevamper.citations.parser import audit_document
    from paperrevamper.citations.resolver import (
        CrossrefResolver,
        DeterministicResolver,
        SemanticScholarResolver,
    )

    path = Path(args.path)
    if not path.exists():
        print(f"[错误] 文件不存在：{path}", file=sys.stderr)
        return 2
    if path.suffix.casefold() not in {".docx", ".pdf", ".xml"}:
        print(f"[跳过] 目前仅支持 .docx、.pdf 或 GROBID .xml：{path}", file=sys.stderr)
        return 2

    if not args.online:
        resolver = DeterministicResolver()
    elif args.provider == "crossref":
        resolver = CrossrefResolver(timeout=args.timeout)
    elif args.provider == "semanticscholar":
        resolver = SemanticScholarResolver(timeout=args.timeout)
    else:
        resolver = _ResolverChain(
            [CrossrefResolver(timeout=args.timeout), SemanticScholarResolver(timeout=args.timeout)]
        )

    cache = JsonCache(args.cache) if args.cache else None
    if args.evidence:
        evidence_path = Path(args.evidence)
        evidence_fetcher = (
            JsonEvidenceFetcher(evidence_path)
            if evidence_path.is_file() and evidence_path.suffix.casefold() == ".json"
            else LocalEvidenceFetcher(evidence_path)
        )
    else:
        evidence_fetcher = None
    try:
        doc = read_document(path, pdf_parser=args.pdf_parser, grobid_endpoint=args.grobid_endpoint)
    except (RuntimeError, ValueError) as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        return 2
    scan_likely = bool((doc.metadata or {}).get("scan_likely"))
    if scan_likely:
        print(
            f"[警告] {path} 没有可提取的文本层，疑似扫描件；引用审计结果不完整。",
            file=sys.stderr,
        )
    result = audit_document(
        doc,
        resolver=resolver,
        cache=cache,
        evidence_fetcher=evidence_fetcher,
    )
    if args.format == "json":
        rendered = json.dumps(result, ensure_ascii=False, indent=2)
    else:
        rendered = _citation_integrity_markdown(result)
    print(rendered)
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{path.stem}.citations.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (out / f"{path.stem}.citations.md").write_text(
            _citation_integrity_markdown(result), encoding="utf-8"
        )
        print(f"[已写入] {out}", file=sys.stderr)
    failures = set(args.fail_on or [])
    if failures:
        status_hit = any(
            item.get("metadata_status") in failures or item.get("publication_status") in failures
            for item in result.get("citations", [])
        )
        if "uncited" in failures:
            status_hit = status_hit or bool(result.get("summary", {}).get("uncited", 0))
        if status_hit:
            return 1
    return 2 if scan_likely else 0


def _cmd_benchmark(args) -> int:
    from paperrevamper.benchmark import render_markdown, run_benchmark

    try:
        result = run_benchmark(
            args.corpus,
            only=args.only,
            skip=args.skip,
            pdf_parser=args.pdf_parser,
            grobid_endpoint=args.grobid_endpoint,
        )
    except (FileNotFoundError, TypeError, ValueError) as exc:
        print(f"[错误] benchmark corpus: {exc}", file=sys.stderr)
        return 2
    rendered = json.dumps(result, ensure_ascii=False, indent=2) if args.format == "json" else render_markdown(result)
    print(rendered)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[已写入] {out}", file=sys.stderr)
    if result.get("status") == "error":
        return 2
    threshold = args.fail_under
    if threshold is not None and float(result.get("summary", {}).get("f1", 0.0)) < threshold:
        return 1
    return 0


def _cmd_run_review(args) -> int:
    from paperrevamper.runner import run_review

    try:
        result = run_review(
            args.run_dir,
            config_path=args.config,
            profile_id=args.profile,
            timeout=args.timeout,
            verify_after=not args.no_verify,
            resume=not args.fresh,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"[错误] run-review: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "ok" else 1


def _cmd_adjudicate(args) -> int:
    from paperrevamper.protocol.adjudication import adjudicate_run, to_markdown

    try:
        result = adjudicate_run(
            args.run_dir,
            args.judgments,
            output_path=args.out,
            required_models=args.required_models,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"[错误] adjudicate: {exc}", file=sys.stderr)
        return 2
    rendered = json.dumps(result, ensure_ascii=False, indent=2) if args.format == "json" else to_markdown(result)
    print(rendered)
    return 0 if result.get("status") == "ok" else 1


def _cmd_run_panel(args) -> int:
    from paperrevamper.panel_runner import run_panel

    try:
        result = run_panel(
            args.run_dir,
            profile_ids=args.profiles,
            config_path=args.config,
            timeout=args.timeout,
            required_models=args.required_models,
            resume=not args.fresh,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"[错误] run-panel: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "ok" else 1


class _ResolverChain:
    """Try public metadata providers in order without turning failures fatal."""

    online = True

    def __init__(self, resolvers):
        self.resolvers = list(resolvers)

    def resolve(self, record):
        last = {"status": "unresolved", "source": "resolver-chain", "metadata": {}}
        for resolver in self.resolvers:
            try:
                payload = resolver.resolve(record)
            except Exception:
                continue
            if isinstance(payload, dict):
                last = payload
                status = str(payload.get("status", "")).casefold()
                if status not in {"", "unresolved", "not_found", "error", "failed"}:
                    return payload
        return last


def _citation_integrity_markdown(result: dict) -> str:
    summary = result.get("summary", {})
    document = result.get("document", {})
    lines = [
        "# Citation Integrity 报告",
        "",
        f"**文件**：`{Path(str(document.get('source_path', ''))).name}`  ",
        f"**参考文献**：{document.get('citation_entries', 0)} 条；正文引用点 {document.get('citation_marks', 0)} 个  ",
        f"**未被正文引用**：{summary.get('uncited', 0)} 条  ",
        "",
        "> 离线模式不会声称文献已被外部数据库验证；只有显式 `--online` 且服务返回成功时才会标记 verified。",
        "",
        "## 状态汇总",
        "",
    ]
    for status, count in sorted((summary.get("metadata_status") or {}).items()):
        lines.append(f"- `{status}`：{count}")
    for status, count in sorted((summary.get("support_status") or {}).items()):
        lines.append(f"- `support:{status}`：{count}")
    lines += ["", "## 条目", ""]
    for index, item in enumerate(result.get("citations", []), 1):
        identifier = item.get("identifier") or item.get("key") or f"entry-{index}"
        status = item.get("metadata_status", "unresolved")
        publication = item.get("publication_status", "unknown")
        flags = ", ".join(item.get("mismatch_fields") or []) or "—"
        cited = "已引用" if item.get("cited") else "列而未引"
        support_values = []
        for site in item.get("citation_sites", []) or []:
            status_value = str(site.get("support_status", "uncertain"))
            try:
                confidence_value = float(site.get("support_confidence", 0.0))
                support_values.append(f"{status_value} ({confidence_value:.2f})")
            except (TypeError, ValueError):
                support_values.append(status_value)
        support = ", ".join(dict.fromkeys(sorted(support_values))) or "—"
        provenance: list[str] = []
        for site in item.get("citation_sites", []) or []:
            for evidence in site.get("evidence_provenance", []) or []:
                if not isinstance(evidence, dict):
                    continue
                source = str(evidence.get("source", "unknown"))
                locator = str(evidence.get("locator", "")).strip()
                provenance.append(f"{source}@{locator}" if locator else source)
        provenance_text = ", ".join(dict.fromkeys(provenance)) or "—"
        lines += [
            f"### {index}. `{identifier}`",
            "",
            f"- **状态**：`{status}`；出版更新：`{publication}`；支撑：`{support}`；{cited}",
            f"- **样式**：`{item.get('style', 'unknown')}`；不一致字段：{flags}",
            f"- **原始条目**：{str(item.get('raw', '')).strip()[:400]}",
            f"- **证据来源**：{provenance_text}",
            "",
        ]
    return "\n".join(lines)


def _cmd_apply(args) -> int:
    from paperrevamper.revise import apply_revision

    ids = [x.strip() for x in args.findings.split(",") if x.strip()]
    result = apply_revision(
        args.path, args.run_dir, ids, out_path=args.out,
        backup=not args.no_backup, text=args.text,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "ok" else 1


def _cmd_plan_revision(args) -> int:
    from paperrevamper.revise import build_revision_plan

    ids = [x.strip() for x in args.findings.split(",") if x.strip()]
    try:
        result = build_revision_plan(args.path, args.run_dir, ids, text=args.text)
    except (FileNotFoundError, ValueError) as exc:
        print(f"[错误] plan-revision: {exc}", file=sys.stderr)
        return 2
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(_revision_plan_markdown(result))
    return 0 if result.get("status") == "ready" else 1


def _revision_plan_markdown(result: dict) -> str:
    lines = [
        "# PaperRevamper Revision Plan",
        "",
        f"- Status: `{result.get('status', '')}`",
        f"- Source: `{result.get('source', '')}`",
        f"- Source hash matches: `{result.get('source_hash_matches', True)}`",
        "",
        "| Proposal | Finding(s) | Level | Blocks | Replacement |",
        "|---|---|---|---|---|",
    ]
    for proposal in result.get("proposals", []) or []:
        replacement = str(proposal.get("new_text", "")).replace("|", "\\|").replace("\n", "<br>")[:240]
        lines.append(
            f"| `{proposal.get('id', '')}` | {', '.join(proposal.get('finding_ids', []) or [])} | "
            f"`{proposal.get('level', '')}` | {', '.join(proposal.get('target_blocks', []) or [])} | {replacement} |"
        )
    if result.get("failed"):
        lines.extend(["", "## Blocked", ""])
        lines.extend(f"- `{item.get('id')}`: {item.get('reason', '')}" for item in result["failed"] if isinstance(item, dict))
    if result.get("revision_plan"):
        lines.extend(["", f"Saved artifact: `{result['revision_plan']}`"])
    if result.get("diff_path"):
        lines.append(f"Diff artifact: `{result['diff_path']}`")
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="paperrevamper",
        description="PaperRevamper —— 投前自审的验证器（默认宿主协作式，也支持显式模型 profile）",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="确定性检查，只出意见，不修改文件")
    c.add_argument("paths", nargs="+")
    c.add_argument("--format", choices=["markdown", "json"], default="markdown")
    c.add_argument("--out")
    c.add_argument("--only", nargs="*")
    c.add_argument("--skip", nargs="*")
    c.add_argument("--pdf-parser", choices=["native", "grobid"], default="native")
    c.add_argument("--grobid-endpoint", help="GROBID processFulltextDocument endpoint")

    pr = sub.add_parser("prepare", help="生成审查包，供宿主 agent 做语义审查")
    pr.add_argument("path")
    pr.add_argument("--out", required=True)
    pr.add_argument("--checklist", help="外部清单 YAML（默认内置 academic.yaml）")
    pr.add_argument("--venue", help="投稿配置 id/别名或单独的 venue YAML；未指定 checklist 时使用其 checklist")
    pr.add_argument("--venue-registry", help="自定义投稿配置 registry.yaml")
    pr.add_argument("--workflow", help="自定义多 Agent workflow JSON")
    pr.add_argument("--pdf-parser", choices=["native", "grobid"], default="native")
    pr.add_argument("--grobid-endpoint", help="GROBID processFulltextDocument endpoint")

    v = sub.add_parser("verify", help="对回填意见做证据门禁、去重、排序")
    v.add_argument("run_dir")
    v.add_argument("--checklist")

    lc = sub.add_parser(
        "list-checklists",
        aliases=["checklists"],
        help="列出已注册的审查清单，或为一份 DOCX/PDF/GROBID XML 给出推荐顺序",
    )
    lc.add_argument("--format", choices=["text", "json"], default="text")
    lc.add_argument("--registry", help="自定义 registry.yaml")
    lc.add_argument("--recommend", help="根据 DOCX/PDF/GROBID XML 标题和文档类型给出推荐顺序")
    lc.add_argument("--pdf-parser", choices=["native", "grobid"], default="native")
    lc.add_argument("--grobid-endpoint", help="GROBID processFulltextDocument endpoint")

    lv = sub.add_parser(
        "list-venues",
        aliases=["venues"],
        help="列出投稿配置；配置只执行作者明确提供的章节门禁",
    )
    lv.add_argument("--format", choices=["text", "json"], default="text")
    lv.add_argument("--registry", help="自定义 venues/registry.yaml")

    ci = sub.add_parser(
        "citation-integrity",
        aliases=["check-citations"],
        help="检查参考文献元数据与出版状态（默认离线；--online 才访问外部服务）",
    )
    ci.add_argument("path")
    ci.add_argument("--format", choices=["markdown", "json"], default="markdown")
    ci.add_argument("--out", help="输出目录，同时写入 .citations.md 与 .citations.json")
    ci.add_argument("--online", action="store_true", help="显式允许访问 Crossref/Semantic Scholar")
    ci.add_argument("--provider", choices=["crossref", "semanticscholar", "both"], default="both")
    ci.add_argument("--timeout", type=float, default=5.0)
    ci.add_argument("--cache", help="本地 JSON 元数据缓存路径")
    ci.add_argument(
        "--evidence",
        help="本地 JSON 引用证据包或文本/TEI 语料目录；passage 可带 text/source/locator/confidence",
    )
    ci.add_argument("--pdf-parser", choices=["native", "grobid"], default="native")
    ci.add_argument("--grobid-endpoint", help="GROBID processFulltextDocument endpoint")
    ci.add_argument(
        "--fail-on",
        action="append",
        choices=["mismatch", "retracted", "concern", "unresolved", "uncited"],
        help="命中指定状态时返回退出码 1，可重复传入",
    )

    bm = sub.add_parser(
        "benchmark",
        help="运行本地标注语料，报告确定性检查的 precision/recall/F1",
    )
    bm.add_argument("corpus", help="JSON/YAML benchmark corpus")
    bm.add_argument("--format", choices=["markdown", "json"], default="markdown")
    bm.add_argument("--out", help="写入完整 JSON 结果的路径")
    bm.add_argument("--only", nargs="*", help="仅运行指定检测器（case 内 detectors 优先）")
    bm.add_argument("--skip", nargs="*", help="跳过指定检测器（case 内 skip 优先）")
    bm.add_argument("--fail-under", type=float, help="F1 低于阈值时返回退出码 1")
    bm.add_argument("--pdf-parser", choices=["native", "grobid"], default="native")
    bm.add_argument("--grobid-endpoint", help="GROBID processFulltextDocument endpoint")

    rr = sub.add_parser(
        "run-review",
        help="显式调用模型 profile 执行审查包中的角色任务，然后运行 verify",
    )
    rr.add_argument("run_dir")
    rr.add_argument("--config", help="模型 profile JSON；默认 out/paperrevamper-llm.json")
    rr.add_argument("--profile", help="模型 profile id，例如 openai、deepseek 或 custom_openai")
    rr.add_argument("--timeout", type=int, default=120)
    rr.add_argument("--no-verify", action="store_true", help="只写入角色 findings，不自动执行 verify")
    rr.add_argument("--fresh", action="store_true", help="忽略上次 runner checkpoint，重新执行所有角色")

    adj = sub.add_parser(
        "adjudicate",
        help="聚合双位置、多模型 panel judgments，输出 confirmed/contested/refuted/unverifiable",
    )
    adj.add_argument("run_dir", help="已运行 verify 的审查包目录")
    adj.add_argument("--judgments", required=True, help="包含 judgments 数组的 panel JSON")
    adj.add_argument("--out", help="裁决输出路径（默认 <run_dir>/adjudication.json）")
    adj.add_argument("--format", choices=["markdown", "json"], default="markdown")
    adj.add_argument("--required-models", type=int, default=2, help="每个 finding 至少需要多少独立 judge model（默认 2）")

    rp = sub.add_parser(
        "run-panel",
        help="显式调用多个模型执行 claim-first/evidence-first panel，然后聚合四态裁决",
    )
    rp.add_argument("run_dir", help="已运行 verify 的审查包目录")
    rp.add_argument("--profiles", nargs="+", required=True, help="至少两个不同的模型 profile id")
    rp.add_argument("--config", help="模型 profile JSON；默认 out/paperrevamper-llm.json")
    rp.add_argument("--timeout", type=int, default=120)
    rp.add_argument("--required-models", type=int, default=2)
    rp.add_argument("--fresh", action="store_true", help="忽略 panel checkpoint，重新执行所有 judge task")

    ap = sub.add_parser("apply", help="在副本上改写，原文件不动，自动备份")
    ap.add_argument("path")
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--findings", required=True, help="逗号分隔，如 F003,F007")
    ap.add_argument("--out")
    ap.add_argument("--text", help="直接给出改写后的段落文本（覆盖 suggested_fix）")
    ap.add_argument("--no-backup", action="store_true")

    pl = sub.add_parser(
        "plan-revision",
        help="只生成可审阅的 EditProposal/冲突计划，不修改 DOCX",
    )
    pl.add_argument("path", help="DOCX 源文件")
    pl.add_argument("--run-dir", required=True)
    pl.add_argument("--findings", required=True, help="逗号分隔，如 F003,F007 或 *")
    pl.add_argument("--text", help="对单段落直接给出 replacement")
    pl.add_argument("--format", choices=["markdown", "json"], default="markdown")

    pnl = sub.add_parser("panel", help="启动本地多 Agent 修改控制面板")
    pnl.add_argument("--run-root", default="out/panel-runs")
    pnl.add_argument("--host", default="127.0.0.1")
    pnl.add_argument("--port", type=int, default=8765)
    pnl.add_argument("--open", action="store_true")
    pnl.add_argument("--allow-remote", action="store_true", help="允许绑定非本机地址；必须同时提供 --auth-token")
    pnl.add_argument("--auth-token", help="保护面板 API 的共享令牌；远程绑定时必填")
    return p


def main(argv: list[str] | None = None) -> int:
    # Windows PowerShell may expose a legacy GBK stdout even when the report
    # contains Unicode punctuation (for example, the evidence warning marker).
    # Reconfigure only real console streams; StringIO-based callers are left
    # untouched.  File outputs already use explicit UTF-8 below.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass
    args = build_parser().parse_args(argv)
    if args.cmd == "panel":
        from paperrevamper.panel import main as panel_main

        panel_args = ["--run-root", args.run_root, "--host", args.host, "--port", str(args.port)]
        if args.open:
            panel_args.append("--open")
        if args.allow_remote:
            panel_args.append("--allow-remote")
        if args.auth_token:
            panel_args.extend(["--auth-token", args.auth_token])
        return panel_main(panel_args)
    return {
        "check": _cmd_check,
        "prepare": _cmd_prepare,
        "verify": _cmd_verify,
        "list-checklists": _cmd_list_checklists,
        "checklists": _cmd_list_checklists,
        "list-venues": _cmd_list_venues,
        "venues": _cmd_list_venues,
        "citation-integrity": _cmd_citation_integrity,
        "check-citations": _cmd_citation_integrity,
        "benchmark": _cmd_benchmark,
        "run-review": _cmd_run_review,
        "adjudicate": _cmd_adjudicate,
        "run-panel": _cmd_run_panel,
        "plan-revision": _cmd_plan_revision,
        "apply": _cmd_apply,
    }[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
