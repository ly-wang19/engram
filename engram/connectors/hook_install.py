"""Install / remove the Claude Code `SessionEnd` hook (`engram-watch --install-hook`).

Mirrors watch_install.py — pure stdlib, every path injectable — but almost all of it is refusal logic
rather than editing logic, because the file it edits is not ours. `~/.claude/settings.json` carries the
owner's ten unrelated hook entries, a statusLine and a plugin list; an installer that "helpfully"
reformatted it, or that removed a neighbour's entry on uninstall, would be a far worse outcome than not
installing at all.

    engram-watch --install-hook --dry-run --settings ~/.claude/settings.json
    engram-watch --install-hook
    engram-watch --uninstall-hook

Three safeties, in the order they fire:

1. **Refuse rather than reformat.** The file must round-trip: `json.loads` re-serialised at the detected
   indent has to reproduce it. JSONC comments, tabs, a hand-sorted key order — any of them and the
   installer prints the entry to paste by hand and exits non-zero.
2. **Back up first**, to `settings.json.engram-bak-<ISO8601>` (deliberately not the plain `.bak` name
   already sitting next to it), then temp-file + `os.replace`, so a crash mid-write cannot truncate it.
3. **Identify our own leaf by the literal module name in its `command`.** Uninstall can then only ever
   take an entry we wrote; the group is dropped only if *we* emptied it, the `SessionEnd` key only if we
   emptied that, and every other key is copied through untouched.

The target (url + key file) is resolved from the *installed watcher job*, never from `~/.claude.json`:
the MCP `engram` server configured there points at a public demo with a shared key, and a hook that
inherited that target would ship the owner's transcripts to it. The resolved values are then written
literally into the command string, so which server a session goes to is auditable by reading
settings.json — no environment, no indirection.
"""
from __future__ import annotations

import copy
import difflib
import json
import os
import shutil
import time
from datetime import datetime
from typing import Optional

HOOK_MODULE = "engram.connectors.session_hook"
DEFAULT_SETTINGS = "~/.claude/settings.json"
DEFAULT_HOOK_LOG = "~/.engram/logs/hook.log"
DEFAULT_KEY_FILE = "~/.engram/watch.key"
DEFAULT_DEADLINE_S = 180

# No `matcher`: for SessionEnd the matcher is matched against `reason`
# (clear / resume / logout / prompt_input_exit / other / bypass_permissions_disabled). Every reason is a
# session worth ingesting, and a build that adds a seventh must not silently skip it.
SLOT = "SessionEnd"

# 5s of foreground budget. The worker is detached, so this only has to cover `$(cat)` plus process
# spawn — measured at well under 10ms for the sh front end. The owner's ten existing entries use 10.
TIMEOUT_S = 5


class RefuseError(Exception):
    """Something about the owner's file we will not paper over. The CLI prints it and exits non-zero."""


# --- the command that goes into settings.json ------------------------------------------------------

def _quotable(value: str, what: str) -> str:
    if "'" in value:
        raise RefuseError(f"{what} contains a single quote ({value!r}); the hook command quotes every "
                          "path with '...' and cannot escape one safely")
    return value


def render_command(python: str, url: str, key_file: str, log: str,
                   deadline: int = DEFAULT_DEADLINE_S) -> str:
    """The exact string written into `hooks.SessionEnd`.

    Shape copied from a third-party Claude Code hook already running on the machine this was built on:
    `[ -x ... ]` so a missing or moved interpreter degrades to a no-op rather than an
    error, and an else-branch that drains stdin to /dev/null so the CLI never sees a broken pipe.
    `$(cat)` reads the payload once in the foreground; `&` hands it to a worker that outlives the exiting
    CLI, because ingest + close is one LLM call of unbounded latency.
    """
    py = _quotable(python, "--python")
    url_ = _quotable(url, "--url")
    kf = _quotable(key_file, "--key-file")
    lg = _quotable(log, "--hook-log")
    # Only spelled out when it differs from the worker's own default, so the common case stays the
    # shortest auditable string a person can read in settings.json.
    extra = f" --deadline {int(deadline)}" if int(deadline) != DEFAULT_DEADLINE_S else ""
    return (
        f"if [ -x '{py}' ]; then p=$({{ command -p cat 2>/dev/null || cat; }}); "
        f"[ -n \"$p\" ] && {{ printf '%s' \"$p\" | nohup '{py}' -m {HOOK_MODULE} "
        f"--url '{url_}' --key-file '{kf}'{extra} >>'{lg}' 2>&1; }} & "
        f"else {{ command -p cat 2>/dev/null || cat; }} >/dev/null 2>&1 || :; fi; exit 0"
    )


def entry_for(command: str, timeout: int = TIMEOUT_S) -> dict:
    """One hook group, matching the shape of the owner's six existing no-matcher slots exactly."""
    return {"hooks": [{"type": "command", "command": command, "timeout": int(timeout)}]}


# --- reading the owner's file ----------------------------------------------------------------------

def dumps(data: dict, indent: int) -> str:
    return json.dumps(data, indent=indent, ensure_ascii=False) + "\n"


def _detect_indent(text: str) -> int:
    for line in text.splitlines():
        stripped = line.lstrip(" ")
        if stripped and stripped != line:
            return len(line) - len(stripped)
    return 2


def load_settings(path: str) -> tuple[dict, int, str]:
    """(data, indent, raw text). Raises RefuseError unless we could rewrite the file without changing
    anything we were not asked to change.

    The round-trip assertion is the whole point: it is what lets `--install-hook` promise that the only
    difference in the file afterwards is the entry it added. A file we cannot reproduce byte for byte is
    a file we decline to write.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            raw = fh.read()
    except FileNotFoundError:
        return {}, 2, ""
    except OSError as exc:
        raise RefuseError(f"cannot read {path}: {exc}")
    if not raw.strip():
        return {}, 2, raw
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise RefuseError(f"{path} is not plain JSON ({exc}) — it may carry // or /* comments, which "
                          "json cannot round-trip")
    if not isinstance(data, dict):
        raise RefuseError(f"{path} is not a JSON object")
    indent = _detect_indent(raw)
    if dumps(data, indent).rstrip("\n") != raw.rstrip("\n"):
        raise RefuseError(f"{path} does not round-trip through json (indent {indent}); rewriting it "
                          "would reformat lines this installer was not asked to touch")
    return data, indent, raw


# --- editing ---------------------------------------------------------------------------------------

def _is_ours(leaf) -> bool:
    return isinstance(leaf, dict) and HOOK_MODULE in str(leaf.get("command") or "")


def find_command(data: dict) -> Optional[str]:
    """The installed Engram command, or None. Used by `--status` and to make install idempotent."""
    slot = ((data.get("hooks") or {}).get(SLOT)) or []
    for group in slot if isinstance(slot, list) else []:
        leaves = group.get("hooks") if isinstance(group, dict) else None
        for leaf in leaves if isinstance(leaves, list) else []:
            if _is_ours(leaf):
                return str(leaf.get("command"))
    return None


def _strip(data: dict) -> int:
    """Remove Engram's leaf wherever it sits, and nothing else. Returns how many leaves went.

    Containers are pruned only when *we* emptied them, never merely because they are empty: a
    third-party group with an empty `hooks` list, or a pre-existing empty `SessionEnd`, is the owner's
    business and must survive an install/uninstall round trip byte for byte.
    """
    hooks = data.get("hooks")
    slot = hooks.get(SLOT) if isinstance(hooks, dict) else None
    if not isinstance(slot, list):
        return 0
    removed = 0
    kept: list = []
    for group in slot:
        leaves = group.get("hooks") if isinstance(group, dict) else None
        if not isinstance(leaves, list):
            kept.append(group)
            continue
        surviving = [leaf for leaf in leaves if not _is_ours(leaf)]
        dropped = len(leaves) - len(surviving)
        removed += dropped
        if not dropped:
            kept.append(group)
        elif surviving:
            kept.append({**group, "hooks": surviving})
    if not removed:
        return 0
    if kept:
        hooks[SLOT] = kept
    else:
        hooks.pop(SLOT, None)
    # A settings.json that had no `hooks` object before the install must look untouched after the
    # uninstall; the only file this loses an empty `"hooks": {}` from is one that already had one.
    if not hooks:
        data.pop("hooks", None)
    return removed


def install(data: dict, command: str, timeout: int = TIMEOUT_S) -> dict:
    """Add (or replace) our SessionEnd entry. Idempotent: re-running with a different --url replaces the
    old entry rather than leaving two hooks feeding two servers."""
    out = copy.deepcopy(data)
    _strip(out)
    hooks = out.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise RefuseError("settings.json `hooks` is not an object")
    slot = hooks.setdefault(SLOT, [])
    if not isinstance(slot, list):
        raise RefuseError(f"settings.json `hooks.{SLOT}` is not a list")
    slot.append(entry_for(command, timeout))
    return out


def uninstall(data: dict) -> tuple[dict, int]:
    out = copy.deepcopy(data)
    return out, _strip(out)


# --- writing ---------------------------------------------------------------------------------------

def diff(old: str, new: str, path: str) -> str:
    return "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                        fromfile=path, tofile=f"{path} (after)"))


def _real(path: str) -> str:
    """The file we must actually edit, with symlinks followed.

    `~/.claude/settings.json` is very often a symlink into a dotfiles repo. `os.replace(tmp, link)`
    would delete the link and drop a plain file in its place: the install would report success, the
    dotfiles copy would never see the entry, and the next `stow`/`git checkout` would silently take the
    hook away again. Resolving first means we edit the file the owner actually versions.
    """
    return os.path.realpath(path) if os.path.islink(path) else path


def backup(path: str, now: Optional[float] = None) -> Optional[str]:
    """Copy first, and to a name of our own: `.bak` and `.bak.<epoch>` already exist next to the real
    file, and clobbering someone else's backup while claiming to make one is the worst of both."""
    path = _real(path)
    if not os.path.exists(path):
        return None
    stamp = datetime.fromtimestamp(now if now is not None else time.time()).strftime("%Y-%m-%dT%H-%M-%S")
    dest = f"{path}.engram-bak-{stamp}"
    # Two installs inside the same second must not leave one backup: the first one is the pristine file,
    # which is exactly the copy someone reaching for a backup wants.
    suffix = 1
    while os.path.exists(dest):
        suffix += 1
        dest = f"{path}.engram-bak-{stamp}-{suffix}"
    shutil.copy2(path, dest)
    return dest


def write(path: str, text: str) -> None:
    """Atomic replace that keeps the file the owner had: same inode target, same permission bits.

    The owner's `~/.claude/settings.json` is 0600, and settings.json is a file that legitimately carries
    an `env` block with API tokens. A plain temp-file + `os.replace` writes the new file at the umask
    (0644 here), so installing a hook would quietly make the owner's config world-readable and leave it
    that way — a permission change nobody asked for and nobody would notice. The temp file is opened
    0600 too, so the content is never briefly readable by others even mid-write.
    """
    path = _real(path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    try:
        mode = os.stat(path).st_mode & 0o7777
    except OSError:
        mode = 0o600  # new file: private by default, matching what settings.json already is
    tmp = f"{path}.{os.getpid()}.engram-tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        # Never leave a half-written `.engram-tmp` sitting in ~/.claude for the owner to wonder about.
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# --- target resolution -----------------------------------------------------------------------------

def _from_argv(argv: list[str]) -> tuple[str, str]:
    url = key_file = ""
    for i, token in enumerate(argv):
        if token == "--url" and i + 1 < len(argv):
            url = argv[i + 1]
        elif token == "--key-file" and i + 1 < len(argv):
            key_file = argv[i + 1]
    return url, key_file


def _from_launchd(home: str, label: str) -> tuple[str, str]:
    import plistlib

    path = os.path.join(home, "Library", "LaunchAgents", f"{label}.plist")
    try:
        with open(path, "rb") as fh:
            doc = plistlib.load(fh)
    except (OSError, ValueError):
        return "", ""
    argv = doc.get("ProgramArguments")
    return _from_argv([str(a) for a in argv]) if isinstance(argv, list) else ("", "")


def _from_systemd(home: str) -> tuple[str, str]:
    import shlex

    path = os.path.join(home, ".config", "systemd", "user", "engram-watch.service")
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return "", ""
    for line in text.splitlines():
        if line.startswith("ExecStart="):
            try:
                return _from_argv(shlex.split(line.split("=", 1)[1]))
            except ValueError:
                return "", ""
    return "", ""


def resolve_target(url: str = "", key_file: str = "", *, home: Optional[str] = None,
                   label: str = "com.engram.watch", env: Optional[dict] = None) -> tuple[str, str, str]:
    """Where the hook feeds, in the one order that cannot pick the public demo.

    The installed watcher job is the authority, because it is the thing whose `seen` ledger and `target`
    hash the hook has to agree with — if they disagree, the hook refuses at runtime anyway.
    `~/.claude.json` is never consulted: its MCP `engram` server points at a shared public demo, and a
    hook that inherited that target would ship the owner's transcripts there.

    Returns (url, key_file, where those came from).
    """
    home = home or os.path.expanduser("~")
    env = os.environ if env is None else env
    if url and key_file:
        return url, key_file, "the flags you passed"

    job_url, job_key = _from_launchd(home, label)
    job_source = f"the installed launchd job {label}"
    if not (job_url or job_key):
        job_url, job_key = _from_systemd(home)
        job_source = "the installed systemd unit engram-watch.service"

    if url:
        resolved_url, where = url, "the flags you passed"
    elif job_url:
        resolved_url, where = job_url, job_source
    elif env.get("ENGRAM_URL") or env.get("ENGRAM_API_URL"):
        resolved_url, where = env.get("ENGRAM_URL") or env.get("ENGRAM_API_URL"), "$ENGRAM_URL"
    else:
        resolved_url, where = "", "nowhere — no watcher job is installed and $ENGRAM_URL is unset"
    resolved_key = key_file or job_key or os.path.join(home, ".engram", "watch.key")
    return resolved_url, resolved_key, where
