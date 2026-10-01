"""browser 路由：Chrome CDP 采集器状态检测与一键启动。

提供给前端求职页（MatcherPage）：
- GET /api/browser/cdp-status: 检测 9222 调试端口连通性及 Boss直聘 / 猎聘 打开状态
- POST /api/browser/launch-cdp: 本地一键唤起带 9222 端口的已登录 Chrome
- GET /api/browser/cdp-command: 获取手动执行的命令与批处理脚本路径
"""
from __future__ import annotations

import ipaddress
import json
import logging
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from careercrew_api.auth.dependencies import CurrentUser
from careercrew_core.state.settings import load_settings
from careercrew_core.tools.browser.cdp import resolve_cdp_url

logger = logging.getLogger(__name__)

router = APIRouter()

_DEFAULT_CDP_URL = "http://127.0.0.1:9222"
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_PS1_SCRIPT = _PROJECT_ROOT / "scripts" / "start_chrome_cdp.ps1"
_BAT_SCRIPT = _PROJECT_ROOT / "scripts" / "start_chrome_cdp.bat"

_GUARD_STRICT = "strict"
_GUARD_CONTAINER = "container"

_COLLECTOR_TABS = ("https://www.zhipin.com", "https://www.liepin.com")

# 采集专用 Chrome 的三平台启动方式：(平台标记, 可执行文件, 专用数据目录)
_COLLECTOR_VARIANTS = (
    ("windows", r'"C:\Program Files\Google\Chrome\Application\chrome.exe"', r"C:\ChromeDevData"),
    ("macos", '"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"', "~/ChromeDevData"),
    ("linux", "google-chrome", "~/.careercrew-chrome"),
)


def _backend_platform() -> str:
    """后端进程所在平台；仅作前端挑不到平台时的兜底，不代表用户所在平台。"""
    if os.name == "nt":
        return "windows"
    return "macos" if sys.platform == "darwin" else "linux"


def _local_guard_mode() -> str:
    """来源校验策略：strict（只认 loopback）| container（额外放行私有网段）。

    配置读取失败时回退 strict——安全默认不能因为配置问题而放松。
    """
    try:
        mode = getattr(load_settings().tools.browser, "local_guard", "")
    except Exception:
        return _GUARD_STRICT
    mode = (mode or "").strip().lower()
    return mode if mode in {_GUARD_STRICT, _GUARD_CONTAINER} else _GUARD_STRICT


def require_local_request(request: Request) -> None:
    """仅允许本机请求触发或读取宿主机 Chrome CDP 控制能力。

    strict：只认 loopback——后端与 Chrome 同机直跑。
    container：后端在容器内，请求经端口映射进来，容器看到的客户端是网桥网关
        （如 172.18.0.1）而非 loopback，需额外放行私有网段。此时"公网不可达"
        由 compose 把发布端口绑定到 127.0.0.1 保证，两者必须同时成立。
    """
    client = request.client
    try:
        address = ipaddress.ip_address(client.host) if client else None
    except ValueError:
        address = None

    if address is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="仅支持本机访问")

    # IPv4-mapped IPv6（::ffff:127.0.0.1 / ::ffff:172.18.0.1）按内层地址判定
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped

    allowed = address.is_loopback
    if not allowed and _local_guard_mode() == _GUARD_CONTAINER:
        allowed = address.is_private or address.is_link_local
    if not allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="仅支持本机访问")


def _get_cdp_base_url() -> str:
    """CDP 端点（探测与接管共用）。域名会被解析成 IP——理由见 resolve_cdp_url。"""
    try:
        cfg = load_settings().tools.search
        url = (getattr(cfg, "boss_cdp_url", "") or "").strip()
        if url:
            return resolve_cdp_url(url).rstrip("/")
    except Exception:
        pass
    return _DEFAULT_CDP_URL


def _check_cdp_alive(base_url: str, timeout: float = 1.2) -> tuple[bool, list[dict[str, Any]]]:
    """通过 HTTP 探测 CDP 端点及标签页列表。"""
    version_url = f"{base_url}/json/version"
    try:
        req = urllib.request.Request(version_url, headers={"User-Agent": "CareerCrew-Check"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return False, []
    except Exception:
        return False, []

    list_url = f"{base_url}/json/list"
    tabs: list[dict[str, Any]] = []
    try:
        req = urllib.request.Request(list_url, headers={"User-Agent": "CareerCrew-Check"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                raw = resp.read().decode("utf-8", errors="ignore")
                data = json.loads(raw)
                if isinstance(data, list):
                    tabs = data
    except Exception:
        pass

    return True, tabs


@router.get("/browser/cdp-status")
async def get_cdp_status(
    _current_user: CurrentUser,
    _local_request: None = Depends(require_local_request),
) -> dict[str, Any]:
    """检查本地 Chrome CDP 调试服务状态。"""
    base_url = _get_cdp_base_url()
    alive, tabs = _check_cdp_alive(base_url)

    boss_opened = False
    liepin_opened = False
    for tab in tabs:
        url = str(tab.get("url", "")).lower()
        if "zhipin.com" in url:
            boss_opened = True
        if "liepin.com" in url:
            liepin_opened = True

    return {
        "connected": alive,
        "cdp_url": base_url,
        "boss_opened": boss_opened,
        "liepin_opened": liepin_opened,
        "tab_count": len(tabs),
        "command": "powershell -ExecutionPolicy Bypass -File scripts/start_chrome_cdp.ps1",
        "bat_path": str(_BAT_SCRIPT.relative_to(_PROJECT_ROOT)) if _BAT_SCRIPT.exists() else "scripts/start_chrome_cdp.bat",
        "setup": _collector_setup(),
        "message": "Chrome 调试服务已连接" if alive else "Chrome 调试服务未启动",
    }


def _collector_setup() -> dict[str, Any]:
    """采集专用 Chrome 的一次性设置指引（不依赖仓库内脚本）。

    命令按宿主机平台给出多份、由前端挑用：后端可能跑在 Linux 容器里，而用户坐在
    Windows 上，用后端进程的 os.name 猜平台会把命令给错（实测踩到过）。

    Chrome 136 起会忽略默认数据目录上的远程调试端口，因此必须显式
    --user-data-dir；登录态持久化在该目录，用户只需登录一次。
    """
    commands = {
        platform: (
            f"{binary} --remote-debugging-port=9222 "
            f"--user-data-dir={user_data_dir} " + " ".join(_COLLECTOR_TABS)
        )
        for platform, binary, user_data_dir in _COLLECTOR_VARIANTS
    }
    return {
        "port": 9222,
        "commands": commands,
        "user_data_dirs": {p: d for p, _b, d in _COLLECTOR_VARIANTS},
        "default_platform": _backend_platform(),
        "steps": [
            "在本机新建一个 Chrome 快捷方式（Windows：桌面右键 → 新建 → 快捷方式），位置填下面的启动命令",
            "用这个快捷方式打开 Chrome，在弹出的窗口里登录 Boss直聘 与 猎聘（只需一次，登录态会保存在专用数据目录）",
            "让这个窗口保持开着，回到本应用即可自动采集岗位与 JD",
        ],
    }


@router.post("/browser/launch-cdp")
async def launch_cdp(
    _current_user: CurrentUser,
    _local_request: None = Depends(require_local_request),
) -> dict[str, Any]:
    """一键在宿主机启动带调试端口的 Chrome 浏览器。"""
    base_url = _get_cdp_base_url()
    alive, _ = _check_cdp_alive(base_url, timeout=0.8)
    if alive:
        return {
            "status": "already_running",
            "connected": True,
            "cdp_url": base_url,
            "message": "Chrome 调试服务已在运行中，无需重复启动。",
        }

    # 后端不在宿主机上（容器内 os.name == "posix"）：既没有桌面，也唤不起宿主机
    # 进程。不伪造"已启动"，改为给出用户在本机执行的一次性设置指引。
    if os.name != "nt":
        return {
            "status": "manual_required",
            "connected": False,
            "cdp_url": base_url,
            "message": "后端运行在容器中，无法代你在宿主机启动浏览器；请按引导在本机启动采集专用 Chrome。",
            "setup": _collector_setup(),
        }

    if not _PS1_SCRIPT.exists():
        return {
            "status": "error",
            "connected": False,
            "cdp_url": base_url,
            "message": f"启动脚本未找到：{_PS1_SCRIPT}",
            "setup": _collector_setup(),
        }

    try:
        # 直接使用 cmd.exe /c start 唤起独立的 PowerShell 窗口执行 start_chrome_cdp.ps1
        # 避免 cmd.exe 直接解析 .bat 文件时因 Windows 默认 ANSI/GBK 导致的编码错乱
        cmd_str = f'cmd.exe /c start "CareerCrew Chrome CDP" powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{_PS1_SCRIPT}"'
        logger.info("launching Chrome CDP via: %s", cmd_str)
        subprocess.Popen(cmd_str, shell=True)

        return {
            "status": "launched",
            "connected": False,
            "cdp_url": base_url,
            "message": "已调起 Chrome 启动脚本！请在弹出的 Chrome 窗口中分别登录 Boss直聘 与 猎聘。",
        }

    except Exception as e:
        logger.error("failed to launch Chrome CDP: %s", e, exc_info=True)
        return {
            "status": "error",
            "connected": False,
            "cdp_url": base_url,
            "message": f"启动 Chrome 失败：{e}。请手动运行 scripts/start_chrome_cdp.ps1",
        }


@router.get("/browser/cdp-command")
async def get_cdp_command(
    _current_user: CurrentUser,
    _local_request: None = Depends(require_local_request),
) -> dict[str, Any]:
    """返回本机手动启动 Chrome CDP 的命令、脚本路径与一次性设置指引。"""
    return {
        "command": "powershell -ExecutionPolicy Bypass -File scripts/start_chrome_cdp.ps1",
        "bat_path": str(_BAT_SCRIPT.relative_to(_PROJECT_ROOT)) if _BAT_SCRIPT.exists() else "scripts/start_chrome_cdp.bat",
        "setup": _collector_setup(),
    }
