from __future__ import annotations

import hashlib
from contextlib import closing
import importlib.util
import json
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "demo/server"))
from store import Store

spec = importlib.util.spec_from_file_location("tokyo_data_import", ROOT / "tools/portable/importar_dados.py")
importer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(importer)


def rows(path):
    with closing(sqlite3.connect(path)) as connection:
        return {
            name: connection.execute(f'SELECT * FROM "{name}" ORDER BY 1').fetchall()
            for name in importer.REQUIRED_COLUMNS
        }


class ImportDataTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="tokyo-data-import-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "original com espaço.sqlite3"
        self.base = self.root / "portable"
        self.base.mkdir()
        self.destination = self.base / "private" / importer.DATABASE_NAME
        self.store = Store(ROOT / "demo", self.source)
        self.password = secrets.token_urlsafe(20)
        self.user = self.store.setup("Importacao isolada", "local@example.invalid", self.password)
        self.token = self.store.session(self.user)
        self.store.clone("base", "Cenario anterior preservado", self.user)
        self.store.mutate("base", 1, self.user, "liquidation", "Baixa recente de teste", lambda state: state["payments"].append({"id": "RECENT-PAYMENT", "event_key": "T:DEMO-O1", "creditor_id": None, "party": "Fornecedor ficticio", "amount_cents": 10000, "date": "2026-10-20", "direction": "out", "evidence": "Documento sintetico", "components_known": False}))
        self.store.mutate("base", 2, self.user, "entry", "Entrada recente de teste", lambda state: state["titles"].append({"id": "RECENT-RECEIPT", "type": "Receber", "party": "Cliente de teste", "original_signed_cents": 20000, "remaining_signed_cents": 20000, "original_due_date": "2026-10-20", "cash_date": "2026-10-20", "scenario_date": "2026-10-20", "description": "Entrada sintetica", "proposed_agreement_id": None}))
        with self.store.connect() as connection:
            connection.execute("INSERT INTO requests VALUES(?,?,?,?,?)", ("PENDING-REQUEST", "base", self.user["id"], "pending", json.dumps({"evidence": "Referencia sintetica"})))
            connection.execute("PRAGMA user_version=130")
            connection.execute("CREATE TABLE original_extension(value TEXT)")
            connection.execute("INSERT INTO original_extension VALUES('estado adicional preservado')")

    def run_import(self):
        return importer.import_database(self.source, base=self.base, port=self.free_port(), confirmed=True)

    def free_port(self):
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            return server.getsockname()[1]

    def seed_destination(self):
        existing = Store(ROOT / "demo", self.destination)
        existing.mutate("base", 1, self.user, "demo-note", "Alteracao anterior da demo", lambda state: state.update(name="Minha demonstracao anterior"))
        return rows(self.destination)

    def test_complete_database_preserves_recent_facts_history_users_and_sessions(self):
        before_hash = hashlib.sha256(self.source.read_bytes()).hexdigest()
        expected = rows(self.source)
        result = self.run_import()
        self.assertEqual(rows(self.destination), expected)
        self.assertIsNone(result["backup"])
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), before_hash)
        restored = Store(ROOT / "demo", self.destination)
        self.assertEqual(restored.auth(self.token)["id"], self.user["id"])
        self.assertEqual(restored.login("local@example.invalid", self.password)["id"], self.user["id"])
        meta, state = restored.load("base")
        self.assertEqual(meta["version"], 3)
        self.assertEqual(state["payments"][0]["id"], "RECENT-PAYMENT")
        self.assertTrue(any(t["id"] == "RECENT-RECEIPT" for t in state["titles"]))
        with closing(sqlite3.connect(self.destination)) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 130)
            self.assertEqual(connection.execute("SELECT value FROM original_extension").fetchone()[0], "estado adicional preservado")

    def test_previous_destination_is_backed_up_without_merging(self):
        prior = self.seed_destination()
        result = self.run_import()
        self.assertEqual(rows(result["backup"]), prior)
        self.assertEqual(rows(self.destination), rows(self.source))
        second = self.run_import()
        self.assertNotEqual(result["backup"], second["backup"])
        self.assertEqual(rows(result["backup"]), prior)
        self.assertEqual(len(list((self.base / "private/backups").glob("*.sqlite3"))), 2)

    def test_committed_uncheckpointed_wal_updates_are_included(self):
        keeper = sqlite3.connect(self.source)
        self.addCleanup(keeper.close)
        keeper.execute("PRAGMA wal_autocheckpoint=0")
        keeper.execute("BEGIN")
        keeper.execute("SELECT COUNT(*) FROM scenarios").fetchone()
        self.store.mutate("base", 3, self.user, "entry", "Ultima entrada em WAL", lambda state: state.update(name="Estado mais recente em WAL"))
        self.assertGreater(Path(str(self.source) + "-wal").stat().st_size, 0)
        source_main_hash = hashlib.sha256(self.source.read_bytes()).hexdigest()
        expected = rows(self.source)
        self.run_import()
        self.assertEqual(rows(self.destination), expected)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), source_main_hash)

    def test_corrupt_source_leaves_both_files_unchanged(self):
        self.seed_destination()
        bad = self.root / "corrupt.sqlite3"
        bad.write_bytes(b"not a SQLite database")
        before = self.destination.read_bytes()
        with self.assertRaises(importer.ImportErrorSafe):
            importer.import_database(bad, base=self.base, port=self.free_port(), confirmed=True)
        self.assertEqual(self.destination.read_bytes(), before)
        self.assertEqual(bad.read_bytes(), b"not a SQLite database")
        self.assertFalse((self.base / "private/backups").exists())

    def test_other_sqlite_database_is_rejected_without_destination_creation(self):
        bad = self.root / "other.sqlite3"
        with closing(sqlite3.connect(bad)) as connection:
            connection.execute("CREATE TABLE unrelated(value)")
            connection.commit()
        with self.assertRaises(importer.ImportErrorSafe):
            importer.import_database(bad, base=self.base, port=self.free_port(), confirmed=True)
        self.assertFalse(self.destination.exists())

    def test_same_file_symlink_and_hardlink_sources_are_rejected(self):
        self.seed_destination()
        candidates = [self.destination]
        linked = self.root / "link.sqlite3"
        linked.symlink_to(self.destination)
        candidates.append(linked)
        hard = self.root / "hard.sqlite3"
        os.link(self.destination, hard)
        candidates.append(hard)
        for source in candidates:
            with self.subTest(source=source.name):
                with self.assertRaises(importer.ImportErrorSafe):
                    importer.import_database(source, base=self.base, port=self.free_port(), confirmed=True)

    def test_occupied_port_is_preserved_and_no_database_is_changed(self):
        prior = self.seed_destination()
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen()
            port = server.getsockname()[1]
            with self.assertRaises(importer.ImportErrorSafe):
                importer.import_database(self.source, base=self.base, port=port, confirmed=True)
            self.assertEqual(rows(self.destination), prior)
            self.assertEqual(server.getsockname()[1], port)

    def test_missing_explicit_confirmation_does_not_modify_destination(self):
        prior = self.seed_destination()
        with self.assertRaises(importer.ImportErrorSafe):
            importer.import_database(self.source, base=self.base, port=self.free_port())
        self.assertEqual(rows(self.destination), prior)

    def test_recently_closed_server_connections_do_not_block_import(self):
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen()
            port = server.getsockname()[1]
            with socket.create_connection(server.getsockname(), timeout=1) as client:
                accepted, _ = server.accept()
                accepted.shutdown(socket.SHUT_WR)
                self.assertEqual(client.recv(1), b"")
                accepted.close()
        result = importer.import_database(self.source, base=self.base, port=port, confirmed=True)
        self.assertEqual(rows(result["database"]), rows(self.source))

    def test_locked_destination_is_preserved(self):
        prior = self.seed_destination()
        with closing(sqlite3.connect(self.destination)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            with self.assertRaises(importer.ImportErrorSafe):
                self.run_import()
        self.assertEqual(rows(self.destination), prior)
        self.assertEqual(rows(self.source)["requests"][0][0], "PENDING-REQUEST")

    def test_invalid_scenario_payload_does_not_modify_destination(self):
        prior = self.seed_destination()
        with closing(sqlite3.connect(self.source)) as connection:
            connection.execute("UPDATE scenarios SET payload='{}' WHERE id='base'")
            connection.commit()
        with self.assertRaises(importer.ImportErrorSafe):
            self.run_import()
        self.assertEqual(rows(self.destination), prior)

    def test_demo_seed_is_identified_without_claiming_it_is_real_data(self):
        selected, summary = importer.inspect_source(self.source, base=self.base, port=self.free_port())
        self.assertEqual(selected, self.source.resolve())
        self.assertTrue(summary["demo_like"])
        self.assertEqual(summary["payments"], 1)
        self.assertEqual(summary["audit_entries"], 3)

    def test_original_launcher_configuration_only_suggests_parent_directory(self):
        installed = self.root / "Applications" / "Tokyo"
        installed.mkdir(parents=True)
        (installed / "settings.json").write_text(json.dumps({"db_path": str(self.source)}), encoding="utf-8-sig")
        before = self.source.read_bytes()
        self.assertEqual(importer.suggested_source_directory(self.root), self.source.parent)
        self.assertEqual(self.source.read_bytes(), before)
        self.assertFalse(self.destination.exists())

    def test_invalid_relative_or_missing_configured_paths_fall_back_to_data_directory(self):
        installed = self.root / "Applications" / "Tokyo"
        fallback = installed / "data"
        fallback.mkdir(parents=True)
        settings = installed / "settings.json"
        for content in ["{broken", "[]", json.dumps({"db_path": "relative.sqlite3"}), json.dumps({"db_path": str(self.root / "missing.sqlite3")})]:
            with self.subTest(content=content):
                settings.write_text(content, encoding="utf-8")
                self.assertEqual(importer.suggested_source_directory(self.root), fallback)
        fallback.rmdir()
        self.assertIsNone(importer.suggested_source_directory(self.root))


if __name__ == "__main__":
    unittest.main()
