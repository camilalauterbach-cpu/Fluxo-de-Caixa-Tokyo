"""Provision the existing Tokyo app and keep encrypted SQLite backups locally."""
from __future__ import annotations

import argparse
from contextlib import closing
import copy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
import tempfile
import threading

PROJECT = Path(__file__).resolve().parents[1]
APP = PROJECT / "app"
BOOTSTRAP = PROJECT / "bootstrap.enc"
BOOTSTRAP_HELPER = PROJECT / "tools/working/desbloquear_tokyo.py"
DATABASE = Path("/var/data/private/tokyo.sqlite3")
BACKUP_MAGIC = b"TOKYO-DB-V1\0"
BACKUP_AAD = b"Tokyo-SQLite-backup-v1"
BACKUP_INTERVAL = 86400
SNAPSHOTS = {
    "creditors": "app/data/credores_snapshot_com_restricoes.json",
    "titles": "app/data/titulos_snapshot.json",
    "issues": "app/data/pendencias_snapshot.json",
    "definitions": "app/data/definicoes_vigentes.json",
}
REQUIRED_COLUMNS = {
    "users": {"id", "name", "email", "hash", "role", "active"},
    "sessions": {"token", "user_id", "expires"},
    "scenarios": {"id", "name", "version", "payload", "updated"},
    "audit": {"id", "scenario_id", "version", "actor", "action", "reason", "created", "before_payload", "after_payload"},
    "facts": {"id", "payload"},
    "requests": {"id", "scenario_id", "requested_by", "status", "payload"},
}


class CloudError(RuntimeError):
    """A fixed diagnostic with no financial records or secret values."""


def application_modules(app_root):
    server = str(Path(app_root).resolve() / "server")
    if server not in sys.path:
        sys.path.insert(0, server)
    from engine import calculate, day, money, START, END, CUTOFF
    from store import Store
    return Store, calculate, day, money, START, END, CUTOFF


def bootstrap_module(path=BOOTSTRAP_HELPER):
    spec = importlib.util.spec_from_file_location("tokyo_cloud_bootstrap", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def reconstructed_state(snapshots, *, app_root=APP):
    """Reproduce the supplied work plan using data only, never bundled Python."""
    _, calculate, day, money, start, end, cutoff = application_modules(app_root)
    try:
        definitions = snapshots["definitions"]
        if not isinstance(definitions, dict):
            raise ValueError
        if (definitions["financial_cutoff_date"], definitions["cashflow_start_date"], definitions["cashflow_end_date"]) != (cutoff, start, end):
            raise ValueError
        policy = definitions["historical_assumptions_not_user_approved"]
        creditors = copy.deepcopy(snapshots["creditors"])
        titles = copy.deepcopy(snapshots["titles"])
        issues = copy.deepcopy(snapshots["issues"])
        for name, records in (("creditors", creditors), ("titles", titles), ("issues", issues)):
            if not isinstance(records, list) or (name != "issues" and not records) or any(not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"] for row in records):
                raise ValueError
            if len({row["id"] for row in records}) != len(records):
                raise ValueError
        proposals = {}
        for creditor in creditors:
            maximum = creditor["current_max_payments"]
            historical = creditor["historical_total_payments"]
            if type(maximum) is not int or not 1 <= maximum <= 36 or type(historical) is not int or historical < 1:
                raise ValueError
            creditor.update(max_payments=maximum, risk="Não informado", priority="Normal")
            proposals[creditor["id"]] = dict(
                n=min(historical, maximum), entry_cents=money(creditor["historical_down_payment_cents"]),
                entry_date=day(creditor["historical_down_payment_date"]),
                first_date=day(creditor["historical_first_balance_date"] or creditor["historical_down_payment_date"]),
                interval="monthly", status="draft", include_reimbursements=False, notes="", evidence="",
            )
        policies = dict(
            floor_cents=money(policy["cash_floor_cents"]),
            tax_reserve_cents=money(policy["tax_uncertainty_reserve_cents"]),
            tax_reserve_from=day(policy["tax_reserve_activation"]),
            operating_reserve_cents=money(policy["january_operating_reserve_cents"]),
            operating_reserve_from=day(policy["january_reserve_activation"]),
        )
        if any(value < 0 for key, value in policies.items() if key.endswith("_cents")):
            raise ValueError
        state = dict(
            schema=1, name="Tokyo — Plano de trabalho", creditors=creditors, titles=titles,
            proposals=proposals, issues=issues, overrides={}, payments=[], policies=policies,
            opening_cash_cents=money(definitions["opening_cash"]["own_cash_cents"]),
            cutoff=cutoff, start=start, end=end, fiscal_review=[],
        )
        calculate(state)
        return state
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        raise CloudError("Os dados iniciais protegidos nao passaram na validacao. Nenhum banco foi criado.") from error


def protected_state(payload_path, key_text, *, app_root=APP, helper_path=BOOTSTRAP_HELPER):
    try:
        bootstrap = bootstrap_module(helper_path)
        payload = Path(payload_path)
        if payload.stat().st_size > bootstrap.MAX_TOTAL + 32 * 1024 * 1024:
            raise ValueError
        plaintext = bootstrap.decrypt(payload.read_bytes(), key_text)
        archive, files = bootstrap.validated_archive(plaintext)
        with archive:
            if not set(SNAPSHOTS.values()) <= set(files):
                raise ValueError
            snapshots = {}
            for name, path in SNAPSHOTS.items():
                if files[path].file_size > 32 * 1024 * 1024:
                    raise ValueError
                snapshots[name] = json.loads(archive.read(files[path]))
        return reconstructed_state(snapshots, app_root=app_root)
    except CloudError:
        raise
    except Exception as error:
        raise CloudError("Nao foi possivel validar os dados protegidos. Confira TOKYO_DATA_KEY e o pacote da instalacao.") from error


def readonly(path):
    connection = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=15)
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA trusted_schema=OFF")
    return connection


def check_database(path):
    try:
        path = Path(path)
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError
        with closing(readonly(path)) as db:
            if db.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise ValueError
            for table, required in REQUIRED_COLUMNS.items():
                columns = {row[1] for row in db.execute(f'PRAGMA table_info("{table}")')}
                if not required <= columns:
                    raise ValueError
            if not db.execute("SELECT 1 FROM users WHERE role='admin' AND active=1 LIMIT 1").fetchone():
                raise ValueError
            states = db.execute("SELECT payload FROM scenarios").fetchall()
            if not states:
                raise ValueError
            for (payload,) in states:
                state = json.loads(payload)
                if not isinstance(state, dict) or any(not isinstance(state.get(key), kind) for key, kind in (("creditors", list), ("titles", list), ("proposals", dict), ("policies", dict))):
                    raise ValueError
                if type(state.get("opening_cash_cents")) is not int or any(not isinstance(state.get(key), str) for key in ("cutoff", "start", "end")):
                    raise ValueError
        return path.resolve()
    except (OSError, sqlite3.DatabaseError, ValueError, TypeError) as error:
        raise CloudError("O banco existente esta indisponivel, incompativel ou sem administradora ativa. Ele foi preservado; nao houve reinicializacao.") from error


def initialize(database=DATABASE, *, payload_path=BOOTSTRAP, environ=None, app_root=APP, helper_path=BOOTSTRAP_HELPER):
    env = os.environ if environ is None else environ
    database = Path(database)
    if database.exists():
        check_database(database)
        return False
    if database.is_symlink() or any(Path(str(database) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise CloudError("Ha arquivos auxiliares de um banco anterior. Preserve o volume e confira a instalacao.")
    email = env.get("TOKYO_ADMIN_EMAIL", "").strip()
    password = env.get("TOKYO_ADMIN_PASSWORD", "")
    name = env.get("TOKYO_ADMIN_NAME", "Gestora Tokyo").strip()
    key = env.get("TOKYO_DATA_KEY", "")
    if not email or "@" not in email or not name or not 10 <= len(password) <= 200 or not key:
        raise CloudError("Configure TOKYO_ADMIN_EMAIL, TOKYO_ADMIN_PASSWORD (10 a 200 caracteres) e TOKYO_DATA_KEY no primeiro inicio.")
    state = protected_state(payload_path, key, app_root=app_root, helper_path=helper_path)
    Store, *_ = application_modules(app_root)
    database.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".tokyo-inicial-", suffix=".sqlite3", dir=database.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        store = Store(app_root, temporary, initial_state=state)
        store.setup(name, email, password)
        check_database(temporary)
        with closing(sqlite3.connect(temporary)) as db:
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            if db.execute("PRAGMA journal_mode=DELETE").fetchone()[0] != "delete":
                raise ValueError
        os.chmod(temporary, 0o600)
        # Both names live on the persistent volume. link() publishes without
        # ever overwriting a bank that another starting process just created.
        try:
            os.link(temporary, database)
        except FileExistsError:
            check_database(database)
            return False
        return True
    except CloudError:
        raise
    except Exception as error:
        raise CloudError("Nao foi possivel criar o banco inicial. O banco anterior, se houver, foi preservado.") from error
    finally:
        temporary.unlink(missing_ok=True)
        for suffix in ("-wal", "-shm", "-journal"):
            Path(str(temporary) + suffix).unlink(missing_ok=True)


def backup_key(text):
    if not isinstance(text, str) or len(text) < 32:
        raise CloudError("Configure TOKYO_BACKUP_KEY com um segredo aleatorio de pelo menos 32 caracteres.")
    return hashlib.sha256(text.encode("utf-8")).digest()


def encrypted_backup(database=DATABASE, *, key_text, directory=None):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key = backup_key(key_text)
    database = check_database(database)
    directory = Path(directory) if directory is not None else database.parent / "backups"
    if directory.is_symlink():
        raise CloudError("A pasta de backups e um atalho. Nenhuma copia foi escrita.")
    temporary = None
    try:
        with closing(readonly(database)) as original, closing(sqlite3.connect(":memory:")) as copied:
            original.backup(copied)
            if copied.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise ValueError
            plaintext = copied.serialize()
        nonce = os.urandom(12)
        encrypted = BACKUP_MAGIC + nonce + AESGCM(key).encrypt(nonce, plaintext, BACKUP_AAD)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        descriptor, name = tempfile.mkstemp(prefix=f"tokyo-{stamp}-", suffix=".tmp", dir=directory)
        temporary = Path(name)
        with os.fdopen(descriptor, "wb") as target:
            target.write(encrypted)
            target.flush()
            os.fsync(target.fileno())
        completed = temporary.with_suffix(".sqlite3.enc")
        os.link(temporary, completed)
        return completed
    except CloudError:
        raise
    except Exception as error:
        raise CloudError("Nao foi possivel concluir o backup criptografado. O banco original foi preservado.") from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def backup_loop(database, key_text, stop, interval=BACKUP_INTERVAL):
    while not stop.is_set():
        try:
            encrypted_backup(database, key_text=key_text)
            print("Tokyo: backup criptografado concluido.", flush=True)
        except Exception:
            print("Tokyo: falha no backup criptografado; confira o volume e a configuracao.", file=sys.stderr, flush=True)
        if stop.wait(interval):
            return


def prepare_permissions(database, environ):
    """Limit the initial root step to the mounted private volume, then drop it."""
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        return
    try:
        uid = int(environ.get("TOKYO_RUN_UID", "10001"))
        gid = int(environ.get("TOKYO_RUN_GID", "10001"))
        if uid <= 0 or gid <= 0:
            raise ValueError
        private = Path("/var/data/private")
        if Path(database).absolute().parent != private or private.is_symlink() or private.parent.is_symlink():
            raise ValueError
        private.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chown(private.parent, uid, gid)
        os.chmod(private.parent, 0o700)
        for root, directories, files in os.walk(private, followlinks=False):
            root_path = Path(root)
            if root_path.is_symlink():
                raise ValueError
            os.chown(root_path, uid, gid)
            os.chmod(root_path, 0o700)
            for filename in directories + files:
                path = root_path / filename
                if path.is_symlink():
                    raise ValueError
                if path.is_file():
                    if not stat.S_ISREG(path.stat().st_mode):
                        raise ValueError
                    os.chown(path, uid, gid)
                    os.chmod(path, 0o600)
        os.setgroups([])
        os.setgid(gid)
        os.setuid(uid)
    except (OSError, ValueError) as error:
        raise CloudError("Nao foi possivel preparar as permissoes do volume privado.") from error


def main(argv=None):
    parser = argparse.ArgumentParser(description="Administrar a instalacao privada Tokyo.")
    parser.add_argument("command", choices=("init", "serve", "backup", "check"))
    args = parser.parse_args(argv)
    env = os.environ
    database = Path(env.get("TOKYO_DB_PATH", str(DATABASE)))
    try:
        prepare_permissions(database, env)
        if args.command == "check":
            check_database(database)
            print("Tokyo: banco e acesso administrativo validos.", flush=True)
            return 0
        if args.command == "backup":
            encrypted_backup(database, key_text=env.get("TOKYO_BACKUP_KEY", ""))
            print("Tokyo: backup criptografado concluido.", flush=True)
            return 0
        if args.command == "serve":
            backup_key(env.get("TOKYO_BACKUP_KEY", ""))
        initialize(database, environ=env)
        if args.command == "init":
            print("Tokyo: banco privado preparado e acesso administrativo verificado.", flush=True)
            return 0
        env["TOKYO_PRODUCTION"] = "1"
        stop = threading.Event()
        thread = threading.Thread(target=backup_loop, args=(database, env["TOKYO_BACKUP_KEY"], stop), daemon=True, name="tokyo-encrypted-backup")
        thread.start()
        try:
            application_modules(APP)
            import app
            return app.main([
                "--host", env.get("TOKYO_HOST", "0.0.0.0"),
                "--port", env.get("TOKYO_PORT", env.get("PORT", "10000")),
                "--db", str(database),
            ]) or 0
        finally:
            stop.set()
            thread.join(timeout=5)
    except CloudError as error:
        print("Tokyo: " + str(error), file=sys.stderr, flush=True)
        return 1
    except Exception as error:
        print("Tokyo: falha de inicializacao (" + type(error).__name__ + "). Confira a configuracao privada.", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
