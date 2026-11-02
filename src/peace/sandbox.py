"""The Sandbox (ADR-023): run a command on a copy of the student's project that cannot change it, see their files
or reach the network. bubblewrap (already installed): a throwaway layer over the project (writes vanish at exit), a
read-only system, an empty /tmp, no /home, no network, a clean environment, and prlimit CPU/memory/file/process
limits under a hard timeout. Only commands the student wrote or approved are ever passed here.
"""

import shutil
import subprocess
import time
from pathlib import Path

TIMEOUT = 120  # seconds of wall time; prlimit caps CPU time separately
OUTPUT = 20_000  # characters kept (the end, where errors are)
LIMITS = ["--cpu=60", "--as=4000000000", "--fsize=200000000", "--nproc=512", "--nofile=1024"]


def available() -> bool:
    return shutil.which("bwrap") is not None and shutil.which("prlimit") is not None


def argv(project: Path, command: str, writable: bool = False) -> list[str]:
    return [
        "bwrap", "--unshare-all", "--die-with-parent", "--new-session",
        "--ro-bind", "/usr", "/usr", "--symlink", "usr/bin", "/bin", "--symlink", "usr/lib", "/lib",
        "--symlink", "usr/lib64", "/lib64", "--symlink", "usr/bin", "/sbin", "--ro-bind", "/etc", "/etc",
        "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
        *(["--bind", str(project), "/work"] if writable else ["--overlay-src", str(project), "--tmp-overlay", "/work"]),
        "--chdir", "/work",
        "--clearenv", "--setenv", "PATH", "/usr/bin", "--setenv", "HOME", "/tmp", "--setenv", "LANG", "C.UTF-8",
        "--", "/usr/bin/prlimit", *LIMITS, "--", "/bin/sh", "-c", command,
    ]


def run(project: Path, command: str, timeout: int = TIMEOUT, writable: bool = False) -> tuple[str, str, float]:
    """(passed | failed | error, the output's end, seconds). `error`: it could not run or took too long. `writable`
    keeps what the command writes in the folder (converting an untrusted upload in its own folder)."""
    if not available():
        return "error", "O bubblewrap não está instalado.", 0.0
    if not project.is_dir():
        return "error", "A pasta do projeto já não existe.", 0.0
    started = time.monotonic()
    try:
        done = subprocess.run(argv(project, command, writable), capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"") + (e.stderr or b"")
        return "error", (out.decode("utf-8", "replace")[-OUTPUT:] + f"\nParado ao fim de {timeout} s.").strip(), float(timeout)
    out = (done.stdout + done.stderr).decode("utf-8", "replace")[-OUTPUT:]
    return ("passed" if done.returncode == 0 else "failed"), out, round(time.monotonic() - started, 1)


def git(project: Path) -> dict | None:
    """Branch, uncommitted files and the last commits, read inside the Sandbox: a repository's own config can make
    git run programs (core.fsmonitor, hooks), so git never runs on it unsandboxed. None if it is not a repository."""
    if not (project / ".git").exists():
        return None
    status, out, _ = run(project, "git -c core.fsmonitor=false -c safe.directory='*' status --porcelain=v1 -b && echo '@@log' && "
                                  "git -c safe.directory='*' log -5 --format='%h %ad %s' --date=short", timeout=20)
    if status != "passed" or "@@log" not in out:
        return {"error": out[-500:]}
    head, log = out.split("@@log", 1)
    lines = head.strip().splitlines()
    branch = lines[0][3:].split("...")[0] if lines and lines[0].startswith("## ") else "?"
    return {"branch": branch, "changed": len(lines) - 1, "commits": [c for c in log.strip().splitlines() if c]}
