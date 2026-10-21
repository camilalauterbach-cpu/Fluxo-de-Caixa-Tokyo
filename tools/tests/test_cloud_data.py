"""Use only synthetic snapshots to test first deployment and persistent data."""
from __future__ import annotations

import base64
from contextlib import closing, redirect_stderr
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "demo"
HELPER = ROOT / "tools/working/desbloquear_tokyo.py"
spec = importlib.util.spec_from_file_location("tokyo_cloud_data", ROOT / "deploy/manage.py")
cloud = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cloud)
Store, _, _, _, START, END, CUTOFF = cloud.application_modules(APP)


def rows(path):
    with closing(sqlite3.connect(path)) as db:
        return {table: db.execute(f'SELECT * FROM "{table}" ORDER BY 1').fetchall() for table in cloud.REQUIRED_COLUMNS}


class CloudDataTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="tokyo-cloud-test-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.database = self.base / "private/tokyo.sqlite3"
        self.payload = self.base / "bootstrap.enc"
        self.key = os.urandom(32)
        self.password = secrets.token_urlsafe(24)
        self.env = {
            "TOKYO_ADMIN_EMAIL": "admin@example.invalid",
            "TOKYO_ADMIN_PASSWORD": self.password,
            "TOKYO_DATA_KEY": base64.urlsafe_b64encode(self.key).decode().rstrip("="),
        }
        self.backup_secret = secrets.token_urlsafe(40)
        self.snapshots = {
            name: json.loads((APP / path.removeprefix("app/")).read_text(encoding="utf-8"))
            for name, path in cloud.SNAPSHOTS.items() if name != "definitions"
        }
        self.snapshots["definitions"] = {
            "financial_cutoff_date": CUTOFF, "cashflow_start_date": START, "cashflow_end_date": END,
            "opening_cash": {"own_cash_cents": 7654321, "credit_limit_cents": 9876543},
            "historical_assumptions_not_user_approved": {
                "cash_floor_cents": 12345, "tax_uncertainty_reserve_cents": 23456,
                "tax_reserve_activation": "2026-11-05", "january_operating_reserve_cents": 34567,
                "january_reserve_activation": "2027-01-01",
            },
        }
        self.write_payload()

    def write_payload(self, snapshots=None):
        bootstrap = cloud.bootstrap_module(HELPER)
        snapshots = self.snapshots if snapshots is None else snapshots
        files = {cloud.SNAPSHOTS[name]: json.dumps(value, ensure_ascii=False).encode() for name, value in snapshots.items()}
        # Neither this Python file nor any supplied reference script may run.
        files["app/server/app.py"] = b"raise AssertionError('encrypted code must not run')"
        files["app/dist/index.html"] = b"<!doctype html><title>Synthetic fixture</title>"
        files["referencias/original.py"] = b"raise AssertionError('reference code must not run')"
        manifest = {"format": 1, "files": [{"path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()} for name, data in sorted(files.items())]}
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in files.items():
                archive.writestr(name, data)
            archive.writestr("manifesto.json", json.dumps(manifest))
        nonce = os.urandom(12)
        self.payload.write_bytes(bootstrap.MAGIC + nonce + AESGCM(self.key).encrypt(nonce, output.getvalue(), bootstrap.AAD))

    def initialize(self, env=None):
        return cloud.initialize(self.database, payload_path=self.payload, environ=self.env if env is None else env, app_root=APP, helper_path=HELPER)

    def test_first_boot_uses_supplied_cash_and_policies_without_executing_bundled_code(self):
        encrypted_before = self.payload.read_bytes()
        with patch("store.seed", side_effect=AssertionError("No demo seed in production")):
            self.assertTrue(self.initialize())
        store = Store(APP, self.database)
        user = store.login(self.env["TOKYO_ADMIN_EMAIL"], self.password)
        self.assertEqual(user["role"], "admin")
        meta, state = store.load("base")
        self.assertEqual(meta["version"], 1)
        self.assertEqual(state["name"], "Tokyo — Plano de trabalho")
        self.assertEqual(state["opening_cash_cents"], 7654321)
        self.assertEqual(state["policies"]["floor_cents"], 12345)
        self.assertEqual(state["policies"]["tax_reserve_cents"], 23456)
        self.assertEqual(state["policies"]["operating_reserve_cents"], 34567)
        self.assertEqual(state["titles"], self.snapshots["titles"])
        self.assertEqual(state["issues"], self.snapshots["issues"])
        self.assertTrue(all(proposal["status"] == "draft" for proposal in state["proposals"].values()))
        self.assertTrue(all(state["proposals"][creditor["id"]]["n"] <= creditor["current_max_payments"] for creditor in state["creditors"]))
        self.assertEqual(self.payload.read_bytes(), encrypted_before)
        self.assertFalse((self.base / "app").exists())
        self.assertFalse((self.base / "referencias").exists())
        self.assertEqual(list(self.database.parent.glob(".tokyo-inicial-*")), [])

    def test_restart_preserves_facts_versions_users_sessions_and_ignores_changed_secrets(self):
        self.initialize()
        store = Store(APP, self.database)
        user = store.login(self.env["TOKYO_ADMIN_EMAIL"], self.password)
        token = store.session(user)
        store.mutate("base", 1, user, "liquidation", "Synthetic recent payment", lambda state: state["payments"].append({"id": "RECENT", "amount_cents": 500, "event_key": "T:DEMO-O1", "date": START, "direction": "out"}))
        before = rows(self.database)
        before_hash = hashlib.sha256(self.database.read_bytes()).hexdigest()
        self.payload.write_bytes(b"not a valid payload anymore")
        changed = {"TOKYO_ADMIN_EMAIL": "new@example.invalid", "TOKYO_ADMIN_PASSWORD": "unrelated new credential"}
        with patch.object(cloud, "protected_state", side_effect=AssertionError("Existing databases must never be reseeded")):
            self.assertFalse(self.initialize(changed))
            self.assertFalse(self.initialize({}))
        self.assertEqual(rows(self.database), before)
        self.assertEqual(hashlib.sha256(self.database.read_bytes()).hexdigest(), before_hash)
        self.assertEqual(Store(APP, self.database).auth(token)["id"], user["id"])
        self.assertEqual(Store(APP, self.database).login(self.env["TOKYO_ADMIN_EMAIL"], self.password)["id"], user["id"])

    def test_missing_admin_configuration_and_wrong_key_cannot_create_a_database(self):
        for env in [{}, dict(self.env, TOKYO_ADMIN_PASSWORD="short"), dict(self.env, TOKYO_DATA_KEY=base64.urlsafe_b64encode(os.urandom(32)).decode())]:
            with self.subTest(configured_fields=sorted(env)):
                with self.assertRaises(cloud.CloudError):
                    self.initialize(env)
                self.assertFalse(self.database.exists())

    def test_missing_or_invalid_financial_definitions_fail_without_a_demo_fallback(self):
        for change in [lambda snapshots: snapshots.pop("definitions"), lambda snapshots: snapshots["definitions"].update(financial_cutoff_date="2026-01-01"), lambda snapshots: snapshots["definitions"].update(opening_cash={"credit_limit_cents": 999}), lambda snapshots: snapshots["definitions"].pop("historical_assumptions_not_user_approved")]:
            snapshots = copy.deepcopy(self.snapshots)
            change(snapshots)
            self.write_payload(snapshots)
            with self.assertRaises(cloud.CloudError):
                self.initialize()
            self.assertFalse(self.database.exists())

    def test_corrupt_or_adminless_existing_database_is_preserved(self):
        self.database.parent.mkdir()
        for data in [b"", b"synthetic corruption"]:
            self.database.write_bytes(data)
            with self.assertRaises(cloud.CloudError):
                self.initialize({})
            self.assertEqual(self.database.read_bytes(), data)
        self.database.unlink()
        self.initialize()
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("UPDATE users SET active=0")
            db.commit()
        before = self.database.read_bytes()
        with self.assertRaises(cloud.CloudError):
            self.initialize()
        self.assertEqual(self.database.read_bytes(), before)

    def test_failed_admin_setup_removes_its_temporary_database(self):
        with patch.object(Store, "setup", side_effect=RuntimeError("synthetic setup failure")):
            with self.assertRaises(cloud.CloudError):
                self.initialize()
        self.assertFalse(self.database.exists())
        self.assertEqual(list(self.database.parent.glob(".tokyo-inicial-*")), [])

    def test_another_first_boot_winner_is_never_overwritten(self):
        winner = self.base / "winner.sqlite3"
        state = cloud.reconstructed_state(self.snapshots, app_root=APP)
        winner_store = Store(APP, winner, initial_state=state)
        winner_store.setup("Earlier owner", "earlier@example.invalid", secrets.token_urlsafe(20))
        before = rows(winner)
        def another_process_won(source, destination):
            shutil.copyfile(winner, destination)
            raise FileExistsError("synthetic competing first boot")
        with patch.object(cloud.os, "link", side_effect=another_process_won):
            self.assertFalse(self.initialize())
        self.assertEqual(rows(self.database), before)
        self.assertEqual(list(self.database.parent.glob(".tokyo-inicial-*")), [])

    def test_encrypted_backup_includes_uncheckpointed_wal_and_is_restorable(self):
        self.initialize()
        store = Store(APP, self.database)
        user = store.login(self.env["TOKYO_ADMIN_EMAIL"], self.password)
        keeper = sqlite3.connect(self.database)
        self.addCleanup(keeper.close)
        keeper.execute("BEGIN")
        keeper.execute("SELECT COUNT(*) FROM scenarios").fetchone()
        store.mutate("base", 1, user, "entry", "Synthetic WAL update", lambda state: state.update(name="Latest synthetic WAL state"))
        self.assertGreater(Path(str(self.database) + "-wal").stat().st_size, 0)
        expected = rows(self.database)
        first = cloud.encrypted_backup(self.database, key_text=self.backup_secret)
        first_bytes = first.read_bytes()
        start = len(cloud.BACKUP_MAGIC)
        self.assertTrue(first_bytes.startswith(cloud.BACKUP_MAGIC))
        self.assertNotIn(b"Latest synthetic WAL state", first_bytes)
        snapshot = AESGCM(hashlib.sha256(self.backup_secret.encode()).digest()).decrypt(first_bytes[start:start + 12], first_bytes[start + 12:], cloud.BACKUP_AAD)
        restored = self.base / "restored.sqlite3"
        restored.write_bytes(snapshot)
        self.assertEqual(rows(restored), expected)
        self.assertEqual(Store(APP, restored).login(self.env["TOKYO_ADMIN_EMAIL"], self.password)["id"], user["id"])
        second = cloud.encrypted_backup(self.database, key_text=self.backup_secret)
        self.assertNotEqual(first, second)
        self.assertEqual(first.read_bytes(), first_bytes)
        self.assertEqual(len(list(first.parent.glob("*.sqlite3.enc"))), 2)

    def test_short_backup_key_is_rejected_without_creating_a_backup(self):
        self.initialize()
        with self.assertRaises(cloud.CloudError):
            cloud.encrypted_backup(self.database, key_text="short")
        self.assertFalse((self.database.parent / "backups").exists())

    def test_failed_backup_write_is_not_published_as_a_completed_backup(self):
        self.initialize()
        before = rows(self.database)
        with patch.object(cloud.os, "fsync", side_effect=OSError("synthetic full volume")):
            with self.assertRaises(cloud.CloudError):
                cloud.encrypted_backup(self.database, key_text=self.backup_secret)
        self.assertEqual(rows(self.database), before)
        self.assertEqual(list((self.database.parent / "backups").iterdir()), [])

    def test_background_backup_waits_one_day_and_never_logs_error_details(self):
        stop = unittest.mock.Mock()
        stop.is_set.return_value = False
        stop.wait.return_value = True
        secret_error = RuntimeError(self.backup_secret)
        with patch.object(cloud, "encrypted_backup", side_effect=secret_error) as backup, redirect_stderr(io.StringIO()) as output:
            cloud.backup_loop(self.database, self.backup_secret, stop)
        backup.assert_called_once_with(self.database, key_text=self.backup_secret)
        stop.wait.assert_called_once_with(86400)
        self.assertNotIn(self.backup_secret, output.getvalue())


if __name__ == "__main__":
    unittest.main()
