"""Zabbix API and update-date rules. No GUI dependencies."""
from dataclasses import dataclass
from datetime import date
import json
import os
import re
import ssl
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen


class ApiError(Exception):
    pass


def normalize_url(value):
    parts = urlsplit(value.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ApiError("Укажите полный адрес http:// или https://.")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ApiError("Адрес не должен содержать пароль, параметры или #.")
    path = parts.path.rstrip("/")
    if not path.endswith("/api_jsonrpc.php"):
        path += "/api_jsonrpc.php"
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


@dataclass(frozen=True)
class UpdateState:
    text: str
    age: int | None
    level: str


def classify(value, today=None):
    today = today or date.today()
    if not value:
        return UpdateState("Нет значения", None, "unknown")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return UpdateState("Ожидается YYYY-MM-dd", None, "unknown")
    try:
        age = (today - date.fromisoformat(value)).days
    except ValueError:
        return UpdateState("Некорректная дата", None, "unknown")
    if age < 0:
        return UpdateState("Дата в будущем", age, "unknown")
    if age >= 30:
        return UpdateState("Требуется обновление", age, "red")
    if age >= 23:
        return UpdateState("Скоро обновлять", age, "yellow")
    return UpdateState("Актуально", age, "green")


class ZabbixApi:
    def __init__(self, url, token):
        self.url = normalize_url(url)
        self.token = token.strip()
        self.modern_auth = False
        self.counter = 0
        # For an internal CA use ZABBIX_CA_FILE; TLS verification stays enabled.
        self.context = ssl.create_default_context(cafile=os.getenv("ZABBIX_CA_FILE") or None)

    def call(self, method, params, authenticated=True):
        self.counter += 1
        payload = {"jsonrpc": "2.0", "method": method, "params": params, "id": self.counter}
        headers = {"Content-Type": "application/json-rpc"}
        if authenticated:
            if self.modern_auth:
                headers["Authorization"] = "Bearer " + self.token
            else:
                payload["auth"] = self.token
        request = Request(self.url, data=json.dumps(payload).encode(), headers=headers)
        try:
            with urlopen(request, timeout=20, context=self.context) as response:
                result = json.load(response)
        except HTTPError as exc:
            raise ApiError(f"HTTP {exc.code}. Проверьте адрес API и доступ к серверу.") from None
        except (URLError, TimeoutError, OSError):
            raise ApiError("Не удалось связаться с Zabbix. Проверьте сеть, адрес и сертификат.") from None
        except (ValueError, UnicodeError):
            raise ApiError("Сервер вернул не JSON. Проверьте адрес api_jsonrpc.php.") from None
        if not isinstance(result, dict):
            raise ApiError("Неожиданный ответ Zabbix API.")
        if "error" in result:
            error = result["error"]
            message = f"{error.get('message', 'Ошибка API')}: {error.get('data', '')}"
            raise ApiError(message.replace(self.token, "[скрыто]") if self.token else message)
        if "result" not in result:
            raise ApiError("В ответе API отсутствует result.")
        return result["result"]

    def connect(self):
        version = self.call("apiinfo.version", {}, authenticated=False)
        try:
            self.modern_auth = tuple(map(int, version.split(".")[:2])) >= (6, 4)
        except (ValueError, AttributeError):
            raise ApiError("Не удалось определить версию Zabbix.") from None
        if not self.token:
            raise ApiError("Введите API-токен.")
        return version

    def hosts(self):
        return self.call("host.get", {"output": ["hostid", "host", "name"], "sortfield": "name"})

    def items(self, hostids):
        if not hostids:
            return []
        result = []
        for offset in range(0, len(hostids), 200):
            result.extend(self.call("item.get", {
                "output": ["itemid", "hostid", "key_", "lastvalue", "lastclock", "state", "status"],
                "hostids": hostids[offset:offset + 200], "filter": {"key_": "is-upd-date"},
                "webitems": True,
            }))
        return result
