"""Local secret storage; no plaintext API credentials in the application database."""

from __future__ import annotations

import base64
import os
from threading import Lock

from sonder.infrastructure.database import DATA_ROOT

_key_lock = Lock()


def _windows_protect(value: bytes, decrypt=False) -> bytes:
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
        ]

    buffer = ctypes.create_string_buffer(value)
    source = Blob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    operation = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    operation.argtypes = [
        ctypes.POINTER(Blob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(Blob),
    ]
    operation.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if not operation(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        kernel.LocalFree(ctypes.cast(target.pbData, ctypes.c_void_p))


def _fernet():
    from cryptography.fernet import Fernet

    if os.getenv("WORKSPACE_ENCRYPTION_KEY"):
        return Fernet(os.environ["WORKSPACE_ENCRYPTION_KEY"].encode("ascii"))
    if os.getenv("PLATFORM_ENV") == "docker":
        raise RuntimeError("WORKSPACE_ENCRYPTION_KEY is required in Docker")
    key_file = DATA_ROOT / "secret.key"
    with _key_lock:
        try:
            descriptor = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(Fernet.generate_key())
        return Fernet(key_file.read_bytes())


def encrypt_key(value: str) -> str:
    if os.name == "nt":
        return "dpapi:" + base64.b64encode(_windows_protect(value.encode("utf-8"))).decode("ascii")
    return "fernet:" + _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_key(value: str) -> str:
    """Worker-only capability. Never expose this operation as an HTTP endpoint."""
    kind, payload = value.split(":", 1)
    if kind == "dpapi" and os.name == "nt":
        return _windows_protect(base64.b64decode(payload), decrypt=True).decode("utf-8")
    if kind == "fernet":
        return _fernet().decrypt(payload.encode("ascii")).decode("utf-8")
    raise ValueError("Credential storage belongs to a different operating system")
