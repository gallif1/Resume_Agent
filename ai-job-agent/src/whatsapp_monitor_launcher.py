"""Start/supervise the WhatsApp Monitor Node sidecar on the cloud host.

The browser talks to Resume Agent at ``/whatsapp-monitor``; FastAPI reverse-
proxies to this localhost Node process (whatsapp-web.js + Chromium).
"""

from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess
import tarfile
import threading
import time
import urllib.request
from pathlib import Path

_lock = threading.Lock()
_proc: subprocess.Popen | None = None
_last_start_attempt = 0.0
_last_error: str | None = None

HOST = os.getenv("WHATSAPP_MONITOR_HOST", "127.0.0.1")
PORT = int(os.getenv("WHATSAPP_MONITOR_PORT", "3100"))
NODE_VERSION = os.getenv("WHATSAPP_NODE_VERSION", "v22.14.0")


def _port_open(host: str = HOST, port: int = PORT, timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _candidate_roots() -> list[Path]:
    here = Path(__file__).resolve()
    return [
        Path("/app/whatsapp_monitor"),
        here.parents[2] / "whatsapp_monitor",
        Path(os.getenv("WHATSAPP_MONITOR_ROOT", "")),
    ]


def find_backend_dir() -> Path | None:
    for root in _candidate_roots():
        if not root or str(root) in (".", ""):
            continue
        index = root / "backend" / "src" / "index.js"
        if index.is_file():
            return root / "backend"
    return None


def find_node_bin() -> str | None:
    explicit = os.getenv("WHATSAPP_NODE_BIN", "").strip()
    if explicit and Path(explicit).is_file() and os.access(explicit, os.X_OK):
        return explicit
    which = shutil.which("node")
    if which:
        return which
    for candidate in (
        "/usr/local/bin/node",
        "/usr/bin/node",
        "/app/whatsapp_monitor/.node/bin/node",
    ):
        p = Path(candidate)
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)
    # Bundled download location next to module
    backend = find_backend_dir()
    if backend is not None:
        bundled = backend.parent / ".node" / "bin" / "node"
        if bundled.is_file() and os.access(bundled, os.X_OK):
            return str(bundled)
    nvm_root = Path.home() / ".nvm" / "versions" / "node"
    if nvm_root.is_dir():
        for v in sorted(nvm_root.glob("*/bin/node"), reverse=True):
            if v.is_file() and os.access(v, os.X_OK):
                return str(v)
    return None


def _node_arch() -> str | None:
    machine = platform.machine().lower()
    if machine in {"x86_64", "amd64"}:
        return "x64"
    if machine in {"aarch64", "arm64"}:
        return "arm64"
    return None


def ensure_node_installed() -> str | None:
    """Return path to node, downloading an official binary tarball if needed."""
    existing = find_node_bin()
    if existing:
        return existing

    backend = find_backend_dir()
    if backend is None:
        return None

    arch = _node_arch()
    if arch is None:
        global _last_error
        _last_error = f"unsupported CPU arch for Node download: {platform.machine()}"
        return None

    install_root = backend.parent / ".node"
    node_bin = install_root / "bin" / "node"
    if node_bin.is_file() and os.access(node_bin, os.X_OK):
        return str(node_bin)

    url = f"https://nodejs.org/dist/{NODE_VERSION}/node-{NODE_VERSION}-linux-{arch}.tar.gz"
    tmp = backend.parent / f".node-{NODE_VERSION}-{arch}.tar.gz"
    print(f"[info] Downloading Node.js {NODE_VERSION} for WhatsApp Monitor…", flush=True)
    try:
        urllib.request.urlretrieve(url, tmp)  # noqa: S310 — official nodejs.org
        install_root.mkdir(parents=True, exist_ok=True)
        with tarfile.open(tmp, "r:gz") as tf:
            # Extract into a staging dir then move bin/lib
            staging = backend.parent / f".node-staging-{NODE_VERSION}"
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            staging.mkdir(parents=True, exist_ok=True)
            tf.extractall(staging)
            extracted = next(staging.iterdir())
            # Move contents to install_root
            if install_root.exists():
                shutil.rmtree(install_root, ignore_errors=True)
            extracted.rename(install_root)
            shutil.rmtree(staging, ignore_errors=True)
        tmp.unlink(missing_ok=True)
    except Exception as exc:  # noqa: BLE001
        _last_error = f"node download failed: {type(exc).__name__}: {exc}"
        print(f"[warn] {_last_error}", flush=True)
        return None

    if node_bin.is_file():
        node_bin.chmod(0o755)
        npm = install_root / "bin" / "npm"
        if npm.is_file():
            npm.chmod(0o755)
        print(f"[info] Node installed at {node_bin}", flush=True)
        return str(node_bin)
    _last_error = "node download finished but binary missing"
    return None


def ensure_npm_deps(backend: Path, node: str) -> bool:
    """Install backend npm deps if node_modules is missing."""
    modules = backend / "node_modules" / "whatsapp-web.js"
    if modules.is_dir():
        return True
    npm = str(Path(node).parent / "npm")
    if not Path(npm).is_file():
        npm = shutil.which("npm") or "npm"
    env = os.environ.copy()
    env["PUPPETEER_SKIP_DOWNLOAD"] = "false"
    print(f"[info] Installing WhatsApp Monitor npm deps in {backend}…", flush=True)
    try:
        cmd = [npm, "ci", "--omit=dev"] if (backend / "package-lock.json").is_file() else [npm, "install", "--omit=dev"]
        subprocess.run(  # noqa: S603
            cmd,
            cwd=str(backend),
            env=env,
            check=True,
            timeout=300,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
        )
        return modules.is_dir()
    except Exception as exc:  # noqa: BLE001
        global _last_error
        _last_error = f"npm install failed: {type(exc).__name__}: {exc}"
        print(f"[warn] {_last_error}", flush=True)
        return False


def _persist_root(module_root: Path) -> Path:
    explicit = os.getenv("WHATSAPP_PERSIST_ROOT", "").strip()
    if explicit:
        return Path(explicit)
    docker_persist = Path("/app/ai-job-agent/data/whatsapp_monitor")
    if docker_persist.parent.is_dir():
        return docker_persist
    return module_root


def _resolve_chrome() -> str | None:
    chrome_hint = Path("/app/whatsapp_monitor/.chrome_path")
    if chrome_hint.is_file():
        path = chrome_hint.read_text(encoding="utf-8").strip()
        if path and Path(path).is_file():
            return path
    for candidate in (
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/local/bin/google-chrome",
    ):
        if Path(candidate).is_file():
            return candidate
    # Playwright browsers path (full chrome or headless shell)
    ms = Path("/ms-playwright")
    if ms.is_dir():
        matches = sorted(ms.glob("chromium-*/chrome-linux*/chrome"))
        if matches:
            return str(matches[-1])
        shells = sorted(ms.glob("chromium_headless_shell-*/chrome-linux*/chrome-headless-shell"))
        if not shells:
            shells = sorted(ms.glob("**/chrome-headless-shell"))
        if shells:
            return str(shells[-1])
    return None


def status() -> dict:
    backend = find_backend_dir()
    node = find_node_bin()
    return {
        "mode": "cloud_server",
        "upstream_up": _port_open(),
        "host": HOST,
        "port": PORT,
        "backend_dir": str(backend) if backend else None,
        "node_bin": node,
        "last_error": _last_error,
        "managed_pid": _proc.pid if _proc and _proc.poll() is None else None,
    }


def ensure_whatsapp_monitor_running(wait_seconds: float = 20.0) -> dict:
    """Start the Node sidecar if it is not already listening. Thread-safe."""
    global _proc, _last_start_attempt, _last_error

    if _port_open():
        return {**status(), "action": "already_running"}

    with _lock:
        if _port_open():
            return {**status(), "action": "already_running"}

        now = time.time()
        recent_start = now - _last_start_attempt < 8 and _proc is not None and _proc.poll() is None

        if not recent_start:
            backend = find_backend_dir()
            if backend is None:
                _last_error = "whatsapp_monitor backend not found on disk"
                return {**status(), "action": "missing_backend"}

            node = ensure_node_installed()
            if node is None:
                _last_error = _last_error or "node binary not found (install Node.js >= 18)"
                return {**status(), "action": "missing_node"}

            if not ensure_npm_deps(backend, node):
                return {**status(), "action": "npm_install_failed"}

            module_root = backend.parent
            persist = _persist_root(module_root)
            data_dir = Path(os.getenv("WHATSAPP_DATA_DIR", persist / "data"))
            session_dir = Path(os.getenv("WHATSAPP_SESSION_DIR", persist / "session"))
            log_dir = Path(os.getenv("WHATSAPP_LOG_DIR", persist / "logs"))
            for d in (data_dir, session_dir, log_dir):
                d.mkdir(parents=True, exist_ok=True)

            env = os.environ.copy()
            # Prefer bundled node/npm on PATH for child tools.
            node_dir = str(Path(node).parent)
            env["PATH"] = f"{node_dir}:{env.get('PATH', '')}"
            env["WHATSAPP_MONITOR_HOST"] = HOST
            env["WHATSAPP_MONITOR_PORT"] = str(PORT)
            env.setdefault("WHATSAPP_MONITOR_BASE_PATH", "/whatsapp-monitor")
            env["WHATSAPP_DATA_DIR"] = str(data_dir)
            env["WHATSAPP_SESSION_DIR"] = str(session_dir)
            env["WHATSAPP_LOG_DIR"] = str(log_dir)
            env.setdefault("PUPPETEER_SKIP_DOWNLOAD", "true")
            chrome = _resolve_chrome()
            if chrome:
                env["PUPPETEER_EXECUTABLE_PATH"] = chrome

            if _proc is not None and _proc.poll() is None:
                try:
                    _proc.terminate()
                except Exception:  # noqa: BLE001
                    pass

            stdout_log = log_dir / "monitor.stdout.log"
            _last_start_attempt = now
            try:
                log_f = open(stdout_log, "a", encoding="utf-8")  # noqa: SIM115
                _proc = subprocess.Popen(  # noqa: S603
                    [node, "src/index.js"],
                    cwd=str(backend),
                    env=env,
                    stdout=log_f,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                _last_error = None
                print(
                    f"[info] WhatsApp Monitor cloud sidecar starting "
                    f"(pid={_proc.pid}, cwd={backend}, port={PORT})",
                    flush=True,
                )
            except Exception as exc:  # noqa: BLE001
                _last_error = f"{type(exc).__name__}: {exc}"
                print(f"[warn] WhatsApp Monitor start failed: {_last_error}", flush=True)
                return {**status(), "action": "start_failed"}

        deadline = time.time() + max(wait_seconds, 1.0)
        while time.time() < deadline:
            if _port_open():
                return {**status(), "action": "started"}
            if _proc is not None and _proc.poll() is not None:
                _last_error = f"sidecar exited early with code {_proc.returncode}"
                return {**status(), "action": "exited_early"}
            time.sleep(0.25)

        _last_error = _last_error or "sidecar started but port not open yet"
        return {**status(), "action": "starting"}
