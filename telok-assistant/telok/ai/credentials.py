"""Protected runtime settings, deliberately outside database/asset backups."""

import ctypes
import json
import os
from contextlib import contextmanager
from pathlib import Path

from filelock import FileLock

from telok.settings import settings


class Blob(ctypes.Structure):
    _fields_ = [("size", ctypes.c_ulong), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def protect(data: bytes, decrypt=False) -> bytes:
    if os.name != "nt":
        return data
    buf = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    crypt = ctypes.windll.crypt32
    ctypes.windll.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    ctypes.windll.kernel32.LocalFree.restype = ctypes.c_void_p
    if decrypt:
        ok = crypt.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target))
    else:
        ok = crypt.CryptProtectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target))
    if not ok:
        raise RuntimeError("Protected credential storage unavailable")
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        ctypes.windll.kernel32.LocalFree(target.data)


def root() -> Path:
    path = settings().credentials_dir.resolve()
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


@contextmanager
def locked():
    with FileLock(str(root() / "credentials.lock"), timeout=30):
        yield


def read() -> dict:
    path = root() / "runtime.bin"
    if not path.exists():
        return {}
    return json.loads(protect(path.read_bytes(), decrypt=True))


def write(value: dict):
    path = root() / "runtime.bin"
    temporary = root() / "runtime.pending"
    content = protect(json.dumps(value, ensure_ascii=False).encode())
    fd = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "wb") as out:
        out.write(content)
        out.flush()
        os.fsync(out.fileno())
    os.replace(temporary, path)
    if os.name != "nt":
        path.chmod(0o600)


def update(**values):
    with locked():
        data = read()
        data.update(values)
        write(data)
    return data
