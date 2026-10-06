"""Small platform credential adapter for local provider API keys.

Windows secrets use the current-user DPAPI context.  Other platforms must use
environment variables; this module intentionally has no plaintext fallback.
The versioned prefix allows config readers to distinguish protected blobs
from legacy plaintext profile values and migrate the latter on save.
"""

from __future__ import annotations

import base64
import ctypes
import os
from ctypes import wintypes


_PREFIX = "dpapi:v1:"
_DESCRIPTION = "PaperAudit provider API key"
_ENTROPY = b"PaperAudit/provider-key/v1"
_CRYPTPROTECT_UI_FORBIDDEN = 0x1


class SecretProtectionError(RuntimeError):
    """Raised when the operating system cannot safely protect a secret."""


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def is_protected(value: str) -> bool:
    return str(value or "").startswith(_PREFIX)


def protect_secret(value: str) -> str:
    """Protect ``value`` for the current Windows user and return a tagged blob."""

    if not value:
        return ""
    if os.name != "nt":
        raise SecretProtectionError("安全本地密钥存储当前仅支持 Windows DPAPI；请改用 api_key_env 环境变量")
    encoded = _dpapi(bytes(value.encode("utf-8")), protect=True)
    return _PREFIX + base64.urlsafe_b64encode(encoded).decode("ascii")


def unprotect_secret(value: str) -> str:
    """Decrypt a tagged DPAPI blob for the current Windows user."""

    raw = str(value or "")
    if not raw.startswith(_PREFIX):
        raise SecretProtectionError("未知密钥存储格式")
    if os.name != "nt":
        raise SecretProtectionError("该密钥由 Windows DPAPI 保护，必须在原 Windows 用户环境中读取")
    try:
        encrypted = base64.urlsafe_b64decode(raw[len(_PREFIX) :].encode("ascii"))
    except (ValueError, UnicodeError) as exc:
        raise SecretProtectionError("DPAPI 密钥编码损坏") from exc
    return _dpapi(encrypted, protect=False).decode("utf-8")


def _dpapi(data: bytes, *, protect: bool) -> bytes:
    if not data:
        raise SecretProtectionError("密钥内容为空")
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    entropy_buffer = (ctypes.c_ubyte * len(_ENTROPY)).from_buffer_copy(_ENTROPY)
    entropy = _DataBlob(len(_ENTROPY), ctypes.cast(entropy_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = _DataBlob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL
    description = wintypes.LPWSTR()
    if protect:
        function = crypt32.CryptProtectData
        function.argtypes = [
            ctypes.POINTER(_DataBlob),
            wintypes.LPCWSTR,
            ctypes.POINTER(_DataBlob),
            wintypes.LPVOID,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(_DataBlob),
        ]
        function.restype = wintypes.BOOL
        ok = function(
            ctypes.byref(source),
            _DESCRIPTION,
            ctypes.byref(entropy),
            None,
            None,
            _CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(output),
        )
    else:
        function = crypt32.CryptUnprotectData
        function.argtypes = [
            ctypes.POINTER(_DataBlob),
            ctypes.POINTER(wintypes.LPWSTR),
            ctypes.POINTER(_DataBlob),
            wintypes.LPVOID,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(_DataBlob),
        ]
        function.restype = wintypes.BOOL
        ok = function(
            ctypes.byref(source),
            ctypes.byref(description),
            ctypes.byref(entropy),
            None,
            None,
            _CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(output),
        )
    if not ok:
        error = ctypes.get_last_error()
        if not protect and description:
            kernel32.LocalFree(ctypes.cast(description, wintypes.HLOCAL))
        raise SecretProtectionError(f"Windows DPAPI 操作失败（错误码 {error}）")
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(output.pbData, wintypes.HLOCAL))
        if not protect and description:
            kernel32.LocalFree(ctypes.cast(description, wintypes.HLOCAL))


__all__ = ["SecretProtectionError", "is_protected", "protect_secret", "unprotect_secret"]
