"""Unlock an authenticated local Tokyo package without storing its access key."""
from __future__ import annotations

import base64
import binascii
from datetime import datetime, timezone
import errno
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import unicodedata
import uuid
import zipfile

BASE = Path(__file__).resolve().parent
PAYLOAD_NAME = "pacote-tokyo.enc"
MAGIC = b"TOKYO-WORK-V1\0"
AAD = b"Tokyo-Trabalho-v1"
PORT = 8845
MAX_FILE = 150 * 1024 * 1024
MAX_TOTAL = 512 * 1024 * 1024
MAX_FILES = 10000
MANIFEST_NAME = "manifesto.json"
ESSENTIAL = {
    "app/server/app.py", "app/dist/index.html",
    "app/data/credores_snapshot_com_restricoes.json",
    "app/data/titulos_snapshot.json", "app/data/pendencias_snapshot.json",
}

# A static command, with no user input in its arguments. The entered key only
# travels through the child process's captured stdout and is never logged.
WINDOWS_KEY_DIALOG = r'''
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()
$form = New-Object System.Windows.Forms.Form
$form.Text = 'Tokyo - Primeira abertura'
$form.ClientSize = New-Object System.Drawing.Size(640, 200)
$form.StartPosition = 'CenterScreen'
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false
$form.MinimizeBox = $false
$label = New-Object System.Windows.Forms.Label
$label.Location = New-Object System.Drawing.Point(20, 18)
$label.Size = New-Object System.Drawing.Size(600, 42)
$label.Text = 'Cole a chave recebida na conversa e clique em Abrir Tokyo. A chave fica visivel para voce conferir.'
$text = New-Object System.Windows.Forms.TextBox
$text.Location = New-Object System.Drawing.Point(20, 70)
$text.Size = New-Object System.Drawing.Size(600, 30)
$text.Font = New-Object System.Drawing.Font('Consolas', 13)
$text.MaxLength = 120
$text.ShortcutsEnabled = $true
$open = New-Object System.Windows.Forms.Button
$open.Text = 'Abrir Tokyo'
$open.Location = New-Object System.Drawing.Point(350, 130)
$open.Size = New-Object System.Drawing.Size(130, 36)
$cancel = New-Object System.Windows.Forms.Button
$cancel.Text = 'Cancelar'
$cancel.Location = New-Object System.Drawing.Point(490, 130)
$cancel.Size = New-Object System.Drawing.Size(130, 36)
$cancel.DialogResult = [System.Windows.Forms.DialogResult]::Cancel
$open.Add_Click({
    if ($text.Text.Trim() -notmatch '^[A-Za-z0-9_-]{43}=?$') {
        [void][System.Windows.Forms.MessageBox]::Show($form, 'Copie a chave inteira da conversa. Ela tem 43 caracteres, sem espacos.', 'Confira a chave', [System.Windows.Forms.MessageBoxButtons]::OK, [System.Windows.Forms.MessageBoxIcon]::Information)
        [void]$text.Focus()
        return
    }
    $form.DialogResult = [System.Windows.Forms.DialogResult]::OK
})
$form.Controls.AddRange(@($label, $text, $open, $cancel))
$form.AcceptButton = $open
$form.CancelButton = $cancel
$form.Add_Shown({$form.Activate(); [void]$text.Focus()})
$result = $form.ShowDialog()
if ($result -eq [System.Windows.Forms.DialogResult]::OK) {
    $response = @{status = 'ok'; key = $text.Text.Trim()}
} else {
    $response = @{status = 'cancelled'}
}
$form.Dispose()
[Console]::Out.WriteLine(($response | ConvertTo-Json -Compress))
'''


class UnlockError(RuntimeError):
    """An error safe to show without revealing payload contents or the key."""


class UnlockCancelled(UnlockError):
    """The user closed the first-start dialog without changing the installation."""


def record_event(base, stage, error=None):
    """Write only fixed stage names and exception classes, never keys or output."""
    try:
        private = Path(base) / 'private'
        if private.is_symlink():
            return
        private.mkdir(exist_ok=True)
        log = private / 'inicializacao.log'
        if log.is_symlink():
            return
        entry = {'utc': datetime.now(timezone.utc).isoformat(), 'stage': stage}
        if error is not None:
            entry['error_type'] = type(error).__name__
            code = getattr(error, 'winerror', None)
            if isinstance(code, int):
                entry['winerror'] = code
            cause = error.__cause__
            if cause is not None:
                entry['cause_type'] = type(cause).__name__
                cause_code = getattr(cause, 'winerror', None)
                if isinstance(cause_code, int):
                    entry['cause_winerror'] = cause_code
        with log.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(entry, ensure_ascii=True) + '\n')
    except OSError:
        # An unavailable diagnostic file must not hide the original error.
        pass


def ask_key():
    if sys.platform == 'win32':
        try:
            completed = subprocess.run(
                ['powershell.exe', '-NoLogo', '-NoProfile', '-STA', '-Command', WINDOWS_KEY_DIALOG],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                encoding='utf-8-sig', errors='strict', check=False,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
            if completed.returncode != 0:
                raise ValueError('Key dialog unavailable')
            response = json.loads(completed.stdout.strip())
            if not isinstance(response, dict):
                raise ValueError('Invalid dialog response')
            if response.get('status') == 'cancelled':
                raise UnlockCancelled('Abertura cancelada. Nenhum banco foi alterado.')
            if response.get('status') != 'ok' or not isinstance(response.get('key'), str):
                raise ValueError('Invalid dialog response')
            decode_key(response['key'])
            return response['key'].strip()
        except UnlockCancelled:
            raise
        except (OSError, ValueError, UnicodeError, UnlockError):
            # Some Windows policies disable PowerShell/WinForms. Cooked console
            # input still supports normal paste; unlike getwch it is visible.
            print('A janela da chave nao abriu. Cole a chave abaixo; ela ficara visivel.', flush=True)
    return input('Cole a chave recebida na conversa e pressione Enter: ').strip()


def crypto_primitives():
    try:
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except (ImportError, OSError) as error:
        raise UnlockError('Os componentes de criptografia incluidos nao iniciaram. Extraia novamente o ZIP completo. Consulte private/inicializacao.log.') from error
    return AESGCM, InvalidTag


def require_stopped(port=PORT):
    if sys.platform == 'win32':
        if windows_port_listening(port):
            raise UnlockError('Feche a janela do Tokyo com Ctrl+C antes de instalar esta atualizacao. A porta 8845 esta em uso; nenhum processo foi encerrado.')
        return
    for family, address in [(socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")]:
        try:
            probe = socket.socket(family)
        except OSError as error:
            if family == socket.AF_INET6 and error.errno in (errno.EAFNOSUPPORT, errno.EPROTONOSUPPORT):
                continue
            raise UnlockError("Nao foi possivel verificar se o Tokyo esta fechado.") from error
        with probe:
            # Connecting detects a listener without mistaking the previous
            # server's TIME_WAIT connections for an application still running.
            probe.settimeout(0.5)
            result = probe.connect_ex((address, port))
            absent = {errno.ECONNREFUSED, errno.EAFNOSUPPORT, errno.EADDRNOTAVAIL, errno.ENETUNREACH}
            if result not in absent:
                raise UnlockError("Feche a janela do Tokyo com Ctrl+C antes de instalar esta atualizacao. A porta 8845 esta ocupada ou indisponivel; nenhum processo foi encerrado.")


def windows_port_listening(port):
    """Query actual TCP listeners; a timed-out connect is not a listener test."""
    import ctypes
    import struct
    try:
        api = ctypes.WinDLL('iphlpapi.dll', use_last_error=True).GetExtendedTcpTable
        api.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32),
                        ctypes.c_int, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        api.restype = ctypes.c_uint32
        # MIB_TCPROW_OWNER_PID and MIB_TCP6ROW_OWNER_PID use fixed DWORDs,
        # independently of pointer size. Both tables start with a DWORD count.
        for family, row_size, port_offset in ((2, 24, 8), (23, 56, 20)):
            size = ctypes.c_uint32()
            code = api(None, ctypes.byref(size), False, family, 3, 0)
            if code == 50 and family == 23:  # IPv6 is not installed.
                continue
            if code not in (0, 122):
                raise OSError(code, 'TCP listener query failed')
            for _ in range(5):
                if not 4 <= size.value <= 8 * 1024 * 1024:
                    raise ValueError('Invalid TCP listener table size')
                table = ctypes.create_string_buffer(size.value)
                code = api(table, ctypes.byref(size), False, family, 3, 0)
                if code == 122:  # The listener list grew; retry with new size.
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
        raise UnlockError('Nao foi possivel consultar as portas do Windows. Consulte private/inicializacao.log.') from error


def decode_key(text):
    try:
        compact = text.strip()
        raw = base64.b64decode(compact + "=" * (-len(compact) % 4), altchars=b"-_", validate=True)
        if len(raw) != 32:
            raise ValueError
        return raw
    except (ValueError, binascii.Error, TypeError) as error:
        raise UnlockError("A chave informada nao e valida. Copie novamente a chave recebida na conversa.") from error


def decrypt(encrypted, key_text):
    if len(encrypted) < len(MAGIC) + 12 + 16 or not encrypted.startswith(MAGIC):
        raise UnlockError("O pacote protegido esta incompleto ou possui formato desconhecido.")
    if len(encrypted) > MAX_TOTAL + 32 * 1024 * 1024:
        raise UnlockError("O pacote protegido excede o limite permitido.")
    AESGCM, InvalidTag = crypto_primitives()
    key = decode_key(key_text)
    start = len(MAGIC)
    try:
        plaintext = AESGCM(key).decrypt(encrypted[start:start + 12], encrypted[start + 12:], AAD)
    except InvalidTag as error:
        raise UnlockError("A chave nao corresponde ao pacote ou o download esta danificado. Nada foi instalado.") from error
    if len(plaintext) > MAX_TOTAL:
        raise UnlockError("O conteudo do pacote excede o limite permitido.")
    return plaintext


def checked_name(name):
    if not name or "\\" in name or "\0" in name or name.startswith("/"):
        raise UnlockError("O pacote possui um caminho de arquivo invalido.")
    path = PurePosixPath(name)
    parts = name.rstrip("/").split("/")
    forbidden = {"CON", "PRN", "AUX", "NUL"} | {f"{prefix}{number}" for prefix in ("COM", "LPT") for number in range(1, 10)}
    if any(part in ("", ".", "..") or ":" in part or part.endswith((" ", ".")) or part.split(".")[0].upper() in forbidden for part in parts):
        raise UnlockError("O pacote possui um caminho de arquivo invalido.")
    if name != MANIFEST_NAME and (not parts or parts[0] not in ("app", "referencias")):
        raise UnlockError("O pacote tenta alterar arquivos fora das pastas permitidas.")
    return path


def validated_archive(plaintext):
    """Validate every path, exact manifest membership and content before writing."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(plaintext))
        infos = archive.infolist()
        if len(infos) > MAX_FILES * 2:
            raise UnlockError("O pacote possui arquivos demais.")
        names = set()
        files = {}
        total = 0
        for info in infos:
            checked_name(info.filename)
            normalized = unicodedata.normalize("NFC", info.filename.rstrip("/")).casefold()
            if normalized in names:
                raise UnlockError("O pacote possui caminhos de arquivo duplicados.")
            names.add(normalized)
            kind = stat.S_IFMT(info.external_attr >> 16)
            if kind not in (0, stat.S_IFREG, stat.S_IFDIR) or info.flag_bits & 1:
                raise UnlockError("O pacote possui atalhos ou arquivos nao permitidos.")
            if info.is_dir():
                continue
            if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                raise UnlockError("O pacote utiliza um formato de compressao nao permitido.")
            if info.file_size > MAX_FILE:
                raise UnlockError("Um arquivo do pacote excede o limite permitido.")
            total += info.file_size
            if total > MAX_TOTAL or len(files) >= MAX_FILES + 1:
                raise UnlockError("O conteudo do pacote excede o limite permitido.")
            files[info.filename] = info
        manifest_info = files.pop(MANIFEST_NAME, None)
        if manifest_info is None or manifest_info.file_size > 4 * 1024 * 1024:
            raise UnlockError("O manifesto de integridade do pacote nao foi encontrado.")
        manifest = json.loads(archive.read(manifest_info))
        if not isinstance(manifest, dict) or manifest.get("format") != 1 or not isinstance(manifest.get("files"), list):
            raise UnlockError("O manifesto de integridade do pacote e invalido.")
        declared = {}
        for entry in manifest["files"]:
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                raise UnlockError("O manifesto de integridade do pacote e invalido.")
            name = entry["path"]
            checked_name(name)
            if name in declared or type(entry.get("size")) is not int or entry["size"] < 0 or not isinstance(entry.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]):
                raise UnlockError("O manifesto de integridade do pacote e invalido.")
            declared[name] = entry
        if set(declared) != set(files) or not ESSENTIAL <= set(files):
            raise UnlockError("O pacote esta incompleto ou difere do seu manifesto.")
        if not any(name.startswith("referencias/") for name in files):
            raise UnlockError("Os arquivos de referencia do pacote nao foram encontrados.")
        for name, info in files.items():
            entry = declared[name]
            digest = hashlib.sha256()
            with archive.open(info) as source:
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
            if info.file_size != entry["size"] or digest.hexdigest() != entry["sha256"]:
                raise UnlockError("A verificacao de integridade de um arquivo falhou.")
        return archive, files
    except UnlockError:
        raise
    except (zipfile.BadZipFile, KeyError, UnicodeError, ValueError, TypeError, RuntimeError, OSError) as error:
        raise UnlockError("O conteudo do pacote protegido nao e valido. Nada foi instalado.") from error


def already_installed(base, fingerprint):
    marker = base / "private" / "pacote_aplicado.json"
    try:
        return not marker.is_symlink() and marker.stat().st_size <= 4096 and json.loads(marker.read_text(encoding="utf-8")) == {"sha256": fingerprint} and (base / "app/server/app.py").is_file()
    except (OSError, ValueError, TypeError):
        return False


def install(staging, base, fingerprint):
    """Swap both source folders, rolling back failures and preserving private data."""
    private = base / "private"
    marker = private / "pacote_aplicado.json"
    updates = private / "atualizacoes"
    if any(path.is_symlink() for path in (private, marker, updates, base / "app", base / "referencias")):
        raise UnlockError("Uma pasta da instalacao e um atalho. Nada foi substituido.")
    private.mkdir(exist_ok=True)
    previous = [name for name in ("app", "referencias") if (base / name).exists()]
    backup = None
    if previous:
        updates.mkdir(exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        backup = updates / (stamp + "-" + uuid.uuid4().hex)
        backup.mkdir()
    moved = []
    placed = []
    try:
        for name in ("app", "referencias"):
            if name in previous:
                os.replace(base / name, backup / name)
                moved.append(name)
            os.replace(staging / name, base / name)
            placed.append(name)
        marker_temporary = staging / "pacote_aplicado.json"
        marker_temporary.write_text(json.dumps({"sha256": fingerprint}) + "\n", encoding="utf-8")
        os.replace(marker_temporary, marker)
    except BaseException:
        for name in reversed(placed):
            os.replace(base / name, staging / ("rollback-" + name))
        for name in reversed(moved):
            os.replace(backup / name, base / name)
        if backup is not None:
            backup.rmdir()
        raise
    return backup


def unlock(*, base=BASE, key_text=None, port=PORT):
    base = Path(base).resolve()
    payload = base / PAYLOAD_NAME
    try:
        if payload.stat().st_size > MAX_TOTAL + 32 * 1024 * 1024:
            raise UnlockError("O pacote protegido excede o limite permitido.")
        encrypted = payload.read_bytes()
    except OSError as error:
        raise UnlockError("O pacote protegido nao foi encontrado. Extraia o ZIP completo.") from error
    fingerprint = hashlib.sha256(encrypted).hexdigest()
    if already_installed(base, fingerprint):
        return {"installed": False, "backup": None}
    require_stopped(port)
    record_event(base, 'verificando_componentes')
    crypto_primitives()
    if key_text is None:
        record_event(base, 'solicitando_chave')
        print('Primeira abertura: cole a chave na janela Tokyo. A chave nao sera salva.', flush=True)
        key_text = ask_key()
    record_event(base, 'validando_pacote')
    plaintext = decrypt(encrypted, key_text)
    archive, files = validated_archive(plaintext)
    staging = Path(tempfile.mkdtemp(prefix=".tokyo-instalacao-", dir=base))
    try:
        with archive:
            for name, info in files.items():
                target = staging.joinpath(*PurePosixPath(name).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open("xb") as destination:
                    shutil.copyfileobj(source, destination, length=1024 * 1024)
        require_stopped(port)
        backup = install(staging, base, fingerprint)
        record_event(base, 'instalacao_concluida')
        return {"installed": True, "backup": backup}
    except UnlockError:
        raise
    except OSError as error:
        raise UnlockError("Nao foi possivel concluir a instalacao. Os arquivos anteriores foram preservados; confira o espaco livre e tente novamente.") from error
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def main():
    record_event(BASE, 'inicio')
    try:
        result = unlock()
        if result['installed']:
            print('Tokyo preparado neste computador. Banco local preservado.', flush=True)
            if result['backup']:
                print('Fontes anteriores guardadas em: ' + str(result['backup']), flush=True)
        else:
            record_event(BASE, 'ja_preparado')
        return 0
    except (UnlockCancelled, KeyboardInterrupt) as error:
        record_event(BASE, 'cancelado', error)
        print('Abertura cancelada. Nenhum banco foi alterado.', flush=True)
        return 2
    except Exception as error:
        record_event(BASE, 'falha', error)
        detail = str(error) if isinstance(error, UnlockError) else 'Falha inesperada (' + type(error).__name__ + ').'
        print('Nao foi possivel preparar o Tokyo: ' + detail, flush=True)
        print('A janela continuara aberta. Diagnostico: private/inicializacao.log', flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
