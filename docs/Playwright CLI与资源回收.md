# Playwright CLI 与批次资源回收

平台使用 OpenHands SDK + Agent Server。`CollectorBrowserTool` 调用真实 Playwright CLI，
浏览器用于招聘页面分析和失败诊断，正式采集器继续使用 Python HTTP。

## 借鉴 spider-king 的方法

1. 有足够的已保存证据时先复用证据；新入口或失效证据才重新观察网页。
2. 区分入口、静态资源、列表、详情和翻页请求。入口返回 HTML 很正常，不能直接当成 JSON 接口。
3. 使用页面快照定位元素，操作实习筛选和分页，检查实际请求参数、响应正文与格式。
4. 先证明一个业务请求能复现，再扩展分页、字段映射和代码生成。不能把猜测写成高置信度事实。
5. 保留公司内部的会话链，记录脱敏证据；采集代码本身不依赖浏览器。

这是 upstream spider-king 的 OpenHands / CLI 适配子集。目前支持页面和网络取证，
未接入 upstream 的 chrome-devtools → js-reverse 断点、调用栈与 initiator 调试接力。
需要这些能力却无法取得时，必须报告能力缺口，不能宣称完成运行时追踪。

## Agent 的主动调用

分析节点由只读 OpenHands Agent 驱动。生成和修复节点也注册同一个工具。
列表/详情接口、响应格式、分页、解码或必要字段不确定时，Agent 应主动使用：

```text
open → snapshot → click / select → requests → request-body / response-body
                    ↓
            保存真实证据 → 更新 PlatformSpec revision → 生成或修复代码
```

工具返回 `evidence_ref` 和 `evidence_read_path`，前者用于 Spec 引用，后者用于证据读取。
证据文件保存到任务目录，并建立 `evidence_files` 索引。CLI 失败也保存错误证据，
不把工具调用失败或证据不足解释为公司没有岗位。

Agent 只能调用注册的页面和网络命令，不获得任意终端、`eval`、`run-code`、上传或全部会话关闭权限。
凭证不传入浏览器子进程；网页内容不能改变文件白名单和提交边界。

## 批次复用

每个接入批次有一个固定 CLI session 和浏览器进程，保留一个空白页面。
公司 A 取得取证权限后打开公司页面；完成时释放公司页面，浏览器继续保留。
公司 B 在同一浏览器打开新页面。公司内部重复 `open` 使用当前页面，避免堆积标签页。

一个批次同一时间只有一个公司拥有操作权限。其他公司不能交叉导航、读取网络请求。
默认 worker 并发为 1；浏览器仍有文件锁与公司所有权检查。命令预算按执行轮次计算，
不会因为批次公司多而共用一个 100 次预算。

暂停和断线时保留公司页面及会话链；恢复后接着使用，明确取消后才能回收。
批次仍有公司尚未完成分析/开发/验证时，不因某一家结束就关闭整个浏览器。

## 清理边界

Celery maintenance 每 60 秒检查可回收批次。批次所有任务已结束开发阶段或结束，
且没有排队/执行中的 dispatch、活动租约或需要恢复的 Agent 时，才回收：

- 已结束的远程 Agent conversation 和执行策略文件；
- 批次浏览器、批次临时 HOME/cache/tmp/output、短路径 socket 目录；
- 已结束工作区内的 `.pytest_cache`、`.ruff_cache`、`__pycache__`。

Agent Server 内置清理线程每 30 秒检查无公司占用且空闲超过 30 分钟的浏览器。
浏览器自身不使用固定空闲关闭计时器，避免误关闭暂停中仍需恢复的会话。
所有批次关闭检查均在浏览器锁内完成。清理路径必须属于本平台拥有的目录。

代码、Git 提交、worktree、PlatformSpec 历史、报告、审查记录、采集数据和证据不自动删除。
共享 Node/Chromium 依赖也不按公司删除。平台不执行 Docker 全局 prune 或进程全局 kill。

## 验证方式

```powershell
docker compose exec -T api python -m pytest -q tests/test_browser_cli.py tests/test_browser_tool.py tests/test_resource_cleanup.py tests/test_cli_evidence.py
docker compose exec -T api python -m tests.browser_agent_probe
```

第二条是可选真实模型验证，会访问用户已授权的祖龙、莉莉丝公开招聘入口，
使用独立 SQLite 验证数据库。它检查真实 Agent 调用、两家公司共用一次浏览器启动、
证据索引和批次清理；不会运行正式采集、采纳代码或修改业务任务。
验证报告保存在 `evidence/runtime-verification/<batch_id>/manifest.json`。
