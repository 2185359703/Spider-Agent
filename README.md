# AI 招聘采集接入平台

输入招聘入口 URL，由 AI 分析网站、开发采集器、验证并汇报，自动生成候选提交；人工运行、人工审查后采纳，有问题则进入 AI 纠错。

当前已完成首阶段后端骨架、数据模型、Alembic 迁移、FastAPI API、Celery 任务入口、LangGraph 图定义、真实 Playwright 分析、真实 OpenHands Agent 代码通道、受管仓库只读检查和自动化测试，并已开始管理后台实现。当前前端提供任务队列、批次视图、新建接入、任务详情、采集样本查看与 JSON/JSONL 导入导出、结构化字段审查、规则版本草稿/发布、接入汇报、候选 diff、候选集中管理、人工运行登记、证据下载、实时 AI 日志和规则/仓库状态查看。离线替身只存在于测试注入，不作为运行时路径。

## 文档入口

| 文档 | 阅读用途 |
| --- | --- |
| [完整开发设计](docs/开发设计.md) | 确认业务范围、系统架构、流程、汇报与人工审查 |
| [PlatformSpec v1](docs/PlatformSpec-v1.md) | 统一中间规范、字段证据、分页、置信度和生成边界 |
| [数据契约与 API](docs/数据契约与API.md) | 实现字段模型、数据库、接口、事件与版本关联 |
| [工作流与运行规范](docs/工作流与运行规范.md) | 实现 LangGraph、OpenHands Agent、Git、隔离运行和恢复 |
| [开发计划与验收](docs/开发计划与验收.md) | 开发顺序、工期假设、验收用例与决策清单 |
| [执行与恢复](docs/执行与恢复.md) | 原生 checkpoint、Agent 会话重连、暂停取消、自动纠错和执行权限 |
| [P1 闭环实现与验收](docs/P1闭环实现与验收.md) | 人工编辑、可靠采集投递、空结果审查、失败证据包及真实链路验收 |
| [Playwright CLI 与资源回收](docs/Playwright%20CLI与资源回收.md) | 主动浏览器取证、批次复用、暂停保留和临时资源清理 |

## Agent Skills

OpenHands 分析、生成和修复会自动加载两个版本化 Skill：

- `agent_skills/collector-onboarding/`：接入工作流、PlatformSpec、空 ID、汇报、人工运行/审查、失败纠错和本地 Git 边界；
- `agent_skills/spider-king-collector/`：从真实网络证据恢复接口、动态状态、响应解码和分页，最终交付无浏览器依赖的 Python HTTP 采集器。

`GET /api/v1/system/health` 会分别返回 `collector_skill` 和 `spider_king_skill` 状态。Skill 由控制面读取并注入 Agent prompt，网页内容不能覆盖这些受信规则。

## 管理后台当前状态

前端位于 `frontend/`，技术栈为 React + TypeScript + Vite + Ant Design，采用白色简约的本地控制台布局。开发时可以分别启动 API 和前端：

采集代码页支持编辑当前入口的采集器、配置和测试，保存后生成新的本地版本，旧提交和已采集数据保留。手工版本执行静态检查、契约测试和回归测试；通过后进入待人工采集，检查失败则保存为草稿，不能启动采集。保存不推送远程，也不会把上一个版本的实测结果当成新版本验证。

采集数据页支持抽查后点击“公司通过”，无需逐条标记通过。一次确认绑定各入口的最新采集轮次与代码提交，入口存在已记录问题时不能通过；多入口同时审查失败会整体回滚。问题仍可在岗位详情页记录并提交 AI 纠错。

```powershell
# 控制面 API
python -m uvicorn auto_spider.api.main:app --host 127.0.0.1 --port 18000

# 管理后台
cd frontend
npm install
$env:VITE_API_BASE_URL = "http://127.0.0.1:18000"
npm run dev
```

必须设置 `VITE_API_BASE_URL`；未设置时前端会明确提示 API 未配置，不加载演示数据。设置后会读取任务、报告、PlatformSpec、逐条工作日志、人工运行、审查、修复和证据索引 API。候选 diff 通过本地 AI 产出仓库只读计算，系统不会把控制面代码推送到远程。

Docker Compose 的 API/worker 默认使用 `python:3.12-slim` 构建。若开发机无法访问 Docker Hub，而本机已有兼容镜像，可以指定本地基底镜像，例如：

```powershell
$env:AUTO_SPIDER_BASE_IMAGE = "jichu-crawler:latest"
docker compose --parallel 1 build api worker
docker compose up -d --no-build api worker
```

启动后可检查 `http://127.0.0.1:18000/healthz` 和 `/api/v1/system/health`。

## 本机资源与构建

本机默认 `WORKER_CONCURRENCY=1`，一次执行一个 AI 接入或修复任务。Celery 预取倍数为 1，每个子进程完成 5 个任务后回收。此前未显式限制时，这台 20 逻辑处理器的电脑会启动 20 个 prefork 子进程。

Compose 为本项目容器设置如下上限，环境变量可覆盖：

| 服务 | 内存上限 | CPU 配额 | 内存配置变量 |
| --- | --- | --- | --- |
| API | 512 MiB | 0.5 | `API_MEMORY_LIMIT` |
| worker | 1536 MiB | 1 | `WORKER_MEMORY_LIMIT` |
| OpenHands Agent Server | 2 GiB | 2 | `AGENT_MEMORY_LIMIT` |
| MySQL | 768 MiB | 1 | `MYSQL_MEMORY_LIMIT` |
| Redis | 128 MiB | 0.25 | `REDIS_MEMORY_LIMIT` |

这些是运行上限，不是预占内存，也不限制 BuildKit 或整个 WSL。超出内存预算的进程可能被容器终止；高负载任务需要单独评估预算。修改 Compose 上限后，需在没有活动任务时重新创建相关容器，并用 `docker stats --no-stream` 核对实际占用和上限。

镜像依赖和 Chromium 安装层位于代码、README 复制层之前。普通代码或文档修改可复用依赖缓存；uv 下载使用 BuildKit 缓存目录。Python 镜像构建上下文排除前端、node_modules、虚拟环境、工作树、日志和证据。

日常启动已有镜像使用 `docker compose up -d --no-build`。需要新镜像时使用 `docker compose --parallel 1 build api worker`，构建失败后先检查原因，不连续重试或同时运行全量测试。若内存紧张且旧采集项目仍在运行，不应使用 `wsl --shutdown` 或停止整个 Docker Desktop 来释放内存。

同一工作目录只保留一个会话执行构建、启动和代码修改。分叉会话仍可能与原会话同时运行；停止终端里的单次构建并不会停止另一个会话再次发起构建。先在 Codex 中停止额外的执行会话，再排查资源占用。

配置依据：[Celery worker 设置](https://docs.celeryq.dev/en/stable/userguide/configuration.html#worker-concurrency)、[Docker 构建缓存](https://docs.docker.com/build/cache/optimize/)。

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

AI 产出仓库是 `D:\Project\Auto_spider_repositories\aicoding-auto_spider`，对应 `https://gitee.com/daxia-com/auto_spider.git`。AI 生成或修复的采集器只在这个仓库创建本地候选分支和提交，默认不推送；不得修改 `C:\Users\ASUS\Desktop\jichu-v5-sync`。当前 AI 产出基线为 `c932dbac4bb75df04008c85d3560c1b7a28ec211`，支持未映射平台暂存采集。

## 本地 `.env` 配置

先复制 `.env.example` 为 `.env`。使用自定义 OpenAI 兼容接口时，至少填写：

```dotenv
OPENHANDS_LLM_MODEL=openai/<models 返回的模型名>
OPENHANDS_LLM_BASE_URL=https://your-endpoint.example/v1
OPENHANDS_LLM_API_MODE=responses
OPENHANDS_LLM_API_KEY=只放在本机，不要提交到 Git
```

`OPENHANDS_SERVER_URL`、`OPENHANDS_SESSION_API_KEY` 和 `OPENHANDS_SECRET_KEY` 必须与 OpenHands Agent Server 一致。生产运行使用 `AGENT_MODE=openhands`、`ANALYSIS_MODE=browser`；测试替身只通过测试代码显式注入，不由运行环境选择。`.env` 已被 `.gitignore` 忽略，不能把密钥写入 `.env.example`、日志、证据或提交记录。

## AI 网关切换

平台支持通过管理后台“系统设置 → AI 网关”切换 profile。切换只影响新建的 Agent 会话，
已经运行的会话会继续使用创建时保存的网关快照。

OpenCode Zen 配置示例：

```dotenv
AI_GATEWAY_PROFILE=opencode_zen
OPENCODE_BASE_URL=https://opencode.ai/zen/v1
OPENCODE_MODEL=deepseek-v4.1-flash
OPENCODE_API_MODE=chat
OPENCODE_API_KEY=只放在本机 .env，不要提交
```

OpenCode Zen 使用 OpenAI 兼容的 `/v1/chat/completions` 接口。切换前必须在 `.env` 配置凭证，
然后执行 `docker compose up -d --build api worker scheduler`。系统设置页不会显示 API key；
数据库只保存当前 profile 名称，AgentExecution 保存模型、地址和 API 模式快照。
