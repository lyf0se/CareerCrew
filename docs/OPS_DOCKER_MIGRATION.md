# 已有 PostgreSQL / Qdrant 迁移到 Compose

本文用于把仓库外手动创建的 `postgres`、`qdrant` 容器数据，迁移到
`docker-compose.yml` 管理的完整三服务栈。

迁移期间旧容器保持在线，只读取一致性快照；新栈使用独立卷：

- PostgreSQL：`careercrew_pgdata`
- Qdrant：`careercrew_qdrant_storage`
- 上传与解析文件：`careercrew_app_uploads`

## 1. 迁移前备份

在仓库根目录执行：

```powershell
$env:CAREERCREW_PG_CONTAINER = "postgres"
$env:QDRANT_URL = "http://127.0.0.1:6333"
$env:QDRANT_COLLECTIONS = "careercrew_mm,careercrew_episodic_v2,careercrew_workspace_messages"

F:\Python_develop\miniconda3\envs\careercrew\python.exe `
  scripts\backup_restore.py create `
  --backup-root data\migrations\pre-docker-migration `
  --retention-days 3650
```

该目录包含 PostgreSQL custom dump、Qdrant 集合 snapshot、`uploads/parsed`
压缩包以及 SHA-256 清单。不要在没有 `manifest.json` 和 `verify` 通过的情况下
继续迁移。

## 2. 启动新数据库

```powershell
docker compose up -d postgres qdrant
docker compose ps postgres qdrant
```

新服务只在 Compose 网络内提供 `postgres:5432` 和 `qdrant:6333`，不会与旧
容器争抢宿主机端口。

## 3. 恢复 PostgreSQL

将备份中的 `postgres.dump` 复制到 `careercrew-postgres`，再执行：

```powershell
docker cp <backup>\postgres.dump careercrew-postgres:/tmp/careercrew-migration.dump
docker exec careercrew-postgres pg_restore `
  --clean --if-exists --no-owner --no-acl --exit-on-error `
  --username careercrew --dbname careercrew `
  /tmp/careercrew-migration.dump
```

## 4. 恢复 Qdrant

对 `manifest.json` 中的每个集合，将对应 `.snapshot` 复制到
`careercrew-qdrant:/qdrant/snapshots/`，然后调用 Qdrant 的
`PUT /collections/<collection>/snapshots/recover`，请求体为：

```json
{"location":"file:///qdrant/snapshots/<snapshot-file>"}
```

恢复完成后，用集合信息接口确认每个集合为 `green`，并抽查
`points_count`。本次实测还对所有 point 的 payload 和 vector 做排序后
SHA-256 对比，源端与目标端必须完全一致。

## 5. 恢复文件数据

把仓库现有的 `data/uploads`、`data/parsed` 复制到
`careercrew_app_uploads` 卷，并将文件属主设为容器用户 `1000:1000`。
复制后逐文件计算 SHA-256，文件数和哈希差异都必须为 0。

## 6. 启动并验收应用

```powershell
docker compose up -d --build app
docker compose ps
Invoke-RestMethod http://127.0.0.1:8000/readyz
```

至少通过以下验收：

- 三服务均为 `healthy`
- `/readyz` 返回 `postgres=ok`、`qdrant=ok`
- PostgreSQL 源库与目标库的公开表数量、逐表行数、字段 schema 指纹一致
- Alembic 版本与源库一致
- Qdrant 所有集合的点数和 point 内容哈希一致
- 上传/解析文件数量与 SHA-256 一致
- 浏览器打开登录页正常，静态资源与 API 入口无控制台错误

## 7. 删除旧数据

只有上面的验收全部通过后才执行：

```powershell
docker rm -f postgres qdrant
docker volume rm postgres-data qdrant_storage
```

删除前先用 `docker ps -a --filter volume=<name>` 确认对应卷没有被其他容器
引用。不要删除 `multimodal-rag-kb_pgdata` 等无关卷。

## 8. 岗位采集的前置条件（迁移后必读）

采集（Boss直聘 / 猎聘）依赖 CDP 接管一个**已登录的真实 Chrome**。后端搬进容器后，
有两处与迁移前不同：

1. **容器连不到宿主机的 `127.0.0.1:9222`**。因此 `config/settings.docker.yaml` 的
   `tools.search.boss_cdp_url` 指向 `http://host.docker.internal:9222`，compose 也补了
   `extra_hosts: host.docker.internal:host-gateway`（Linux/WSL 需要；Docker Desktop
   已内置该解析）。
2. **容器无法代你启动宿主机浏览器**（没有桌面、也没 PowerShell）。"一键启动"在容器
   部署下不再假装启动成功，而是返回一段本机设置指引。用户需要在本机做一次性设置：
   新建一个 Chrome 快捷方式，位置填：

   ```
   "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir=C:\ChromeDevData https://www.zhipin.com https://www.liepin.com
   ```

   用它打开 Chrome，在弹出的窗口里登录一次 Boss直聘 与 猎聘 即可。登录态保存在
   `C:\ChromeDevData`，之后启动免登录。`--user-data-dir` 不能省：Chrome 136 起会忽略
   默认数据目录上的远程调试端口。

设置完成后可以在应用里验收：职位匹配页的采集器卡片显示"已就绪"；收藏一个岗位时
后端会自动打开详情页抓取 JD 快照（此前这一直是空的）。若卡片显示不可用，卡片会给出
具体原因，不再静默消失。

### 为什么端口只绑定到 127.0.0.1

`docker-compose.yml` 把发布端口绑在 `127.0.0.1`，与 `settings.docker.yaml` 里
`tools.browser.local_guard: container` 是一体两面：前者保证公网不可达，后者让"来自本机"
的判定在容器视角下（客户端 IP 是网桥网关）依然成立。只放宽其一，都会把宿主机 Chrome
的控制权暴露给局域网。

### 为什么 `host.docker.internal` 能用（而 Chrome 明明只监听 127.0.0.1）

Windows 上 Chrome 的调试端口**只绑 `127.0.0.1`**，且 `--remote-debugging-address=0.0.0.0`
在 Windows 上不生效（实测 netstat 仍为 `127.0.0.1:9222`）。Docker Desktop 会把
`host.docker.internal` 的流量转发到宿主机回环，所以端口其实是通的——但直接连会失败，
因为 Chrome 的 DevTools HTTP 端点有 DNS-rebinding 防护：**Host 头不是 `localhost`
或 IP 字面量时一律返回 500**，而经 `host.docker.internal` 访问时 Host 头正是该域名。

因此 `careercrew_core/tools/browser/cdp.py` 的 `resolve_cdp_url()` 会在连接前把域名
解析成 IP（`http://host.docker.internal:9222` → `http://192.168.65.254:9222`）。
探测与接管两处都走这个函数，不要绕开它直接拼 URL。

排查这件事时先用 `curl -H 'Host: localhost' http://host.docker.internal:9222/json/version`
区分"网络不通"与"Host 头被拒"：返回 200 说明链路正常，只需走 IP 解析。
