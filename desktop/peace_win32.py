"""Windows side of the desktop actions (peace_actions.py), through the Win32 API with ctypes only.

Windows are described the way the Hyprland code describes clients (address, class, title, focusHistoryID,
at, size), so matching and verification in peace_actions work unchanged. Workspaces and saved layouts have no
public Windows API and stay Linux-only.
"""

import ctypes
import os
from ctypes import wintypes
from pathlib import Path

user32 = ctypes.windll.user32
shell32 = ctypes.windll.shell32
kernel32 = ctypes.windll.kernel32
dwmapi = ctypes.windll.dwmapi

GWL_EXSTYLE, WS_EX_TOOLWINDOW, DWMWA_CLOAKED = -20, 0x80, 14
SW_SHOWNORMAL, SW_RESTORE, WM_CLOSE = 1, 9, 0x10
SWP_NOZORDER, SWP_NOACTIVATE, MONITOR_DEFAULTTONEAREST = 0x4, 0x10, 2
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
FO_DELETE, FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_NOERRORUI = 3, 0x4, 0x10, 0x40, 0x400
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.GetForegroundWindow.restype = wintypes.HWND
shell32.ShellExecuteW.restype = ctypes.c_void_p


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


class SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
                ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL), ("hNameMappings", ctypes.c_void_p),
                ("lpszProgressTitle", wintypes.LPCWSTR)]


def _exe(hwnd: int) -> str:
    """The window's program, e.g. "obsidian" for Obsidian.exe."""
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        ok = kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size))
        return Path(buf.value).stem.lower() if ok else ""
    finally:
        kernel32.CloseHandle(handle)


def _rect(hwnd: int) -> tuple[int, int, int, int]:
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def _shown(hwnd: int) -> bool:
    """A window on the taskbar: visible, titled, not a tool window, not a hidden (cloaked) app window."""
    if not user32.IsWindowVisible(hwnd) or not user32.GetWindowTextLengthW(hwnd):
        return False
    if user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
        return False
    cloaked = wintypes.DWORD()
    dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
    return not cloaked.value


def clients() -> list[dict]:
    found: list[int] = []
    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(lambda hwnd, _: found.append(hwnd) or True)
    user32.EnumWindows(callback, 0)
    front = user32.GetForegroundWindow()
    out = []
    for hwnd in filter(_shown, found):
        buf = ctypes.create_unicode_buffer(user32.GetWindowTextLengthW(hwnd) + 1)
        user32.GetWindowTextW(hwnd, buf, len(buf))
        exe = _exe(hwnd)
        x, y, w, h = _rect(hwnd)
        out.append({"address": hex(hwnd), "class": exe, "initialClass": exe, "title": buf.value, "workspace": {"id": 1},
                    "floating": True, "at": [x, y], "size": [w, h], "focusHistoryID": 0 if hwnd == front else 1})
    return out


def focus(address: str) -> bool:
    hwnd = int(address, 16)
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    # Windows only lets the foreground app hand over focus; a synthetic ALT tap makes this agent eligible.
    user32.keybd_event(0x12, 0, 0, 0)
    user32.keybd_event(0x12, 0, 2, 0)
    user32.SetForegroundWindow(hwnd)
    return user32.GetForegroundWindow() == hwnd


def close(address: str) -> None:
    user32.PostMessageW(int(address, 16), WM_CLOSE, 0, 0)  # the app may still ask to save


def exists(address: str) -> bool:
    return bool(user32.IsWindow(int(address, 16)))


def place(address: str, x: int, y: int, width: int, height: int) -> tuple[int, int, int, int]:
    """Put a window at a share (percent) of its monitor's work area (without the taskbar); the target rect."""
    hwnd = int(address, 16)
    info = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
    user32.GetMonitorInfoW(user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST), ctypes.byref(info))
    work = info.rcWork
    w, h = work.right - work.left, work.bottom - work.top
    target = (work.left + w * x // 100, work.top + h * y // 100, w * width // 100, h * height // 100)
    user32.ShowWindow(hwnd, SW_RESTORE)  # a maximized window ignores the new size
    user32.SetWindowPos(hwnd, 0, *target, SWP_NOZORDER | SWP_NOACTIVATE)
    return target


def rect(address: str) -> tuple[int, int, int, int]:
    return _rect(int(address, 16))


def shell_open(target: str, params: str = "") -> bool:
    """Open a file, folder or shortcut like a double-click (no shell command line involved)."""
    return (shell32.ShellExecuteW(None, "open", target, params or None, None, SW_SHOWNORMAL) or 0) > 32


def trash(path: Path) -> bool:
    op = SHFILEOPSTRUCTW(wFunc=FO_DELETE, pFrom=str(path) + "\0",
                         fFlags=FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI)
    return shell32.SHFileOperationW(ctypes.byref(op)) == 0 and not op.fAnyOperationsAborted


def applications() -> dict[str, dict]:
    """Start-menu shortcuts (this user's first), by name, like the Linux desktop entries."""
    roots = [Path(os.environ.get("APPDATA", "")), Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData"))]
    apps: dict[str, dict] = {}
    for root in roots:
        folder = root / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        if not folder.is_dir():
            continue
        for link in sorted(folder.rglob("*.lnk")):
            name = link.stem
            if name in apps or "uninstall" in name.lower() or "desinstal" in name.lower():
                continue
            apps[name] = {"id": name, "name": name, "path": str(link), "wm_class": "", "hidden": False}
    return apps
