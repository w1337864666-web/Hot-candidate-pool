# Architecture

## Components

- `app.py` 暴露 FastAPI 页面和表单接口，并通过 Jinja2 渲染候选池、候选详情和运行记录。
- `hotspot_agent/sources.py` 负责演示快照、海外与国内 RSS/Atom 预设、RSS/Atom 解析、HN 元数据清洗、超时、重试、来源统计和错误收集。
- `hotspot_agent/content.py` 负责按 URL 去重和缓存的正文抽取、每来源 5 篇的并行限额、摘要回退和抽取错误保留。
- `hotspot_agent/core.py` 定义账号画像、反馈画像、去重聚类、评分和确定性候选文案。
- `hotspot_agent/data_api.py` 负责可选 Tavily Search 新闻补充、响应标准化、来源追溯和独立错误统计。
- `hotspot_agent/ai.py` 负责构造受限来源材料、调用 OpenAI 兼容模型、生成优化标题与摘要、有界选题调整、文案增强、智能审核门禁、结构化结果校验和失败回退。
- `hotspot_agent/pipeline.py` 编排 RSS 与数据 API 输入、反馈读取、有限候选模型评估、重新排序和运行保存。
- `hotspot_agent/scheduler.py` 负责北京时间每日调度检查、按日期抢占防重、后台线程执行扫描及成功/失败回写。
- `hotspot_agent/storage.py` 负责 SQLite 初始化、增量迁移、API 配置、查询和审核持久化。
- `hotspot_agent/templates/` 和 `hotspot_agent/static/` 提供服务端渲染的统一仪表盘壳层、本地图标、响应式样式与渐进增强交互；不需要独立前端构建步骤。

## Request Flow

`GET /settings/api` 展示不含密钥明文的本地配置，`POST /settings/api` 保存模型开关、模型地址、候选限额、AI 审核门槛、Tavily 开关和每日调度。FastAPI lifespan 每 30 秒读取一次调度设置；到达北京时间计划时刻后先以本地日期抢占 `scheduled_scans`，成功抢占的工作在线程中调用同一 `run_scan`，完成后关联运行记录，异常则保存失败原因。`POST /scan` 读取账号画像，根据 `mode=demo|live|domestic` 载入演示快照、海外来源预设或国内来源预设；真实扫描按配置追加 Tavily 新闻结果，读取文章正文缓存并做最佳努力抽取，读取历史审核信号，执行确定性分析，再把初筛前 N 条热点的来源摘要与有界正文片段交给模型进行内容整理、选题评分和智能审核，最后重新排序并保存。AI 明确通过且置信度达标的条目进入候选一；超过额度的条目标为未评估；模型不可用时以可见的规则兜底保持离线演示。`GET /candidates` 支持 `status=pending|final|ai_filtered` 阶段筛选，并继续通过运行模式派生三类来源；`GET /candidates/{id}` 同时展示 AI 整理结果、原始证据、独立规则相关性、真实热度、来源可信度和双重审核状态；`GET /review/next` 只在候选一中选择下一条；`POST /candidates/{id}/review` 拒绝绕过 AI 门禁的条目，人工采用或修改后进入候选二。删除、恢复、反馈与运行记录流程保持原有持久化语义。

## Persistence Shape

来源配置写入 `sources`，标准化文章、正文缓存、真实互动量和质量结果写入 `articles`，聚类事件、四段调整、基础评分信号、独立相关性、推荐结论和质量结果写入 `events`，优化标题、优化摘要、AI 审核结论、候选草稿和软删除时间写入 `candidates`，人工审核历史写入 `review_actions`，扫描输入、画像、触发方式、来源统计和错误写入 `runs`，本机 API 与调度配置写入 `app_settings`，每日调度结果写入 `scheduled_scans`。关联表 `event_articles` 连接事件和文章；候选来源分类与工作流阶段在查询时派生，不重复存储展示标签。

## Technical Decisions

- 采用 FastAPI + Jinja2 服务端渲染，保持单一 Python 服务和低部署复杂度。
- 采用 SQLite，便于本地演示和保留审核历史；通过增量迁移避免破坏已有数据库。
- 每日调度使用标准库 `asyncio`、FastAPI lifespan 和 SQLite 唯一键，不增加常驻调度依赖；同一数据通路服务手动和自动扫描。
- 热度仅使用来源公开互动量，当前先接 HN points/comments 并用对数尺度降低极端值影响；缺少信号保持为零并在界面解释，不做跨来源伪推断。
- 来源可信度与内容质量分开建模：可信度直接进入基础优先级，内容质量继续用于完整性和风险解释。
- `relevance_score` 与模型调整解耦，先由关键词、账号关注/回避主题和未经证实风险规则产生二元推荐，后续接入模型时仍保留该确定性基线。
- 采用规则式画像与反馈调整，确保分值上下限、原因和回退行为可解释。
- AI 调用放在确定性初筛之后，只处理有限数量热点；来源正文被视为不可信证据而不是指令。代码约束调分、审核枚举、理由长度、置信度和通过门槛，AI 只承担第一重审核，不能执行人工终审或发布。
- 模型不可用时采用显式规则兜底以保持演示与人工处理；该状态与真实 AI 通过分开显示。模型已配置但超过额度的条目不进入候选一，防止把未评估条目误称为 AI 通过。
- Tavily Search 使用 `news + week + basic` 的固定低成本参数，最多补充十条结果；RSS 仍是主输入，数据 API 是独立可失败的补充通道。
- 相同来源的文章以标题相似度为主要聚类依据，避免 HN 等来源的固定摘要句式造成误合并；跨来源事件才同时使用标题和摘要相似度。
- 确定性模板是正式默认内容路径并限制为 280 字符；AI 成功响应同样经过限长，AI 风险只补充而不删除规则风险。
- 界面交互继续使用原生链接和表单提交；本地静态 JavaScript 只负责侧栏记忆、移动导航和提交反馈，不接管请求或改变服务端重定向语义。


## Chat Demo Architecture

聊天入口通过 POST /api/chat/messages 创建结构化任务计划，通过 POST /api/tasks/{task_id}/confirm 原子确认执行，并通过 GET /api/tasks/{task_id} 轮询状态。hotspot_agent/chat.py 负责 OpenAI 兼容模型解析和规则回退；hotspot_agent/chat_tasks.py 在独立线程中复用现有 run_scan；hotspot_agent/chat_store.py 使用同一 SQLite 文件保存会话、消息和任务状态。

聊天任务状态为 waiting_confirmation、queued、running、succeeded、partial_success 和 failed。模型失败时任务计划强制切换到 demo 来源并保留回退原因。聊天入口不改变确定性聚类、基础评分、规则风险和人工审核边界。

## Static Cloudflare Demo

`demo/` 独立提供 HTML、CSS 和原生 JavaScript，不依赖 Python 服务。界面沿用本地工作台配色和工作流；固定候选取材于 sources.demo_items，合并两条近似标题，明确展示样例评分及来源栏目入口。

哈希路由处理候选列表、详情、运行记录和设置。版本化 localStorage 保存审核、文案、运行、聊天及待确认计划；不可用时使用内存。扫描按固定 ID 展示现有样例并记录本次操作，不重置审核和删除状态。

Cloudflare Pages 使用 GitHub main 自动部署，root_dir=demo、build_command 为空、destination_dir=.；只发布 demo 内静态资源。无 Functions、D1 或模型绑定，CSP connect-src none 阻止后台网络调用。
