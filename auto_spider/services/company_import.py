"""Preview large company/link lists and submit them as one independently recoverable batch."""

import csv
import hashlib
import io
import re
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import select

from auto_spider.db.models import OnboardingBatch, OnboardingTask
from auto_spider.schemas import CreateBatchRequest, IntakeItem, platform_key_from_url
from auto_spider.services.browser_evidence import sanitize_url
from auto_spider.services.tasks import create_batch

MAX_ROWS = 5000


def preview_company_import(session, text):
    if len(text.encode("utf-8")) > 2_000_000:
        raise ValueError("名单过大，单批最多 5000 行")
    rows, seen = [], set()
    lines = text.lstrip("\ufeff").splitlines()
    for index, line in enumerate(lines, 1):
        if not line.strip():
            continue
        urls = list(re.finditer(r"https?://[^\s\"<>，]+", line))
        if index == 1 and line.strip().replace('"', "").lower() in {
            "公司名称,招聘链接",
            "公司名称\t招聘链接",
            "公司,链接",
            "company,url",
            "company\turl",
            "公司名称,招聘页面链接",
        }:
            continue
        delimiter = "\t" if "\t" in line else ","
        parts = next(csv.reader(io.StringIO(line), delimiter=delimiter))
        if len(parts) >= 2 and parts[1].strip().startswith(("http://", "https://")):
            name, url = parts[0].strip(), parts[1].strip()
        elif len(urls) == 1:
            name, url = line[: urls[0].start()].strip(" \t,;|"), urls[0].group().rstrip(",;|")
        else:
            name, url = parts[0].strip(), ""
        row = {
            "line": index,
            "company_name": name,
            "entry_url": sanitize_url(url) if url else "",
            "status": "VALID",
            "message": "",
            "platform_key": None,
        }
        try:
            if not name or not url:
                raise ValueError("每行需要公司名称和一个招聘链接")
            item = IntakeItem(entry_url=url, platform_name=name)
            canonical = str(item.entry_url)
            if item.entry_url.username or item.entry_url.password:
                raise ValueError("链接不能包含账号或密码")
            if sanitize_url(url) != url:
                raise ValueError("招聘链接包含敏感或临时鉴权参数，请使用公开入口")
            existing = session.scalar(
                select(OnboardingTask).where(OnboardingTask.normalized_url == canonical).limit(1)
            )
            if canonical in seen or existing:
                row.update(
                    status="DUPLICATE", message="链接已存在" if existing else "名单内链接重复"
                )
                if existing:
                    row["existing_task_id"] = existing.task_id
            seen.add(canonical)
            base = platform_key_from_url(canonical)
            key = base[:95] + "_" + hashlib.sha256(canonical.encode()).hexdigest()[:10]
            row.update(entry_url=canonical, platform_key=key)
        except (ValueError, ValidationError) as exc:
            row.update(status="INVALID", message=str(exc).split("\n")[0])
        rows.append(row)
        if len(rows) > MAX_ROWS:
            raise ValueError("单批最多 5000 行，请拆成多个批次")
    if not rows:
        raise ValueError("请填写公司名单")
    return {
        "rows": rows,
        "total": len(rows),
        "valid": sum(r["status"] == "VALID" for r in rows),
        "duplicates": sum(r["status"] == "DUPLICATE" for r in rows),
        "invalid": sum(r["status"] == "INVALID" for r in rows),
    }


def submit_company_import(session, text, request_id, actor):
    digest = hashlib.sha256(text.encode()).hexdigest()
    previous = session.scalar(
        select(OnboardingBatch).where(OnboardingBatch.client_request_id == request_id)
    )
    if previous:
        saved = previous.intake_json or {}
        if saved.get("request_hash") != digest or previous.created_by != actor:
            raise ValueError("相同请求编号的名单或用户不一致")
        return saved["result"]
    preview = preview_company_import(session, text)
    items = [
        IntakeItem(
            entry_url=r["entry_url"],
            platform_name=r["company_name"],
            platform_key=r["platform_key"],
        )
        for r in preview["rows"]
        if r["status"] == "VALID"
    ]
    if items:
        batch, tasks = create_batch(
            session,
            CreateBatchRequest(items=items, client_request_id=request_id),
            actor,
            commit=False,
        )
    else:
        batch = OnboardingBatch(
            batch_id=uuid4().hex,
            client_request_id=request_id,
            requested_count=preview["total"],
            accepted_count=0,
            rejected_count=preview["total"],
            status="NO_NEW_TASKS",
            created_by=actor,
        )
        session.add(batch)
        tasks = []
    batch.requested_count = preview["total"]
    batch.rejected_count = preview["invalid"] + preview["duplicates"]
    result = {
        "batch_id": batch.batch_id,
        "task_ids": [t.task_id for t in tasks],
        "accepted_count": len(tasks),
        "rejected_count": preview["invalid"],
        "duplicate_count": preview["duplicates"],
        "rows": preview["rows"],
    }
    batch.intake_json = {"request_hash": digest, "result": result}
    session.commit()
    return result
