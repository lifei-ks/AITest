#!/usr/bin/env python
"""自动推送：自适应代理探测 + 代理未启动时拉起代理软件，然后 push。

解决的具体问题：
  本机代理端口会变（7890 → 7897 → 其它），git 全局配置的固定端口一旦失效，
  每次 push 都要手工试端口；代理软件没启动时更是直接失败。

探测顺序（每个候选都做「端口监听 + 真实访问 GitHub」双重校验，排除 502 僵尸代理）：
  1. Windows 系统代理（注册表 ProxyEnable / ProxyServer，跟随代理软件设置自动变化）
  2. 环境变量 HTTPS_PROXY / HTTP_PROXY / ALL_PROXY
  3. 常见代理端口扫描
  都不可用且允许启动时 → 拉起 Clash Verge，轮询等待其监听。

用法:
    python scripts/git_push.py                  # 推到 origin 的当前分支
    python scripts/git_push.py origin main      # 指定远程与分支
    python scripts/git_push.py --no-launch      # 不自动启动代理软件
    python scripts/git_push.py --dry-run        # 只探测代理，不推送

退出码: 0 成功 / 1 失败（便于 CI 或脚本判断）
"""
from __future__ import annotations

import argparse
import glob
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

PROXY_PORTS = (
    7897, 7890, 7891, 7892, 7893, 7898,
    12450, 10809, 10808, 1080, 2080, 20171, 8889,
)

PROXY_EXES = (
    r"C:\Program Files\Clash Verge\clash-verge.exe",
    r"C:\Program Files (x86)\Clash Verge\clash-verge.exe",
    r"C:\Program Files\Clash for Windows\Clash for Windows.exe",
    r"C:\Program Files\v2rayN\v2rayN.exe",
)

PROXY_EXE_GLOBS = (
    r"C:\Users\*\AppData\Local\Programs\clash-verge\*.exe",
    r"C:\Users\*\AppData\Local\Programs\Clash Verge\*.exe",
    r"C:\Users\*\AppData\Local\Programs\v2rayN\*.exe",
    r"C:\Users\*\AppData\Local\Clash Verge\*.exe",
)

PROBE_TARGET = "github.com:443"
ENV_PROXY_KEYS = ("http_proxy", "https_proxy", "all_proxy")


def log(msg: str) -> None:
    print(msg, flush=True)


def _port_of(proxy: str) -> int | None:
    """从 http://host:port 提取端口；非 http 代理（socks 等）返回 None。"""
    p = proxy.strip().rstrip("/")
    if not p.lower().startswith("http://"):
        return None
    tail = p[len("http://"):]
    if ":" not in tail:
        return 80
    tail = tail.split("/")[0]
    try:
        return int(tail.rsplit(":", 1)[1])
    except (IndexError, ValueError):
        return None


def _port_open(port: int, timeout: float = 0.6) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        try:
            s.connect(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _proxy_works(proxy: str, timeout: float = 6.0) -> bool:
    """用 CONNECT 隧道探测 github.com:443，与 git 的真实握手方式一致。

    注意不能只发 GET 请求：实测某些代理对普通 HTTPS 请求放行（200），
    但 CONNECT 到 github.com:443 会返回 502，导致误判为可用。
    """
    port = _port_of(proxy)
    if port is None:
        return False
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            request = (
                f"CONNECT {PROBE_TARGET} HTTP/1.1\r\n"
                f"Host: {PROBE_TARGET}\r\n"
                "Proxy-Connection: keep-alive\r\n\r\n"
            )
            sock.sendall(request.encode("ascii"))
            head = sock.recv(256)
        first_line = head.split(b"\r\n", 1)[0]
        return b" 200" in first_line
    except (OSError, ValueError):
        return False


def _windows_system_proxies() -> list[str]:
    """读注册表里的系统代理。代理软件改端口时这里会同步变，所以最可靠。"""
    out: list[str] = []
    try:
        import winreg
    except ImportError:
        return out
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        ) as key:
            if winreg.QueryValueEx(key, "ProxyEnable")[0] != 1:
                return out
            server = str(winreg.QueryValueEx(key, "ProxyServer")[0])
    except OSError:
        return out
    # 形式可能是 "127.0.0.1:7897" 或 "http=127.0.0.1:1;https=127.0.0.1:7897"
    for part in server.split(";"):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            scheme, _, value = part.partition("=")
            if scheme.strip().lower() not in ("http", "https"):
                continue
            part = value.strip()
        if not part:
            continue
        out.append(part if part.lower().startswith("http") else f"http://{part}")
    return out


def _env_proxies() -> list[str]:
    out = []
    for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        value = os.environ.get(name)
        if value and value.lower().startswith(("http://", "https://")):
            out.append(value)
    return out


def _candidates() -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    # 固定端口优先：代理软件的混合端口通常就在这里，比环境变量/系统代理残留更可信
    pool = (
        [f"http://127.0.0.1:{p}" for p in PROXY_PORTS]
        + _windows_system_proxies()
        + _env_proxies()
    )
    for item in pool:
        key = item.lower().rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def find_proxy(verbose: bool = True) -> str | None:
    for proxy in _candidates():
        port = _port_of(proxy)
        if port is None:
            continue
        if not _port_open(port):
            continue
        if _proxy_works(proxy):
            if verbose:
                log(f"[proxy] 可用代理: {proxy}")
            return proxy
        if verbose:
            log(f"[proxy] 跳过 {proxy}（端口在听但无法建立到 GitHub 的隧道）")
    return None


def find_proxy_by_port(verbose: bool = False) -> str | None:
    """只扫常见端口，不读环境变量/系统代理残留。

    刚拉起代理软件时用它：此时环境变量里可能有别的（如沙箱或旧工具留下的）端口，
    会把探测带偏，导致明明新代理已就绪却用错端口。
    """
    for port in PROXY_PORTS:
        if not _port_open(port):
            continue
        proxy = f"http://127.0.0.1:{port}"
        if _proxy_works(proxy):
            if verbose:
                log(f"[proxy] 可用代理: {proxy}")
            return proxy
    return None


def find_proxy_exe() -> str | None:
    for exe in PROXY_EXES:
        if Path(exe).exists():
            return exe
    for pattern in PROXY_EXE_GLOBS:
        for hit in glob.glob(pattern):
            if "uninstall" not in hit.lower():
                return hit
    return None


def launch_proxy(exe: str, wait_seconds: int = 60) -> str | None:
    log(f"[proxy] 未发现可用代理，启动: {exe}")
    creationflags = 0
    if os.name == "nt":
        # DETACHED_PROCESS：让代理软件独立于当前终端存活
        creationflags = 0x00000008
    try:
        subprocess.Popen(
            [exe],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            close_fds=True,
            creationflags=creationflags,
        )
    except OSError as exc:
        log(f"[proxy] 启动失败: {exc}")
        return None
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        time.sleep(1.5)
        proxy = find_proxy_by_port(verbose=False)
        if proxy:
            log(f"[proxy] 代理已就绪: {proxy}")
            return proxy
    log(f"[proxy] 等待 {wait_seconds}s 仍无可用代理（可能需要在代理软件里选节点/开启混合端口）")
    return None


def current_branch() -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return (proc.stdout or "").strip() or "main"


def _git_env() -> dict[str, str]:
    """清掉环境变量代理，避免和 -c 配置打架；禁用交互式凭据提示，防止卡死。"""
    env = {k: v for k, v in os.environ.items() if k.lower() not in ENV_PROXY_KEYS}
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _run_git(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=_git_env(),
    )


def push(remote: str, branch: str, proxy: str | None) -> bool:
    # 空值 = 显式禁用代理，可覆盖全局配置里失效的固定端口
    args = ["-c", f"http.https://github.com.proxy={proxy or ''}", "push", remote, branch]
    label = proxy or "直连"
    log(f"[push] 使用 {label} 推送 {remote}/{branch} ...")
    proc = _run_git(args)
    ok = proc.returncode == 0
    output = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if ok:
        for line in output.splitlines():
            if line.strip():
                log(f"       {line.strip()}")
        log("[push] 成功")
        return True
    log(f"[push] 失败（退出码 {proc.returncode}）")
    for line in output.splitlines()[:12]:
        if line.strip():
            log(f"       {line.strip()}")
    lowered = output.lower()
    if "authentication failed" in lowered or "could not read username" in lowered or "403" in lowered:
        log("[hint] 这是凭据问题，不是网络问题：配置 PAT 或 SSH key 后重试")
    elif "non-fast-forward" in lowered or "fetch first" in lowered or "rejected" in lowered:
        log("[hint] 远程有新提交，先 git pull --rebase 再推送")
    elif "connection" in lowered or "timed out" in lowered or "reset" in lowered:
        log("[hint] 网络不通：确认代理软件已启动且节点可用")
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="自适应代理的 git push")
    parser.add_argument("remote", nargs="?", default="origin")
    parser.add_argument("branch", nargs="?", default=None)
    parser.add_argument("--no-launch", action="store_true", help="不自动启动代理软件")
    parser.add_argument("--dry-run", action="store_true", help="只探测代理，不推送")
    parser.add_argument("--wait", type=int, default=60, help="启动代理后等待秒数")
    parser.add_argument(
        "--fix-global", action="store_true",
        help="把 git 全局的 github 代理改成当前探测到的可用代理（让手动 git push 也能用）",
    )
    args = parser.parse_args()

    branch = args.branch or current_branch()
    log(f"[info] 目标 {args.remote}/{branch}")

    proxy = find_proxy()
    if proxy is None and not args.no_launch:
        exe = find_proxy_exe()
        if exe:
            proxy = launch_proxy(exe, wait_seconds=args.wait)
        else:
            log("[proxy] 未找到代理软件，跳过自动启动")

    if args.fix_global and proxy:
        subprocess.run(
            ["git", "config", "--global", "http.https://github.com.proxy", proxy],
            capture_output=True, text=True,
        )
        log(f"[config] git 全局 github 代理已更新为 {proxy}")

    if args.dry_run:
        log(f"[dry-run] 最终代理: {proxy or '（直连，无可用代理）'}")
        return 0

    # 代理偶发断流（节点切换 / 隧道瞬断）时重试，最多 3 轮；每轮重新探测避免死守失效端口
    for attempt in range(1, 4):
        if attempt > 1:
            log(f"[retry] 第 {attempt}/3 轮，重新探测代理 ...")
            time.sleep(2)
            proxy = find_proxy(verbose=False) or find_proxy_by_port(verbose=False)
        if proxy and push(args.remote, branch, proxy):
            return 0
        if not proxy:
            log("[push] 无可用代理，跳过本轮")

    log("[push] 代理方式均失败，尝试直连兜底 ...")
    if push(args.remote, branch, None):
        return 0
    log("[push] 全部失败。请确认代理软件已启动并选好节点，然后重试：python scripts/git_push.py")
    return 1


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    sys.exit(main())
