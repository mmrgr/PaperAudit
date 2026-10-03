# PaperAudit

**论文投前自审与修改工具。** 输入 Word 文档，输出可逐条核查的问题清单，并在你确认后于副本上改写。

## 核心分工

PaperAudit 默认由**宿主 agent（WorkBuddy / Codex）协调多个学术审查角色**，也支持在控制面板中配置 OpenAI、Anthropic、Gemini、DeepSeek、通义、智谱、Moonshot 及任意 OpenAI 兼容 API 做单次调用或后续 Agent 扩展。项目始终负责可复核的解析、证据门禁和报告。

| 谁 | 做什么 |
|---|---|
| **PaperAudit** | 解析、定位、清单、证据门禁、改写执行、回归复验 |
| **宿主 agent / 可选模型 API** | 按顺序号执行语义审查，同序 Agent 并行并回填结构化结果；可配置 API 用于连接测试和直接调用 |
| **你** | 勾选要改哪些 |

好处：Coding Agent 模式不需要 API Key；角色职责和 skill 路由可追踪；分批读章节天然省上下文且可并行。

---

## 安装

```bash
pip install -e .
```

> Scripts 目录通常不在 PATH，因此**用 `python -m paperaudit.cli`，不要用裸 `paperaudit` 命令**。

---

## 四步工作流

```bash
# 1. 解析 → 审查包（清单 + 分章节原文）
python -m paperaudit.cli prepare 论文.docx --out ./run/

# 2. 多 Agent 读 run/context/sec_*.md，按角色回填 findings.<role>.json

# 3. 证据门禁 → 去重 → 排序 → 报告
python -m paperaudit.cli verify ./run/

# 4. 确认后改写（输出到新文件，自动备份，原文件不动）
python -m paperaudit.cli apply 论文.docx --run-dir ./run/ --findings F003,F007
```

`prepare` 会生成 `collaboration.plan.json` 和 `collaboration.md`。宿主按顺序号运行多个独立角色：默认四个角色都是顺序 1，因此并行执行；可以把某个角色改为顺序 2、3……让它依次执行。各角色分别输出 `findings.<role>.json`，所有结果随后由代码统一做证据门禁和保守去重。旧的裁决输出文件仍可被历史运行读取。

## 可视化多 Agent 修改控制面板

不需要逐行修改 JSON 或代码时，可以启动本地控制面板：

```text
python -m paperaudit.cli panel --run-root out/panel-runs --open
```

面板提供论文路径和审查包创建、阶段图、角色 skill 和任务提示、证据意见筛选、`accept / reject / contest` 决策、源文件 hash 复核、副本修改、备份、回归结果和 trace 时间线。宿主 Agent 仍负责实际运行审查角色；面板显示 pending 并提供复制任务提示，不会伪造模型已执行。

“流程编排”区域还可以新增、停用或删除 Agent，修改角色名称、提示词、skills、清单组和执行顺序。顺序号从小到大依次执行，同一顺序号并行。通过“验证流程”和“保存流程”后，下一次审查包会生成新的 `collaboration.plan.json`；已有运行目录保持不可变。命令行也支持：

流程编排同时提供工作流画布：可以用鼠标拖动 Agent 模块调整可视布局，点击模块即可在设置面板中选择多个上游任务。画布上的箭头表示信息依赖，保存普通信息流会写入目标任务的 `depends_on`；拖动只改变 `position`，不会改变执行顺序。保存或验证时会检查未知任务和普通依赖循环。

点击任意画布模块会打开设置面板：Agent 可修改名称、提示词、启用状态和执行顺序；起始节点和末尾报告节点也可修改名称、说明和位置。设置面板还可以给当前模块添加有最大次数的循环回流，例如把 `verify` 的结果送回某个 Agent 重做。循环回流保存在 `feedback_edges` 中，不会破坏一次性依赖图，由宿主按最大次数执行。

控制面板会异步执行解析、验证和副本修改，并实时显示“正在执行……”阶段、进度百分比和失败原因；过程同时写入 `trace.jsonl`。工作流页的“模型与调用通道”可以保存多个提供商配置、使用环境变量引用密钥、测试连接、删除自定义配置，并保留 WorkBuddy / Codex 宿主通道。

```text
python -m paperaudit.cli prepare 论文.docx --out run --workflow workflow.json
```

### 第 1 步产物

| 文件 | 内容 |
|---|---|
| `manifest.json` | 概览、doc_type、清单项数、确定性预检结果 |
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

`verbatim_quote` 必须真实存在于原文 —— **编造的引文会被证据门禁拦下**。

### 第 4 步的安全保证

- 输出到 `<原名>_revised.docx`（新文件）
- 改前自动备份 `<原名>.bak-<时间戳>.docx`
- 改完自动回归复验
- **原文件始终保持未修改**

`--text "..."` 可覆盖 `suggested_fix`，便于口述改法。

---

## 审查清单

`checklists/academic.yaml`，8 组 40 项，按文档类型（论文 / 开题报告）自动过滤：

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
给出显式清单才能把定位外部化。支持 `--checklist` 导入外部清单（会议/期刊要求）。

---

## 附带：独立确定性检查

不需要宿主参与，单独跑机械检查：

```bash
python -m paperaudit.cli check 论文.docx
python -m paperaudit.cli check 论文.docx --format json
python -m paperaudit.cli check a.docx b.docx --out ./out/   # 多篇串行
```

### PDF 测试适配层（非原生支持）

项目本身只接收 `.docx`。如需对 PDF 做输入烟测，可在装有 `pdfplumber` 的环境中运行：

```powershell
$env:PYTHONPATH = "src"
python tests/pdf_adapter_smoke.py 论文.pdf --out out/pdf-smoke/
```

该脚本把每页抽取文本投影为临时 DOCX，再运行现有确定性检查；它会同时输出页数、字符数、SHA-256、原文抽取和限制说明。双栏顺序、表格、公式、图形与作者-年份引用不会因此获得原生支持，投影结果不能替代 PDF 适配器。

## Windows 桌面版

运行 `build_exe.ps1 -Mode onedir` 生成 `dist\PaperAudit\PaperAudit.exe`；`-Mode onefile` 生成单文件 `dist\PaperAudit.exe`。双击后会自动打开本地控制面板，顶部切换“论文审查”和“多 Agent 工作流”两个功能区。运行数据默认保存到 `%LOCALAPPDATA%\PaperAudit\runs`。完整说明见 [docs/EXE_RELEASE.md](docs/EXE_RELEASE.md)。

检测器：`citations` `crossref` `structure` `terminology` `numbers`。
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
PaperAudit 是**验证型**的 —— 叠加而非竞争：

```
PaperAudit prepare（找问题）
      ↓
academic-humanizer（润色）
      ↓
PaperAudit check（复验有没有改坏）
```

---

## 目录

```
src/paperaudit/
├─ models.py              核心 schema（块 ID 定位）
├─ ingest/docx_reader.py  DOCX → DocumentIR
├─ evidence/              5 个确定性检测器（零 LLM）
├─ checklist.py           清单加载与外部导入
├─ prepare.py             审查包生成
├─ verify.py              证据门禁 + 去重 + 报告
├─ revise.py              副本改写 + 备份 + 回归
├─ reporting.py           报告输出
└─ cli.py                 prepare / verify / apply / check
checklists/academic.yaml  审查清单（一等公民）
skills/paper-audit/       Codex 与 WorkBuddy Skill
```

## 已知限制

- 只支持 `.docx`
- 对 `.pdf`、`.doc` 等输入，`check` 会明确跳过并返回非零状态；如需测试 PDF，先使用独立适配层将文本投影为临时 `.docx`，结果不代表原生 PDF 支持
- 语义审查依赖宿主模型质量
- 判断性问题（贡献是否成立）不在能力范围内 —— 这是"验证型"定位的边界，不假装能做
