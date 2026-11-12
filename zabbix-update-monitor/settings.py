"""User-scoped Windows DPAPI protection; never fall back to plaintext."""
import base64
import binascii
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import sys
import tempfile


class SettingsError(Exception):
    pass


class DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _crypt(data, decrypt=False):
    if sys.platform != "win32":
        raise SettingsError("Защищённое сохранение токена поддерживается только в Windows.")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    blob_ptr = ctypes.POINTER(DataBlob)
    crypt32.CryptProtectData.argtypes = [blob_ptr, wintypes.LPCWSTR, blob_ptr, ctypes.c_void_p,
                                         ctypes.c_void_p, wintypes.DWORD, blob_ptr]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [blob_ptr, ctypes.c_void_p, blob_ptr, ctypes.c_void_p,
                                           ctypes.c_void_p, wintypes.DWORD, blob_ptr]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    buffer = ctypes.create_string_buffer(data)
    source = DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = DataBlob()
    try:
        if decrypt:
            ok = crypt32.CryptUnprotectData(ctypes.byref(source), None, None, None, None,
                                            1, ctypes.byref(target))
        else:
            # CRYPTPROTECT_UI_FORBIDDEN only; never use LOCAL_MACHINE scope.
            ok = crypt32.CryptProtectData(ctypes.byref(source), "Zabbix API token", None,
                                          None, None, 1, ctypes.byref(target))
        if not ok:
            raise SettingsError("Windows не удалось " + ("расшифровать" if decrypt else "зашифровать") +
                                " токен. Введите его заново под текущим пользователем Windows.")
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        ctypes.memset(buffer, 0, ctypes.sizeof(buffer))
        if target.pbData:
            ctypes.memset(target.pbData, 0, target.cbData)
            kernel32.LocalFree(ctypes.cast(target.pbData, ctypes.c_void_p))


def protect_token(token):
    return "dpapi:" + base64.b64encode(_crypt(token.encode("utf-8"))).decode("ascii") if token else ""


def unprotect_token(value):
    if not value:
        return ""
    try:
        if not isinstance(value, str) or not value.startswith("dpapi:"):
            raise ValueError()
        encrypted = base64.b64decode(value[6:], validate=True)
        if not encrypted:
            raise ValueError()
        return _crypt(encrypted, decrypt=True).decode("utf-8")
    except (ValueError, UnicodeError, binascii.Error):
        raise SettingsError("Повреждён сохранённый токен. Введите его заново.") from None


def write_settings(path, config, token):
    path = Path(path)
    protected = dict(config)
    protected.pop("api_token", None)
    protected["api_token_protected"] = protect_token(token)
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix="settings-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(protected, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError:
        raise SettingsError("Не удалось сохранить защищённые настройки.") from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def read_settings(path):
    path = Path(path)
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, ""
    except (OSError, ValueError):
        raise SettingsError("Не удалось прочитать файл настроек.") from None
    if not isinstance(config, dict):
        raise SettingsError("Некорректный формат файла настроек.")
    if "api_token_protected" in config:
        token = unprotect_token(config["api_token_protected"])
    else:
        token = str(config.get("api_token", ""))
    if "api_token" in config:
        # Replace the legacy file only after encryption has succeeded.
        write_settings(path, config, token)
        config.pop("api_token", None)
    return config, token
