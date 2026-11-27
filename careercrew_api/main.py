"""FastAPI 应用：CORS + /api 挂载 + 生产托管 careercrew_web/dist（SPA fallback）。

开发：uvicorn careercrew_api.main:app --reload --port 8100（+ vite :5276 代理 /api）
生产：npm run build -> uvicorn 单端口托管 careercrew_web/dist（SPA fallback 到 index.html）
"""
from __future__ import annotations

import logging
import os
import threading
import time
import traceback
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from careercrew_api.auth.middleware import TrustedOriginMiddleware
from careercrew_api.logging_config import new_request_id, request_id_var, setup_logging
from careercrew_api.routers import (
    agent,
    attachments,
    auth,
    browser,
    career,
    chat,
    consult,
    context,
    data,
    feedback,
    interview,
    knowledge,
    preparation,
    quality,
    resume,
    threads,
)
from careercrew_api.runtime import RuntimeInitError
from careercrew_core.state.settings import load_auth_settings

logger = logging.getLogger("careercrew_api")

DIST = Path(__file__).resolve().parents[1] / "careercrew_web" / "dist"

setup_logging()


_SHARE_PATH_PREFIXES = ("/api/career/share/", "/share/")
_SHARE_PRIVACY_HEADERS = {
    "Cache-Control": "no-store, max-age=0",
    "Pragma": "no-cache",
    "X-Robots-Tag": "noindex, nofollow",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}


def _is_share_token_path(path: str) -> bool:
    return any(path.startswith(prefix) and len(path) > len(prefix)
               for prefix in _SHARE_PATH_PREFIXES)


def _safe_request_path(path: str) -> str:
    """分享令牌是 bearer 凭据，应用访问日志只记录经过脱敏的路由形状。"""
    for prefix in _SHARE_PATH_PREFIXES:
        if path.startswith(prefix) and len(path) > len(prefix):
            return f"{prefix}[redacted]"
    return path


class _UvicornSharePathFilter(logging.Filter):
    """Redact share bearer credentials from Uvicorn's server-level access log."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        # Uvicorn AccessFormatter arguments are:
        # (client_addr, method, full_path, http_version, status_code).
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            values = list(args)
            values[2] = _safe_request_path(values[2])
            record.args = tuple(values)
        return True


_uvicorn_share_path_filter = _UvicornSharePathFilter()
logging.getLogger("uvicorn.access").addFilter(_uvicorn_share_path_filter)


def _recover_interrupted_upload_tasks() -> int:
    """启动时收口租约已过期的任务；新鲜租约由其他 worker 持有，不会被触碰。"""
    from careercrew_core import upload_tasks

    store = upload_tasks.get_upload_task_store()
    return store.mark_interrupted() if store is not None else 0


def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def _validation_detail(errors: list[dict]) -> str:
    """把 FastAPI 参数校验错误转成中文提示；自定义校验器已是中文则直接使用。"""
    parts: list[str] = []
    for err in errors[:3]:
        msg = str(err.get("msg", ""))
        if _has_cjk(msg):
            parts.append(msg)
            continue
        loc = [
            str(p) for p in err.get("loc", [])
            if p not in ("body", "query", "path", "header", "cookie")
        ]
        field = ".".join(loc) or "参数"
        parts.append(f"「{field}」填写不正确")
    return "；".join(parts) or "请求参数不正确，请检查后重试"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """过期/长期吊销刷新会话清理（守护线程，避免阻塞事件循环）。"""
    from careercrew_api.auth.dependencies import get_auth_service
    from careercrew_api.dream import start_dream_scheduler

    stop = threading.Event()
    interval = max(get_auth_service().settings.cleanup_interval_hours, 1) * 3600

    interrupted = _recover_interrupted_upload_tasks()
    if interrupted:
        logger.warning("upload task recovery: marked %d expired tasks interrupted", interrupted)

    def _cleanup_once() -> None:
        try:
            removed = get_auth_service().store.delete_expired_refresh_sessions()
            if removed:
                logger.info("refresh session cleanup: %d sessions removed", removed)
        except Exception:
            logger.warning("refresh session cleanup failed", exc_info=True)

    def _loop() -> None:
        _cleanup_once()  # 启动即先清一轮（与附件 TTL 清理口径一致），此后按间隔
        while not stop.wait(interval):
            _cleanup_once()

    # 状态已落 PostgreSQL，任意 worker 可查；执行线程仍归提交请求的 worker。
    workers = int(os.environ.get("WEB_CONCURRENCY", "1") or "1")
    if workers > 1:
        logger.warning(
            "WEB_CONCURRENCY=%d：上传任务状态可跨 worker 查询，但解析执行仍在"
            "提交请求的 worker 内；部署退出应保留优雅停机窗口",
            workers,
        )

    thread = threading.Thread(target=_loop, name="refresh-session-cleanup", daemon=True)
    thread.start()

    # 附件 TTL 清理（每日一轮，启动即先跑一次；失败不中断服务）
    from careercrew_api.maintenance import CLEANUP_INTERVAL_SECONDS, run_attachment_cleanup_once

    def _attachment_loop() -> None:
        while True:
            try:
                removed = run_attachment_cleanup_once()
                if removed:
                    logger.info("attachment TTL cleanup: %d items removed", len(removed))
            except Exception:
                logger.warning("attachment TTL cleanup failed, retry next cycle", exc_info=True)
            if stop.wait(CLEANUP_INTERVAL_SECONDS):
                return

    threading.Thread(target=_attachment_loop, name="attachment-ttl-cleanup", daemon=True).start()

    # HR 回复监听（E 批次）：定时拉 Boss 未读会话写入情景记忆；默认关闭
    from careercrew_api.deps import get_runtime_dep
    from careercrew_api.hr_monitor import start_hr_monitor

    try:
        rt = get_runtime_dep()
        hm = rt.settings.hr_monitor
        cdp = (hm.cdp_url or getattr(rt.settings.tools.search, "boss_cdp_url", "") or "").strip()
        start_hr_monitor(get_runtime_dep, get_auth_service, hm.enabled and bool(cdp),
                         hm.interval_minutes, stop)
    except Exception:
        logger.warning("hr_monitor 配置读取失败，本轮不启动", exc_info=True)

    # Auto Dream：每日低峰 consolidation（memory.consolidation.dream_schedule="HH:MM" 开启，off 关闭）
    from careercrew_api.deps import get_runtime_dep

    rt = get_runtime_dep()
    schedule = ""
    try:
        if getattr(rt, "settings", None) is not None:
            schedule = rt.settings.memory.consolidation.dream_schedule
    except Exception:
        logger.warning("dream scheduler configuration unavailable; scheduler disabled")
    start_dream_scheduler(get_runtime_dep, get_auth_service, schedule, stop)

    yield
    stop.set()


def create_app() -> FastAPI:
    # 重组件保持惰性初始化，但认证生产配置必须在启动时 fail-fast。
    auth_settings = load_auth_settings()
    app = FastAPI(title="CareerCrew API", version="0.1.0", lifespan=lifespan)

    app.add_middleware(
        TrustedOriginMiddleware, allowed_origins=list(auth_settings.trusted_origins)
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(auth_settings.trusted_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── request_id 关联：X-Request-ID 透传或生成，日志自动携带，响应头回写 ──
    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        rid = (request.headers.get("X-Request-ID") or "").strip() or new_request_id()
        request_id_var.set(rid)
        start = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
        finally:
            try:
                from careercrew_core.observability.metrics import get_metrics_registry

                route = request.scope.get("route")
                route_path = getattr(route, "path", request.url.path)
                get_metrics_registry().observe_http(
                    request.method, route_path, status_code,
                    time.perf_counter() - start,
                )
            except Exception:
                logger.debug("http metrics recording failed", exc_info=True)
        if _is_share_token_path(request.url.path):
            response.headers.update(_SHARE_PRIVACY_HEADERS)
        # task-per-request 隔离，不 reset：流式响应体（NDJSON）在 middleware
        # 返回后仍需携带同一 request_id 排查跨 logger 报错。
        response.headers["X-Request-ID"] = rid
        logger.info(
            "%s %s -> %d (%.0f ms)",
            request.method, _safe_request_path(request.url.path), response.status_code,
            (time.perf_counter() - start) * 1000,
        )
        return response

    # /api 路由
    app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
    app.include_router(browser.router, prefix="/api", tags=["browser"])
    # threads（conversation Source of Truth）先于 data 注册，故 POST/PATCH/DELETE
    # /api/threads 及 /api/threads/{id}/messages|clear|export、/api/messages/{id}/regenerate
    # 均由 threads.py 接管；data.py 仍定义 GET /api/threads（memory 列表）与其
    # PATCH/DELETE 旧路由，但后者已被 threads.py 的同路径路由遮蔽（保留供兼容引用）。
    app.include_router(threads.router, prefix="/api", tags=["threads"])
    app.include_router(feedback.router, prefix="/api", tags=["feedback"])
    app.include_router(quality.router, prefix="/api/quality", tags=["quality"])
    app.include_router(data.router, prefix="/api", tags=["data"])
    app.include_router(chat.router, prefix="/api/chat", tags=["chat"])
    app.include_router(interview.router, prefix="/api/interview", tags=["interview"])
    app.include_router(resume.router, prefix="/api/resume", tags=["resume"])
    app.include_router(preparation.router, prefix="/api/preparation", tags=["preparation"])
    app.include_router(career.router, prefix="/api/career", tags=["career"])
    app.include_router(consult.router, prefix="/api/consult", tags=["consult"])
    app.include_router(knowledge.router, prefix="/api/knowledge", tags=["knowledge"])
    app.include_router(context.router, prefix="/api/context", tags=["context"])
    app.include_router(attachments.router, prefix="/api/chat/attachments", tags=["attachments"])
    app.include_router(agent.router, prefix="/api", tags=["agent"])
    from careercrew_api.routers import metrics
    app.include_router(metrics.router, tags=["metrics"])
    from careercrew_api.routers import user_settings
    app.include_router(user_settings.router, prefix="/api", tags=["settings"])

    # ── 无鉴权探针（放 LB/K8s/监控后面；须注册在 SPA fallback 通配路由之前） ──
    @app.get("/healthz")
    async def healthz() -> dict:
        """liveness：进程存活即 200，不触碰任何外部依赖（避免重启风暴）。"""
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        """readiness：基础设施可达性（Postgres/Qdrant）。任一不可达 → 503，LB 摘除流量。

        有意绕过 runtime 重组件惰性初始化：探针只探测依赖连通性，不触发模型加载。
        组件级明细仍走带鉴权的 GET /api/health（data 路由）。
        """
        import os

        from qdrant_client import QdrantClient

        from careercrew_core.state.settings import load_settings

        checks: dict[str, str] = {}

        try:
            from careercrew_api.auth.dependencies import get_auth_service

            store = get_auth_service().store
            with store._connect() as conn:  # noqa: SLF001 — 探针复用账号库连接池
                conn.execute("SELECT 1")
            checks["postgres"] = "ok"
        except Exception:
            # 探针无鉴权，异常细节（DSN/主机/驱动错误）只进服务端日志，不回给客户端
            logger.warning("readyz postgres check failed", exc_info=True)
            checks["postgres"] = "unavailable"

        try:
            cfg = load_settings().vector_store
            client = QdrantClient(url=cfg.url or os.environ.get("QDRANT_URL", ""), timeout=3)
            client.get_collections()
            checks["qdrant"] = "ok"
        except Exception:
            logger.warning("readyz qdrant check failed", exc_info=True)
            checks["qdrant"] = "unavailable"

        ok = all(v == "ok" for v in checks.values())
        return JSONResponse(
            status_code=200 if ok else 503,
            content={"status": "ready" if ok else "not_ready", "checks": checks},
        )

    # 生产模式：托管 careercrew_web/dist（SPA fallback）
    if DIST.exists():
        # 静态资源（JS/CSS/图片）直接托管
        app.mount("/assets", StaticFiles(directory=str(DIST / "assets")), name="assets")

        # SPA fallback：非 /api 路径 -> index.html（支持前端路由如 /interview）
        @app.get("/{full_path:path}")
        async def spa_fallback(full_path: str):
            # API 路径绝不能回退到 HTML。否则前端调用一个尚未部署的接口时，
            # 会把 index.html 当 JSON 解析，最终只看到 "Unexpected token '<'"。
            if full_path == "api" or full_path.startswith("api/"):
                return JSONResponse(status_code=404, content={"detail": "API 接口不存在"})
            # full_path 来自 URL，可能含 ../ 等点段（如 /..%2f..%2f.env）；
            # 必须解析后确认仍落在 DIST 内，否则回退 index.html，防止任意文件读取。
            file_path = (DIST / full_path).resolve()
            if full_path and file_path.is_relative_to(DIST) and file_path.is_file():
                return FileResponse(file_path)
            return FileResponse(str(DIST / "index.html"))

    # ── 全局异常处理：所有未捕获异常统一返回中文 JSON，不再出现英文 "Internal Server Error" ──
    @app.exception_handler(RuntimeInitError)
    async def runtime_init_error_handler(_request: Request, exc: RuntimeInitError) -> JSONResponse:
        """重组件初始化失败（向量库连接等）→ 503，用户可见中文提示。"""
        msg = str(exc).strip()
        if not _has_cjk(msg):
            msg = "AI 服务初始化失败，请检查后端配置后重试"
        logger.error("Runtime init error: %s", exc)
        return JSONResponse(status_code=503, content={"detail": f"AI 服务暂不可用：{msg}"})

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
        """请求参数校验失败 → 422，把英文校验错误汇总成中文提示。"""
        return JSONResponse(status_code=422, content={"detail": _validation_detail(exc.errors())})

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        """兜底：未处理异常 → 500 中文提示，完整堆栈打到服务端日志便于排查。"""
        logger.error(
            "Unhandled error on %s %s: %s\n%s",
            request.method, _safe_request_path(request.url.path), exc, traceback.format_exc(),
        )
        return JSONResponse(status_code=500, content={"detail": "服务器内部错误，请稍后重试"})

    return app


app = create_app()
