"""Convert observable SDK events into readable admin activity rows."""

import json

from auto_spider.services.browser_evidence import sanitize_text


def activity_event(event):
    event_type = type(event).__name__
    action = getattr(event, "action", None)
    observation = getattr(event, "observation", None)
    detail = {"event_type": event_type, "actor": "AI", "action_type": "message"}
    if event_type == "ConversationErrorEvent":
        code = sanitize_text(str(getattr(event, "code", "AGENT_ERROR")))[:200]
        error_detail = sanitize_text(str(getattr(event, "detail", "")))[:4000]
        detail.update(
            actor="Agent Server",
            action_type="error",
            error_code=code,
            error_detail=error_detail,
            severity="ERROR",
        )
        return f"{code}: {error_detail}", detail
    if action:
        name = type(action).__name__
        if name in {"BrowserAction", "CollectorBrowserAction"}:
            command = getattr(action, "action", "")
            label = {
                "open": "打开招聘页面",
                "goto": "切换页面",
                "snapshot": "读取页面结构",
                "click": "点击页面元素",
                "select": "切换筛选条件",
                "fill": "填写筛选内容",
                "press": "操作页面",
                "requests": "查看网络请求",
                "request": "读取请求",
                "request-headers": "读取请求头",
                "request-body": "读取请求参数",
                "response-headers": "读取响应头",
                "response-body": "读取响应正文",
                "console": "查看页面控制台",
                "screenshot": "保存页面截图",
                "close": "释放公司页面",
            }.get(command, command)
            detail.update(
                actor="浏览器工具",
                action_type="browser",
                command=command,
                url=getattr(action, "url", None),
                phase="start",
            )
            return f"浏览器：{label}", detail
        path = getattr(action, "path", None)
        writing = "Write" in name
        detail.update(
            area=getattr(action, "area", "workspace"),
            actor="工具",
            action_type="write" if writing else "read",
            path=path,
            tool=name,
            phase="start",
            tool_call_id=str(getattr(event, "tool_call_id", "")),
        )
        message = f"{'开始修改文件' if writing else '开始读取文件'}：{path or name}"
    elif observation:
        failed = bool(getattr(observation, "is_error", False))
        detail.update(
            actor="工具",
            action_type="result",
            phase="failed" if failed else "completed",
            is_error=failed,
            tool_call_id=str(getattr(event, "tool_call_id", "")),
        )
        if hasattr(observation, "model_dump"):
            raw = observation.model_dump(mode="json")
            detail["output"] = sanitize_text(str(raw)[:8000])
            for content in raw.get("content", []):
                try:
                    payload = json.loads(content.get("text", ""))
                except (ValueError, TypeError):
                    continue
                if not isinstance(payload, dict) or not payload.get("action"):
                    continue
                detail.update(
                    actor="浏览器工具",
                    command=payload["action"],
                    output=sanitize_text(str(payload.get("output", "")))[:8000],
                    error_code=payload.get("error_code"),
                )
                if payload.get("error_code") == "BROWSER_STALE_REF" and (
                    (payload.get("recovery") or {}).get("status") == "completed"
                ):
                    detail.update(
                        severity="WARNING",
                        recoverable=True,
                        recovery=payload["recovery"],
                        phase="needs_reselection",
                    )
                    return "页面引用已失效，已刷新快照，等待重新选择目标", detail
        message = "工具执行失败" if failed else "工具执行完成"
    else:
        llm = getattr(event, "llm_message", None)
        message = " ".join(getattr(c, "text", "") for c in getattr(llm, "content", ()))
        error = getattr(event, "error", None) or getattr(event, "exception", None)
        if error:
            message, detail["action_type"] = str(error), "error"
        if not message:
            message = event_type
    return sanitize_text(message[:4000]), detail
