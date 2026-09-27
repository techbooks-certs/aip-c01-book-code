"""Open local regular files without following a final symbolic link."""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import re
import stat

from .baseline import BoundaryError

_REPARSE_POINT = 0x400
_DIRECTORY = 0x10


class _FileTime(ctypes.Structure):
    _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]


class _FileInfo(ctypes.Structure):
    _fields_ = [
        ("attributes", ctypes.c_uint32),
        ("created", _FileTime), ("accessed", _FileTime), ("written", _FileTime),
        ("volume", ctypes.c_uint32),
        ("size_high", ctypes.c_uint32), ("size_low", ctypes.c_uint32),
        ("links", ctypes.c_uint32),
        ("index_high", ctypes.c_uint32), ("index_low", ctypes.c_uint32),
    ]


def _windows_api():
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                               ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
                               ctypes.c_void_p]
    api.CreateFileW.restype = ctypes.c_void_p
    api.GetFileInformationByHandle.argtypes = [ctypes.c_void_p, ctypes.POINTER(_FileInfo)]
    api.GetFileInformationByHandle.restype = ctypes.c_int
    api.GetFileType.argtypes = [ctypes.c_void_p]
    api.GetFileType.restype = ctypes.c_uint32
    api.CloseHandle.argtypes = [ctypes.c_void_p]
    api.CloseHandle.restype = ctypes.c_int
    return api


def _descriptor_from_handle(handle):
    import msvcrt
    return msvcrt.open_osfhandle(int(handle), os.O_RDONLY | os.O_BINARY | os.O_NOINHERIT)


def _open_windows(path: str) -> int:
    # A local drive path avoids device namespaces, network shares and alternate
    # data streams in this reader. The caller has made it absolute.
    if not re.fullmatch(r"[A-Za-z]:\\[^:]*", path) or "\x00" in path:
        raise BoundaryError("Use a regular file on a local Windows drive")
    api = _windows_api()
    handle = api.CreateFileW(
        "\\\\?\\" + path, 0x80000000, 0x1, None, 3,
        0x00200000 | 0x02000000, None,
    )
    # GENERIC_READ; FILE_SHARE_READ (deny concurrent write/delete); OPEN_EXISTING;
    # OPEN_REPARSE_POINT prevents following the final link. BACKUP_SEMANTICS lets
    # us identify and reject directory handles without reading directory data.
    if handle in (None, ctypes.c_void_p(-1).value):
        raise OSError("Could not open the local input file for protected reading")
    try:
        info = _FileInfo()
        if not api.GetFileInformationByHandle(handle, ctypes.byref(info)):
            raise OSError("Could not inspect the opened input file")
        if info.attributes & (_REPARSE_POINT | _DIRECTORY) or api.GetFileType(handle) != 1:
            raise BoundaryError("Input must be a regular file, not a reparse point or device")
        fd = _descriptor_from_handle(handle)
        handle = None  # The CRT descriptor now owns the native handle.
        return fd
    finally:
        if handle is not None:
            api.CloseHandle(handle)


def open_regular_readonly(path: Path) -> int:
    """Return an owned binary descriptor; the caller must close it.

    The input and its ancestors must be ordinary local filesystem paths. Use a
    directory not writable by untrusted actors; this is not a filesystem sandbox.
    The platform-specific open protects the final component against link following.
    """
    path = Path(path).absolute()
    for part in (path, *path.parents):
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & _REPARSE_POINT:
            raise BoundaryError("Symlink and reparse-point input paths are not supported")
    if not stat.S_ISREG(path.lstat().st_mode):
        raise BoundaryError("Input must be a regular file")
    if os.name == "nt":
        return _open_windows(str(path))
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_NONBLOCK"):
        raise BoundaryError("This platform cannot enforce the required file-open checks")
    return os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
