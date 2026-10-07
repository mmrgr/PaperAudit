# PaperRevamper

**PaperRevamper — Evidence-grounded pre-submission verifier for research manuscripts。** 输入 Word、文本型 PDF 或 GROBID TEI，输出可逐条核查的问题清单，并在你确认后于 DOCX 副本上安全改写。

## 核心分工

PaperRevamper 默认由**宿主 agent（WorkBuddy / Codex）协调多个学术审查角色**，也支持在控制面板中配置 OpenAI、Anthropic、Gemini、DeepSeek、通义、智谱、Moonshot 及任意 OpenAI 兼容 API 做单次调用或后续 Agent 扩展。项目始终负责可复核的解析、证据门禁和报告。

| 谁 | 做什么 |
|---|---|
| **PaperRevamper** | 解析、定位、清单、证据门禁、改写执行、回归复验 |
| **宿主 agent / 可选模型 API** | 按顺序号执行语义审查，同序 Agent 并行并回填结构化结果；可配置 API 用于连接测试和直接调用 |
| **你** | 勾选要改哪些 |

好处：Coding Agent 模式不需要 API Key；角色职责和 skill 路由可追踪；分批读章节天然省上下文且可并行。

---

## 安装

```bash
pip install -e .
# 原生 PDF 审查（可选）
pip install -e ".[pdf]"
```

> Scripts 目录通常不在 PATH，因此**用 `python -m paperrevamper.cli`，不要用裸 `paperrevamper` 命令**。

### 改名兼容

项目现名为 **PaperRevamper**，Python 包和命令也使用 `paperrevamper`。升级时会继续读取旧版的 `out/paperaudit-llm.json`、`.paperaudit/jobs/`、旧 Windows 运行目录以及旧面板令牌名称；新任务和新配置使用 `paperrevamper` 名称。Windows DPAPI 密钥格式保持兼容，已有 API Key 不需要重新录入。

---

## 四步工作流

```bash
# 1. 解析 → 审查包（清单 + 分章节原文）
python -m paperrevamper.cli prepare 论文.docx --out ./run/
# PDF 也可直接 prepare；需要先安装 paperrevamper[pdf]
# python -m paperrevamper.cli prepare 论文.pdf --out ./run/
# 可选：使用本机 GROBID 服务解析 PDF（保留 TEI 的页码/坐标）
# python -m paperrevamper.cli prepare 论文.pdf --out ./grobid-run/ --pdf-parser grobid --grobid-endpoint http://127.0.0.1:8070/api/processFulltextDocument
# 也可以直接审查已经保存的 GROBID TEI XML
# python -m paperrevamper.cli check paper.tei.xml

# 2. 多 Agent 读 run/context/sec_*.md，按角色回填 findings.<role>.json

# 3. 证据门禁 → 去重 → 排序 → 报告
python -m paperrevamper.cli verify ./run/

# 也可以显式选择模型 profile 由 PaperRevamper 执行角色（推荐 API Key 通过环境变量）
# python -m paperrevamper.cli run-review ./run/ --config ./out/paperrevamper-llm.json --profile openai

# 可选：聚合两个独立 judge model 的 claim-first/evidence-first 双位置裁决
# judgments.json 需包含 finding_id、judge_model、position、verdict(yes/no/cannot_assess)
# python -m paperrevamper.cli adjudicate ./run/ --judgments ./judgments.json
# 也可以显式调用已配置的多个 provider，自动执行两种位置并写入 checkpoint
# python -m paperrevamper.cli run-panel ./run/ --profiles openai deepseek --config ./out/paperrevamper-llm.json
# 若上次运行中断，默认从 review.runtime.json checkpoint 继续；--fresh 强制全部重跑

# 配置也可以由面板保存，或手写最小 JSON，并设置 OPENAI_API_KEY：
# {"active_profile":"openai","profiles":[{"id":"openai","protocol":"openai_compatible","base_url":"https://api.openai.com/v1","model":"gpt-4.1-mini","api_key_env":"OPENAI_API_KEY","enabled":true}]}

# 4. 确认后改写（输出到新文件，自动备份，原文件不动）
# 先生成只读 EditProposal/冲突预览（不会修改 DOCX）
python -m paperrevamper.cli plan-revision 论文.docx --run-dir ./run/ --findings F003,F007 --format markdown
# 用户确认后再应用
python -m paperrevamper.cli apply 论文.docx --run-dir ./run/ --findings F003,F007

# 可选：引用完整性审计（默认离线；只有 --online 才访问公共元数据服务）
python -m paperrevamper.cli citation-integrity 论文.docx --format markdown
python -m paperrevamper.cli citation-integrity 论文.docx --online --provider both --cache ./out/citations-cache.json
# CI 门禁：命中状态时返回退出码 1
python -m paperrevamper.cli citation-integrity 论文.docx --fail-on mismatch --fail-on retracted --fail-on unresolved

# 清单注册表：查看可用清单，或按文档类型给出推荐顺序
python -m paperrevamper.cli list-checklists
python -m paperrevamper.cli list-checklists --recommend 论文.docx --format json

# 投稿配置：只执行配置中明确声明的章节门禁，并把配置快照写入 run
python -m paperrevamper.cli list-venues
python -m paperrevamper.cli prepare 论文.docx --out ./run/ --venue generic
# 会议/期刊规则应先保存为经过审核的 venue YAML，再显式传入：
# python -m paperrevamper.cli prepare 论文.docx --out ./run/ --venue ./venue.yaml

# 本地标注语料回归：输出 precision / recall / F1
python -m paperrevamper.cli benchmark benchmarks/smoke.json --format markdown --fail-under 1
```

`prepare` 会生成 `collaboration.plan.json` 和 `collaboration.md`。宿主按顺序号运行多个独立角色：默认四个角色都是顺序 1，因此并行执行；可以把某个角色改为顺序 2、3……让它依次执行。各角色分别输出 `findings.<role>.json`，所有结果随后由代码统一做证据门禁和保守去重。旧的裁决输出文件仍可被历史运行读取。`--venue` 使用配置驱动的投稿模式：配置中的 `required_sections` 会生成确定性章节缺失 finding，原始配置会保存为 `venue.profile.json`；内置配置只是模板，不声称替代官方 author guidelines。

## 可视化多 Agent 修改控制面板

不需要逐行修改 JSON 或代码时，可以启动本地控制面板：

```text
python -m paperrevamper.cli panel --run-root out/panel-runs --open
```

面板默认只绑定本机地址。若确需局域网访问，必须显式开启并设置令牌；浏览器打开启动日志中的带令牌地址：

```powershell
python -m paperrevamper.cli panel --host 0.0.0.0 --allow-remote --auth-token "替换为长随机令牌" --open
```

远程模式下 API 请求必须带 `X-PaperRevamper-Token` 或有效的 HttpOnly Cookie；首次打开可使用启动日志中的带令牌地址完成 bootstrap，浏览器随后会移除地址栏中的 query token。这样可以避免把本机文档、模型配置和修改接口无认证暴露到网络。

面板提供论文路径和审查包创建、PDF 原生/GROBID 解析器选择、阶段图、角色 skill 和任务提示、证据意见筛选、`accept / reject / contest` 决策、双位置 Panel 裁决、源文件 hash 复核、EditProposal 修订计划预览、副本修改、备份、回归结果和 trace 时间线。默认由宿主 Agent 执行审查角色；需要直接调用已配置模型时使用 `run-review`，需要 provider-backed 双位置裁决时使用 `run-panel` 或面板中的“模型 Panel”按钮。每个模型/位置任务会单独保存响应和 hash，`panel.runtime.json` 支持中断恢复，确定性 `adjudicate` 只聚合完整判断，不把缺失结果当作支持。Windows 上通过面板保存的 API Key 使用当前用户 DPAPI 加密，配置文件不会保存明文密钥；跨平台环境建议使用 `api_key_env`。

解析、验证、副本修改和直接模型审查任务会把状态持久化到运行目录下的 `.paperrevamper/jobs/`；模型角色另写入 `review.runtime.json`，按 plan/profile 指纹逐角色 checkpoint。服务重启后未完成任务会标记为 `interrupted`，面板可协作取消正在运行的任务，并在重新校验输入后恢复可恢复任务。

“流程编排”区域还可以新增、停用或删除 Agent，修改角色名称、提示词、skills、清单组和执行顺序。顺序号从小到大依次执行，同一顺序号并行。通过“验证流程”和“保存流程”后，下一次审查包会生成新的 `collaboration.plan.json`；已有运行目录保持不可变。命令行也支持：

流程编排同时提供工作流画布：可以用鼠标拖动 Agent 模块调整可视布局，点击模块即可在设置面板中选择多个上游任务。画布上的箭头表示信息依赖，保存普通信息流会写入目标任务的 `depends_on`；拖动只改变 `position`，不会改变执行顺序。保存或验证时会检查未知任务和普通依赖循环。

点击任意画布模块会打开设置面板：Agent 可修改名称、提示词、启用状态和执行顺序；起始节点和末尾报告节点也可修改名称、说明和位置。设置面板还可以给当前模块添加有最大次数的循环回流，例如把 `verify` 的结果送回某个 Agent 重做。循环回流保存在 `feedback_edges` 中，不会破坏一次性依赖图，由宿主按最大次数执行。

控制面板会异步执行解析、验证和副本修改，并实时显示“正在执行……”阶段、进度百分比和失败原因；过程同时写入 `trace.jsonl`。工作流页的“模型与调用通道”可以保存多个提供商配置、使用环境变量引用密钥、测试连接、删除自定义配置，并保留 WorkBuddy / Codex 宿主通道。`max_output_tokens` 会映射到 Anthropic 的 `max_tokens`、Gemini 的 `generationConfig.maxOutputTokens`；OpenAI 兼容 API 默认发送 `max_tokens`，可按服务改为 `max_completion_tokens` 或关闭该字段。

```text
python -m paperrevamper.cli prepare 论文.docx --out run --workflow workflow.json
```

### 第 1 步产物

| 文件 | 内容 |
|---|---|
| `manifest.json` | 概览、doc_type、清单项数、确定性预检结果 |
| `ir_snapshot.json` | 可移植的解析快照；源文件移动或变化时供 verify 复现证据门禁 |
| `outline.md` | 章节树（带块 ID） |
| `deterministic.md` | 工具已查出的问题 |
| `context/sec_*.md` | 按章节切分的原文，带 `[`p_0063`]` 锚点 |
| `findings.template.json` | 回填模板 |

### 回填格式

```json
{
  "findings": [
    {
      "checklist_id": "A04",
      "severity": "major",
      "block_ids": ["p_0063"],
      "verbatim_quote": "原文中能精确匹配的连续片段",
      "rationale": "为什么是问题",
      "suggested_fix": "改写后的整段文本",
      "confidence": 0.8
    }
  ]
}
```

`verbatim_quote` 必须真实存在于原文 —— **编造的引文会被证据门禁拦下**。`verify` 会优先校验源文件 hash；源文件不可用或已变化时使用 `ir_snapshot.json`，避免对另一份文稿给出报告。

DOCX 标题识别同时读取 Heading 样式、样式继承、`w:outlineLvl`，并对常见手工编号/命名标题做保守回退，减少学校模板中“加粗居中但不是 Heading 样式”造成的结构漏检。

DOCX 解析还会读取 `word/footnotes.xml` 和 `word/endnotes.xml` 中的正文段落；脚注/尾注使用独立块锚点参与引用、数字和交叉引用检查，恶意或过大的 note XML 会被拒绝并记录警告。

`verify` 随后构建精度优先的 Finding Graph：只有共享位置且证据高度重合的同类意见才会折叠为重复项；其余意见保留为 `supports`、`contradicts` 或 `same_root_cause` 关系。报告中的 `support`、`reviewer_ids`、`related` 和 `finding_graph` 摘要用于复核，原始角色文件始终保留。

### 第 4 步的安全保证

- 输出到 `<原名>_revised.docx`（新文件）
- 改前自动备份 `<原名>.bak-<时间戳>.docx`
- 改完自动回归复验
- 修订前校验审查包中的源文件 hash；源文件变化时拒绝执行
- 同一段落被多个意见修改、`C_substantive` 提案或乐观锁前置条件不满足时拒绝写入
- 使用临时 DOCX 文件原子发布，保存失败不会留下半成品
- **原文件始终保持未修改**

`--text "..."` 可覆盖 `suggested_fix`，便于口述改法。

---

## 审查清单

`checklists/registry.yaml` 是清单注册表；当前内置通用 `academic` 清单（8 组 40 项），并提供 PRISMA、STROBE、CONSORT 三个标记为 advisory 的研究设计精简包。推荐只定位候选项，不声称替代官方完整指南：

`venues/registry.yaml` 是投稿配置注册表。配置可以指定清单、必需章节和停止条件；使用 `--venue-registry` 可指向团队审核过的本地版本。PaperRevamper 只执行配置中明确的机械门禁，不会把模板推断成期刊官方要求。

| 组 | 项数 | 覆盖 |
|---|---|---|
| `structure` 结构与完整性 | 4 | 章节规范、篇幅失衡、内容重复、编号体系 |
| `argument` 论证与逻辑 | 6 | 研究问题、gap、贡献清晰度、过度声明、论证链 |
| `method` 方法严谨性 | 6 | 可复现性、数据来源、基线/消融、假设边界、混淆因素 |
| `data` 数据与统计 | 5 | 统计检验、数字一致性、图文吻合、可疑数值 |
| `citation` 引用规范 | 6 | 真实性、支撑性、缺引、格式统一、覆盖度 |
| `figure` 图表规范 | 4 | 编号对应、题注、正文解读、可读性 |
| `language` 表述与语言 | 5 | 术语统一、缩写定义、冗余、病句 |
| `consistency` 跨章节一致性 | 4 | 摘要与正文、首尾呼应、符号定义 |

清单是**一等公民**：瓶颈在「定位错误」而非「修复」（RefineBench 实证），
给出显式清单才能把定位外部化。`list-checklists --recommend` 会同时扫描标题、摘要和正文中的研究设计词，再按关键词和文档类型排序。`--checklist academic`、`--checklist prisma` 等可直接引用注册表别名，也支持传入外部 YAML（会议/期刊要求）。

---

## 附带：独立确定性检查

不需要宿主参与，单独跑机械检查：

```bash
python -m paperrevamper.cli check 论文.docx
python -m paperrevamper.cli check 论文.docx --format json
python -m paperrevamper.cli check a.docx b.docx --out ./out/   # 多篇串行
```

### 原生 PDF 审查（可选依赖）

安装 `pdfplumber` 后，`check` 和 `prepare` 可直接读取 PDF，不再先投影成临时 DOCX：

```powershell
pip install -e ".[pdf]"
python -m paperrevamper.cli check 论文.pdf --out out/pdf/
python -m paperrevamper.cli prepare 论文.pdf --out out/pdf-run/
```

原生适配器保留页码和 bounding box 锚点，并将可识别的表格作为 `TABLE` block 写入 `DocumentIR`；`ir_snapshot.json` 会保存这些几何信息。PDF 当前支持审查和报告，安全修订仍只对 DOCX 开放。若 PDF 没有可提取的文本层，工具会标记 `scan_likely` 并以非零状态退出，避免把“没有读到文字”误报成“没有问题”。复杂双栏阅读顺序、公式语义、扫描件 OCR、批注和完整视觉版式仍需专用解析器或 GROBID 增强。

如果已运行 GROBID，可通过 `--pdf-parser grobid` 走 `processFulltextDocument`，解析 TEI 中的章节、参考文献、表格、引用和 `coords` 页坐标；保存的 GROBID `.xml` 也能直接作为输入。服务不可用时命令失败并返回错误，不会静默降级到另一种解析结果。GROBID 解析器是可选服务，不会增加核心 Python 依赖。

`check` / `citation-integrity` 遇到扫描件会输出警告并返回退出码 `2`；这表示结果不完整，需要先接入 OCR 或提供带文本层的 PDF。

旧的 `tests/pdf_adapter_smoke.py` 仍保留，用于对比纯文本投影路径；它不代表原生 PDF 结果。

## Windows 桌面版

运行 `build_exe.ps1 -Mode onedir` 生成 `dist\PaperRevamper\PaperRevamper.exe`；`-Mode onefile` 生成单文件 `dist\PaperRevamper.exe`。双击后会自动打开本地控制面板，顶部切换“论文审查”和“多 Agent 工作流”两个功能区。运行数据默认保存到 `%LOCALAPPDATA%\PaperRevamper\runs`。完整说明见 [docs/EXE_RELEASE.md](docs/EXE_RELEASE.md)。

检测器：`citations` `crossref` `structure` `terminology` `numbers` `privacy`。

其中 `numbers` 使用 `numeric_facts` 的保守数字事实图：从正文和表格提取百分比、均值±不确定度、置信区间、p 值、样本量和类别总计，并在上下文足够一致时报告冲突；同段明确报告的 precision/recall/F1 还会做数学一致性校验；年份、引用编号和无明确指标标签的裸数字会跳过。

`verify` 还会运行精度优先的 Finding Graph：同类意见只有在共享位置且证据高度重合时才折叠为重复项；支持、矛盾和共同根因关系会保留在 `finding_graph`、`support`、`reviewer_ids` 与 `related` 字段中，原始角色结果不被覆盖。

`citation-integrity` 是独立的引用完整性审计：解析 DOI、arXiv、PMID 和作者年份条目，关联正文引用位置，并输出 `verified / mismatch / unresolved` 元数据状态以及 `corrected / retracted / concern / unknown` 出版状态。无 DOI 的作者年份条目在显式 `--online` 时可通过 Crossref bibliographic search 做保守标题/年份匹配；匹配分数会保留在 metadata 中。若返回摘要、描述或通过 `--evidence evidence.json` 提供证据段落，还会给出标记为 heuristic 的 `supported / partially_supported / unsupported / uncertain` 支撑信号；它不会把词面重合当成最终学术判断。默认离线运行不会声称文献已被外部数据库验证；`--online` 才会调用 Crossref 或 Semantic Scholar，结果会写入可复用的本地 JSON 缓存。

`adjudicate` 是独立的 panel 裁决层：它要求每条 finding 至少有两个独立 judge model，并分别提交 `claim_first` 与 `evidence_first` 两种位置的二元判断。只有四组判断全部一致才进入 `confirmed` 或 `refuted`；位置交换不一致进入 `contested`，缺少位置、模型或全部弃权进入 `unverifiable`。原始判断和 `raw_json` 会写入 `adjudication.json`，不会覆盖角色 findings。`run-panel` 是显式 provider 编排器：它读取同一份 bounded finding/IR packet，隔离不可信论文内容，按模型×位置并行调用 `llm.chat`，逐任务落盘并从 checkpoint 恢复；模型响应无法解析时保留失败任务，最终仍由 `adjudicate` 给出四态结果。

`plan-revision` 是安全修订的只读阶段：它生成 `revision.plan.json` 和可读的 `revision.diff`，为每个 EditProposal 保存目标块、字符范围、`expected_old_text`、replacement、风险级别和冲突图。只有计划通过后，`apply` 才会在作者 accept 的 finding 上创建备份并写入新文件；多段替换、C 级实质改写、源 hash 变化和目标冲突会在计划阶段阻断。

本地证据包也可以为每个 passage 写入 `source`、`locator`、`confidence`，例如 `{"doi:10.1234/x": [{"text": "...", "source": "local-paper", "locator": "p. 3", "confidence": 0.91}]}`；报告会同时保留 `evidence_passages`、`evidence_provenance` 和 `evidence_confidence`，避免把摘要、全文片段和作者提供的材料混成同一种证据。解析器给摘要/description/summary/snippet 设置 0.65/0.60/0.55/0.45 的保守先验；显式低置信度证据不会被词面重合直接升级为 `supported`。`--evidence` 也接受文本/Markdown/TEI 目录：文件名或前 8 KiB 中的 DOI、arXiv、PMID 用来建立关联，段落会按正文 claim 做可复现的词面排序，定位保留为 `相对路径#pN`。目录检索完全离线，不会把未标识的文档推断成某条引用的证据。例如：`python -m paperrevamper.cli citation-integrity 论文.docx --evidence .\evidence\`。

### 确定性检查基准

`benchmark` 读取 JSON/YAML 标注语料，支持本地 DOCX/PDF/GROBID XML 或嵌入式 `DocumentIR`。每个 `expected` 标签至少写 `issue_type`，可选 `severity` 和 `block_ids`；带锚点的标签只与共享块 ID 的发现匹配。结果提供逐案例和 micro precision/recall/F1/FPR，可用 `--fail-under` 作为 CI 门槛。`benchmarks/smoke.json` 是基础回归集，`benchmarks/adversarial.json` 额外覆盖引用断号、空题注、数字冲突、缩写定义和标题跳号的正负样本；真实语料应去标识化并随文档快照版本化。

### 双盲与隐私预检

`privacy` 检测器直接扫描 DOCX 的 OOXML 包，检查作者/机构元数据、批注与修订作者、隐藏文字、页眉页脚身份信息、嵌入文件名和外部本地路径；PDF 模式检查 Author/Creator 元数据和正文身份短语；GROBID TEI 模式检查 `teiHeader` 作者、邮箱和机构信息。它不会修改原稿；报告中的 `privacy_risk` 条目应在投稿前逐项确认。
零 LLM 成本，可进 CI。

---

## 证据门禁

每条意见的 `verbatim_quote` 必须能在原文**精确子串匹配**（仅允许空白归一化，不允许模糊匹配）。
匹配失败 → 降级 `unverifiable`，不计入确认问题，但保留在轨迹中。

这是最便宜也最有效的幻觉过滤器 —— 实测能精准拦下编造的引文。

---

## 实测（开题报告样例，327 块 / 63 条文献）

| 项 | 结果 |
|---|---|
| 文档类型识别 | `proposal` 正确 |
| 引用检查 | 编号连续、无列而未引/引而未录（真阴性） |
| 注入缺陷召回 | 3 类（删文献 / 断图引用 / 章节跳号）全部命中 |
| 证据门禁 | 编造引文 1/1 被拦截 |
| 改写 | 新文件 + 备份 + 零回归，原文件未动 |

误报修正经验（已固化为规则）：

- `GB/T 32910` 的 `GB` —— 标准编号，非缩写
- `NSGA-II` 的 `II` —— 算法名后半截
- `ηWTW` 的 `WTW` —— 符号下标

> 关键：**参考文献区边界的正确识别直接决定误报率**。初版未识别"9 参考文献"标题，
> 误报从 3 项飙到 15 项。

---

## 与已有学术 skill 的关系

`nature-citation`、`academic-humanizer`、`nature-polishing` 等都是**生成型**的，
PaperRevamper 是**验证型**的 —— 叠加而非竞争：

```
PaperRevamper prepare（找问题）
      ↓
academic-humanizer（润色）
      ↓
PaperRevamper check（复验有没有改坏）
```

---

## 目录

```
src/paperrevamper/
├─ models.py              核心 schema（块 ID 定位）
├─ ingest/docx_reader.py  DOCX → DocumentIR
├─ ingest/pdf_reader.py   PDF → page-aware DocumentIR（可选 pdfplumber）
├─ ingest/grobid_reader.py GROBID PDF/TEI → 带坐标的 DocumentIR
├─ benchmark.py           标注语料回归与 precision/recall/F1/FPR/gate 统计
├─ citations/             Citation Integrity、证据检索与 provenance
├─ evidence/              6 个确定性检测器（零 LLM）
├─ checklist.py           清单注册、推荐、加载与外部导入
├─ venue.py               投稿配置注册、章节门禁与配置快照
├─ prepare.py             审查包生成
├─ verify.py              证据门禁 + 去重 + 报告
├─ protocol/finding_graph.py  意见关系图与精度优先聚合
├─ jobs.py                面板任务持久化、取消与恢复
├─ runner.py              显式模型 profile 的角色执行、checkpoint 与 verify
├─ panel_runner.py        多 provider 双位置 panel 执行、bounded batch 与 checkpoint
├─ revise.py              副本改写 + 备份 + 回归
├─ reporting.py           报告输出
└─ cli.py                 prepare / verify / plan-revision / apply / check / citation-integrity / benchmark / run-review / adjudicate / run-panel
checklists/registry.yaml  清单注册表
venues/registry.yaml      投稿配置注册表（保守模板）
benchmarks/smoke.json     确定性检查回归语料
checklists/academic.yaml  内置审查清单（一等公民）
skills/paper-audit/       Codex 与 WorkBuddy Skill
```

## 已知限制

- 支持 `.docx` 和原生文本型 `.pdf`；扫描件 OCR、`.doc`、LaTeX 尚未接入
- PDF 的复杂双栏、公式、批注和视觉版式仍可能需要 GROBID 或专用布局解析器
- 引用支撑判断仍是词面启发式；本地证据目录只检索文件名/前缀中明确标识的 DOI、arXiv 或 PMID，不替代人工核读或联网全文索引
- 语义审查依赖宿主模型质量
- 判断性问题（贡献是否成立）不在能力范围内 —— 这是"验证型"定位的边界，不假装能做

项目提供 MIT 许可证、`CITATION.cff` 和 GitHub Actions smoke CI。CI 通过 `tests/smoke_suite.py` 统一运行确定性检查、引用审计、证据目录、直接模型 runner 与 provider-backed panel 的离线桩测试、面板 API、UI 静态文件同步和 wheel 构建，并在 Windows 上构建和启动桌面包；它不代表真实期刊语料上的召回率。
