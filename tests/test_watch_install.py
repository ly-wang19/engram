"""`engram-watch --install`: the scheduler that makes the memory feed itself.

Everything here runs against an injected `home` and a recording `run`, so no test loads a real launchd
or systemd job — the one thing this module must never do on a contributor's machine."""
from __future__ import annotations

import configparser
import os
import plistlib
import shutil
import subprocess

import pytest

from engram.connectors import watch_install as wi

PY = "/opt/python/bin/python3"
ARGV = ["--once", "--url", "http://127.0.0.1:8000", "--key-file", "/h/.engram/watch.key",
        "--state", "/h/.engram/watch_state.json", "--limit", "25"]


class Recorder:
    """Fake launchctl/systemctl: records calls, and models the one bit of launchd state that matters —
    `print` succeeds only between `bootstrap` and `bootout`."""

    def __init__(self, fail: tuple[str, ...] = ()):
        self.calls: list[list[str]] = []
        self.fail = fail
        self.loaded = False

    def __call__(self, cmd: list[str]) -> subprocess.CompletedProcess:
        self.calls.append(cmd)
        if cmd[0] == "plutil":  # lint for real when available; the plist must be a real plist
            return subprocess.run(cmd, capture_output=True, text=True)
        code = 1 if any(f in " ".join(cmd) for f in self.fail) else 0
        if cmd[:2] == ["launchctl", "bootstrap"] and not code:
            self.loaded = True
        elif cmd[:2] == ["launchctl", "bootout"]:
            self.loaded = False
        elif cmd[:2] == ["launchctl", "print"] and not self.loaded:
            code = 1
        return subprocess.CompletedProcess(cmd, code, "", "boom" if code else "")


def test_plist_round_trips_and_carries_no_secret():
    text = wi.render_launchd_plist("com.engram.watch", PY, ARGV, 1800, "/h/.engram/logs/watch.log")
    doc = plistlib.loads(text.encode("utf-8"))
    assert doc["Label"] == "com.engram.watch"
    assert doc["ProgramArguments"] == [PY, "-m", "engram.connectors.watch", *ARGV]
    assert doc["StartInterval"] == 1800 and doc["RunAtLoad"] is True
    assert doc["StandardOutPath"] == doc["StandardErrorPath"] == "/h/.engram/logs/watch.log"
    assert doc["ProcessType"] == "Background"
    # the key travels only through the 0600 key file: the plist is world-readable and shows in
    # `launchctl print`, so no EnvironmentVariables; no KeepAlive (a tick, not a service)
    for absent in ("EnvironmentVariables", "KeepAlive", "WorkingDirectory"):
        assert absent not in doc
    assert "secret" not in text and "--key " not in text


@pytest.mark.skipif(shutil.which("plutil") is None, reason="plutil is macOS-only")
def test_plist_passes_plutil_lint(tmp_path):
    p = tmp_path / "x.plist"
    p.write_text(wi.render_launchd_plist("com.engram.watch", PY, ARGV, 1800, "/h/log"))
    assert subprocess.run(["plutil", "-lint", str(p)], capture_output=True).returncode == 0


def test_install_launchd_writes_key_0600_and_loads_in_order(tmp_path):
    rec = Recorder()
    info = wi.install_launchd("com.engram.watch", PY, ARGV, 1800, home=str(tmp_path), run=rec,
                              key="secret-key")
    plist = tmp_path / "Library" / "LaunchAgents" / "com.engram.watch.plist"
    assert plist.exists() and info["plist"] == str(plist)
    assert plistlib.loads(plist.read_bytes())["Label"] == "com.engram.watch"
    key = tmp_path / ".engram" / "watch.key"
    assert key.read_text() == "secret-key\n"
    assert oct(key.stat().st_mode & 0o777) == "0o600"
    assert "secret-key" not in plist.read_text()
    launchctl = [c[1] for c in rec.calls if c[0] == "launchctl"]
    assert launchctl == ["bootout", "bootstrap", "print"], rec.calls
    assert rec.calls[-2] == ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist)]
    assert info["loaded"] is True and info["key_file"] == str(key)
    # preflight first (the scheduler's clean environment, not the caller's), then lint, then load
    assert rec.calls[0][:2] == ["env", "-i"] and rec.calls[0][-2:] == ["-c", wi.PREFLIGHT_CODE]
    assert "chdir('/')" in wi.PREFLIGHT_CODE  # cwd is on sys.path for `-c`; launchd starts from `/`
    if shutil.which("plutil"):
        assert rec.calls[1][:2] == ["plutil", "-lint"] and info["lint"] == "ok"

    # idempotent re-install: same files, the key kept (no --key this time), still bootout->bootstrap
    rec2 = Recorder()
    wi.install_launchd("com.engram.watch", PY, ARGV, 3600, home=str(tmp_path), run=rec2)
    assert key.read_text() == "secret-key\n"
    assert plistlib.loads(plist.read_bytes())["StartInterval"] == 3600
    assert [c[1] for c in rec2.calls if c[0] == "launchctl"] == ["bootout", "bootstrap", "print"]


def test_install_launchd_without_any_key_refuses(tmp_path):
    with pytest.raises(wi.InstallError):
        wi.install_launchd("com.engram.watch", PY, ARGV, 1800, home=str(tmp_path), run=Recorder())
    assert not (tmp_path / "Library" / "LaunchAgents" / "com.engram.watch.plist").exists()


def test_install_refuses_when_the_interpreter_cannot_import_the_watcher(tmp_path):
    """Seen on the first real install: plist loaded, `launchctl print` happy, every tick logging
    `No module named engram.connectors.watch`. The preflight must fail BEFORE anything is written."""
    rec = Recorder(fail=("import engram.connectors.watch",))  # matches PREFLIGHT_CODE
    with pytest.raises(wi.InstallError, match="pip install -e"):
        wi.install_launchd("com.engram.watch", PY, ARGV, 1800, home=str(tmp_path), run=rec, key="k")
    assert not (tmp_path / "Library").exists() and not (tmp_path / ".engram").exists()
    assert not any(c[0] == "launchctl" for c in rec.calls)
    with pytest.raises(wi.InstallError, match="pip install -e"):
        wi.install_systemd(PY, ARGV, 1800, home=str(tmp_path), run=rec, key="k")
    assert not (tmp_path / ".config").exists()


def test_install_launchd_bootstrap_failure_is_an_error(tmp_path):
    with pytest.raises(wi.InstallError):
        wi.install_launchd("com.engram.watch", PY, ARGV, 1800, home=str(tmp_path),
                           run=Recorder(fail=("bootstrap",)), key="k")


def test_uninstall_launchd_removes_plist_and_purge_removes_the_rest(tmp_path):
    rec = Recorder()
    wi.install_launchd("com.engram.watch", PY, ARGV, 1800, home=str(tmp_path), run=rec, key="k")
    p = wi.paths_for(str(tmp_path))
    for f in ("state", "lock", "log"):
        os.makedirs(os.path.dirname(p[f]), exist_ok=True)
        open(p[f], "w").close()

    out = wi.uninstall_launchd("com.engram.watch", home=str(tmp_path), run=rec)
    plist = tmp_path / "Library" / "LaunchAgents" / "com.engram.watch.plist"
    assert not plist.exists() and out["removed"] == [str(plist)]
    assert rec.calls[-2] == ["launchctl", "bootout", f"gui/{os.getuid()}/com.engram.watch"]
    assert rec.calls[-1] == ["launchctl", "print", f"gui/{os.getuid()}/com.engram.watch"]
    assert out["unloaded"] is True
    assert os.path.exists(p["key"]) and os.path.exists(p["state"])  # not purged

    out = wi.uninstall_launchd("com.engram.watch", purge=True, home=str(tmp_path), run=rec)
    # --install created the log directory; purge takes it back too, so ~/.engram is left as found.
    log_dir = os.path.dirname(p["log"])
    assert set(out["removed"]) == {p["key"], p["state"], p["lock"], p["log"], log_dir}
    for f in ("key", "state", "lock", "log"):
        assert not os.path.exists(p[f])
    assert not os.path.exists(log_dir)


def test_uninstall_launchd_waits_for_an_in_flight_tick(tmp_path, monkeypatch):
    """bootout returns before launchd has torn the running tick down; `launchctl print` still succeeds
    for a moment. RunAtLoad means that is the normal state right after --install."""
    class SlowTeardown(Recorder):
        def __init__(self, polls_until_gone: int):
            super().__init__()
            self.pending = polls_until_gone

        def __call__(self, cmd):
            if cmd[:2] == ["launchctl", "print"] and self.pending > 0:
                self.pending -= 1
                self.calls.append(cmd)
                return subprocess.CompletedProcess(cmd, 0, "", "")
            return super().__call__(cmd)

    naps: list[float] = []
    monkeypatch.setattr(wi, "_sleep", naps.append)
    rec = SlowTeardown(polls_until_gone=2)
    out = wi.uninstall_launchd("com.engram.watch", home=str(tmp_path), run=rec)
    prints = [c for c in rec.calls if c[:2] == ["launchctl", "print"]]
    assert out["unloaded"] is True and len(prints) == 3 and len(naps) == 2

    rec = SlowTeardown(polls_until_gone=10 ** 6)  # the tick outlives the wait
    out = wi.uninstall_launchd("com.engram.watch", home=str(tmp_path), run=rec, wait_s=0)
    assert out["unloaded"] is False


def test_systemd_units_parse_and_install_enables_timer(tmp_path):
    service, timer = wi.render_systemd_units(PY, ARGV, 1800, "/h/log")
    cp = configparser.ConfigParser()
    cp.read_string(service)
    assert cp["Service"]["Type"] == "oneshot"
    assert cp["Service"]["ExecStart"].startswith(f"{PY} -m engram.connectors.watch --once")
    cp = configparser.ConfigParser()
    cp.read_string(timer)
    assert cp["Timer"]["OnBootSec"] == "2min" and cp["Timer"]["OnUnitActiveSec"] == "1800s"
    assert cp["Timer"]["Persistent"] == "true"

    rec = Recorder()
    info = wi.install_systemd(PY, ARGV, 1800, home=str(tmp_path), run=rec, key="k")
    unit_dir = tmp_path / ".config" / "systemd" / "user"
    assert (unit_dir / "engram-watch.service").exists() and (unit_dir / "engram-watch.timer").exists()
    assert rec.calls[0][:2] == ["env", "-i"]  # preflight, same as launchd
    assert rec.calls[1:] == [["systemctl", "--user", "daemon-reload"],
                             ["systemctl", "--user", "enable", "--now", "engram-watch.timer"]]
    assert info["loaded"] is True

    rec = Recorder()
    wi.uninstall_systemd(purge=True, home=str(tmp_path), run=rec)
    assert not (unit_dir / "engram-watch.timer").exists()
    assert rec.calls[0] == ["systemctl", "--user", "disable", "--now", "engram-watch.timer"]
    assert not os.path.exists(wi.paths_for(str(tmp_path))["key"])


def test_cron_line():
    line = wi.render_cron_line(PY, ARGV, 1800, "/h/log")
    assert line.startswith("*/30 * * * * ") and line.endswith(">> /h/log 2>&1")
    assert f"{PY} -m engram.connectors.watch --once --url http://127.0.0.1:8000" in line
    assert wi.render_cron_line(PY, ARGV, 7200, "/h/log").startswith("0 */2 * * * ")


def test_cli_install_dry_run_prints_plist_and_writes_nothing(tmp_path, monkeypatch, capsys):
    from engram.connectors import watch

    orig = wi.paths_for
    monkeypatch.setattr(wi, "paths_for", lambda home=None: orig(str(tmp_path)))
    code = watch.main(["--install", "--scheduler", "launchd", "--label", "com.engram.watch.t",
                       "--interval", "1h", "--url", "http://127.0.0.1:9", "--key", "test", "--dry-run"])
    out = capsys.readouterr().out
    assert code == 0 and "[dry-run] preflight:" in out
    doc = plistlib.loads(out.split("\n[dry-run]")[0].encode("utf-8"))
    assert doc["Label"] == "com.engram.watch.t" and doc["StartInterval"] == 3600
    assert "--key-file" in doc["ProgramArguments"] and "test" not in doc["ProgramArguments"]
    assert not (tmp_path / ".engram").exists() and not (tmp_path / "Library").exists()


def test_cli_install_cron_writes_key_file_and_never_edits_crontab(tmp_path, monkeypatch, capsys):
    """cron has no loader to call, so the key file is the only thing --install writes for it; the
    printed line reads the key from that file, and it must exist with mode 0600 before the first tick."""
    from engram.connectors import watch

    orig = wi.paths_for
    monkeypatch.setattr(wi, "paths_for", lambda home=None: orig(str(tmp_path)))
    code = watch.main(["--install", "--scheduler", "cron", "--interval", "30m",
                       "--url", "http://127.0.0.1:9", "--key", "test"])
    out = capsys.readouterr().out
    assert code == 0 and "crontab -e" in out and "*/30 * * * * " in out
    key = tmp_path / ".engram" / "watch.key"
    assert key.read_text() == "test\n" and oct(key.stat().st_mode & 0o777) == "0o600"
    assert "--key-file" in out and "--key test" not in out  # the line carries the file, never the key
    # without a key, and no key file yet, the install refuses instead of printing a line that cannot run
    key.unlink()
    assert watch.main(["--install", "--scheduler", "cron", "--url", "http://127.0.0.1:9"]) == 1
