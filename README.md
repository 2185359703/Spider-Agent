# AI 招聘采集接入平台

输入招聘入口 URL，由 AI 分析网站、开发采集器、验证并汇报，自动生成候选提交；人工运行、人工审查后采纳，有问题则进入 AI 纠错。

当前已完成首阶段后端骨架、数据模型、Alembic 迁移、FastAPI API、Celery 任务入口、LangGraph 图定义、fake 分析/修复闭环、受管仓库只读检查和自动化测试。真实 Playwright/spider-king 取证、真实 OpenHands Agent 代码修改、React 管理后台和 Docker 运行尚待后续阶段。

## 文档入口

| 文档 | 阅读用途 |
| --- | --- |
| [完整开发设计](docs/开发设计.md) | 确认业务范围、系统架构、流程、汇报与人工审查 |
| [PlatformSpec v1](docs/PlatformSpec-v1.md) | 统一中间规范、字段证据、分页、置信度和生成边界 |
| [数据契约与 API](docs/数据契约与API.md) | 实现字段模型、数据库、接口、事件与版本关联 |
| [工作流与运行规范](docs/工作流与运行规范.md) | 实现 LangGraph、OpenHands Agent、Git、隔离运行和恢复 |
| [开发计划与验收](docs/开发计划与验收.md) | 开发顺序、工期假设、验收用例与决策清单 |

## 已确认的边界

- 工作目录：`D:\Project\Auto_spider`。
- 正式采集由人工运行，运行后必须人工审查。
- AI 自动开发、验证、汇报、生成候选提交，并修复能通过代码或配置解决的问题。
- 不做网站变化监控，不做运行时 AI 兜底解析。
- `platform_id`、`entity_id` 先留空，不伪造编号。
- 汇报业务规则尚未定稿；文档中的默认阈值和业务处理策略均标为建议。

原有 `思路流程.md` 和 `mermaid-diagram.png` 保留。本文档中出现的模块、接口、目录和命令除明确注明“已核对”外，均是待实现设计。

## 本地 `.env` 配置

先复制 `.env.example` 为 `.env`。使用自定义 OpenAI 兼容接口时，至少填写：

```dotenv
OPENHANDS_LLM_MODEL=openai/<models 返回的模型名>
OPENHANDS_LLM_BASE_URL=https://your-endpoint.example/v1
OPENHANDS_LLM_API_MODE=responses
OPENHANDS_LLM_API_KEY=只放在本机，不要提交到 Git
```

`OPENHANDS_SERVER_URL`、`OPENHANDS_SESSION_API_KEY` 和 `OPENHANDS_SECRET_KEY` 必须与 OpenHands Agent Server 一致。首次验证建议保留 `AGENT_MODE=fake`；确认 Agent Server 已启动后，再改为 `AGENT_MODE=openhands`。`.env` 已被 `.gitignore` 忽略，不能把密钥写入 `.env.example`、日志、证据或提交记录。
