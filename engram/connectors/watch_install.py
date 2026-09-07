"""Install / remove the scheduled job that keeps memory fed (`engram-watch --install`).

Pure stdlib, and every system call goes through an injectable `run` so the tests never touch a real
launchd or systemd. `home` is injectable for the same reason: the files this module owns are

    ~/Library/LaunchAgents/<label>.plist              (macOS, launchd)
    ~/.config/systemd/user/engram-watch.{service,timer}  (Linux, systemd --user)
    ~/.engram/watch.key                               (API key, mode 0600)
    ~/.engram/watch_state.json  ~/.engram/watch.lock  ~/.engram/logs/watch.log

Why a scheduler and not a daemon: a transcript is only worth reading once the session has gone quiet
(connectors/watch.py), so a periodic `--once` is the whole job. launchd's StartInterval + RunAtLoad and
a systemd timer with Persistent=true both re-run a tick that was missed while the laptop slept.
"""
from __future__ import annotations

import os
import plistlib
import shlex
import shutil
import subprocess
import time
from typing import Callable, Optional

MODULE = "engram.connectors.watch"
DEFAULT_LABEL = "com.engram.watch"
SYSTEMD_UNIT = "engram-watch"

Runner = Callable[[list[str]], subprocess.CompletedProcess]

_sleep = time.sleep  # injectable: the uninstall poll below must not slow the tests down
UNLOAD_WAIT_S = 10.0

# `python -c` puts the *current directory* on sys.path, so a preflight run from inside the repo passes
# even when the installed package lacks the module (that is exactly how the first real install got a
# green preflight and a job logging ModuleNotFoundError). Leave the repo before importing: launchd and
# systemd start the job from `/`.
def _preflight_code(module: str) -> str:
    return f"import os; os.chdir('/'); import {module}"


PREFLIGHT_CODE = _preflight_code(MODULE)


class InstallError(Exception):
    """A step the installer cannot recover from (bad plist, missing key). The CLI prints it and exits 1."""


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def paths_for(home: Optional[str] = None) -> dict[str, str]:
    """Every file the watcher + scheduler own, rooted at `home` (defaults to the real home)."""
    home = home or os.path.expanduser("~")
    engram = os.path.join(home, ".engram")
    return {
        "home": home,
        "key": os.path.join(engram, "watch.key"),
        "state": os.path.join(engram, "watch_state.json"),
        "lock": os.path.join(engram, "watch.lock"),
        "log": os.path.join(engram, "logs", "watch.log"),
        "launch_agents": os.path.join(home, "Library", "LaunchAgents"),
        "systemd_user": os.path.join(home, ".config", "systemd", "user"),
    }


def write_key_file(path: str, key: str) -> None:
    """The API key is the namespace this memory belongs to; it must not be readable by other users on
    the machine and must never appear in the plist (which is world-readable and shows up in `launchctl
    print`). 0600 at create time, re-asserted on re-install because O_CREAT does not chmod an existing file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(key.strip() + "\n")
    os.chmod(path, 0o600)


def preflight_import(python: str, *, module: str = MODULE, home: Optional[str] = None,
                     run: Runner = _run) -> Optional[str]:
    """Can `python` import `module` with the environment a scheduler (or a hook) gives it? Returns the
    error text, or None when it can.

    Found on the first real install: the plist loaded, `launchctl print` said running, and the tick
    logged `No module named engram.connectors.watch` — the shell had the repo on sys.path via cwd, the
    editable install pointed at an older checkout, and launchd runs from `/` with a clean environment.
    An install that succeeds while every tick fails is worse than a refusal, so this runs with the
    scheduler's environment (no PYTHONPATH, no venv activation, cwd `/`), not the caller's."""
    p = paths_for(home)
    res = run(["env", "-i", f"HOME={p['home']}", "PATH=/usr/bin:/bin:/usr/sbin:/sbin",
               python, "-c", _preflight_code(module)])
    if res.returncode == 0:
        return None
    detail = (res.stderr or res.stdout or "").strip().splitlines()
    return (f"{python} cannot import {module} in a clean environment "
            f"({detail[-1] if detail else 'no output'}). launchd/systemd run without your shell's "
            f"PYTHONPATH or cwd, so the package must be installed into that interpreter: "
            f"`{python} -m pip install -e <repo>` (or pass --python <interpreter that has it>)")


# --- renderers -------------------------------------------------------------------------------------

def render_launchd_plist(label: str, python: str, argv: list[str], interval_s: int, log_path: str) -> str:
    """No EnvironmentVariables (the key lives in a 0600 file, not in the plist), no KeepAlive (this is
    a tick, not a service), no WorkingDirectory (the module is imported by absolute interpreter)."""
    doc = {
        "Label": label,
        "ProgramArguments": [python, "-m", MODULE, *argv],
        "StartInterval": int(interval_s),
        "RunAtLoad": True,
        "StandardOutPath": log_path,
        "StandardErrorPath": log_path,
        "ProcessType": "Background",
    }
    return plistlib.dumps(doc, sort_keys=False).decode("utf-8")


def render_systemd_units(python: str, argv: list[str], interval_s: int, log_path: str) -> tuple[str, str]:
    cmd = " ".join(shlex.quote(a) for a in [python, "-m", MODULE, *argv])
    service = (
        "[Unit]\n"
        "Description=Engram: feed memory from local agent sessions\n"
        "\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"ExecStart={cmd}\n"
        f"StandardOutput=append:{log_path}\n"
        f"StandardError=append:{log_path}\n"
    )
    timer = (
        "[Unit]\n"
        "Description=Engram watch tick\n"
        "\n"
        "[Timer]\n"
        "OnBootSec=2min\n"
        f"OnUnitActiveSec={int(interval_s)}s\n"
        "Persistent=true\n"
        "\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )
    return service, timer


def render_cron_line(python: str, argv: list[str], interval_s: int, log_path: str) -> str:
    """cron has minute granularity; intervals under an hour become `*/N`, larger ones hourly steps."""
    minutes = max(1, int(interval_s) // 60)
    if minutes < 60:
        spec = f"*/{minutes} * * * *"
    else:
        hours = max(1, minutes // 60)
        spec = f"0 */{hours} * * *" if hours < 24 else "0 3 * * *"
    cmd = " ".join(shlex.quote(a) for a in [python, "-m", MODULE, *argv])
    return f"{spec} {cmd} >> {shlex.quote(log_path)} 2>&1"


# --- launchd ---------------------------------------------------------------------------------------

def _domain() -> str:
    return f"gui/{os.getuid()}"


def install_launchd(label: str, python: str, argv: list[str], interval_s: int, *,
                    home: Optional[str] = None, run: Runner = _run, log_path: Optional[str] = None,
                    key: Optional[str] = None, key_path: Optional[str] = None) -> dict:
    """Write the key file (if a key was given), render + lint + write the plist, (re)load it.

    Idempotent: `bootout` first (ignored when nothing is loaded), then `bootstrap`, so re-running with
    a new interval or interpreter replaces the job instead of erroring on "already loaded"."""
    p = paths_for(home)
    key_path = key_path or p["key"]
    log_path = log_path or p["log"]
    problem = preflight_import(python, home=home, run=run)
    if problem:
        raise InstallError(problem)
    if key:
        write_key_file(key_path, key)
    elif not os.path.exists(key_path):
        raise InstallError(f"no API key: pass --key once (stored at {key_path}, mode 0600)")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    os.makedirs(p["launch_agents"], exist_ok=True)

    plist_path = os.path.join(p["launch_agents"], f"{label}.plist")
    text = render_launchd_plist(label, python, argv, interval_s, log_path)
    with open(plist_path, "w", encoding="utf-8") as fh:
        fh.write(text)
    lint = "skipped (plutil not found)"
    if shutil.which("plutil"):
        res = run(["plutil", "-lint", plist_path])
        if res.returncode != 0:
            os.remove(plist_path)
            raise InstallError(f"plutil -lint rejected the plist: {(res.stderr or res.stdout).strip()}")
        lint = "ok"

    run(["launchctl", "bootout", f"{_domain()}/{label}"])  # not loaded yet is the normal first case
    boot = run(["launchctl", "bootstrap", _domain(), plist_path])
    if boot.returncode != 0:
        raise InstallError(f"launchctl bootstrap failed: {(boot.stderr or boot.stdout).strip()}")
    shown = run(["launchctl", "print", f"{_domain()}/{label}"])
    return {
        "scheduler": "launchd", "label": label, "plist": plist_path, "python": python,
        "interval_s": int(interval_s), "log": log_path, "key_file": key_path, "lint": lint,
        "loaded": shown.returncode == 0,
    }


def uninstall_launchd(label: str, purge: bool = False, *, home: Optional[str] = None,
                      run: Runner = _run, wait_s: float = UNLOAD_WAIT_S) -> dict:
    """bootout, wait until launchd has actually let go of the job, then delete the files.

    launchd tears a running service down asynchronously: `bootout` returns while the in-flight tick is
    still being signalled, and `launchctl print` keeps succeeding for a moment. RunAtLoad means a tick
    is *always* in flight right after `--install`, so the first uninstall on this machine reported
    success while `launchctl list` still showed the job's PID. Poll (bounded) so that "uninstalled"
    means gone; `unloaded` is False when the job outlived the wait."""
    p = paths_for(home)
    plist_path = os.path.join(p["launch_agents"], f"{label}.plist")
    run(["launchctl", "bootout", f"{_domain()}/{label}"])
    unloaded = _wait_unloaded(label, run, wait_s)
    removed = _remove(plist_path)
    if purge:
        removed += _purge(p)
    return {"scheduler": "launchd", "label": label, "removed": removed, "unloaded": unloaded}


def _wait_unloaded(label: str, run: Runner, wait_s: float) -> bool:
    deadline = time.monotonic() + wait_s
    while True:
        if run(["launchctl", "print", f"{_domain()}/{label}"]).returncode != 0:
            return True
        if time.monotonic() >= deadline:
            return False
        _sleep(0.25)


# --- systemd ---------------------------------------------------------------------------------------

def install_systemd(python: str, argv: list[str], interval_s: int, *,
                    home: Optional[str] = None, run: Runner = _run, log_path: Optional[str] = None,
                    key: Optional[str] = None, key_path: Optional[str] = None) -> dict:
    p = paths_for(home)
    key_path = key_path or p["key"]
    log_path = log_path or p["log"]
    problem = preflight_import(python, home=home, run=run)
    if problem:
        raise InstallError(problem)
    if key:
        write_key_file(key_path, key)
    elif not os.path.exists(key_path):
        raise InstallError(f"no API key: pass --key once (stored at {key_path}, mode 0600)")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    os.makedirs(p["systemd_user"], exist_ok=True)
    service, timer = render_systemd_units(python, argv, interval_s, log_path)
    service_path = os.path.join(p["systemd_user"], f"{SYSTEMD_UNIT}.service")
    timer_path = os.path.join(p["systemd_user"], f"{SYSTEMD_UNIT}.timer")
    with open(service_path, "w", encoding="utf-8") as fh:
        fh.write(service)
    with open(timer_path, "w", encoding="utf-8") as fh:
        fh.write(timer)
    run(["systemctl", "--user", "daemon-reload"])
    res = run(["systemctl", "--user", "enable", "--now", f"{SYSTEMD_UNIT}.timer"])
    if res.returncode != 0:
        raise InstallError(f"systemctl enable failed: {(res.stderr or res.stdout).strip()}")
    return {
        "scheduler": "systemd", "label": SYSTEMD_UNIT, "service": service_path, "timer": timer_path,
        "python": python, "interval_s": int(interval_s), "log": log_path, "key_file": key_path,
        "loaded": True,
    }


def uninstall_systemd(purge: bool = False, *, home: Optional[str] = None, run: Runner = _run) -> dict:
    p = paths_for(home)
    run(["systemctl", "--user", "disable", "--now", f"{SYSTEMD_UNIT}.timer"])
    removed = _remove(os.path.join(p["systemd_user"], f"{SYSTEMD_UNIT}.timer"))
    removed += _remove(os.path.join(p["systemd_user"], f"{SYSTEMD_UNIT}.service"))
    run(["systemctl", "--user", "daemon-reload"])
    if purge:
        removed += _purge(p)
    return {"scheduler": "systemd", "label": SYSTEMD_UNIT, "removed": removed}


# --- shared ----------------------------------------------------------------------------------------

def _remove(path: str) -> list[str]:
    try:
        os.remove(path)
        return [path]
    except FileNotFoundError:
        return []


def _purge(p: dict[str, str]) -> list[str]:
    removed: list[str] = []
    for key in ("key", "state", "lock", "log"):
        removed += _remove(p[key])
    # --install created the log directory; leave the owner's ~/.engram exactly as we found it.
    log_dir = os.path.dirname(p["log"])
    try:
        if log_dir and not os.listdir(log_dir):
            os.rmdir(log_dir)
            removed.append(log_dir)
    except OSError:
        pass
    return removed
