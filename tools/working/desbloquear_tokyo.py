"""Unlock an authenticated local Tokyo package without storing its access key."""
from __future__ import annotations

import base64
import binascii
from datetime import datetime, timezone
import errno
import getpass
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import socket
import stat
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


class UnlockError(RuntimeError):
    """An error safe to show without revealing payload contents or the key."""


def require_stopped(port=PORT):
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
    try:
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as error:
        raise UnlockError("Os componentes de criptografia incluidos nao foram encontrados. Extraia novamente o ZIP completo.") from error
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
    if key_text is None:
        print("Primeira abertura: cole a chave recebida na conversa. A chave nao sera salva.", flush=True)
        key_text = getpass.getpass("Chave de acesso ao pacote: ")
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
        return {"installed": True, "backup": backup}
    except UnlockError:
        raise
    except OSError as error:
        raise UnlockError("Nao foi possivel concluir a instalacao. Os arquivos anteriores foram preservados; confira o espaco livre e tente novamente.") from error
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def main():
    result = unlock()
    if result["installed"]:
        print("Tokyo preparado neste computador. Chave nao salva; banco local preservado.", flush=True)
        if result["backup"]:
            print("Fontes anteriores guardadas em: " + str(result["backup"]), flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (UnlockError, OSError, EOFError) as error:
        print("Nao foi possivel preparar o Tokyo: " + str(error), flush=True)
        raise SystemExit(1)
    except KeyboardInterrupt:
        print("\nOperacao cancelada. Nenhuma chave foi salva.", flush=True)
        raise SystemExit(1)
