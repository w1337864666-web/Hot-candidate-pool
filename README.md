# 每日热点到候选内容池 Agent

面向海外 AI 产品内容与增长工作的本地可运行 Agent：从公开 RSS/Atom 与可选数据 API 发现热点，完成标准化、去重和事件聚类；可选大模型进一步整理标题与摘要、执行智能初审，通过后进入候选一，人工终审后进入候选二。

## 技术栈

- Python 3.11+
- FastAPI + Uvicorn
- Jinja2 服务端渲染界面
- SQLite
- RSS/Atom（`feedparser`）与 `httpx` 超时/重试
- OpenAI Python SDK，可配置 OpenAI 兼容中转 API
- 标准库 `asyncio` 每日调度（无需额外调度服务）

## 本地运行（Windows）

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
uvicorn app:app --reload
```

也可以直接双击项目根目录的 `start_agent.bat` 一键启动。它会检查虚拟环境和 `.env`，预检 Python HTTPS 连接，自动选择可用端口，等待 `/health` 就绪后打开候选内容池；关闭启动窗口或按 `Ctrl+C` 会停止服务。PowerShell 等价命令为：

```powershell
.\scripts\start_agent.ps1
.\scripts\start_agent.ps1 -Port 8000 -NoBrowser
.\scripts\start_agent.ps1 -CheckOnly
```

打开 `http://127.0.0.1:8000/candidates`，可以选择“演示快照”、“海外 RSS / Atom”或“国内 RSS / Atom”。没有网络或 AI API 时仍可使用规则初筛兜底展示完整流程；国内模式用于海外来源访问不稳定时的替代输入。侧栏左下角“模型、数据与调度”进入本机配置页，可开启每天一次的北京时间自动扫描并选择来源模式；顶部“人工终审工作区”直接处理候选一。

真实 RSS/Atom 扫描时，请在本机普通 PowerShell/终端中启动 uvicorn，确保 Python 进程具有出站 HTTPS 权限；不要用受限沙箱中的进程判断真实来源是否可用：

```powershell
cd E:\vibecoding\每日热点到候选内容池agent
.venv\Scripts\Activate.ps1
python -m uvicorn app:app --reload
```

如果所在网络要求代理，可在启动前设置 `HTTP_PROXY` 和 `HTTPS_PROXY`，或把它们写入本地 `.env`（不要提交真实凭据）。应用会在每次扫描时读取当前代理环境并对每个来源独立重试；VPN 路由变化通常不需要重启应用。如果运行环境拒绝 Python 建立 socket（Windows `WinError 10013`），请检查防火墙/安全软件的 Python 出站规则，或改用允许联网的本机终端启动。

启动文件只检查防火墙问题，不会自动添加规则、关闭防火墙或申请管理员权限；如果预检提示 `WinError 10013`，请允许项目 `.venv\Scripts\python.exe` 访问出站 HTTPS 后重新运行启动文件。

## 配置 OpenAI 兼容 API

在项目根目录 `.env` 中填写：

```text
OPENAI_API_KEY=你的密钥
OPENAI_BASE_URL=https://你的中转站/v1
OPENAI_MODEL=你的模型名称
```

也可以在 `/settings/api` 页面配置，密钥保存在本机 SQLite 中且不会回显。页面允许分别启用选题评分、内容整理和智能审核，并设置每轮处理数量与 AI 审核最低置信度。模型选题调整限制在 `-10..+10`，必须带理由；智能审核通过也必须有理由且达到置信度门槛。

可选的 Tavily Search 用于补充最近一周新闻，与 RSS 结果合并后进入同一流水线：

```text
TAVILY_API_KEY=你的密钥
DATA_API_ENABLED=1
```

`HOTSPOT_DB_PATH` 可选，用于指定 SQLite 文件位置。密钥和数据库文件均不会提交到 Git。

## 产品工作流

```text
RSS/数据 API -> 标准化/去重/聚类 -> 规则评分 -> AI 整理与初审 -> 候选一 -> 人工终审 -> 候选二
```

确定性流水线负责 RSS 清洗、事件聚类、基础评分、跟进判断和不超过 280 字符的英文候选文案，是无密钥时的正式默认路径。OpenAI 兼容 API 在配置后读取受限的来源材料，生成中文优化标题与事实摘要、内容角度、英文文案和风险提示，并执行第一重智能审核；模型不参与聚类或基础评分。只有 AI 明确通过且置信度达标的热点进入候选一，人工负责第二重事实、品牌与合规终审，通过或修改后进入候选二。无模型时会明确显示“规则初筛兜底”，不冒充 AI 审核。审核反馈仍只产生有限、可解释的下一轮调整，不进行自动训练，也不自动发布到 X。

基础优先级现在同时使用时效性、AI 主题信号、不同来源验证、真实热度和来源可信度。真实热度当前读取 Hacker News RSS 公开的 points/comments 并做 `0..100` 标准化；其他来源没有公开互动量时显示“暂无热度信号”，不会填入模拟数字。来源可信度按官方公告、媒体新闻、社区趋势和数据检索分级并直接进入基础分。

账号相关性是独立的 `relevance_score`，不受模型调分影响。当前由 AI 领域关键词、账号关注/回避主题和未经证实风险规则模拟，并明确输出“推荐 / 不推荐”及理由；这一步不调用大模型。

每日调度默认关闭。在 `/settings/api` 开启后，应用运行期间每 30 秒检查一次是否到达北京时间设定时刻，同一日期只自动执行一次；即使当天修改时间也不会重复运行。成功、失败、关联运行编号和演示回退状态可在 `/runs` 查看。

扫描入口提供两个独立的真实来源预设：海外预设包含 TechCrunch AI、OpenAI News 和 Hacker News AI；国内预设包含 36氪、少数派和 IT之家。两组来源都按统一的 `SourceConfig` 结构进入流水线，真实来源逐个请求，单个来源的超时或解析失败会被记录，不阻断其他来源；所选预设全部失败时切换到固定演示快照。

Hacker News RSS 中的 `Article URL`、`Comments URL`、`Points` 和评论数字会在标准化阶段转换为自然英文摘要，原文 URL 单独保存在来源字段，不进入候选文案。完整的两轮真实扫描、审核与反馈变化见 [`docs/REAL_E2E_CASE.md`](docs/REAL_E2E_CASE.md)。

## 页面与接口

- `/candidates`：全部热点、候选一、候选二、AI 未通过和来源分类
- `/candidates/{id}`：AI 整理摘要、原始证据、双重审核状态和人工终审
- `/review/next`：打开候选一中最终优先级最高的下一条
- `POST /scan`：手动运行演示、海外 RSS 或国内 RSS 扫描（`mode=demo|live|domestic`）
- `POST /candidates/{id}/review`：通过、修改或驳回
- `POST /candidates/{id}/delete`：软删除候选二中的最终候选
- `POST /candidates/{id}/restore`：从“已删除”恢复候选
- `/runs`：手动/自动扫描运行记录、调度结果和错误
- `/settings/api`：本机模型、数据 API 与每日自动扫描配置
- `/health`：健康检查

候选详情会分别展示基础分、账号画像调整、历史反馈调整、模型调整和最终分；模型参与时同时展示理由与置信度。账号画像包含目标受众、关注主题、回避主题、语气和自由文本说明，并保存在对应运行记录中。

候选池顶部提供“全部 / 国内来源 / 海外来源 / 演示快照”标签页；默认“全部”也会将候选拆成三个独立区块，并分别在组内按最终优先级排序。候选列表和详情仍会显示对应分类标签。分类来自候选关联的运行记录；即使用户选择了真实来源，扫描全部失败并使用演示回退时，候选仍会准确归入“演示快照”。

候选二中的最终候选可以删除并进入左侧“已删除”列表；删除是可恢复的，不会清理来源、审核历史或反馈数据。

## 验证

```powershell
python -m unittest discover -s tests -v
```

当前版本刻意不包含多智能体、向量数据库、自动训练、权限系统和 X 自动发布。正文抽取、内容质量评估和来源运行监控已接入：正文按 URL 缓存，失败时保留 RSS 摘要；运行记录展示来源成功/失败、重试、耗时和回退状态。X 资讯获取仍作为后续可选增强，若接入将使用官方 X API，默认关闭，不使用网页抓取；X 自动发布仍不在范围内。

可复现的真实交付案例命令：

```powershell
.venv\Scripts\python.exe -X utf8 scripts\generate_delivery_case.py
```

## 聊天式 Demo 入口

候选池顶部提供自然语言任务入口。用户输入“抓取最近 24 小时 AI Agent 相关热点并生成候选”等需求后，系统先展示来源、时间范围和输出类型计划；确认后在后台执行并轮询任务状态。默认使用标注清楚的演示快照，模型 API 未配置或调用失败时显示规则解析回退提示；原有扫描表单、真实来源模式和双重审核流程保持不变。