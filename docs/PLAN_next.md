# PaperAudit 后续开发计划

更新时间：2026-10-07

本轮完成正确性、安全边界、发布清理和阶段 A 发布门禁，不引入新的审查能力，也不推送 GitHub。下面的顺序用于下一轮评审和拆分任务。

## 已完成的修复与清理

1. Finding 增加稳定 `uid`；`F001` 等编号只用于展示。作者决定、面板状态和修订入口按 `uid` 关联，并按当前源文件 hash 隔离旧决定。
2. 确定性证据门禁改为保留词边界、拒绝未知 block；跨多个原文片段的检测器必须显式声明 `disjoint_parts`。
3. 补齐 Finding 证据字段的 round-trip，避免 verify 丢失 evidence refs、页码、字符范围和门禁状态。
4. 修复空 profile 保存覆盖配置、Anthropic 256 token 截断、模型响应大小无限制和 Gemini key 出现在 URL 的问题。
5. 对模型/GROBID endpoint 增加 http(s)、主机解析和私网地址检查；远程面板额外禁止 loopback endpoint。面板令牌改为 bootstrap cookie，并在浏览器端移除 query 历史。
6. 移除仓库对 `dist/` 生成物的跟踪，保留本地产物；构建脚本不再依赖本机绝对 Python 路径，PyInstaller 版本固定；修复面板恢复路径的 lint 问题。

## 下一阶段建议

### 阶段 A：把基线变成可审计的发布门禁（实现完成，待 CI 验证 Windows 构建）

- `tests/smoke_suite.py` 统一运行 24 个 smoke 脚本，并覆盖稳定 UID、旧决策隔离、严格 quote gate、endpoint policy。
- `.github/workflows/ci.yml` 增加 Windows onedir 构建与 EXE `--help`/内置资源 smoke；`requirements-build.txt` 固定构建依赖，脚本限制 CPython 3.11–3.13。
- jobs 恢复扫描改为运行根目录和其直接子目录，不再对用户数据做全树 `rglob`；runner 对截断/省略写入 trace 并在 prompt 中明确提示。
- `venue.stop_criteria` 明确写入 `advisory`、未执行状态；章节缺失仍是独立的确定性检查。

**阶段门**：所有确定性回归通过；无跨版本决定误套；构建产物可在干净 Windows 环境启动。

### 阶段 B：确定性统计审计器 v2

先做可复算、可解释的规则：`t/F/χ²/z/r` 与自由度、p 值、置信区间、均值±SD/SE、百分比/计数、混淆矩阵指标。每条结果保留公式、输入数字、来源 block 和复算值。暂不训练新模型。

**阶段门**：每个 detector 有正例、负例和跨章节例；报告 precision、recall、FPR、anchor accuracy 和运行时间。

### 阶段 C：Issue Ledger 与多版本追踪

把一次 run 扩展成 manuscript project：跨版本匹配 `uid`，记录 open / addressed / partial / disputed / regressed；输出章节、数字、引用和图表的变化摘要。先做本地 JSON ledger 和 CLI，再接面板。

**阶段门**：同一问题在 v1→v2 保持身份；新增、关闭和回归均可复现；安全修订只读取当前版本的 accepted ledger 条目。

### 阶段 D：Claim–Evidence Graph

在现有 citation integrity 和本地证据目录之上抽象 `EvidenceProvider`。先支持本地库和已有元数据服务，统一保存 source、locator、quote、retrieval timestamp；外部全文检索和 contradiction detection 放在后面。

### 阶段 E：LaTeX/BibTeX backend

复用 DocumentIR 和稳定锚点，先支持 `.tex/.bib` 的 source file + line mapping、引用解析和只读审计；暂不做 Overleaf/浏览器插件。

## 暂缓事项

视觉/公式审计、训练专用 reviewer、SaaS/远程 GUI、Docker sandbox、复杂 OCR 和大规模外部金标准语料，等阶段 A–D 有真实文档验证后再排期。

## 本轮评审请先确认

1. 是否接受 `uid` 作为后续跨版本追踪的唯一内部身份，`Fxxx` 只保留为当前报告显示编号。
2. 下一轮优先做阶段 A 的发布门禁，还是直接进入阶段 B 的统计复算器。
3. 是否保留 `dist/` 本地构建产物；默认不再纳入 Git。
