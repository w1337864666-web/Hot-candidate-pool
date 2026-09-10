# 真实端到端交付案例

本案例于 2026-08-22 使用 `scripts/generate_delivery_case.py` 生成。脚本使用隔离 SQLite 数据库连续执行两轮真实 RSS 扫描，在两轮之间完成一次人工审核，不改动日常候选池。下列分值是当日真实扫描留下的历史快照；当前基础公式已增加真实热度和来源可信度，重新运行时分值与排名会随新公式和实时 feed 变化。

## 输入与账号画像

- 目标受众：英语市场的 AI 用户、创作者和产品团队。
- 关注主题：AI products、agents、developer tools、creator workflows。
- 回避主题：rumors、unverified claims。
- 内容语气：简洁、专业、基于事实、不过度宣传。
- AI 模式：确定性模板。运行环境未配置外部 API Key，符合正式默认回退路径。

OpenAI 兼容链路另由本地 HTTP 合约测试覆盖，真实经过 SDK 请求、结构化响应解析、文案限长和 SQLite 持久化。该测试只证明集成链路可用，不把模拟响应描述为外部模型效果。

## 第一轮真实扫描

| 来源类型 | 来源 | 状态 | 输入数 | 尝试次数 | 演示回退 |
| --- | --- | --- | ---: | ---: | --- |
| 媒体新闻 | TechCrunch AI | 正常 | 20 | 1 | 否 |
| 官方公告 | OpenAI News | 正常 | 20 | 1 | 否 |
| 社区趋势 | Hacker News AI | 正常 | 20 | 1 | 否 |

- 总输入：60 条真实资讯。
- 聚类后候选：57 条。
- 来源级回退：未发生。
- 7 个单篇正文抽取错误被隔离并回退到 RSS 摘要，没有阻断扫描。
- HN 摘要已从 `Article URL / Comments URL / Points` 字段串转换为自然英文，例如：`A Hacker News discussion links to calnewport.com about this story. It currently has 1 point and 0 comments.`

## 排序与人工审核

本节分值用于证明当次真实输入、审核持久化和反馈闭环，不作为当前版本的固定评分基准。

选择候选 **The builder’s guide to GPT‑5.6**：

| 指标 | 审核前 |
| --- | ---: |
| 排名 | 6 |
| 基础分 | 70 |
| 账号画像调整 | +4 |
| 历史反馈调整 | 0 |
| 模型调整 | 0 |
| 最终分 | 74 |
| 跟进判断 | 建议评估 |

人工执行“采用”，审核意见为：`Delivery case: relevant and grounded for the account audience.` 审核动作、英文文案和意见均写入 `review_actions`。

确定性英文候选文案为：

> The builder’s guide to GPT‑5.6
>
> Why it matters: Learn how startups use GPT-5.6 to build faster, more cost-efficient AI agents with smarter model selection and new Responses API capabilities
>
> The signal is relevant to AI product teams, but its implications still need verification.

## 第二轮反馈变化

第二轮再次从三类真实来源获取 60 条资讯，未使用演示回退。实时 feed 在两轮之间新增内容，因此本轮得到 58 条候选。

| 指标 | 审核前 | 第二轮 | 变化 |
| --- | ---: | ---: | ---: |
| 排名 | 6 | 2 | 上升 4 位 |
| 基础分 | 70 | 70 | 0 |
| 账号画像调整 | +4 | +4 | 0 |
| 历史反馈调整 | 0 | +10 | +10 |
| 模型调整 | 0 | 0 | 0 |
| 最终分 | 74 | 84 | +10 |

变化来自已采用主题和“官方公告”来源类型的规则式反馈信号。基础分与画像分保持不变，说明反馈调整没有改写原有评分依据。

## 复现方式

```powershell
.venv\Scripts\python.exe -X utf8 scripts\generate_delivery_case.py
```

该命令需要访问三个公开 RSS 来源。输出包含两轮来源状态、输入与聚类数量、真实 HN 热度、来源可信度、独立相关性、审核动作以及同一候选的分数和排名变化；结果以运行当时的实时数据为准。
