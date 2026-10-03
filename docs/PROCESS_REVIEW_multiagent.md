# 多 Agent 学术审查过程复盘

日期：2026-10-02

## 本轮实际过程

1. 使用 `gpt-6.1-sol` worker 做只读架构规划，检查项目现状、论文审查 skill 边界、证据门禁和 PDF 测试限制。
2. 将规划落地为角色计划、skill 注册表、清单路由、结构化 Finding 字段和运行轨迹。
3. 用 seeded DOCX 运行 `prepare → verify`，确认计划、角色状态、确定性结果和报告都能生成。
4. 写入四个角色的临时候选结果，确认 `verify` 能稳定聚合、拦截未通过证据，并在报告中显示各角色覆盖。
5. 检查多个顺序组 Agent 的输入、输出和报告，确认同序 Agent 并行、不同序号依次执行。
6. 使用本地控制面板 smoke test 完成 `prepare → state → role finding → verify → accept → apply`，并验证源文件未改变、备份存在、回归为空。
7. 增加流程编辑器与 workflow API，验证可停用角色、增加自定义 Agent、修改并行组、覆盖任务依赖、保存配置并让新审查包使用自定义计划。

## 复盘结论

- 可见性问题已由 `collaboration.plan.json`、`collaboration.md`、`review.md` 协作表和 `trace.jsonl` 解决。
- 发现角色现在按论证、方法/数据、引用/图表、语言/术语分工，并显式路由到对应 academic skills。
- 证据门禁、精确去重和最终计数仍由代码控制，避免角色自行宣布问题成立。
- 当前仍是宿主协调式协作：PaperAudit 不内置模型 API 调度；实际角色由 WorkBuddy/Codex 运行并回填 JSON。
- PDF 仍通过文本投影 smoke test，尚未提供原生页码、图表和公式 DocumentIR。
- 控制面板目前不直接启动模型角色；它提供任务提示复制、文件状态读取、作者决策和安全修改入口，保留宿主协调式模型架构。

## 验证证据

- `python -m compileall -q src`：通过。
- `skills/academic/registry.yaml` 与 `checklists/skill-routing.yaml`：PyYAML 解析通过。
- 无角色输出：5 项确定性意见，角色均显示 `pending`。
- 四角色候选输出：9 项合并，5 项通过证据门禁，4 项被拒。
- 顺序工作流：默认 Agent 为顺序 1；自定义 Agent 改为顺序 2 后，计划显示其依赖顺序 1 的 Agent。
- 控制面板：`tests/panel_smoke.py` 输出 `roles=5`、`stages=8`、完成 1 条副本修改，源文件字节不变；同时验证自定义并行角色会生成 review task，循环依赖会被拒绝。
