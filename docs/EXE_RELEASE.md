# PaperAudit Windows 桌面版

## 两个功能区

同一个 `PaperAudit.exe` 内提供两个功能区：

1. **论文审查**：选择 DOCX，生成审查包，查看确定性检查和 Agent 意见，执行 verify、作者决定和副本修改回归。
2. **多 Agent 工作流**：新增/删除/停用 Agent，设置顺序、提示词、普通依赖、起始/末尾节点和有次数上限的反馈回流；配置主流模型 API、OpenAI 兼容 API 或保留 WorkBuddy/Codex 宿主通道。

顶部导航可以在两个功能区之间切换。运行目录默认写入 `%LOCALAPPDATA%\PaperAudit\runs`，不会写入安装目录或临时解压目录。

## 构建

在 PowerShell 中执行：

```powershell
.\build_exe.ps1 -Mode onedir
```

交付文件为 `dist\PaperAudit\PaperAudit.exe`。确认 onedir 版本可用后，可以构建单文件版本：

```powershell
.\build_exe.ps1 -Mode onefile
```

## 运行边界

桌面版包含当前全部本地论文处理、报告、修改副本和工作流设计功能。解析、验证和副本修改使用后台任务执行，界面会轮询显示实时阶段和进度，事件追加到 `trace.jsonl`。模型配置保存到运行根目录上一级的 `config.json`，支持环境变量引用 API Key；`host_agent` 配置保留 Codex/WorkBuddy 直接执行的通道。输入支持 `.docx`、文本型 `.pdf` 和 GROBID `.xml`；扫描件仍会明确标记为需要 OCR。构建脚本会同时打包 `checklists/`、`venues/` 和控制面板静态资源。
