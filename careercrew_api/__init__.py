"""careercrew_api - FastAPI 后端（与 CLI 平级的组合根）。

双服务本地开发：uvicorn :8100（API）+ vite :5276（前端）。
生产模式：npm run build -> FastAPI StaticFiles 托管 careercrew_web/dist（SPA fallback）。
"""
