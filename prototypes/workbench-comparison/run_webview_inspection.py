"""Launch WebView2 normally and retain host exit, stderr and descendant outcomes.
Uses unprivileged Toolhelp snapshots (no WMI, admin, ACL change or forced kill).
"""

from __future__ import annotations

import argparse
import ctypes as c
import json
import os
import subprocess
import sys
import time
from ctypes import wintypes as w
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[1]
K = c.WinDLL("kernel32", use_last_error=True)


class Entry(c.Structure):
    _fields_ = [
        ("dwSize", w.DWORD),
        ("cntUsage", w.DWORD),
        ("pid", w.DWORD),
        ("heap", c.c_size_t),
        ("module", w.DWORD),
        ("threads", w.DWORD),
        ("parent", w.DWORD),
        ("priority", w.LONG),
        ("flags", w.DWORD),
        ("exe", w.WCHAR * 260),
    ]


K.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
K.CreateToolhelp32Snapshot.restype = w.HANDLE
K.Process32FirstW.argtypes = [w.HANDLE, c.POINTER(Entry)]
K.Process32NextW.argtypes = [w.HANDLE, c.POINTER(Entry)]
K.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
K.OpenProcess.restype = w.HANDLE
K.GetExitCodeProcess.argtypes = [w.HANDLE, c.POINTER(w.DWORD)]
K.CloseHandle.argtypes = [w.HANDLE]


def is_elevated():
    advapi = c.WinDLL("advapi32", use_last_error=True)
    advapi.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, c.POINTER(w.HANDLE)]
    advapi.GetTokenInformation.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.POINTER(w.DWORD)]
    K.GetCurrentProcess.restype = w.HANDLE
    token = w.HANDLE()
    if not advapi.OpenProcessToken(K.GetCurrentProcess(), 8, c.byref(token)):
        raise c.WinError(c.get_last_error())
    try:
        elevation = w.DWORD()
        size = w.DWORD()
        if not advapi.GetTokenInformation(token, 20, c.byref(elevation), c.sizeof(elevation), c.byref(size)):
            raise c.WinError(c.get_last_error())
        return bool(elevation.value)
    finally:
        K.CloseHandle(token)


def snapshot():
    handle = K.CreateToolhelp32Snapshot(2, 0)
    if handle == c.c_void_p(-1).value:
        raise c.WinError(c.get_last_error())
    rows = []
    try:
        item = Entry()
        item.dwSize = c.sizeof(item)
        ok = K.Process32FirstW(handle, c.byref(item))
        while ok:
            rows.append((item.pid, item.parent, item.exe))
            ok = K.Process32NextW(handle, c.byref(item))
    finally:
        K.CloseHandle(handle)
    return rows


def _associate_descendants(rows, associated):
    changed = True
    while changed:
        changed = False
        for pid, parent, exe in rows:
            if parent in associated and pid not in associated:
                associated[pid] = {"pid": pid, "parent_pid": parent, "exe": exe, "role": "descendant"}
                changed = True


def _refresh_exit_codes(associated, handles):
    for pid, row in associated.items():
        if pid not in handles and "exit_code" not in row:
            handle = K.OpenProcess(0x1000, False, pid)
            if handle:
                handles[pid] = handle
            else:
                row["observation_error"] = c.get_last_error()
        if pid not in handles:
            continue
        code = w.DWORD()
        if K.GetExitCodeProcess(handles[pid], c.byref(code)):
            row["exit_code"] = None if code.value == 259 else code.value


def _monitor_host(host, started):
    associated = {host.pid: {"pid": host.pid, "role": "host"}}
    handles = {}
    exit_seen = None
    while True:
        _associate_descendants(snapshot(), associated)
        _refresh_exit_codes(associated, handles)
        if host.poll() is not None:
            exit_seen = exit_seen or time.monotonic()
            all_stopped = all(row.get("exit_code") is not None for row in associated.values())
            if all_stopped or time.monotonic() - exit_seen > 10:
                break
        time.sleep(0.05)
    for handle in handles.values():
        K.CloseHandle(handle)
    return associated, round(time.monotonic() - started, 2)


def _inspection_command(args):
    command = [sys.executable, "-m", "llm_agent.interfaces.workbench.webview"]
    for option in ("workspace", "home", "config", "profile"):
        value = getattr(args, option)
        if value is not None:
            command += ["--" + option.replace("_", "-"), value]
    source_path = str(REPOSITORY / "src")
    old_pythonpath = os.environ.get("PYTHONPATH", "")
    pythonpath = os.pathsep.join(part for part in (source_path, old_pythonpath) if part)
    return command, dict(os.environ, PYTHONPATH=pythonpath)


def _inspect_process(command, env, folder):
    started = time.monotonic()
    with (folder / "stdout.log").open("wb") as out, (folder / "stderr.log").open("wb") as err:
        host = subprocess.Popen(command, stdout=out, stderr=err, env=env, cwd=Path.cwd())
        associated, elapsed = _monitor_host(host, started)
        result = {
            "command": command,
            "elevated": False,
            "host_pid": host.pid,
            "host_exit_code": host.poll(),
            "elapsed_seconds": elapsed,
            "processes": list(associated.values()),
            "sampling_interval_ms": 50,
            "limitations": "Short-lived children between snapshots may be missed. No forced termination.",
        }
    stderr = (folder / "stderr.log").read_text(encoding="utf-8", errors="replace")
    result["chromium_1411_observed"] = "Error = 1411" in stderr
    (folder / "lifecycle.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main(argv=None):
    if is_elevated():
        raise RuntimeError("Inspection must run as an ordinary user, not administrator")
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace")
    parser.add_argument("--home")
    parser.add_argument("--config")
    parser.add_argument("--profile")
    args = parser.parse_args(argv)
    folder = HERE / "evidence" / "endpoints-20261022" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    folder.mkdir(parents=True)
    command, env = _inspection_command(args)
    result = _inspect_process(command, env, folder)
    print(folder)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
