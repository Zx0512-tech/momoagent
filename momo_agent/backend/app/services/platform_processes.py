from __future__ import annotations

import ctypes
import os
import signal

from ctypes import wintypes


def _posix_process_is_zombie(pid: int) -> bool:
    """Linux 上把僵尸进程视为已退出；其他 POSIX 平台安全回退。"""

    stat_path = f'/proc/{pid}/stat'
    try:
        raw = open(stat_path, encoding='utf-8').read()
    except (FileNotFoundError, PermissionError, OSError):
        return False
    closing_paren = raw.rfind(')')
    if closing_paren < 0:
        return False
    fields = raw[closing_paren + 2:].split()
    return bool(fields) and fields[0] == 'Z'


def process_exists(pid: int) -> bool:
    """判断平台 worker 是否仍存活，不向目标进程发送信号。"""

    if pid <= 0:
        return False
    if os.name == 'nt':
        process = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not process:
            return False
        try:
            exit_code = ctypes.c_ulong()
            return bool(
                ctypes.windll.kernel32.GetExitCodeProcess(process, ctypes.byref(exit_code))
            ) and exit_code.value == 259
        finally:
            ctypes.windll.kernel32.CloseHandle(process)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    # kill(pid, 0) 对已退出但尚未被父进程 wait() 的 zombie 仍返回成功。
    # Dispatcher 的语义需要的是“还能执行工作吗”，因此 zombie 必须按已退出处理。
    return not _posix_process_is_zombie(pid)


class _ProcessEntry32W(ctypes.Structure):
    _fields_ = [
        ('dwSize', wintypes.DWORD),
        ('cntUsage', wintypes.DWORD),
        ('th32ProcessID', wintypes.DWORD),
        ('th32DefaultHeapID', ctypes.c_size_t),
        ('th32ModuleID', wintypes.DWORD),
        ('cntThreads', wintypes.DWORD),
        ('th32ParentProcessID', wintypes.DWORD),
        ('pcPriClassBase', wintypes.LONG),
        ('dwFlags', wintypes.DWORD),
        ('szExeFile', wintypes.WCHAR * 260),
    ]


def _windows_process_tree(root_pid: int) -> list[int]:
    snapshot = ctypes.windll.kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        return [root_pid]
    children: dict[int, list[int]] = {}
    entry = _ProcessEntry32W()
    entry.dwSize = ctypes.sizeof(entry)
    try:
        has_entry = ctypes.windll.kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while has_entry:
            children.setdefault(int(entry.th32ParentProcessID), []).append(int(entry.th32ProcessID))
            has_entry = ctypes.windll.kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        ctypes.windll.kernel32.CloseHandle(snapshot)

    ordered: list[int] = []

    def visit(process_id: int) -> None:
        ordered.append(process_id)
        for child_pid in children.get(process_id, []):
            visit(child_pid)

    visit(root_pid)
    return ordered


def terminate_process_tree(pid: int) -> None:
    """终止 worker 及其执行器子进程，同时避免误杀当前服务进程组。"""

    if not process_exists(pid):
        return
    if os.name == 'nt':
        for process_id in reversed(_windows_process_tree(pid)):
            handle = ctypes.windll.kernel32.OpenProcess(0x0001, False, process_id)
            if not handle:
                continue
            try:
                ctypes.windll.kernel32.TerminateProcess(handle, 1)
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        return
    try:
        target_pgid = os.getpgid(pid)
        # 正常 dispatcher worker 通过 start_new_session=True 启动，拥有独立进程组；
        # 恢复的外部 PID 或测试进程可能与服务本身同组。此时 killpg 会连同 API/CI
        # 主进程一起终止，必须降级为只终止目标 PID，宁可留下未知子进程也不能自杀。
        if target_pgid == os.getpgrp():
            os.kill(pid, signal.SIGTERM)
            return
        os.killpg(target_pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
