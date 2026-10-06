---
name: paper-audit
description: >-
  对 Word 论文 / 开题报告 / 学位论文做投前自审与修改：结构完整性、论证逻辑、方法严谨性、
  数据一致性、引用真实性与支撑性、图表规范、术语统一、跨章节一致性。
  工作流为「工具解析定位 + 你做语义审查 + 工具门禁校验 + 副本改写」。
  当用户要求"审查论文/开题报告/学位论文""投前检查一下""审一下这篇 docx"
  "检查引用""看看有没有问题""帮我改一下论文"时使用。
  不用于纯润色改写（那些用 academic-humanizer / nature-polishing）。
description_zh: "Word/PDF/GROBID 论文投前自审与修改：工具负责解析定位与门禁，宿主或显式模型 runner 负责语义审查，副本改写不动原文件"
description_en: "Pre-submission review and revision for Word, PDF, and GROBID manuscripts: the tool handles parsing and grounding, host or explicit model runner handles semantic review, revisions go to a copy"
version: 0.2.0
allowed-tools: Bash,Read,Write,Edit,Grep
---

# PaperAudit —— 论文投前自审与修改

**分工**：工具做它擅长的（解析、定位、清单、门禁、改写执行、回归）；**你做你擅长的**（语义审查与判断）。
默认不需要 API Key，且天然支持并行（分批读章节）；若用户显式配置 provider，也可以通过 `run-review` 执行结构化模型角色。

## 多 Agent 学术审查协作

`prepare` 会生成 `collaboration.plan.json` 与 `collaboration.md`。宿主应按计划启动独立角色：顺序号从小到大依次执行，同一顺序号并行，不能把整篇论文交给一个“通用审稿 agent”：

| 角色 | 负责范围 | 主要 skill |
|---|---|---|
| `argument_reviewer` | 结构、研究问题、gap、贡献、过度声明、首尾一致 | `nature-writing`、本 skill |
| `method_data_reviewer` | 方法、数据来源、假设、统计、敏感性和复现 | `nature-writing`、`nature-data`、本 skill |
| `citation_figure_reviewer` | 引用支撑、引用格式、图表编号/题注/解读 | `nature-citation`、`nature-reader`、本 skill |
| `language_editor` | 术语、缩写、语法、冗余、claim-evidence 表述 | `academic-humanizer`、`nature-polishing`、本 skill |

各角色按顺序读取允许的章节上下文并独立写 `findings.<role>.json`；代码随后统一执行证据门禁、保守去重并生成审查报告。历史运行中的 `findings.adjudicated.json` 仍可兼容读取。

### 修改多个 Agent 的既定流程

控制面板的“流程编排”允许一次性调整整个角色协作契约：启用/停用或删除多个 Agent，新增自定义 Agent，修改每个 Agent 的职责提示、skills、清单组和执行顺序。顺序号从小到大依次执行，同一顺序号并行。点击“验证流程”后再保存；“应用到下一次审查包”会把同一份配置传给 `prepare`，已有运行目录的计划和结果不会被改写。

流程编排还提供可拖拽工作流画布。拖动 Agent 模块只保存 `position` 布局；点击模块并选择多个上游后，箭头会转成目标任务的 `depends_on`，从而表达一对多或多对一信息依赖。验证和保存时会拒绝未知任务或普通依赖循环。

点击模块可编辑 Agent 参数或起始/末尾节点的名称与说明，也可为目标模块配置带 `max_iterations` 上限的 `feedback_edges` 循环回流。反馈边不加入一次性依赖图，由宿主按上限重新运行目标 Agent；普通依赖仍执行环检测。

也可以把配置保存为 JSON，供命令行复用：

```powershell
python -m paperaudit.cli prepare 论文.docx --out run --workflow workflow.json
```

配置会写入 `collaboration.plan.json` 并带有 workflow hash。依赖必须指向已存在任务且不能形成环；角色输出文件只能是运行目录内的相对路径。默认由宿主 Agent 执行角色；显式模型 profile 可用 `python -m paperaudit.cli run-review <运行目录> --profile <id>` 执行，结果仍必须经过同一套 schema、证据门禁和报告生成。

`run-review` 会把 plan/profile 指纹和已完成角色写入 `review.runtime.json`；重试时只重跑缺失或失败角色。使用 `--fresh` 可显式清空 checkpoint 并全部重跑。

如果配置了两个独立 judge model，可在 `verify` 后用 `python -m paperaudit.cli run-panel <运行目录> --profiles <model-a> <model-b> --config <llm.json>` 自动执行双位置 provider 任务。长审查包按 bounded finding batch 拆分；每个模型/位置/batch 响应写入独立 JSON，`panel.runtime.json` 保存输入指纹、完成任务和失败原因，默认可恢复；`--fresh` 强制重跑。也可以用 `adjudicate <运行目录> --judgments <panel.json>` 聚合已有判断。每条记录必须包含 `finding_id`、`judge_model`、`position`（`claim_first`/`evidence_first`）和 `verdict`（`yes`/`no`/`cannot_assess`）。代码只把两模型、两位置全部一致的结果标为 `confirmed` 或 `refuted`；其余保留为 `contested` 或 `unverifiable`，并写入 `adjudication.json`。模型响应解析失败会保留失败任务，不会把缺失判断当成支持。

### 学术 skills 的职责边界

- `nature-writing` 先修论证结构，再修句子；不发明数据、方法、结果或引用。
- `nature-polishing` 与 `academic-humanizer` 只在论证成立后润色，并保持数字、结果和引用不变。
- `nature-reader` 负责长文分段、图表关联和源锚点，不替代审查结论。
- `nature-citation` 只在需要补/核引用时使用，必须核对全文或权威元数据，不能以标题相关代替支撑。
- `nature-data` 专注数据、代码和可复现性声明；`nature-response` 只用于已有审稿意见的逐条回复。

## 铁律

- **绝不修改原文件**。改写一律输出到新文件，改前自动备份。
- 每条意见必须能在原文**精确匹配**到引文，否则被证据门禁拦下，不得报给用户。
- 不确定就说不确定，不要编造意见，尤其**不得编造引用**。

## 命令形式（重要）

Scripts 目录不在 PATH，**必须用 `python -m paperaudit.cli`，不要用裸 `paperaudit` 命令**。

```bash
python -m paperaudit.cli <子命令> ...
```

若报 `No module named paperaudit`，到项目目录执行 `pip install -e .` 后重试。

---

## 工作流

### 第 1 步：prepare —— 生成审查包

```bash
python -m paperaudit.cli prepare <文件.docx> --out <运行目录>
# 有经审核的投稿配置时，显式启用其章节门禁
python -m paperaudit.cli prepare <文件.docx> --out <运行目录> --venue <venue-id-or-yaml>
```

| 产物 | 用途 |
|---|---|
| `manifest.json` | 概览、清单项数、确定性预检结果 |
| `outline.md` | 章节树（带块 ID） |
| `deterministic.md` | 工具已查出的问题（引用编号、交叉引用、编号跳号…） |
| `context/sec_*.md` | **按章节切分的原文**，带 `[`p_0063`]` 锚点 |
| `findings.template.json` | 回填模板 |
| `collaboration.plan.json` / `collaboration.md` | 角色、skill 路由、依赖和门禁 |
| `trace.jsonl` | 不含原文的过程事件（准备、角色输入、验证） |
| `venue.profile.json` | `--venue` 使用的配置快照；只记录明确声明的章节和停止条件 |

`list-venues` 可查看可用配置。内置 venue 只是保守模板，不替代官方 author guidelines；团队规则应保存为审核过的 YAML，并用 `--venue-registry` 显式传入。

### 第 2 步：你做语义审查

1. 读 `outline.md` 把握结构
2. **分批读 `context/sec_*.md`**（不要一次读全文——分批省上下文，且可并行）
3. 按下方清单逐项检查
4. 各顺序组 Agent 分别回填到 `<运行目录>/findings.<role>.json`；代码统一门禁后生成 `findings.json`。旧的 `findings.llm.json` 和历史裁决文件仍兼容。

```json
{
  "findings": [
    {
      "checklist_id": "A04",
      "severity": "major",
      "block_ids": ["p_0063"],
      "verbatim_quote": "原文中能精确匹配的连续片段",
      "rationale": "为什么是问题",
      "suggested_fix": "改写后的整段文本（第 4 步用；不填则不可自动改写）",
      "confidence": 0.8
    }
  ]
}
```

**`verbatim_quote` 是关键**：必须是原文真实存在的连续子串（去空白后能匹配）。
编造的引文在第 3 步会被门禁拦下。

### 第 3 步：verify —— 门禁与报告

```bash
python -m paperaudit.cli verify <运行目录>
```

1. **证据门禁** —— 引文匹配失败的条目降级 `unverifiable`，**不计入确认问题**
2. 与工具的确定性结果合并
3. 按严重度排序，产出 `review.md` 与 `findings.json`

### 第 4 步：plan-revision —— 生成 EditProposal 预览

```bash
python -m paperaudit.cli plan-revision <原文件.docx> --run-dir <运行目录> \
  --findings F003,F007 --format markdown
```

该步骤不修改 DOCX，只生成 `revision.plan.json` 和可读的 `revision.diff`。每个提案包含目标块、字符范围、`expected_old_text`、replacement、风险级别和冲突图；多段替换、C 级实质改写、源 hash 变化或目标冲突会被标为 blocked。

### 第 5 步：apply —— 改写（须用户确认后）

```bash
python -m paperaudit.cli apply <原文件.docx> --run-dir <运行目录> \
  --findings F003,F007 [--text "改写后的段落文本"] [--out 输出路径]
```

- 输出到**新文件**（默认 `<原名>_revised.docx`）
- 改前**自动备份** `<原名>.bak-<时间戳>.docx`
- 改完**自动回归复验**（重跑确定性检查，报告是否引入新问题）
- **原文件始终保持未修改**

`--findings '*'` 可一键应用所有带 `suggested_fix` 的条目；
`--text "..."` 覆盖条目里的改写文本，便于用户口述改法。
若所选条目没有改写内容，返回体里的 `fixable` 会列出可改写的 ID。

**回归结果必须看**：若 `regressions` 非空，说明改写引入了新问题
（典型如改写时删掉了引用导致"列而未引"），要如实告知用户。

---

## 补充：数值论断的机器产物门禁（有代码/数据仓库时必做）

当论文背后存在**可运行的代码与机器产物**（实验 CSV/JSON、模型输出）时，
仅做引文门禁不够——最危险的一类错误是**文档里的数字与机器产物不一致**，
尤其当文档自称"已更新"而实际沿用旧值时。这类错误引文门禁抓不到。

**做法**（详见此模式的成功案例：AI-UWM 项目的 `scripts/audit_doc_consistency.py`）：

1. **建真值表**：从机器产物（JSON/CSV）读出每个可引用数字，键为 `(estimand, 统计量)`。
   注意 **estimand 必须显式区分**——"物理上限""政策达标容量""多年可持续边界"是不同量，
   混称成一个"承载力"是常见错误来源。
2. **从文档抽论断**：用正则匹配文档中的数字句式（表格行、`A/B/C MW` 三元组等），
   映射到真值表键。
3. **比对并返回非零退出码**：不一致或无法追溯即失败，使其可用作提交前门禁。
4. **反向验证门禁本身**：故意把文档里一个数字改错，确认门禁能报 FAIL（否则门禁是摆设）。
5. **扫描裸露数字**：报告文档中未被真值表覆盖的容量数字数量，提示人工确认，
   但**不要**为凑覆盖率而虚构真值。

**同类陷阱**（遇到要主动检查）：

- 文档顶部的"当前口径覆盖块"自称最新，实则含旧值——优先核对这一块。
- 同一文件内自相矛盾（一处说"结论已撤回"，另一处仍把该结论当结论用）。
- 把**模型下界**（如干式冷却的零耗水）或**右删失值**（如政策阈值贴网格上界）
  写成精确值；右删失只能作下界解释，并报删失率。
- 把**合成/情景结果**写成实测结论；正文须标出驱动数据是合成还是实测。
- 把"批次内 bootstrap 区间窄"当成"分布已收敛"——**这是两件事**。
  正确做法是换独立随机种子复算，只把跨种子稳定的统计量（如中心位置与上尾）
  写成稳定；`p05` 这类陡峭区间的分位数允许一个网格步的波动。

**改写原则**：删掉无信息量的防御性免责声明，直接写结论、证据与适用边界；
不要用堆叠"仅供参考"代替方法解释。

---

## 审查清单（8 组 40 项）

完整定义见 `checklists/academic.yaml`，按文档类型自动过滤（论文 / 开题报告）。

| 组 | 覆盖 |
|---|---|
| `structure` 结构与完整性 | 章节规范、篇幅失衡、内容重复、编号体系 |
| `argument` 论证与逻辑 | 研究问题、gap 是否成立、贡献清晰度、过度声明、论证链断裂 |
| `method` 方法严谨性 | 可复现性、数据来源、基线/消融、假设边界、混淆因素 |
| `data` 数据与统计 | 统计检验、数字跨章节一致、图文吻合、有效数字、可疑数值 |
| `citation` 引用规范 | 真实性、是否支撑论断、关键论断缺引、格式统一、覆盖度 |
| `figure` 图表规范 | 编号对应、题注完整、正文解读、可读性 |
| `language` 表述与语言 | 术语统一、缩写定义、冗余、病句、语气一致 |
| `consistency` 跨章节一致性 | 摘要与正文、首尾呼应、符号定义、成果与内容匹配 |

---

## 输出给用户的格式

先结论，再清单。不要把 JSON 原样丢给用户。

```
检出 9 项：重要 4、次要 3、细节 2。

重要
- [F003 引而未录] 正文引用了 2 个文献表中不存在的编号：5、12（p_0088、p_0156）
- [F007 过度声明] "首次提出"缺乏支撑，综述[23]已有类似工作（p_0134）

次要
- [F001 缩写未定义] CWTW、WSIMOD 首次出现未见全称（p_0241）— 启发式，需你确认

另有 2 条因无法在原文定位已被剔除。
```

**必须保留「需你确认」的限定**，不要说成确定结论。

---

## 边界

- 支持 `.docx`、文本型 `.pdf` 和 GROBID `.xml` 审查；安全修订目前只对 `.docx` 开放
- 只做**可核查**的问题；"创新点够不够""该不该接收"这类判断交还用户，不要替他下结论
- **绝不编造引用**：怀疑某条引用不实时，标记为待核实并说明无法自动验证
- 第 4 步改写前必须经用户确认，不得自动执行
