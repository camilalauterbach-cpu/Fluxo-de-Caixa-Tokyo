from __future__ import annotations

import base64
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import stat
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("tokyo_unlock", ROOT / "tools/working/desbloquear_tokyo.py")
unlocker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(unlocker)


class UnlockTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="tokyo-unlock-tests-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.key = os.urandom(32)
        self.key_text = base64.urlsafe_b64encode(self.key).decode().rstrip("=")
        self.files = {name: b"[]" if name.endswith(".json") else b"synthetic fixture" for name in unlocker.ESSENTIAL}
        self.files["referencias/nota.txt"] = b"Synthetic reference, never executed."

    def package(self, files=None, *, manifest_changes=None, extra=None):
        files = dict(self.files if files is None else files)
        declared = [{"path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()} for name, data in sorted(files.items())]
        manifest = {"format": 1, "files": declared}
        if manifest_changes:
            manifest_changes(manifest)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in files.items():
                archive.writestr(name, data)
            archive.writestr("manifesto.json", json.dumps(manifest))
            if extra:
                extra(archive)
        nonce = os.urandom(12)
        encrypted = unlocker.MAGIC + nonce + AESGCM(self.key).encrypt(nonce, buffer.getvalue(), unlocker.AAD)
        (self.base / unlocker.PAYLOAD_NAME).write_bytes(encrypted)
        return encrypted

    def free_port(self):
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            return server.getsockname()[1]

    def apply(self, key_text=None):
        return unlocker.unlock(base=self.base, key_text=self.key_text if key_text is None else key_text, port=self.free_port())

    def preserved_private(self):
        private = self.base / "private"
        private.mkdir(exist_ok=True)
        (private / "tokyo.sqlite3").write_bytes(b"existing database fixture must not change")
        (private / "session.txt").write_bytes(b"existing private fixture")
        return {path.name: path.read_bytes() for path in private.iterdir() if path.is_file()}

    def assert_private(self, expected):
        for name, data in expected.items():
            self.assertEqual((self.base / "private" / name).read_bytes(), data)

    def assert_no_staging(self):
        self.assertEqual(list(self.base.glob(".tokyo-instalacao-*")), [])

    def test_install_preserves_database_and_never_saves_key(self):
        expected_private = self.preserved_private()
        encrypted = self.package()
        result = self.apply()
        self.assertTrue(result["installed"])
        self.assertIsNone(result["backup"])
        self.assert_private(expected_private)
        for name, data in self.files.items():
            self.assertEqual((self.base / name).read_bytes(), data)
        marker = json.loads((self.base / "private/pacote_aplicado.json").read_text())
        self.assertEqual(marker, {"sha256": hashlib.sha256(encrypted).hexdigest()})
        for path in self.base.rglob("*"):
            if path.is_file():
                self.assertNotIn(self.key_text.encode(), path.read_bytes())
        self.assert_no_staging()

    def test_matching_marker_skips_key_and_all_installation_changes_even_if_running(self):
        self.package()
        self.apply()
        before = {path.relative_to(self.base): path.read_bytes() for path in self.base.rglob("*") if path.is_file()}
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0));server.listen()
            with patch.object(unlocker.getpass, "getpass", side_effect=AssertionError("must not ask again")):
                result = unlocker.unlock(base=self.base, port=server.getsockname()[1])
        self.assertFalse(result["installed"])
        self.assertEqual(before, {path.relative_to(self.base): path.read_bytes() for path in self.base.rglob("*") if path.is_file()})

    def test_wrong_key_and_tampered_envelope_preserve_everything(self):
        expected_private = self.preserved_private()
        encrypted = self.package()
        for data, key in [(encrypted, base64.urlsafe_b64encode(os.urandom(32)).decode()), (encrypted[:-1] + bytes([encrypted[-1] ^ 1]), self.key_text), (b"invalid envelope", self.key_text)]:
            with self.subTest(kind=len(data)):
                (self.base / unlocker.PAYLOAD_NAME).write_bytes(data)
                with self.assertRaises(unlocker.UnlockError):
                    self.apply(key)
                self.assert_private(expected_private)
                self.assertFalse((self.base / "app").exists())
                self.assert_no_staging()

    def test_update_saves_previous_sources_and_preserves_all_private_state(self):
        expected_private = self.preserved_private()
        self.package();self.apply()
        original_files = dict(self.files)
        self.files["app/server/app.py"] = b"updated synthetic source"
        self.package()
        result = self.apply()
        self.assertIsNotNone(result["backup"])
        for name, data in original_files.items():
            self.assertEqual((result["backup"] / name).read_bytes(), data)
        self.assert_private(expected_private)
        self.assertEqual((self.base / "app/server/app.py").read_bytes(), b"updated synthetic source")
        self.assert_no_staging()

    def test_active_port_prevents_update_and_key_prompt(self):
        self.package();self.apply()
        before = {path.relative_to(self.base): path.read_bytes() for path in self.base.rglob("*") if path.is_file()}
        self.files["app/server/app.py"] = b"new synthetic source"
        self.package()
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0));server.listen()
            with patch.object(unlocker.getpass, "getpass", side_effect=AssertionError("must check port first")):
                with self.assertRaises(unlocker.UnlockError):
                    unlocker.unlock(base=self.base, port=server.getsockname()[1])
            with socket.create_connection(server.getsockname(), timeout=1):
                accepted, _ = server.accept()
                accepted.close()
        for name, data in before.items():
            if name != Path(unlocker.PAYLOAD_NAME):
                self.assertEqual((self.base / name).read_bytes(), data)

    def test_recently_closed_server_connections_do_not_block_installation(self):
        self.package()
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen()
            port = server.getsockname()[1]
            with socket.create_connection(server.getsockname(), timeout=1) as client:
                accepted, _ = server.accept()
                accepted.shutdown(socket.SHUT_WR)
                self.assertEqual(client.recv(1), b"")
                accepted.close()
        result = unlocker.unlock(base=self.base, key_text=self.key_text, port=port)
        self.assertTrue(result["installed"])

    def test_unsafe_paths_symlinks_and_duplicate_windows_names_are_rejected(self):
        expected_private = self.preserved_private()
        def symlink(archive):
            info = zipfile.ZipInfo("app/danger")
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, "../../private/tokyo.sqlite3")
        variants = [
            lambda archive: archive.writestr("app/../../outside", "unsafe"),
            lambda archive: archive.writestr("private/tokyo.sqlite3", "unsafe"),
            lambda archive: archive.writestr("app\\outside", "unsafe"),
            lambda archive: archive.writestr("app/CON.txt", "unsafe"),
            lambda archive: archive.writestr("app/DIST/index.html", "duplicate"),
            symlink,
        ]
        for extra in variants:
            self.package(extra=extra)
            with self.assertRaises(unlocker.UnlockError):
                self.apply()
            self.assert_private(expected_private)
            self.assertFalse((self.base / "app").exists())
        self.assert_no_staging()

    def test_manifest_and_required_file_failures_leave_existing_app_intact(self):
        self.package();self.apply()
        original = (self.base / "app/server/app.py").read_bytes()
        for change in [lambda manifest: manifest["files"].pop(), lambda manifest: manifest["files"][0].update(sha256="0" * 64), lambda manifest: manifest["files"][0].update(size=99999)]:
            self.package(manifest_changes=change)
            with self.assertRaises(unlocker.UnlockError):
                self.apply()
            self.assertEqual((self.base / "app/server/app.py").read_bytes(), original)
        del self.files["app/dist/index.html"]
        self.package()
        with self.assertRaises(unlocker.UnlockError):
            self.apply()
        self.assertEqual((self.base / "app/server/app.py").read_bytes(), original)

    def test_size_limits_reject_before_any_installation(self):
        self.package()
        with patch.object(unlocker, "MAX_FILE", 4):
            with self.assertRaises(unlocker.UnlockError):
                self.apply()
        self.assertFalse((self.base / "app").exists())
        self.assert_no_staging()

    def test_mid_swap_failure_rolls_back_sources_marker_and_database(self):
        private = self.preserved_private()
        self.package();self.apply()
        before = {path.relative_to(self.base): path.read_bytes() for path in self.base.rglob("*") if path.is_file()}
        self.files["app/server/app.py"] = b"updated synthetic source"
        self.package()
        actual_replace = os.replace
        failed = False
        def replace(source, target):
            nonlocal failed
            if not failed and Path(source).name == "referencias" and Path(source).parent.name.startswith(".tokyo-instalacao-"):
                failed = True
                raise OSError("simulated source swap failure")
            return actual_replace(source, target)
        with patch.object(unlocker.os, "replace", side_effect=replace):
            with self.assertRaises(unlocker.UnlockError):
                self.apply()
        self.assertTrue(failed)
        self.assert_private(private)
        for name, data in before.items():
            if name != Path(unlocker.PAYLOAD_NAME):
                self.assertEqual((self.base / name).read_bytes(), data)
        self.assert_no_staging()


if __name__ == "__main__":
    unittest.main()
