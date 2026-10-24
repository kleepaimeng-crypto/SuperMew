"""Handle-relative, no-reparse file access for the Windows Skill loader.

NtOpenFile anchors each child to an already opened directory, just like openat.
Neither enumeration nor reads resolve a previously checked pathname again.
Native APIs are bound lazily; importing this module is safe on POSIX.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import errno
from functools import lru_cache
import os
from pathlib import Path
import stat
import struct


class _UnicodeString(ctypes.Structure):
    _fields_ = [
        ("Length", wintypes.USHORT),
        ("MaximumLength", wintypes.USHORT),
        ("Buffer", wintypes.LPWSTR),
    ]


class _ObjectAttributes(ctypes.Structure):
    _fields_ = [
        ("Length", wintypes.ULONG),
        ("RootDirectory", wintypes.HANDLE),
        ("ObjectName", ctypes.POINTER(_UnicodeString)),
        ("Attributes", wintypes.ULONG),
        ("SecurityDescriptor", ctypes.c_void_p),
        ("SecurityQualityOfService", ctypes.c_void_p),
    ]


class _IoStatus(ctypes.Structure):
    _fields_ = [("Status", ctypes.c_void_p), ("Information", ctypes.c_size_t)]


class _AttributeTag(ctypes.Structure):
    _fields_ = [("Attributes", wintypes.DWORD), ("ReparseTag", wintypes.DWORD)]


@lru_cache(maxsize=1)
def _api() -> tuple[ctypes.CDLL, ctypes.CDLL]:
    if os.name != "nt":
        raise OSError("Windows handle APIs are unavailable")
    native = ctypes.WinDLL("ntdll", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    native.NtOpenFile.argtypes = [
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.DWORD,
        ctypes.POINTER(_ObjectAttributes),
        ctypes.POINTER(_IoStatus),
        wintypes.ULONG,
        wintypes.ULONG,
    ]
    native.NtOpenFile.restype = wintypes.LONG
    native.NtQueryDirectoryFile.argtypes = [
        wintypes.HANDLE,
        wintypes.HANDLE,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(_IoStatus),
        ctypes.c_void_p,
        wintypes.ULONG,
        wintypes.ULONG,
        ctypes.c_ubyte,
        ctypes.c_void_p,
        ctypes.c_ubyte,
    ]
    native.NtQueryDirectoryFile.restype = wintypes.LONG
    native.RtlNtStatusToDosError.argtypes = [wintypes.LONG]
    native.RtlNtStatusToDosError.restype = wintypes.ULONG
    kernel.GetFileInformationByHandleEx.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel.GetFileInformationByHandleEx.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.GetCurrentProcess.argtypes = []
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.DuplicateHandle.argtypes = [
        wintypes.HANDLE,
        wintypes.HANDLE,
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    ]
    kernel.DuplicateHandle.restype = wintypes.BOOL
    return native, kernel


def _status_error(status: int) -> OSError:
    if status & 0xFFFFFFFF == 0xC000050B:  # STATUS_REPARSE_POINT_ENCOUNTERED
        return OSError(errno.ELOOP, "symlinks and reparse points are not allowed")
    native, _ = _api()
    return ctypes.WinError(native.RtlNtStatusToDosError(status))


def _component(name: str) -> None:
    if (
        not name
        or name in {".", ".."}
        or any(char in name for char in "\\/:\x00")
        or name.endswith((" ", "."))
    ):
        raise OSError(errno.EINVAL, "invalid skill path component")


def _open(name: str, parent: int | None = None) -> tuple[int, int]:
    native, kernel = _api()
    text = ctypes.create_unicode_buffer(name)
    length = len(name.encode("utf-16-le"))
    if length > 65532:
        raise OSError(errno.ENAMETOOLONG, "skill path is too long")
    unicode_name = _UnicodeString(
        length, length + 2, ctypes.cast(text, wintypes.LPWSTR)
    )
    attributes = _ObjectAttributes(
        ctypes.sizeof(_ObjectAttributes),
        parent,
        ctypes.pointer(unicode_name),
        0x1040,
        None,
        None,  # OBJ_DONT_REPARSE | OBJ_CASE_INSENSITIVE
    )
    handle = wintypes.HANDLE()
    result = _IoStatus()
    status = native.NtOpenFile(
        ctypes.byref(handle),
        0x120089,
        ctypes.byref(attributes),
        ctypes.byref(result),
        7,
        0x200020,
        # FILE_GENERIC_READ; share read/write/delete; OPEN_REPARSE_POINT + sync I/O
    )
    if status < 0:
        raise _status_error(status)
    opened = handle.value
    assert opened is not None
    try:
        info = _AttributeTag()
        if not kernel.GetFileInformationByHandleEx(
            opened, 9, ctypes.byref(info), ctypes.sizeof(info)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        if info.Attributes & 0x400:  # FILE_ATTRIBUTE_REPARSE_POINT
            raise OSError(errno.ELOOP, "symlinks and reparse points are not allowed")
        mode = stat.S_IFDIR if info.Attributes & 0x10 else stat.S_IFREG
        return opened, mode
    except BaseException:
        kernel.CloseHandle(opened)
        raise


class WindowsDirectory:
    def __init__(self, handle: int) -> None:
        self.handle = handle

    @classmethod
    def open_root(cls, path: Path) -> WindowsDirectory:
        absolute = Path(os.path.abspath(path))
        # Only ordinary local-drive paths are accepted, never device namespaces.
        if len(absolute.drive) != 2 or absolute.drive[1] != ":":
            raise OSError(errno.EINVAL, "skill root must be a local Windows path")
        handle, mode = _open("\\??\\" + str(absolute))
        return cls._directory(handle, mode)

    @classmethod
    def _directory(cls, handle: int, mode: int) -> WindowsDirectory:
        if not stat.S_ISDIR(mode):
            _api()[1].CloseHandle(handle)
            raise OSError(errno.ENOTDIR, "skill path must be a directory")
        return cls(handle)

    def close(self) -> None:
        _api()[1].CloseHandle(self.handle)

    def duplicate(self) -> WindowsDirectory:
        kernel = _api()[1]
        process = kernel.GetCurrentProcess()
        copied = wintypes.HANDLE()
        if not kernel.DuplicateHandle(
            process, self.handle, process, ctypes.byref(copied), 0, False, 2
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        assert copied.value is not None
        return WindowsDirectory(copied.value)

    def open_directory(self, name: str) -> WindowsDirectory:
        _component(name)
        return self._directory(*_open(name, self.handle))

    def entry_mode(self, name: str) -> int:
        _component(name)
        handle, mode = _open(name, self.handle)
        _api()[1].CloseHandle(handle)
        return mode

    def open_file(self, name: str) -> int:
        import msvcrt

        _component(name)
        handle, mode = _open(name, self.handle)
        if not stat.S_ISREG(mode):
            _api()[1].CloseHandle(handle)
            raise OSError(errno.EINVAL, "skill path must be a regular file")
        try:
            # Ownership transfers to the CRT descriptor; os.close releases it.
            return msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        except BaseException:
            _api()[1].CloseHandle(handle)
            raise

    def list_names(self) -> list[str]:
        native, _ = _api()
        buffer = ctypes.create_string_buffer(65536)
        result = _IoStatus()
        names: list[str] = []
        restart = True
        while True:
            status = native.NtQueryDirectoryFile(
                self.handle,
                None,
                None,
                None,
                ctypes.byref(result),
                buffer,
                len(buffer),
                12,
                False,
                None,
                restart,  # FileNamesInformation
            )
            restart = False
            if status & 0xFFFFFFFF == 0x80000006:  # STATUS_NO_MORE_FILES
                return names
            if status < 0:
                raise _status_error(status)
            data = buffer.raw[: result.Information]
            offset = 0
            while True:
                if offset + 12 > len(data):
                    raise OSError(errno.EIO, "invalid directory information")
                next_offset, _, name_size = struct.unpack_from("<III", data, offset)
                end = offset + 12 + name_size
                if name_size % 2 or end > len(data):
                    raise OSError(errno.EIO, "invalid directory entry")
                name = data[offset + 12 : end].decode("utf-16-le")
                if name not in {".", ".."}:
                    names.append(name)
                if not next_offset:
                    break
                if next_offset < 12 + name_size:
                    raise OSError(errno.EIO, "invalid directory offset")
                offset += next_offset
