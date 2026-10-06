# 多 Agent 学术审查过程复盘

日期：2026-10-06

## 本轮实际过程

1. 使用 `gpt-6.1-sol` worker 做只读架构规划，检查项目现状、论文审查 skill 边界、证据门禁和 PDF 测试限制。
2. 将规划落地为角色计划、skill 注册表、清单路由、结构化 Finding 字段和运行轨迹。
3. 用 seeded DOCX 运行 `prepare → verify`，确认计划、角色状态、确定性结果和报告都能生成。
4. 写入四个角色的临时候选结果，确认 `verify` 能稳定聚合、拦截未通过证据，并在报告中显示各角色覆盖。
5. 检查多个顺序组 Agent 的输入、输出和报告，确认同序 Agent 并行、不同序号依次执行。
6. 使用本地控制面板 smoke test 完成 `prepare → state → role finding → verify → accept → apply`，并验证源文件未改变、备份存在、回归为空。
7. 增加流程编辑器与 workflow API，验证可停用角色、增加自定义 Agent、修改并行组、覆盖任务依赖、保存配置并让新审查包使用自定义计划。
8. 增加 citation integrity、privacy、numeric fact graph、Finding Graph、原生 PDF/GROBID TEI、可恢复 job、直接模型 runner 和 provider-backed 双位置 panel；离线 benchmark 与 adversarial corpus 均纳入 CI。
9. 增加 `plan-revision`/`/api/revision/plan`，用 EditProposal、expected-old-text、冲突图和 `revision.diff` 把修订预览与实际写入分开。
10. 增加配置驱动的投稿模式：`venues/registry.yaml`、`list-venues`、`prepare --venue` 和 required-section 确定性门禁；保存 `venue.profile.json` 作为运行快照。

## 复盘结论

- 可见性问题已由 `collaboration.plan.json`、`collaboration.md`、`review.md` 协作表和 `trace.jsonl` 解决。
- 发现角色现在按论证、方法/数据、引用/图表、语言/术语分工，并显式路由到对应 academic skills。
- 证据门禁、精确去重和最终计数仍由代码控制，避免角色自行宣布问题成立。
- 默认仍是宿主协调式协作，但显式 `run-review` 和 `run-panel` 已支持配置 provider；所有响应仍需进入统一 schema、证据门禁和确定性裁决。
- 原生文本型 PDF 与 GROBID TEI 已提供页码、坐标、图表/引用锚点；扫描 OCR、复杂双栏、公式语义和完整视觉版式仍需专用评估。
- 控制面板已支持直接模型角色、双位置 Panel 和修订计划预览；真实 provider 成本、跨期刊 gold corpus 与交互式 proposal 编辑仍待独立评估。投稿配置目前只执行作者明确声明的章节门禁，不替代官方投稿指南。

## 验证证据

- `python -m compileall -q src`：通过。
- `skills/academic/registry.yaml` 与 `checklists/skill-routing.yaml`：PyYAML 解析通过。
- 无角色输出：5 项确定性意见，角色均显示 `pending`。
- 四角色候选输出：9 项合并，5 项通过证据门禁，4 项被拒。
- 顺序工作流：默认 Agent 为顺序 1；自定义 Agent 改为顺序 2 后，计划显示其依赖顺序 1 的 Agent。
- 控制面板：`tests/panel_smoke.py` 验证自定义并行角色、依赖环拒绝、Panel profile 校验、revision plan 强制门禁、完成副本修改、源文件字节不变和备份存在。
- 离线全套 smoke：23 个脚本、`compileall`、`pip check`、静态 UI/Skill 同步和 wheel 内容检查通过；新增 `tests/venue_smoke.py`。
