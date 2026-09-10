# Technical Documents

本目录记录架构、实现方案、技术决策和权衡。开始跨模块实现任务时先阅读这里，并用 `search` 或 `检索` 查找相关组件；不要把这里的方案说明当成当前工程约束的唯一来源，当前约束应回到 `docs/specs/` 核对。

## Technical Index

- [`ARCHITECTURE.md`](ARCHITECTURE.md)：服务组件、请求流程、数据边界和关键实现权衡。

## Maintenance Workflow

- 架构变化、模块边界和技术权衡更新到对应技术文档。
- 产品目标和验收标准写入 `docs/requirements/`；稳定工程约束写入 `docs/specs/`。
- 代码改动后运行相关测试，并核对技术文档中的模块入口是否仍然有效。
