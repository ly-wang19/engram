"""Run Engram inside a host app: `python -m engram.embedded --data-dir D --key-file K --announce F`.

The host (如我) owns the lifecycle. It starts this process, reads the port from the announce file, forwards
`/engram/*` here with the prefix stripped and the key attached, and stops it with its own process tree.
This entry point turns off what makes a standalone server convenient but an embedded one surprising:

- it binds 127.0.0.1 on a free port and writes `{"port", "pid", "version"}` to `--announce` only once it
  is listening, so the host never has to guess or show a port;
- one namespace (`me`) behind one random key, kept in `--key-file` (0600) and never printed — no
  anonymous mode, whatever the environment says;
- no `.env` from the working directory; framing allowed for the host's origin only;
- the model is Codex run by the host (`--llm codex`, the runner named by ENGRAM_CODEX_RUNNER), its daily
  counter kept in the data dir; the watch waits six hours of quiet before distilling a session, and an
  agent session keeps a 24k-character head+tail window in memory;
- with `--parent-pid`, it exits when the host does, even if the host was killed without cleaning up.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import socket
import sys
import threading
import time
from typing import Optional

QUIET_SECONDS = 6 * 3600
# What the summary and conclusions passes read of a session anyway; the full transcript stays in Codex's
# own session file. Keeps a year of daily sessions inside a desktop app's memory budget.
AGENT_SESSION_MAX_CHARS = 24000


def ensure_key(path: str) -> str:
    """The namespace key: read it, or create a random one readable only by this user."""
    try:
        with open(path, encoding="utf-8") as fh:
            key = fh.read().strip()
        if key:
            return key
    except FileNotFoundError:
        pass
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    key = secrets.token_urlsafe(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(key + "\n")
    return key


def _alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not handle:
            return False
        try:
            return kernel32.WaitForSingleObject(handle, 0) == 0x102  # WAIT_TIMEOUT: still running
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _announce_when_ready(server, sock: socket.socket, path: str, timeout: float = 120.0) -> None:
    from . import __version__

    deadline = time.time() + timeout
    while not server.started:
        if server.should_exit or time.time() > deadline:
            return
        time.sleep(0.05)
    payload = {"port": sock.getsockname()[1], "pid": os.getpid(), "version": __version__}
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    os.replace(tmp, path)


def _exit_with_parent(server, parent_pid: int, interval: float = 2.0) -> None:
    while not server.should_exit:
        if not _alive(parent_pid):
            server.should_exit = True
            return
        time.sleep(interval)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m engram.embedded", description=__doc__.split("\n")[0])
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--key-file", required=True, help="the namespace key; created (0600) if missing")
    ap.add_argument("--announce", required=True, help="JSON {port, pid, version}, written once listening")
    ap.add_argument("--namespace", default="me")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--parent-pid", type=int, default=0)
    ap.add_argument("--ui-dist", default="", help="the console build to serve at /ui (an embedded build)")
    ap.add_argument("--llm", default="codex", help='"codex" (default), any ENGRAM_LLM name, or "" for none')
    args = ap.parse_args(argv)

    data_dir = os.path.abspath(args.data_dir)
    key = ensure_key(args.key_file)
    os.environ.pop("ENGRAM_OPEN", None)
    os.environ.update({
        "ENGRAM_DATA_DIR": data_dir,
        "ENGRAM_API_KEYS": f"{args.namespace}:{key}",
        "ENGRAM_NO_DOTENV": "1",
        "ENGRAM_EMBEDDED": "1",
        "ENGRAM_LLM": args.llm,
    })
    os.environ.setdefault("ENGRAM_EMBEDDER", "hashing")
    os.environ.setdefault("ENGRAM_CODEX_STATE", os.path.join(data_dir, ".codex-usage.json"))
    os.environ.setdefault("ENGRAM_WATCH_QUIET_SECONDS", str(QUIET_SECONDS))
    os.environ.setdefault("ENGRAM_AGENT_SESSION_MAX_CHARS", str(AGENT_SESSION_MAX_CHARS))
    if args.ui_dist:
        os.environ["ENGRAM_UI_DIST"] = os.path.abspath(args.ui_dist)

    import uvicorn

    from .server.app import app

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind((args.host, 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", access_log=False))
    threading.Thread(target=_announce_when_ready, args=(server, sock, args.announce), daemon=True).start()
    if args.parent_pid:
        threading.Thread(target=_exit_with_parent, args=(server, args.parent_pid), daemon=True).start()
    server.run(sockets=[sock])
    return 0


if __name__ == "__main__":
    sys.exit(main())
