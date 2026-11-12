import json
import unittest
from datetime import date, timedelta
from unittest.mock import patch
from core import ApiError, ZabbixApi, classify, normalize_url


class RulesTest(unittest.TestCase):
    def test_boundaries(self):
        today = date(2026, 11, 12)
        for days, expected in [(0, "green"), (22, "green"), (23, "yellow"), (29, "yellow"), (30, "red"), (365, "red")]:
            with self.subTest(days=days):
                state = classify((today - timedelta(days=days)).isoformat(), today)
                self.assertEqual((state.age, state.level), (days, expected))

    def test_bad_values(self):
        for value in ["", "2026-02-30", "2026-1-02", "12.11.2026", "2027-01-01"]:
            with self.subTest(value=value):
                self.assertEqual(classify(value, date(2026, 11, 12)).level, "unknown")

    def test_url(self):
        self.assertEqual(normalize_url("https://example.com/zabbix/"), "https://example.com/zabbix/api_jsonrpc.php")
        self.assertEqual(normalize_url("https://example.com/api_jsonrpc.php"), "https://example.com/api_jsonrpc.php")
        for value in ["example.com", "file:///tmp/a", "https://user:pass@example.com", "https://example.com/?token=x"]:
            with self.assertRaises(ApiError):
                normalize_url(value)


class Response:
    def __init__(self, result):
        self.result = result

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self):
        return json.dumps(self.result).encode()


class ApiTest(unittest.TestCase):
    @patch("core.urlopen")
    def test_auth_versions_and_exact_key(self, urlopen):
        for version in ["6.0.40", "6.4.0", "7.0.10", "7.4.0"]:
            with self.subTest(version=version):
                urlopen.side_effect = [Response({"result": version}), Response({"result": []})]
                api = ZabbixApi("https://example.com", "secret")
                api.connect()
                api.items(["42"])
                request = urlopen.call_args.args[0]
                payload = json.loads(request.data)
                self.assertEqual(payload["params"]["filter"], {"key_": "is-upd-date"})
                self.assertEqual(payload["params"]["hostids"], ["42"])
                if version.startswith("6.0"):
                    self.assertEqual(payload["auth"], "secret")
                else:
                    self.assertNotIn("auth", payload)
                    self.assertEqual(request.get_header("Authorization"), "Bearer secret")

    @patch("core.urlopen")
    def test_empty_selection_never_queries_all_hosts(self, urlopen):
        api = ZabbixApi("https://example.com", "secret")
        self.assertEqual(api.items([]), [])
        urlopen.assert_not_called()

    @patch("core.urlopen", return_value=Response({"error": {"message": "Denied", "data": "secret"}}))
    def test_error_redacts_token(self, urlopen):
        api = ZabbixApi("https://example.com", "secret")
        with self.assertRaises(ApiError) as error:
            api.hosts()
        self.assertNotIn("secret", str(error.exception))


if __name__ == "__main__":
    unittest.main()
