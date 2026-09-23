"""Start/supervise the isolated WhatsApp Monitor Node sidecar.

Used by the FastAPI reverse proxy so ``/whatsapp-monitor`` works without a
manual ``npm start``, both locally and inside Docker when the entrypoint did
not launch the sidecar (common on --no-build EC2 injects).
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

_lock = threading.Lock()
_proc: subprocess.Popen | None = None
_last_start_attempt = 0.0
_last_error: str | None = None

HOST = os.getenv("WHATSAPP_MONITOR_HOST", "127.0.0.1")
PORT = int(os.getenv("WHATSAPP_MONITOR_PORT", "3100"))


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
        here.parents[2] / "whatsapp_monitor",  # <repo>/whatsapp_monitor
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
    if explicit and Path(explicit).exists():
        return explicit
    which = shutil.which("node")
    if which:
        return which
    for candidate in (
        "/usr/local/bin/node",
        "/usr/bin/node",
        "/usr/local/bin/google-chrome",  # not node — skip
    ):
        p = Path(candidate)
        if p.name == "node" and p.is_file() and os.access(p, os.X_OK):
            return str(p)
    nvm_root = Path.home() / ".nvm" / "versions" / "node"
    if nvm_root.is_dir():
        for v in sorted(nvm_root.glob("*/bin/node"), reverse=True):
            if v.is_file() and os.access(v, os.X_OK):
                return str(v)
    return None


def _persist_root(module_root: Path) -> Path:
    explicit = os.getenv("WHATSAPP_PERSIST_ROOT", "").strip()
    if explicit:
        return Path(explicit)
    docker_persist = Path("/app/ai-job-agent/data/whatsapp_monitor")
    if docker_persist.parent.is_dir():
        return docker_persist
    return module_root


def status() -> dict:
    backend = find_backend_dir()
    node = find_node_bin()
    return {
        "upstream_up": _port_open(),
        "host": HOST,
        "port": PORT,
        "backend_dir": str(backend) if backend else None,
        "node_bin": node,
        "last_error": _last_error,
        "managed_pid": _proc.pid if _proc and _proc.poll() is None else None,
    }


def ensure_whatsapp_monitor_running(wait_seconds: float = 12.0) -> dict:
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
            node = find_node_bin()
            if backend is None:
                _last_error = "whatsapp_monitor backend not found on disk"
                return {**status(), "action": "missing_backend"}
            if node is None:
                _last_error = "node binary not found (install Node.js >= 18)"
                return {**status(), "action": "missing_node"}

            module_root = backend.parent
            persist = _persist_root(module_root)
            data_dir = Path(os.getenv("WHATSAPP_DATA_DIR", persist / "data"))
            session_dir = Path(os.getenv("WHATSAPP_SESSION_DIR", persist / "session"))
            log_dir = Path(os.getenv("WHATSAPP_LOG_DIR", persist / "logs"))
            for d in (data_dir, session_dir, log_dir):
                d.mkdir(parents=True, exist_ok=True)

            env = os.environ.copy()
            env["WHATSAPP_MONITOR_HOST"] = HOST
            env["WHATSAPP_MONITOR_PORT"] = str(PORT)
            env.setdefault("WHATSAPP_MONITOR_BASE_PATH", "/whatsapp-monitor")
            env["WHATSAPP_DATA_DIR"] = str(data_dir)
            env["WHATSAPP_SESSION_DIR"] = str(session_dir)
            env["WHATSAPP_LOG_DIR"] = str(log_dir)
            env.setdefault("PUPPETEER_SKIP_DOWNLOAD", "true")

            if not env.get("PUPPETEER_EXECUTABLE_PATH"):
                chrome_hint = Path("/app/whatsapp_monitor/.chrome_path")
                if chrome_hint.is_file():
                    env["PUPPETEER_EXECUTABLE_PATH"] = chrome_hint.read_text(encoding="utf-8").strip()
                else:
                    for candidate in (
                        "/usr/bin/chromium",
                        "/usr/bin/chromium-browser",
                        "/usr/bin/google-chrome",
                        "/usr/bin/google-chrome-stable",
                        "/usr/local/bin/google-chrome",
                    ):
                        if Path(candidate).is_file():
                            env["PUPPETEER_EXECUTABLE_PATH"] = candidate
                            break

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
                    f"[info] WhatsApp Monitor sidecar starting "
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
