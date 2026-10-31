"""`python -m engram.embedded` as a host app runs it: a free local port announced only once listening, one
namespace behind a generated key, framing for the host's origin only, Chinese recall with the default
embedder, and an exit when the host process goes away."""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest

pytest.importorskip("uvicorn")
pytest.importorskip("fastapi")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _call(port: int, path: str, key: str | None = None, body: dict | None = None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                 data=json.dumps(body).encode() if body is not None else None,
                                 method="POST" if body is not None else "GET")
    req.add_header("Content-Type", "application/json")
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, {k.lower(): v for k, v in resp.headers.items()}, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as exc:
        return exc.code, {k.lower(): v for k, v in exc.headers.items()}, None


def test_embedded_server_lifecycle(tmp_path):
    parent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    announce, key_file = tmp_path / "run" / "engram.json", tmp_path / "engram.key"
    env = {k: v for k, v in os.environ.items() if not k.startswith("ENGRAM_")}
    env["ENGRAM_OPEN"] = "1"  # must be ignored: the embedded server never runs anonymous
    env["PYTHONPATH"] = ROOT
    proc = subprocess.Popen([sys.executable, "-m", "engram.embedded", "--data-dir", str(tmp_path / "data"),
                             "--key-file", str(key_file), "--announce", str(announce),
                             "--parent-pid", str(parent.pid), "--llm", ""],
                            cwd=str(tmp_path), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        deadline = time.time() + 60
        while not announce.exists():
            assert proc.poll() is None, proc.stdout.read().decode(errors="replace")
            assert time.time() < deadline, "no announce"
            time.sleep(0.1)
        port = json.loads(announce.read_text())["port"]
        key = key_file.read_text().strip()
        if os.name != "nt":
            assert stat.S_IMODE(key_file.stat().st_mode) == 0o600

        status, headers, _ = _call(port, "/health")
        assert status == 200
        assert headers.get("x-frame-options") == "SAMEORIGIN"
        assert "frame-ancestors 'self'" in headers.get("content-security-policy", "")
        assert _call(port, "/v1/stats")[0] == 401
        assert _call(port, "/v1/stats", key="me")[0] == 401  # the namespace name is not a key

        status, _, out = _call(port, "/v1/import", key, {"sessions": [
            {"session_id": "s1", "messages": [{"role": "user", "content": "熔断线：两小时无交付即停下汇报"}]},
            {"session_id": "s2", "messages": [{"role": "user", "content": "下周三和客户复盘合同条款"}]}]})
        assert status == 200 and out["episodes"] == 2
        status, _, out = _call(port, "/v1/recall", key, {"query": "合同", "answer": False})
        assert status == 200 and "合同条款" in out["context"]

        parent.kill()
        parent.wait()  # reaped, as the host's own parent would; a zombie still answers kill(pid, 0)
        proc.wait(timeout=15)  # gone with its host
    finally:
        parent.kill()
        if proc.poll() is None:
            proc.kill()
