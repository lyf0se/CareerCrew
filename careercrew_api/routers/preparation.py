"""Job preparation API, usable without initializing any LLM runtime."""
import logging
import os
from functools import lru_cache
from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Response

from careercrew_api.auth.dependencies import CurrentUser
from careercrew_core.preparation.exports import export_docx, export_pdf
from careercrew_core.preparation.models import (
    JdCaptureInput,
    JdCaptureResult,
    Opportunity,
    OpportunityInput,
    PreparationSession,
    PreparationSessionInput,
    ResumeVersion,
    ResumeVersionInput,
)
from careercrew_core.preparation.store import PreparationStore
from careercrew_core.state.settings import load_settings
from careercrew_core.tools.browser.job_detail import channel_for_url, fetch_job_jd

logger = logging.getLogger(__name__)

router = APIRouter()


@lru_cache(maxsize=1)
def get_preparation_store() -> PreparationStore:
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        raise HTTPException(status_code=503, detail="岗位准备存储尚未配置")
    return PreparationStore(dsn)


Store = Annotated[PreparationStore, Depends(get_preparation_store)]


def _found(value):
    if value is None:
        raise HTTPException(status_code=404, detail="岗位、简历版本或准备会话不存在")
    return value


def _collector_cdp_url() -> str:
    """采集用 CDP 端点（tools.search.boss_cdp_url）；未配置返回空串表示不尝试采集。"""
    try:
        return (load_settings().tools.search.boss_cdp_url or "").strip()
    except Exception:  # 配置缺失不应让收藏流程报错
        return ""


@router.get("/opportunities", response_model=list[Opportunity])
def list_opportunities(user: CurrentUser, store: Store):
    return store.list_opportunities(user["id"])


@router.post("/opportunities", response_model=Opportunity, status_code=201)
def create_opportunity(payload: OpportunityInput, user: CurrentUser, store: Store):
    row = store.create_opportunity(user["id"], payload.model_dump())
    from careercrew_core.career.product_events import safe_event
    safe_event(store.pool, user['id'], 'opportunity_saved', 'preparation')
    return row


@router.get("/opportunities/{opportunity_id}", response_model=Opportunity)
def get_opportunity(opportunity_id: str, user: CurrentUser, store: Store):
    return _found(store.get_opportunity(user["id"], opportunity_id))


@router.post("/capture-jd", response_model=JdCaptureResult)
def capture_jd(payload: JdCaptureInput, _user: CurrentUser):
    """按岗位链接预取 JD 正文（收藏岗位前调用，也可在手动录入里一键填充）。

    采集失败一律以 status 表达、不抛错：数据库对 jd 有 NOT NULL 约束，
    所以流程是"先取到 JD 再建岗位"，取不到时由前端引导手动粘贴。
    """
    url = (payload.url or "").strip()
    if not channel_for_url(url):
        return {
            "status": "unsupported",
            "jd": "",
            "message": "该链接不在可自动采集的来源内（目前支持 Boss直聘 与 猎聘），请手动粘贴 JD",
        }

    cdp_url = _collector_cdp_url()
    if not cdp_url:
        return {
            "status": "failed",
            "jd": "",
            "message": "采集器尚未配置：请先在工具设置里启用 CDP 采集（tools.search.boss_cdp_url）",
        }

    try:
        jd = fetch_job_jd(url, cdp_url=cdp_url)
    except Exception as err:
        logger.warning("capture jd failed for %s: %s", url, err)
        return {"status": "failed", "jd": "", "message": str(err) or "采集失败，请稍后重试"}

    if not jd:
        return {
            "status": "failed",
            "jd": "",
            "message": "未能从详情页读到 JD 正文，请确认采集浏览器已登录 Boss直聘 且窗口保持打开",
        }

    return {"status": "captured", "jd": jd, "message": f"已采集 JD（{len(jd)} 字）"}


@router.put("/opportunities/{opportunity_id}", response_model=Opportunity)
def update_opportunity(opportunity_id: str, payload: OpportunityInput, user: CurrentUser, store: Store):
    return _found(store.update_opportunity(user["id"], opportunity_id, payload.model_dump()))


@router.delete("/opportunities/{opportunity_id}")
def delete_opportunity(opportunity_id: str, user: CurrentUser, store: Store):
    if not store.delete_opportunity(user["id"], opportunity_id):
        _found(None)
    return {"ok": True}


@router.get("/opportunities/{opportunity_id}/versions", response_model=list[ResumeVersion])
def list_versions(opportunity_id: str, user: CurrentUser, store: Store):
    _found(store.get_opportunity(user["id"], opportunity_id))
    return store.list_versions(user["id"], opportunity_id)


@router.post("/opportunities/{opportunity_id}/versions", response_model=ResumeVersion, status_code=201)
def create_version(opportunity_id: str, payload: ResumeVersionInput, user: CurrentUser, store: Store):
    return _found(store.create_version(user["id"], opportunity_id, payload.model_dump()))


@router.get("/opportunities/{opportunity_id}/versions/{version_id}/export")
def export_version(opportunity_id: str, version_id: str, user: CurrentUser, store: Store,
                   format: Literal["pdf", "docx"] = "pdf"):
    version = _found(store.get_version(user["id"], opportunity_id, version_id))
    if format == "pdf":
        body = export_pdf(version["label"], version["content"])
        mime = "application/pdf"
    else:
        body = export_docx(version["label"], version["content"])
        mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    name = "".join(c for c in version["label"] if c not in '/\\:*?"<>|' and ord(c) >= 32)
    name = (name.strip(" .") or "简历") + "." + format
    return Response(body, media_type=mime, headers={
        "Content-Disposition": f"attachment; filename=resume.{format}; filename*=UTF-8''{quote(name, safe='')}",
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
    })


@router.post("/opportunities/{opportunity_id}/sessions", response_model=PreparationSession, status_code=201)
def create_session(opportunity_id: str, payload: PreparationSessionInput, user: CurrentUser, store: Store):
    return _found(store.create_session(user["id"], opportunity_id, payload.model_dump()))


@router.get("/sessions/{thread_id}", response_model=PreparationSession)
def get_session(thread_id: str, user: CurrentUser, store: Store):
    return _found(store.get_session(user["id"], thread_id))
