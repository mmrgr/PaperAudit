# PaperAudit 工作交接

> 供新窗口接续开发。最后更新：2026-10-06。

## 1. 项目在哪

| 项 | 路径 |
|---|---|
| **项目根目录** | `C:\Users\mmrgr\Desktop\论文\lwxm` |
| 源码 | `<根>/src/paperaudit/` |
| 审查清单 | `<根>/checklists/academic.yaml` |
| Skill 源 | `<根>/skills/paper-audit/SKILL.md` |
| 设计方案 | `<根>/docs/PLAN_v2.md`（现行）、`docs/PLAN.md`（v1 存档） |
| 测试样本 | `<根>/tests/_tmp/seeded.docx`（注入缺陷版，1.5MB） |
| 记忆 | `<根>/.workbuddy/memory/2026-09-23.md`（含踩坑记录） |

**Skill 已安装到三处且内容一致（241 行）**：
- `~/.workbuddy/skills/paper-audit/`（WorkBuddy）
- `~/.agents/skills/paper-audit/`（Codex / 通用）
- 项目内 `skills/paper-audit/`（源）

⚠️ 改动 Skill 后记得**三处同步**，否则不同宿主行为不一致。

本轮已同步项目版本元数据至 `0.2.0`（与 `src/paperaudit/__init__.py`、Skill 版本一致）。

---

## 2. 环境（最重要的一条）

```bash
python -m paperaudit.cli ...     # ✅ 正确
paperaudit ...                   # ❌ 不可用！Scripts 不在 PATH
```

- 宿主实际使用的 python：`C:\Users\mmrgr\.workbuddy\binaries\python\versions\3.13.12\python.exe`
- paperaudit 已以 editable 方式装入该 python（指向 `src/`，改代码立即生效，无需重装）
- 依赖：`python-docx` 1.2.0、`PyYAML` 6.0.3；原生 PDF 可选 `pdfplumber`（`pip install -e ".[pdf]"`）
- 重装：`cd <项目根> && pip install -e .`

> 曾经踩过坑：命令装在 venv 里导致宿主调用 command not found，**这是上一轮"效果不好"的主因**，别再退回去了。

---

## 3. 架构：宿主协作式（核心决策，别推翻）

**默认由宿主 agent 协调；PaperAudit 的确定性审查层不调外部 API。** 用户也可以通过显式 `run-review` profile 选择直接模型执行角色，结果仍必须进入同一套 JSON schema、证据门禁和 `verify`。

| 谁 | 做什么 |
|---|---|
| PaperAudit | 解析、定位、清单、证据门禁、改写执行、回归复验 |
| 宿主 agent | 按清单做语义审查与判断 |
| 用户 | 勾选改哪些 |

好处：零 API Key、审查深度随宿主模型、分批读章节天然省上下文且可并行。

### 四步工作流

```bash
# 1 解析 → 审查包（清单 + 分章节原文，带块 ID 锚点）
python -m paperaudit.cli prepare 论文.docx --out ./run/

# 2 宿主按 collaboration.plan.json 并行运行四个角色，分别回填 run/findings.<role>.json

# 2b 按 `order` 顺序执行 Agent；同一 order 并行，分别写 run/findings.<role>.json

# 3 证据门禁 + 合并确定性结果 + 排序 + 报告
python -m paperaudit.cli verify ./run/

# 4 确认后改写（新文件 + 备份 + 回归复验，原文件不动）
python -m paperaudit.cli apply 论文.docx --run-dir ./run/ --findings '*'

# 可选引用完整性审计；默认离线，--online 才访问公共元数据服务
python -m paperaudit.cli citation-integrity 论文.docx --online --provider both --cache ./out/citations-cache.json

# 确定性检查回归语料
python -m paperaudit.cli benchmark benchmarks/smoke.json --fail-under 1
```

另有一个独立命令 `check`（纯确定性检查，零 LLM，可进 CI）：
```bash
python -m paperaudit.cli check a.docx b.docx --out ./out/   # 多篇串行
```

### 模块

| 文件 | 职责 |
|---|---|
| `models.py` | 核心 schema（块 ID 定位、Finding/Verdict 四态） |
| `ingest/docx_reader.py` | DOCX → DocumentIR |
| `ingest/pdf_reader.py` | PDF → 带页码/bounding box 的 DocumentIR（可选 pdfplumber） |
| `ingest/grobid_reader.py` | 可选 GROBID PDF 服务/保存的 TEI XML → DocumentIR；保留章节、引用、参考文献、表格和 coords 锚点 |
| `evidence/` | 6 个确定性检测器：citations / crossref / structure / terminology / numbers（numeric fact graph） / privacy |
| `checklist.py` | 清单注册/推荐、外部导入、doc_type 自动过滤 |
| `venue.py` | 投稿配置注册、required_sections 门禁、配置快照 |
| `prepare.py` | 审查包生成 |
| `verify.py` | 证据门禁 + 去重 + 排序 + 报告；源文件 hash 校验与 IR 快照回退 |
| `protocol/finding_graph.py` | 精度优先的意见关系图：重复、支持、矛盾、共同根因 |
| `jobs.py` | 面板任务持久化、协作取消与恢复 |
| `revise.py` | 副本改写 + 备份 + 回归复验 |
| `reporting.py` | Markdown/JSON 报告 |
| `cli.py` | prepare / verify / plan-revision / apply / check / citation-integrity / benchmark / run-review / adjudicate / run-panel |
| `citations/` | CitationRecord、DOI/arXiv/PMID 解析、Crossref/Semantic Scholar 可选解析器、本地缓存；证据段落保留 source/locator/confidence provenance；LocalEvidenceFetcher 支持带标识的文本/TEI 目录离线检索 |
| `benchmark.py` | JSON/YAML 标注语料回归，逐案例与 micro precision/recall/F1/FPR |
| `runner.py` | 显式模型 profile 的角色执行器；不可信材料隔离、逐角色 checkpoint、输出落盘、trace 和可选 verify |
| `panel_runner.py` | 多 provider × claim-first/evidence-first 并行执行、逐任务响应 hash、checkpoint 恢复和确定性 adjudicate 编排 |
| `secret_store.py` | Windows 当前用户 DPAPI 密钥保护；模型配置公开接口不返回密文或明文 |

---

## 4. 已完成与验证结果

| 能力 | 状态 | 验证 |
|---|---|---|
| DOCX 解析 + 稳定块 ID | ✅ | 支持 outlineLvl、样式继承、手工编号/命名标题回退，以及 footnotes/endnotes 独立块锚点 |
| 6 个确定性检测器 | ✅ | 零 LLM；包含 DOCX 匿名与隐私预检 |
| 文档类型识别 | ✅ | 开题报告→`proposal` 正确 |
| 注入缺陷召回 | ✅ | 3 类（删文献/断图引用/章节跳号）**全中** |
| 证据门禁拦幻觉 | ✅ | 编造引文 1/1 拦截 |
| 改写 + 备份 + 回归 | ✅ | 新文件 + 备份 + 零回归，原文件未动；修订前校验审查包 hash、拒绝覆盖源文件/既有输出、C 级建议和冲突段落不自动写入 |
| 回归捕获副作用 | ✅ | 改写删掉引用 → 报 `citation_unused` |
| 数字冲突证据可定位 | ✅ | 数字检查保留真实匹配片段，避免拼接引文被门禁误拒 |
| 数字事实图 v1 | ✅ | 段落/表格提取百分比、±、CI、p/n、显著性矛盾、表格总计和 precision/recall/F1 数学一致性，并跳过年份与引用编号 |
| Finding Graph v1 | ✅ | 共享位置与证据重合才建边；合并重复意见并保留支持、矛盾、共同根因关系 |
| 门禁实现统一 | ✅ | `check` / `prepare` / `verify` 共用同一定位规则；未知 block ID 不回退全文 |
| verify 精确去重 | ✅ | Finding Graph 仅合并共享位置且证据高度重合的同类意见，并保留支持/矛盾/共同根因关系 |
| 原生 PDF 审查 | ✅ | `ingest/pdf_reader.py`、`ingest/grobid_reader.py`、`tests/pdf_native_smoke.py`；支持 native/GROBID 两条显式路径，保留页码/bounding box；扫描件无文本层时标记 `scan_likely` 并返回非零状态；明确不支持 PDF 修订 |
| 不支持格式退出状态 | ✅ | `check` 遇到非 `.docx` 返回退出码 2，避免把跳过误判为成功 |
| 多 Agent 角色计划 | ✅ | `collaboration.plan.json`、可配置 Agent 顺序；同序并行、异序串行 |
| 学术 skill 路由 | ✅ | `skills/academic/registry.yaml`、`checklists/skill-routing.yaml`，并同步三处项目 skill |
| 角色结果聚合 | ✅ | `verify` 合并 `findings.<role>.json`，优先读取 `findings.adjudicated.json`，报告角色状态 |
| 协作过程追踪 | ✅ | `trace.jsonl` 记录角色输入文件、候选数和验证结果；`review.md` 展示执行顺序与角色覆盖 |
| 可移植审查包 | ✅ | `ir_snapshot.json` 保存解析 IR；源文件移动、删除或 hash 变化时 verify 使用快照 |
| 引用完整性 v1 | ✅ | 离线保守解析 + 可选 Crossref/Semantic Scholar；DOI-less 条目支持保守 bibliographic search；输出 metadata/publication 状态、证据 provenance 与引用位置 |
| 过程复盘 | ✅ | `docs/PROCESS_REVIEW_multiagent.md` 记录本轮 sol worker 规划、实施步骤和验证证据 |
| 可视化修改控制面板 | ✅ | `paperaudit panel` + `src/paperaudit/panel.py` + `ui/control-panel.html`；支持状态、决策、证据和安全副本修改 |
| 可编辑 Agent 工作流 | ✅ | 面板支持角色增删/启停、skills、清单组、并行组、提示词和任务依赖覆盖；`prepare --workflow` 可复用 JSON 配置 |
| 清单注册表 | ✅ | `checklists/registry.yaml`、academic + PRISMA/STROBE/CONSORT advisory 包、别名解析与基于研究设计词的文档推荐；仍可传入外部 YAML |
| 投稿配置模式 | ✅ | `venues/registry.yaml`、`list-venues`、`prepare --venue`；只执行显式 required_sections，保存 `venue.profile.json`；内置项是模板而非官方规则 |
| 确定性基准 runner | ✅ | `benchmark` 命令、嵌入式 DocumentIR/本地文档语料、锚点匹配与 CI `--fail-under`；附 `benchmarks/smoke.json` |
| 对抗回归语料 | ✅ | `benchmarks/adversarial.json` 覆盖 12 个引用断号、空题注、数字冲突、缩写定义和标题跳号正负样本；CI 固定 precision/recall/FPR，并统计 gate-rejected |
| GROBID/TEI 隐私审计 | ✅ | `privacy` 检查 TEI header 作者、邮箱和机构信息；面板可选择 native/GROBID parser |
| 本地全文证据目录 | ✅ | `citation-integrity --evidence <dir>` 扫描带 DOI/arXiv/PMID 标识的文本/Markdown/TEI 文件，按 claim 词面排序并保留相对路径段落定位；不做网络检索或未标识文档推断 |
| 直接模型角色执行 | ✅ | `run-review` 逐角色读取 bounded artifacts，强制 untrusted prompt 边界，输出 `findings.<role>.json`，写入 plan/profile 绑定的 `review.runtime.json` 并默认运行 `verify`；`host_agent` profile 不会被误调用 |
| 双位置多模型 panel 裁决 | ✅ | `run-panel` 按 provider × `claim_first`/`evidence_first` 并行执行并写入 `panel.runtime.json` checkpoint；`adjudicate` 要求两个独立 judge model，代码聚合为 `confirmed / contested / refuted / unverifiable`，保留原始判断 |
| EditProposal 修订计划 | ✅ | `plan-revision`、`/api/revision/plan` 和面板预览生成目标 span、`expected_old_text`、replacement、风险级别、冲突图及 `revision.diff`；`apply` 复用同一 preflight 并写入 `revision.plan.json` |
| 模型密钥保护 | ✅ | Windows DPAPI 保存 `api_key_protected`，配置文件无明文 key；环境变量仍是跨平台推荐路径 |
| 发布与 CI 基础设施 | ✅ | MIT `LICENSE`、`CITATION.cff`、`CHANGELOG.md` 和 `.github/workflows/ci.yml`；统一 smoke 入口、三版本 Python + Windows 桌面包构建、wheel 构建 |
| Gemini system prompt | ✅ | Gemini 适配器将 system 消息映射到 `systemInstruction`，不会丢失证据门禁约束 |
| 控制面板远程访问保护 | ✅ | 非本机绑定必须显式 `--allow-remote` + `--auth-token`；API 校验 header/Cookie 令牌，启动 URL 只用于一次性 bootstrap |
| 面板任务持久化与恢复 | ✅ | `.paperaudit/jobs/*.json`；重启标记 `interrupted`，支持取消和重新校验后恢复 |

清单 8 组 40 项：structure(4) argument(6) method(6) data(5) citation(6) figure(4) language(5) consistency(4)。

---

## 5. 关键设计约束（改代码前先读）

1. **块 ID 用 body 序（`p_0063`），绝不用字符偏移** —— 偏移在插入后会整体位移，导致内容插到错误位置。
2. **参考文献区边界决定 FPR** —— `_BIB_HEADING` 必须容忍编号前缀（"9 参考文献"）。初版没识别，误报从 3 项飙到 15 项。
3. **证据门禁要求 quote 能在原文精确匹配**（仅允许空白归一化，不允许模糊匹配）。清单型 quote 按顿号拆分逐项判定。
4. **构造出来的 quote 会被门禁拦截** —— structure 检测器必须用真实标题文本，不能用"2.1…3"这种拼接。
5. **缩写检测的噪音规则**（正则 `(?<![\w\-])([A-Z][A-Z0-9]{1,5}(?:\.[0-9]+)?)(?![A-Za-z0-9/])`）：
   - `GB/T 32910` 的 GB = 标准编号
   - `NSGA-II` 的 II = 连字符后段
   - `ηWTW` 的 WTW = 符号下标
   - 注意 `(?<![^\W\d_\-])` 是**错的**，`-` 会被 `^\W` 排除导致断言失效
6. **定义查找用全文扫描**，不要只看首次出现那一段（否则后文有定义的 WUE 会误报）。
7. **precision-first**：HALLMARK 实证表明低发生率下 FPR 而非召回率决定可用性，宁可漏报不误报。
8. **绝不修改原文件** —— 改写输出到新文件，改前备份。这是用户的硬要求。

---

## 6. 待办（按优先级）

| 优先级 | 事项 | 说明 |
|---|---|---|
| 高 | 用真实文档端到端跑一遍 | 原开题报告已不在（`Desktop\开题\` 被清空），需用户提供新样本 |
| 中 | 扩展回归语料 | 已有 benchmark runner、smoke corpus 与十二例对抗回归集；下一步用去标识真实文档补充跨章节数字对齐与格式回归 |
| 中 | 数字事实图继续扩展 | 当前规则覆盖高价值统计事实；摘要↔正文↔结论的语义实体对齐仍需真实语料评估后再扩展 |
| 中 | 合并确定性结果进 verify 时的关系审计 | Finding Graph v1 已接入；仍需用真实多角色语料校准边关系阈值 |
| 低 | PDF 解析深度 | 已支持 pdfplumber 原生解析和显式 GROBID PDF/TEI 路径；无文本层会标记 `scan_likely` 并阻止“空文档=无问题”的误判；扫描件 OCR、复杂双栏、公式、批注和完整视觉版式仍待后续 |
| 中 | 扩展模型 runtime / 四态 panel | `run-review`、`run-panel` 与 `adjudicate` 已提供逐角色执行、双位置 provider 编排、checkpoint、裁决和 verify；真实 provider 的跨模型成本、长文 packet 质量和完整 UI 任务流仍需独立评估 |
| 中 | 修订 proposal editor | 已提供只读 `plan-revision` 和面板预览；复杂多段 proposal 的可视 diff、用户逐 proposal 编辑仍可继续增强 |
| 中 | 外部文献检索（Crossref/S2） | 已提供显式 `citation-integrity --online`、摘要支撑和 `--evidence` 本地 JSON/文本/TEI 证据；联网全文检索、外部论文索引和 OCR 仍待后续 |

**暂不做的**（PLAN_v2 已定）：token/模型预算系统、Docker sandbox、LaTeX 编译门禁、reputation 矩阵、远程 SaaS GUI、MCP server。本地控制面板已接入。

---

## 7. 复现验证的方式

```bash
cd "C:\Users\mmrgr\WorkBuddy\2026-09-22-14-09-30\PaperAudit"
PY="C:\Users\mmrgr\.workbuddy\binaries\python\versions\3.13.12\python.exe"

# 确定性检查（用现有样本）
"$PY" -m paperaudit.cli check tests/_tmp/seeded.docx

# 召回验证：注入 3 类缺陷，应全部命中
# 需传入一份「干净」的 .docx 作为基准（注入脚本要求原文编号连续、结构完整）
PYTHONPATH=src "$PY" tests/seeded_check.py <你的文档.docx>
# 或：PAPERAUDIT_SAMPLE=<路径> PYTHONPATH=src "$PY" tests/seeded_check.py
```

⚠️ `tests/_tmp/seeded.docx` 是**已注入缺陷**的样本（缺 [5]、有图9-9、编号跳号），
用它当基准会导致部分注入项失败。召回验证请用干净文档。

---

## 8. 与 DelphiOpt 的关系

PaperAudit 结构对标用户的另一个项目 `mmrgr/DelphiOpt`（预算感知的多智能体代码优化运行时），继承了它的核心心法 **"LLM 提议，可执行的证据做决定"**，并做了两处关键改造：

1. 把"发现"与"裁决"解耦（论文没有客观门禁，不能照搬代码优化的接受判据）
2. 不做预算系统（用户明确说先不做）

DelphiOpt 的中文 README 在 `C:\Users\mmrgr\WorkBuddy\2026-09-22-14-09-30\DelphiOpt\`（README.md 中文 + README.en.md 英文），尚未推送到 GitHub（本机 git 无法访问 GitHub）。
