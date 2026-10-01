# CareerCrew 全栈镜像（多阶段：web-builder 构建前端，builder 装依赖进独立 venv，runtime 组装最小产物）
# 构建上下文即仓库根：docker build -t careercrew .
#
# 分层策略：
# 1. web-builder：Node 22 编译 React 前端，产物输出到 dist/
# 2. builder：占位包 + pyproject 先装全部 Python 依赖（含 CPU 版 torch/FlagEmbedding）
# 3. runtime：组装 Python venv 与前端 dist，单端口启动提供静态 SPA 与 API 服务

# ── 阶段 0：前端构建 ──
FROM node:25-alpine AS web-builder
WORKDIR /web
COPY careercrew_web/package*.json ./
RUN npm ci --prefer-offline
COPY careercrew_web/ ./
RUN npm run build

# ── 阶段一：依赖构建 ──
FROM python:3.12-slim AS builder

ARG PIP_PROXY=""

ENV PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=10 \
    PYTHONDONTWRITEBYTECODE=1
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
WORKDIR /app

# 先单独装 CPU 版 torch（FlagEmbedding/sentence-transformers 的依赖），
# 避免后续 pip 解析时拉取 CUDA 版（多出 ~4GB nvidia 运行库）
COPY pyproject.toml README.md ./
RUN --mount=type=cache,target=/root/.cache/pip \
    mkdir -p careercrew_core careercrew_ai careercrew_api careercrew_mcp \
    && for p in careercrew_core careercrew_ai careercrew_api careercrew_mcp; do \
         echo '"""placeholder for dependency caching"""' > $p/__init__.py; done \
    && if [ -n "$PIP_PROXY" ]; then PIP_EXTRA="--proxy $PIP_PROXY"; else PIP_EXTRA=""; fi \
    && pip install $PIP_EXTRA torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install $PIP_EXTRA ".[web]"

# 真实源码覆盖占位包；项目本体以 --no-deps 安装（依赖已就位，秒级），
# 保证非 /app 工作目录启动（如 alembic、scripts）也能导入正确代码
COPY careercrew_core/ careercrew_core/
COPY careercrew_ai/ careercrew_ai/
COPY careercrew_api/ careercrew_api/
COPY careercrew_mcp/ careercrew_mcp/
COPY alembic.ini ./
RUN pip install --no-deps .

# ── 阶段二：运行时 ──
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# healthcheck 用 curl；psycopg/argon2 等带二进制轮子无需编译工具链
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv

COPY alembic.ini README.md pyproject.toml ./
COPY migrations/ migrations/
COPY config/ config/
COPY scripts/ scripts/
COPY careercrew_core/ careercrew_core/
COPY careercrew_ai/ careercrew_ai/
COPY careercrew_api/ careercrew_api/
COPY careercrew_mcp/ careercrew_mcp/

# Fail the image build if a published migration was edited or the revision
# manifest no longer describes the checked-in migration set.
RUN python scripts/validate_migrations.py --static

# 拷贝阶段 0 构建的前端生产构建产物，供 FastAPI 单端口托管 + SPA fallback
COPY --from=web-builder /web/dist /app/careercrew_web/dist

# 非 root 运行；data/（上传与解析产物）以 volume 挂载时注意属主
RUN useradd --create-home appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# 裸 docker run 也有健康检查（compose 的 healthcheck 与此等价）
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=5 \
    CMD curl -fsS http://localhost:8000/healthz || exit 1

# Alembic baseline 先行（#8 约定），失败即退出不做半启动
CMD ["sh", "-c", "alembic upgrade head && uvicorn careercrew_api.main:app --host 0.0.0.0 --port 8000"]
