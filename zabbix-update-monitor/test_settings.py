import json
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from settings import SettingsError, protect_token, read_settings, unprotect_token, write_settings


@unittest.skipUnless(sys.platform == "win32", "Windows DPAPI required")
class ProtectedSettingsTest(unittest.TestCase):
    def test_roundtrip_and_legacy_migration(self):
        token = "test-token-юникод-123"
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            original = {"url": "https://example.com/api_jsonrpc.php", "hostids": ["42"], "api_token": token}
            path.write_text(json.dumps(original), encoding="utf-8")
            config, restored = read_settings(path)
            self.assertEqual(restored, token)
            content = path.read_text(encoding="utf-8")
            saved = json.loads(content)
            self.assertNotIn("api_token", saved)
            self.assertNotIn(token, content)
            self.assertNotIn("api_token", config)
            self.assertEqual(saved["hostids"], ["42"])
            self.assertEqual(read_settings(path)[1], token)
            child = subprocess.run([sys.executable, "-c",
                "import sys; from settings import read_settings; "
                "assert read_settings(sys.argv[1])[1] == 'test-token-юникод-123'", str(path)],
                cwd=Path(__file__).resolve().parent, capture_output=True)
            self.assertEqual(child.returncode, 0, "Token must decrypt in a new process")
            write_settings(path, config, "")
            self.assertEqual(read_settings(path)[1], "")

    def test_tamper_is_rejected(self):
        encrypted = protect_token("test-token")
        self.assertEqual(unprotect_token(encrypted), "test-token")
        # Overwrite ciphertext with a malformed DPAPI blob.
        with self.assertRaises(SettingsError):
            unprotect_token("dpapi:AAAA")

    def test_encryption_failure_never_writes_plaintext(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            path.write_text('{"url":"https://example.com"}', encoding="utf-8")
            previous = path.read_bytes()
            with patch("settings._crypt", side_effect=SettingsError("failure")):
                with self.assertRaises(SettingsError):
                    write_settings(path, {}, "sensitive-token")
            self.assertEqual(path.read_bytes(), previous)
            self.assertEqual(list(Path(folder).glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
