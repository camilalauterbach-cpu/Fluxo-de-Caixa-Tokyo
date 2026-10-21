"""Import a user-selected local Tokyo database without uploading or merging data."""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import errno
import json
import os
from pathlib import Path
import socket
import sqlite3
import sys
import tempfile

BASE = Path(__file__).resolve().parent
DATABASE_NAME = "tokyo-demonstracao.sqlite3"
PORT = 8845
REQUIRED_COLUMNS = {
    "users": {"id", "name", "email", "hash", "role", "active"},
    "sessions": {"token", "user_id", "expires"},
    "scenarios": {"id", "name", "version", "payload", "updated"},
    "audit": {"id", "scenario_id", "version", "actor", "action", "reason", "created", "before_payload", "after_payload"},
    "facts": {"id", "payload"},
    "requests": {"id", "scenario_id", "requested_by", "status", "payload"},
}


class ImportErrorSafe(RuntimeError):
    """An import failure that can be displayed without database contents."""


def require_stopped(port=PORT):
    if sys.platform == 'win32':
        if windows_port_listening(port):
            raise ImportErrorSafe('A porta do Tokyo esta em uso. Feche o programa com Ctrl+C antes de importar. Nenhum processo foi encerrado.')
        return
    for family, host in [(socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")]:
        try:
            probe = socket.socket(family)
        except OSError as error:
            if family == socket.AF_INET6 and error.errno in (errno.EAFNOSUPPORT, errno.EPROTONOSUPPORT):
                continue
            raise ImportErrorSafe("Nao foi possivel verificar se o Tokyo esta fechado.") from error
        with probe:
            probe.settimeout(0.5)
            result = probe.connect_ex((host, port))
            absent = {errno.ECONNREFUSED, errno.EAFNOSUPPORT, errno.EADDRNOTAVAIL, errno.ENETUNREACH}
            if result not in absent:
                raise ImportErrorSafe(
                    "A porta da demonstracao esta em uso ou indisponivel. Feche a janela do Tokyo "
                    "com Ctrl+C antes de importar. Nenhum processo foi encerrado."
                )


def windows_port_listening(port):
    """Read the native listener table instead of timing out on a free port."""
    import ctypes
    import struct
    try:
        api = ctypes.WinDLL('iphlpapi.dll', use_last_error=True).GetExtendedTcpTable
        api.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32),
                        ctypes.c_int, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        api.restype = ctypes.c_uint32
        for family, row_size, port_offset in ((2, 24, 8), (23, 56, 20)):
            size = ctypes.c_uint32()
            code = api(None, ctypes.byref(size), False, family, 3, 0)
            if code == 50 and family == 23:
                continue
            if code not in (0, 122):
                raise OSError(code, 'TCP listener query failed')
            for _ in range(5):
                if not 4 <= size.value <= 8 * 1024 * 1024:
                    raise ValueError('Invalid TCP listener table size')
                table = ctypes.create_string_buffer(size.value)
                code = api(table, ctypes.byref(size), False, family, 3, 0)
                if code == 122:
                    continue
                if code != 0:
                    raise OSError(code, 'TCP listener query failed')
                count = struct.unpack_from('<I', table.raw)[0]
                if 4 + count * row_size > len(table):
                    raise ValueError('Truncated TCP listener table')
                for index in range(count):
                    if struct.unpack_from('!H', table.raw, 4 + index * row_size + port_offset)[0] == port:
                        return True
                break
            else:
                raise OSError('TCP listener list changed repeatedly')
        return False
    except (OSError, ValueError, AttributeError) as error:
        raise ImportErrorSafe('Nao foi possivel consultar as portas do Windows. Nenhum banco foi alterado.') from error


def source_path(source, destination):
    try:
        resolved = Path(source).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ImportErrorSafe("O arquivo escolhido nao foi encontrado.") from error
    if not resolved.is_file():
        raise ImportErrorSafe("Escolha o arquivo do banco ou uma copia de seguranca SQLite.")
    if resolved == destination.resolve() or (destination.exists() and resolved.samefile(destination)):
        raise ImportErrorSafe("A origem ja e o banco desta pasta. Escolha o banco original ou seu backup atual.")
    return resolved


def read_only(path):
    connection = sqlite3.connect(Path(path).as_uri() + "?mode=ro", uri=True, timeout=3)
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA trusted_schema=OFF")
    return connection


def validate(connection):
    try:
        if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise ImportErrorSafe("O banco nao passou na verificacao de integridade. Nada foi substituido.")
        for table, required in REQUIRED_COLUMNS.items():
            actual = {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
            if not required <= actual:
                raise ImportErrorSafe("O arquivo nao possui a estrutura esperada de um banco Tokyo.")
        states = connection.execute("SELECT payload FROM scenarios").fetchall()
        if not states:
            raise ImportErrorSafe("O banco escolhido nao contem cenarios Tokyo.")
        demo_like = False
        for row in states:
            state = json.loads(row[0])
            if not isinstance(state, dict):
                raise ValueError
            for key, kind in [("creditors", list), ("titles", list), ("proposals", dict), ("policies", dict)]:
                if not isinstance(state.get(key), kind):
                    raise ValueError
            if not isinstance(state.get("opening_cash_cents"), int):
                raise ValueError
            creditors = state["creditors"]
            demo_like = demo_like or state.get("name") == "Demonstração — dados fictícios" or (
                bool(creditors) and all(isinstance(c, dict) and str(c.get("id", "")).startswith("DEMO-") for c in creditors)
            )
        return {
            "scenarios": len(states),
            "payments": connection.execute("SELECT COUNT(*) FROM facts").fetchone()[0],
            "audit_entries": connection.execute("SELECT COUNT(*) FROM audit").fetchone()[0],
            "demo_like": demo_like,
        }
    except ImportErrorSafe:
        raise
    except (sqlite3.DatabaseError, json.JSONDecodeError, ValueError, TypeError) as error:
        raise ImportErrorSafe("O arquivo nao e um banco Tokyo valido. Nada foi substituido.") from error


def inspect_source(source, *, base=BASE, port=PORT):
    require_stopped(port)
    destination = Path(base).resolve() / "private" / DATABASE_NAME
    selected = source_path(source, destination)
    try:
        with closing(read_only(selected)) as connection:
            summary = validate(connection)
    except (sqlite3.DatabaseError, OSError) as error:
        raise ImportErrorSafe("Nao foi possivel ler o banco escolhido em modo somente leitura.") from error
    return selected, summary


def consistent_copy(source, target):
    """Copy the complete SQLite snapshot, including committed WAL data."""
    try:
        with closing(read_only(source)) as original:
            original.execute("BEGIN")
            summary = validate(original)
            with closing(sqlite3.connect(target, timeout=3)) as copied:
                original.backup(copied, pages=256, sleep=0.05)
                copied.commit()
                # The detached snapshot must be a complete single-file database.
                copied.execute("PRAGMA journal_mode=DELETE")
                validate(copied)
            return summary
    except ImportErrorSafe:
        raise
    except (sqlite3.DatabaseError, OSError) as error:
        raise ImportErrorSafe("Nao foi possivel criar uma copia consistente. Os bancos originais foram preservados.") from error


def prepare_replacement(destination):
    """Refuse active/locked databases; never discard an unexplained WAL file."""
    if not destination.exists():
        if any(Path(str(destination) + suffix).exists() for suffix in ("-wal", "-shm")):
            raise ImportErrorSafe("Ha arquivos auxiliares de um banco nesta pasta. Nada foi substituido.")
        return
    try:
        with closing(sqlite3.connect(destination, timeout=0.3)) as connection:
            connection.execute("PRAGMA busy_timeout=300")
            # Open WAL normally before taking exclusive locks so SQLite itself
            # can retire the shared-memory file when this last connection closes.
            connection.execute("PRAGMA journal_mode").fetchone()
            connection.execute("PRAGMA locking_mode=EXCLUSIVE")
            connection.execute("BEGIN EXCLUSIVE")
            connection.commit()
            checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if checkpoint and checkpoint[0] != 0:
                raise ImportErrorSafe("O banco desta pasta ainda esta em uso. Feche o Tokyo e tente novamente.")
        if any(Path(str(destination) + suffix).exists() for suffix in ("-wal", "-shm")):
            raise ImportErrorSafe("O banco desta pasta ainda possui arquivos ativos de WAL. Nada foi substituido.")
    except sqlite3.DatabaseError as error:
        raise ImportErrorSafe("O banco desta pasta esta em uso ou nao pode ser substituido com seguranca.") from error


def import_database(source, *, base=BASE, port=PORT, confirmed=False):
    if not confirmed:
        raise ImportErrorSafe("A importacao precisa ser confirmada antes de trocar a base desta pasta.")
    selected, _ = inspect_source(source, base=base, port=port)
    base = Path(base).resolve()
    private = base / "private"
    destination = private / DATABASE_NAME
    if private.is_symlink() or destination.is_symlink():
        raise ImportErrorSafe("A pasta privada ou o banco e um atalho. Nada foi substituido.")
    private.mkdir(parents=True, exist_ok=True)
    temporary = None
    backup = None
    try:
        descriptor, name = tempfile.mkstemp(prefix=".importacao-tokyo-", suffix=".sqlite3", dir=private)
        os.close(descriptor)
        temporary = Path(name)
        summary = consistent_copy(selected, temporary)
        require_stopped(port)
        if destination.exists():
            backups = private / "backups"
            backups.mkdir(exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            descriptor, name = tempfile.mkstemp(prefix=f"tokyo-antes-importacao-{stamp}-", suffix=".sqlite3", dir=backups)
            os.close(descriptor)
            backup = Path(name)
            try:
                consistent_copy(destination, backup)
            except Exception:
                backup.unlink(missing_ok=True)
                backup = None
                raise
        require_stopped(port)
        prepare_replacement(destination)
        os.replace(temporary, destination)
        temporary = None
        return {"database": destination, "backup": backup, **summary}
    except (OSError, sqlite3.DatabaseError) as error:
        raise ImportErrorSafe("A copia nao foi concluida. Nada foi enviado; a origem foi preservada.") from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
            for suffix in ("-wal", "-shm", "-journal"):
                Path(str(temporary) + suffix).unlink(missing_ok=True)


def suggested_source_directory(profile=None):
    """Use the original launcher's setting only to position the file picker."""
    profile = profile or os.environ.get("USERPROFILE")
    if not profile:
        return None
    installed = Path(profile) / "Applications" / "Tokyo"
    settings = installed / "settings.json"
    try:
        # Read only this known configuration file; never search for databases or
        # open a configured bank until the user actually selects it.
        if settings.stat().st_size <= 65536:
            configured = json.loads(settings.read_text(encoding="utf-8-sig"))
            if isinstance(configured, dict) and isinstance(configured.get("db_path"), str):
                candidate = Path(configured["db_path"])
                if candidate.is_absolute() and candidate.is_file():
                    return candidate.parent
    except (OSError, ValueError, TypeError):
        pass
    fallback = installed / "data"
    return fallback if fallback.is_dir() else None


def choose_source():
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class OpenFileName(ctypes.Structure):
                _fields_ = [
                    ("lStructSize", wintypes.DWORD), ("hwndOwner", wintypes.HWND),
                    ("hInstance", wintypes.HINSTANCE), ("lpstrFilter", wintypes.LPCWSTR),
                    ("lpstrCustomFilter", wintypes.LPWSTR), ("nMaxCustFilter", wintypes.DWORD),
                    ("nFilterIndex", wintypes.DWORD), ("lpstrFile", wintypes.LPWSTR),
                    ("nMaxFile", wintypes.DWORD), ("lpstrFileTitle", wintypes.LPWSTR),
                    ("nMaxFileTitle", wintypes.DWORD), ("lpstrInitialDir", wintypes.LPCWSTR),
                    ("lpstrTitle", wintypes.LPCWSTR), ("Flags", wintypes.DWORD),
                    ("nFileOffset", wintypes.WORD), ("nFileExtension", wintypes.WORD),
                    ("lpstrDefExt", wintypes.LPCWSTR), ("lCustData", wintypes.LPARAM),
                    ("lpfnHook", ctypes.c_void_p), ("lpTemplateName", wintypes.LPCWSTR),
                    ("pvReserved", ctypes.c_void_p), ("dwReserved", wintypes.DWORD),
                    ("FlagsEx", wintypes.DWORD),
                ]

            buffer = ctypes.create_unicode_buffer(32768)
            options = OpenFileName()
            options.lStructSize = ctypes.sizeof(OpenFileName)
            options.lpstrFile = ctypes.cast(buffer, wintypes.LPWSTR)
            options.nMaxFile = len(buffer)
            options.lpstrFilter = "Banco Tokyo (*.sqlite3;*.sqlite;*.db)\0*.sqlite3;*.sqlite;*.db\0Todos os arquivos\0*.*\0\0"
            suggested = suggested_source_directory()
            options.lpstrInitialDir = str(suggested) if suggested else None
            options.lpstrTitle = "Escolha o banco ATUAL do Tokyo original ou seu backup mais recente"
            options.Flags = 0x00080000 | 0x00001000 | 0x00000800 | 0x00000008 | 0x00000004
            dialog = ctypes.WinDLL("comdlg32", use_last_error=True)
            dialog.GetOpenFileNameW.argtypes = [ctypes.POINTER(OpenFileName)]
            dialog.GetOpenFileNameW.restype = wintypes.BOOL
            if dialog.GetOpenFileNameW(ctypes.byref(options)):
                return Path(buffer.value)
            if dialog.CommDlgExtendedError() == 0:
                return None
        except (AttributeError, OSError):
            pass
        print("Nao foi possivel abrir o seletor de arquivo. Voce pode informar o caminho abaixo.")
    value = input("Caminho do banco original ou backup (Enter para cancelar): ").strip().strip('"')
    return Path(value) if value else None


def main(argv=None):
    parser = argparse.ArgumentParser(description="Copiar dados do Tokyo original somente no seu computador.")
    parser.add_argument("--source", type=Path, help="Banco SQLite original ou backup atual.")
    parser.add_argument("--yes", action="store_true", help="Confirmacao explicita para uso automatizado.")
    args = parser.parse_args(argv)
    require_stopped()
    print("IMPORTAR DADOS DO TOKYO ORIGINAL - operacao somente neste computador")
    print("Escolha o banco que contem as baixas e entradas mais recentes, nao uma planilha ou ZIP.")
    print("Pare de lancar na instalacao antiga durante a copia para evitar duas bases divergentes.")
    selected = args.source or choose_source()
    if selected is None:
        print("Importacao cancelada. Nenhum banco foi alterado.")
        return 0
    selected, summary = inspect_source(selected)
    print("Origem selecionada: " + str(selected))
    print(f"Encontrados {summary['scenarios']} cenario(s), {summary['payments']} movimento(s) realizado(s) e {summary['audit_entries']} registro(s) de historico.")
    if summary["demo_like"]:
        print("ATENCAO: esta origem tem identificacao de DEMONSTRACAO e pode nao conter os dados da empresa.")
    print("O banco original sera aberto somente para leitura e continuara no seu local.")
    print("A base atual desta pasta sera guardada em private/backups antes da troca. As duas bases NAO serao mescladas.")
    if not args.yes and input("Digite IMPORTAR para confirmar (qualquer outra resposta cancela): ").strip() != "IMPORTAR":
        print("Importacao cancelada. Nenhum banco foi alterado.")
        return 0
    result = import_database(selected, confirmed=True)
    print("Importacao concluida. Nenhum dado foi enviado para a internet.")
    if result["backup"]:
        print("Copia da base anterior desta pasta: " + str(result["backup"]))
    print("Abra INICIAR_TOKYO.cmd e entre com o acesso da instalacao original.")
    print("Se os dados esperados nao aparecerem, preserve as duas bases e confira se escolheu o banco atual correto.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nOperacao interrompida. Preserve os bancos e confira a mensagem anterior antes de tentar novamente.")
        raise SystemExit(1)
    except (ImportErrorSafe, OSError, sqlite3.DatabaseError) as error:
        print("Nao foi possivel importar: " + str(error))
        raise SystemExit(1)
