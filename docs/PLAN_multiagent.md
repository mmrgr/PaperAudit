# PaperRevamper 多 Agent 学术审查实施计划

## 设计依据

- `paper-audit`：定位、证据门禁、确定性检查、报告和安全副本修订。
- `nature-reader`：长文分段、页码/图表/源锚点；PDF 输入时作为前置适配层。
- `nature-writing`：研究问题、gap、贡献和章节论证；缺证据只能标记缺口。
- `nature-polishing`：论证成立后的段落和学术语言润色。
- `academic-humanizer`：保持数字、结果、引用不变，审计 claim-evidence 纪律。
- `nature-citation`：显式触发的引用检索与支持等级，不把元数据候选当作证据。
- `nature-data`：数据/代码/FAIR 与 Data Availability，不能越界改统计方法。
- `nature-response`：已有审稿意见时才启用的逐条回复流程。
- DelphiOpt：独立首轮、证据闭环、代码聚合和可审计轨迹。

## 当前实现（M7 可交付闭环）

```text
prepare / ingest
    ├─ deterministic checks
    └─ collaboration.plan.json
          ├─ argument_reviewer          ┐
          ├─ method_data_reviewer       │ 独立并行首轮
          ├─ citation_figure_reviewer   │
          └─ language_editor            ┘
                    ↓
          order=1 的 Agent（同序并行）
                    ↓
verify：统一 quote/block 门禁 + 精确去重 + 角色覆盖报告
                    ↓
用户确认后 apply：副本修改 + 回归
```

### 已落地

1. `skills/academic/registry.yaml`：skill provenance、触发条件、输出契约和边界。
2. `checklists/skill-routing.yaml`：清单组到角色和 skill 的路由。
3. `src/paperrevamper/collaboration.py`：角色、短 contract、任务依赖和 plan artifact。
4. `prepare`：生成 `collaboration.plan.json`、`collaboration.md`，manifest 记录角色计划。
5. `verify`：按顺序计划汇总 `findings.<role>.json`，执行证据门禁、保守去重并报告各角色状态和候选数。
6. `Finding`：补充 reviewer、页/字符锚点、owner skill 和兼容性的 suggested fix 字段。
7. 项目/`.agents`/WorkBuddy 三处 `paper-audit` skill 已同步多 Agent 协作说明。
8. `trace.jsonl` 记录 `prepare_complete`、角色输入加载和 `verify_complete`，`review.md` 展示实际角色覆盖；缺失角色保持 `pending`。
9. `paperrevamper panel` 提供本地控制面板：状态图、角色任务、证据意见、作者决策、源 hash 门禁、副本修改和回归轨迹。
10. 流程编辑器允许新增/停用角色、修改 skills/清单组/并行组/提示词，并校验任务依赖环；自定义流程只作用于新运行。
11. `run-review` 提供显式 provider 的结构化角色执行、并行依赖调度、输出 hash、plan/profile checkpoint 和 `--fresh` 重跑。
12. 原生 PDF/GROBID TEI、引用完整性、本地证据、隐私审计、数字事实图、清单 registry、Finding Graph、benchmark runner、provider-backed 双位置 panel runner 和确定性聚合已接入。
13. `benchmarks/adversarial.json` 提供 12 个确定性正负样本，CI 固定 precision/recall/FPR 和 evidence gate 拒绝数。

## 尚未接入的阶段

- 双向位置交换与四态 panel 裁决：`run-panel` 已把配置的 provider 按模型×位置并行执行，写入逐任务响应、checkpoint 和 `panel.judgments.json`；`adjudicate` 继续负责确定性聚合和 `adjudication.json` 审计产物，完整 UI 任务流仍需独立评估。
- 独立 `EditProposal` 与 C 级改写阻断：`plan-revision`/`/api/revision/plan` 已生成目标 span、`expected_old_text`、replacement、风险级别、冲突图和 `revision.diff`；`suggested_fix` 仍作为旧输入兼容层，后续可继续扩展 proposal editor。
- 真实模型/真实期刊 gold corpus：CI 使用离线 fake provider 和 de-identified embedded IR；真实 provider 成本、跨期刊泛化和视觉/OCR 仍需独立评估。
- 控制面板仍以宿主协作和 pending 状态为主；直接模型按钮已接入 `run-review`，模型 Panel 按钮已接入 `/api/panel/start`，真实 provider 的交互式成本/失败提示仍需独立评估。

## 开源交互参考

本项目只借鉴状态图、人工审核队列和 trace/replay 的交互模式，不复制其论文判断逻辑或代码：

- [LangGraph](https://github.com/langchain-ai/langgraph)：图状态和人工中断。
- [AutoGen Studio](https://github.com/microsoft/autogen/tree/main/python/packages/autogen-studio)：可视化团队和任务图。
- [agent-orchestration-dashboard](https://github.com/pintarkristian/agent-orchestration-dashboard)：角色状态、事件流和运行监控。
- [agentic-paper-review](https://github.com/ngstcf/agentic-paper-review)：论文审查标准和来源配置的交互方向。

## 验收门槛

- 同一 `prepare` 运行必须产生可复核的角色计划。
- 角色输出缺失时显示 `pending/skipped`，不得伪装为完成。
- 角色之间独立发现；代码负责证据门禁、精确去重和最终计数。
- 任何改稿前保留源文件 hash、备份和用户选择；改后重跑确定性回归。
- 角色输出必须保留真实引句；外部引用、数据和审稿回复 skill 默认不触发。
