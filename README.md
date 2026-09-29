# AI 招聘采集接入平台

输入招聘入口 URL，由 AI 分析网站、开发采集器、验证并汇报，自动生成候选提交；人工运行、人工审查后采纳，有问题则进入 AI 纠错。

当前已完成首阶段后端骨架、数据模型、Alembic 迁移、FastAPI API、Celery 任务入口、LangGraph 图定义、fake 分析/修复闭环、受管仓库只读检查和自动化测试，并已开始管理后台实现。当前前端提供任务队列、批次视图、新建接入、任务详情、采集样本查看与 JSON/JSONL 导入导出、接入汇报、候选 diff、候选集中管理、人工运行登记、人工审查、证据下载和规则/仓库状态查看；真实 Playwright/spider-king 取证、真实 OpenHands Agent 批量运行和正式部署仍按后续阶段推进。

## 文档入口

| 文档 | 阅读用途 |
| --- | --- |
| [完整开发设计](docs/开发设计.md) | 确认业务范围、系统架构、流程、汇报与人工审查 |
| [PlatformSpec v1](docs/PlatformSpec-v1.md) | 统一中间规范、字段证据、分页、置信度和生成边界 |
| [数据契约与 API](docs/数据契约与API.md) | 实现字段模型、数据库、接口、事件与版本关联 |
| [工作流与运行规范](docs/工作流与运行规范.md) | 实现 LangGraph、OpenHands Agent、Git、隔离运行和恢复 |
| [开发计划与验收](docs/开发计划与验收.md) | 开发顺序、工期假设、验收用例与决策清单 |

## 管理后台当前状态

前端位于 `frontend/`，技术栈为 React + TypeScript + Vite + Ant Design，采用白色简约的本地控制台布局。开发时可以分别启动 API 和前端：

```powershell
# 控制面 API
python -m uvicorn auto_spider.api.main:app --host 127.0.0.1 --port 18000

# 管理后台
cd frontend
npm install
$env:VITE_API_BASE_URL = "http://127.0.0.1:18000"
npm run dev
```

当 `VITE_API_BASE_URL` 未设置时，前端使用内置演示数据；设置后会读取任务、报告、PlatformSpec、工作流事件、人工运行、审查、修复和证据索引 API。候选 diff 通过本地 AI 产出仓库只读计算，系统不会把控制面代码推送到远程。

## 已确认的边界

- 工作目录：`D:\Project\Auto_spider`。
- 正式采集由人工运行，运行后必须人工审查。
- AI 自动开发、验证、汇报、生成候选提交，并修复能通过代码或配置解决的问题。
- 不做网站变化监控，不做运行时 AI 兜底解析。
- `platform_id`、`entity_id` 先留空，不伪造编号。
- 汇报业务规则尚未定稿；文档中的默认阈值和业务处理策略均标为建议。

原有 `思路流程.md` 和 `mermaid-diagram.png` 保留。本文档中出现的模块、接口、目录和命令除明确注明“已核对”外，均是待实现设计。

## 三个仓库的边界

本地控制面 `D:\Project\Auto_spider` 只保存 AI 编排、数据库、验证器和运行记录，不配置远程仓库，也不推送自身代码。

采集代码源仓库是 `D:\Project\Auto_spider_repositories\fun-crawler-v2`，当前基线为 `16cba8439396e371973e4ee0301d3a88f2f32ba5`，只作为实验输入和只读参考。

AI 产出仓库是 `D:\Project\Auto_spider_repositories\aicoding-auto_spider`，对应 `https://gitee.com/daxia-com/auto_spider.git`。AI 生成或修复的采集器只在这个仓库创建候选分支、提交和推送；不得修改 `C:\Users\ASUS\Desktop\jichu-v5-sync`。

## 本地 `.env` 配置

先复制 `.env.example` 为 `.env`。使用自定义 OpenAI 兼容接口时，至少填写：

```dotenv
OPENHANDS_LLM_MODEL=openai/<models 返回的模型名>
OPENHANDS_LLM_BASE_URL=https://your-endpoint.example/v1
OPENHANDS_LLM_API_MODE=responses
OPENHANDS_LLM_API_KEY=只放在本机，不要提交到 Git
```

`OPENHANDS_SERVER_URL`、`OPENHANDS_SESSION_API_KEY` 和 `OPENHANDS_SECRET_KEY` 必须与 OpenHands Agent Server 一致。首次验证建议保留 `AGENT_MODE=fake`；确认 Agent Server 已启动后，再改为 `AGENT_MODE=openhands`。`.env` 已被 `.gitignore` 忽略，不能把密钥写入 `.env.example`、日志、证据或提交记录。
