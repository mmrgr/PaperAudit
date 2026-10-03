# PaperAudit 工作交接

> 供新窗口接续开发。最后更新：2026-10-02。

## 1. 项目在哪

| 项 | 路径 |
|---|---|
| **项目根目录** | `C:\Users\mmrgr\WorkBuddy\2026-09-22-14-09-30\PaperAudit` |
| 源码 | `<根>/src/paperaudit/` |
| 审查清单 | `<根>/checklists/academic.yaml` |
| Skill 源 | `<根>/skills/paper-audit/SKILL.md` |
| 设计方案 | `<根>/docs/PLAN_v2.md`（现行）、`docs/PLAN.md`（v1 存档） |
| 测试样本 | `<根>/tests/_tmp/seeded.docx`（注入缺陷版，1.5MB） |
| 记忆 | `<根>/.workbuddy/memory/2026-09-23.md`（含踩坑记录） |

**Skill 已安装到三处且内容一致（189 行）**：
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
- 依赖：`python-docx` 1.2.0、`PyYAML` 6.0.3
- 重装：`cd <项目根> && pip install -e .`

> 曾经踩过坑：命令装在 venv 里导致宿主调用 command not found，**这是上一轮"效果不好"的主因**，别再退回去了。

---

## 3. 架构：宿主协作式（核心决策，别推翻）

**宿主 agent 本身就是 LLM，PaperAudit 不调任何 API。**

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
| `evidence/` | 5 个确定性检测器：citations / crossref / structure / terminology / numbers |
| `checklist.py` | 清单加载、外部导入、doc_type 自动过滤 |
| `prepare.py` | 审查包生成 |
| `verify.py` | 证据门禁 + 去重 + 排序 + 报告 |
| `revise.py` | 副本改写 + 备份 + 回归复验 |
| `reporting.py` | Markdown/JSON 报告 |
| `cli.py` | prepare / verify / apply / check |

---

## 4. 已完成与验证结果

| 能力 | 状态 | 验证 |
|---|---|---|
| DOCX 解析 + 稳定块 ID | ✅ | 327 块、38 标题、6 表格 |
| 5 个确定性检测器 | ✅ | 零 LLM |
| 文档类型识别 | ✅ | 开题报告→`proposal` 正确 |
| 注入缺陷召回 | ✅ | 3 类（删文献/断图引用/章节跳号）**全中** |
| 证据门禁拦幻觉 | ✅ | 编造引文 1/1 拦截 |
| 改写 + 备份 + 回归 | ✅ | 新文件 + 备份 + 零回归，原文件未动 |
| 回归捕获副作用 | ✅ | 改写删掉引用 → 报 `citation_unused` |
| 数字冲突证据可定位 | ✅ | 数字检查保留真实匹配片段，避免拼接引文被门禁误拒 |
| 门禁实现统一 | ✅ | `check` / `prepare` / `verify` 共用同一定位规则；未知 block ID 不回退全文 |
| verify 精确去重 | ✅ | 仅合并同类型、同引文、同目标块的重复意见 |
| PDF 适配层 smoke test | ✅ | `tests/pdf_adapter_smoke.py`；明确记录投影损失，不宣称原生 PDF 支持 |
| 不支持格式退出状态 | ✅ | `check` 遇到非 `.docx` 返回退出码 2，避免把跳过误判为成功 |
| 多 Agent 角色计划 | ✅ | `collaboration.plan.json`、可配置 Agent 顺序；同序并行、异序串行 |
| 学术 skill 路由 | ✅ | `skills/academic/registry.yaml`、`checklists/skill-routing.yaml`，并同步三处项目 skill |
| 角色结果聚合 | ✅ | `verify` 合并 `findings.<role>.json`，优先读取 `findings.adjudicated.json`，报告角色状态 |
| 协作过程追踪 | ✅ | `trace.jsonl` 记录角色输入文件、候选数和验证结果；`review.md` 展示执行顺序与角色覆盖 |
| 过程复盘 | ✅ | `docs/PROCESS_REVIEW_multiagent.md` 记录本轮 sol worker 规划、实施步骤和验证证据 |
| 可视化修改控制面板 | ✅ | `paperaudit panel` + `src/paperaudit/panel.py` + `ui/control-panel.html`；支持状态、决策、证据和安全副本修改 |
| 可编辑 Agent 工作流 | ✅ | 面板支持角色增删/启停、skills、清单组、并行组、提示词和任务依赖覆盖；`prepare --workflow` 可复用 JSON 配置 |

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
| 中 | 补单元测试 | 目前只有 `tests/seeded_check.py`（集成式召回验证），缺单元测试；本轮用临时合成 DOCX 做了数字门禁与 verify 去重 smoke test |
| 中 | `numbers` 检测器太保守 | 目前只报"同一指标多个取值"，可做更实的跨章节数字核对 |
| 中 | 合并确定性结果进 verify 时去重 | 现按 issue_type 粗判，可能与宿主意见重复 |
| 低 | 原生支持 `.doc` / PDF | 本轮仅完成 PDF 文本投影 smoke test；双栏、表格、图形、公式和作者-年份引用仍需独立适配器 |
| 中 | 接入真实模型 runtime / 四态 panel | 当前控制面板展示角色计划和 pending 状态；PaperAudit 本身不调用模型，避免 API/权限耦合 |
| 低 | 外部文献检索（Crossref/S2） | v1 明确不做，避免编造引用 |

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
