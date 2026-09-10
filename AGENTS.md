# Agent Routing

本文件是项目级 Agent 的唯一启动入口。开始任务时先阅读本文件，再按任务类型查阅下表对应的文档入口；不要假设不同 Agent 产品会自动发现嵌套的 `AGENTS.md`。

## Documentation Navigation

| 需要处理的内容 | 先阅读或检索 |
| --- | --- |
| 当前工程行为、标准、契约、约束或边界 | 阅读或检索 [`docs/specs/AGENTS.md`](docs/specs/AGENTS.md) |
| 产品意图、用户需求或验收标准 | 阅读或检索 [`docs/requirements/AGENTS.md`](docs/requirements/AGENTS.md) |
| 架构、实现方式、技术原因或权衡 | 阅读或检索 [`docs/technical/AGENTS.md`](docs/technical/AGENTS.md) |

## Task Workflow

- 修改已有工程行为前，先阅读适用的 Engineering Spec。
- 开发新功能前，先查阅相关需求和技术文档，再核对适用的 Engineering Spec。
- 接受新的需求或技术决定后，在同一变更中更新受影响的文档。
- 需要了解决策原因时，检索 `docs/requirements/` 或 `docs/technical/`，不要从 Spec 反推原因。
- 代码变更完成后运行相关测试，并运行 `python .agents/skills/agents-spec/scripts/audit_agents_md.py . --check` 验证文档路由。

## Project Entry Points

- 应用入口为 `app.py`，启动命令为 `uvicorn app:app --reload`。
- 项目级 `agents-spec` 技能位于 `.agents/skills/agents-spec/`，属于工程基础设施，不是业务文档存储位置。
- 产品交付说明和演示脚本保留在 [`docs/DELIVERY.md`](docs/DELIVERY.md) 与 [`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md)；它们是交付材料，不替代三类文档入口。
