# Engineering Specs

本目录是当前工程行为、标准、契约、约束和边界的权威来源。修改代码前先阅读相关 Spec，并用 `search` 或 `检索` 查找对应主题；不要把产品动机或决策历史写入 Spec。

## Spec Index

- [`ENGINEERING_BASELINE.md`](ENGINEERING_BASELINE.md)：运行、流水线、数据持久化、来源容错、评分、AI 回退和发布边界。

## Maintenance Workflow

- 新增或修改稳定、可验证的工程规则时，更新对应 Spec。
- 需求和验收标准放到 `docs/requirements/`，架构方案与技术权衡放到 `docs/technical/`。
- 运行测试和 `python .agents/skills/agents-spec/scripts/audit_agents_md.py . --check`，确认实现与文档路由一致。
