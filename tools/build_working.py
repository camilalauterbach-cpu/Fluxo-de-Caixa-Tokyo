#!/usr/bin/env python3
"""Build the Windows Tokyo package with an authenticated encrypted app payload.

Real source data, references and the key remain outside the repository. The
distributable contains only the encrypted payload and generic launch helpers.
Requires cryptography on the build machine; Windows dependencies are pinned.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
import unicodedata
import urllib.request
import zipfile

from build_portable import (
    HELPERS as PORTABLE, REPOSITORY, add_bytes, json_bytes, safe_relative,
    source_files, verified_python_package, verify_manifest,
)

HELPERS = REPOSITORY / "tools" / "working"
ROOT = "Tokyo-Trabalho/"
MAGIC = b"TOKYO-WORK-V1\0"
AAD = b"Tokyo-Trabalho-v1"
MAX_FILE = 150 * 1024 * 1024
MAX_TOTAL = 512 * 1024 * 1024
MAX_FILES = 10000


def read_key(path: Path) -> tuple[bytes, bytes]:
    text = path.read_text(encoding="ascii").strip()
    try:
        key = base64.b64decode(text + "=" * (-len(text) % 4), altchars=b"-_", validate=True)
    except (ValueError, binascii.Error) as error:
        raise ValueError("The private key file must contain a base64url AES-256 key.") from error
    if len(key) != 32:
        raise ValueError("The private key must contain exactly 32 decoded bytes.")
    return key, text.encode("ascii")


def reference_name(index: int, original: str) -> str:
    # Keep the original name in the encrypted index, and make a portable Windows
    # filename even when a supplied attachment contains reserved characters.
    leaf = original.replace("\\", "/").split("/")[-1]
    leaf = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", leaf).rstrip(" .") or "arquivo"
    if len(leaf) > 160:
        extension = Path(leaf).suffix[:20]
        leaf = leaf[:140] + extension
    return f"referencias/{index:03d}_{leaf}"


def payload_files(app: Path, references_manifest: Path | None) -> tuple[dict[str, bytes], list[dict], int]:
    sources = source_files(app)
    source_manifest = verify_manifest(app, sources)
    files = {"app/" + relative.as_posix(): (app / relative).read_bytes() for relative in sources}
    references = []
    if references_manifest is not None:
        supplied = json.loads(references_manifest.read_text(encoding="utf-8"))
        if not isinstance(supplied, list):
            raise ValueError("The reference manifest must be a file list.")
        for index, item in enumerate(supplied, start=1):
            source = Path(item["path"])
            if not source.is_absolute():
                source = references_manifest.parent / source
            if source.is_symlink() or not source.is_file():
                raise ValueError("A reference is missing or is a symlink.")
            data = source.read_bytes()
            if len(data) != item["bytes"]:
                raise ValueError("A reference differs from the supplied byte count.")
            name = reference_name(index, str(item["name"]))
            files[name] = data
            references.append({
                "original_name": str(item["name"]), "path": name,
                "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
            })
    files["referencias/ARQUIVOS.json"] = json_bytes({"format": 1, "files": references})
    normalized = set()
    for name, data in files.items():
        safe_relative(name)
        key = unicodedata.normalize("NFC", name).casefold()
        if key in normalized:
            raise ValueError("The protected payload has duplicate Windows file paths.")
        normalized.add(key)
        if len(data) > MAX_FILE:
            raise ValueError("A protected file exceeds the allowed size.")
    if len(files) > MAX_FILES or sum(map(len, files.values())) > MAX_TOTAL - 4 * 1024 * 1024:
        raise ValueError("The protected payload exceeds the allowed size or file count.")
    return files, source_manifest, len(references)


def protect(files: dict[str, bytes], key: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    manifest = {"format": 1, "files": [
        {"path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        for name, data in sorted(files.items())
    ]}
    contents = io.BytesIO()
    with zipfile.ZipFile(contents, "w") as archive:
        for name, data in sorted(files.items()):
            add_bytes(archive, name, data)
        add_bytes(archive, "manifesto.json", json_bytes(manifest))
    plaintext = contents.getvalue()
    if len(plaintext) > MAX_TOTAL:
        raise ValueError("The protected ZIP exceeds the allowed size.")
    nonce = os.urandom(12)
    encrypted = AESGCM(key).encrypt(nonce, plaintext, AAD)
    # Validate against the exact Windows bootstrap contract before publishing.
    spec = importlib.util.spec_from_file_location("tokyo_build_unlock", HELPERS / "desbloquear_tokyo.py")
    bootstrap = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bootstrap)
    envelope = MAGIC + nonce + encrypted
    checked = bootstrap.decrypt(envelope, base64.urlsafe_b64encode(key).decode("ascii"))
    with bootstrap.validated_archive(checked)[0]:
        pass
    return envelope


def dependency_bytes(cache: Path, entry: dict) -> bytes:
    path = cache / entry["filename"]
    if Path(entry["filename"]).name != entry["filename"]:
        raise ValueError("Unexpected dependency filename.")
    if not path.exists():
        if not entry["url"].startswith("https://files.pythonhosted.org/"):
            raise ValueError("Dependencies must use the pinned HTTPS PyPI source.")
        with urllib.request.urlopen(entry["url"], timeout=60) as response:
            data = response.read()
        if len(data) != entry["size"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise ValueError("A downloaded Windows dependency failed SHA-256 verification.")
        cache.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".download")
        temporary.write_bytes(data)
        temporary.replace(path)
    data = path.read_bytes()
    if len(data) != entry["size"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
        raise ValueError("A Windows dependency failed SHA-256 verification.")
    return data


def runtime_files(python_bytes: bytes, wheels: list[bytes]) -> dict[str, bytes]:
    files = {}
    with zipfile.ZipFile(io.BytesIO(python_bytes)) as package:
        for item in package.infolist():
            if item.is_dir() or not item.filename.startswith("tools/"):
                continue
            relative = safe_relative(item.filename.removeprefix("tools/"))
            files["python/" + relative.as_posix()] = package.read(item)
    for data in wheels:
        with zipfile.ZipFile(io.BytesIO(data)) as wheel:
            if wheel.testzip() is not None:
                raise ValueError("A Windows dependency failed ZIP integrity verification.")
            for item in wheel.infolist():
                if item.is_dir():
                    continue
                relative = safe_relative(item.filename)
                parts = relative.parts
                if parts[0].endswith(".data"):
                    if len(parts) < 3 or parts[1] not in ("purelib", "platlib"):
                        raise ValueError("An unsupported wheel installation path was found.")
                    relative = Path(*parts[2:])
                name = "python/Lib/site-packages/" + relative.as_posix()
                if name in files:
                    raise ValueError("Windows dependencies have conflicting file paths.")
                files[name] = wheel.read(item)
    return files


def helpers() -> dict[str, bytes]:
    files = {name: (HELPERS / name).read_bytes() for name in (
        "abrir_tokyo.py", "INICIAR_TOKYO.cmd", "LEIA_PRIMEIRO.txt",
        "IMPORTAR_DADOS_REAIS.cmd", "desbloquear_tokyo.py",
    )}
    importer = (PORTABLE / "importar_dados.py").read_bytes()
    original = b'DATABASE_NAME = "tokyo-demonstracao.sqlite3"'
    if importer.count(original) != 1:
        raise ValueError("The shared importer no longer has the expected database configuration.")
    files["importar_dados.py"] = importer.replace(original, b'DATABASE_NAME = "tokyo.sqlite3"')
    return {
        name: data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n") if name.endswith(".cmd") else data
        for name, data in files.items()
    }


def build(args) -> dict:
    app, output = args.source.resolve(), args.output.resolve()
    if output.is_relative_to(app):
        raise ValueError("The distributable must be written outside the private source tree.")
    files, manifest, references = payload_files(app, args.references_manifest)
    key, key_text = read_key(args.key_file)
    protected = protect(files, key)
    provenance = json.loads((PORTABLE / "python-source.json").read_text(encoding="utf-8"))
    python_bytes = verified_python_package(args.python_package.resolve(), provenance)
    dependencies = json.loads((HELPERS / "crypto-dependencies.json").read_text(encoding="utf-8"))
    runtime = runtime_files(python_bytes, [dependency_bytes(args.wheel_cache, item) for item in dependencies["wheels"]])
    summary = {
        "application_version": json.loads((app / "package.json").read_text(encoding="utf-8"))["version"],
        "mode": "working_encrypted", "manifest_entries": len(manifest),
        "protected_files": len(files), "reference_attachments": references,
        "python_version": provenance["version"], "python_sha512_verified": True,
        "windows_dependencies_sha256_verified": True,
        "windows_native_execution_tested": False,
        "source_manifest_sha256": hashlib.sha256((app / "manifest.json").read_bytes()).hexdigest(),
        "protected_payload_sha256": hashlib.sha256(protected).hexdigest(),
        "database_included": False, "key_included": False,
    }
    if args.verify_only:
        return {**summary, "verification_only": True, "archive_written": False}
    outer = helpers()
    outer.update(runtime)
    outer["pacote-tokyo.enc"] = protected
    outer["ORIGEM_PYTHON.json"] = json_bytes(provenance)
    outer["DEPENDENCIAS_PYTHON.json"] = json_bytes(dependencies)
    outer["PYTHON_ARQUIVOS_SHA256.json"] = json_bytes([
        {"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        for name, data in sorted(runtime.items())
    ])
    outer["BUILD_INFO.json"] = json_bytes({
        **summary, "initial_seed_data": "encrypted_uploaded_snapshots",
        "runtime_files": len(runtime), "encryption": "AES-256-GCM",
        "payload_format": "Tokyo-Trabalho-v1",
    })
    # Public outer contents are generic helpers, runtime, hashes and ciphertext.
    # No source folder, attachment list, SQLite file or access key is included.
    for name, data in outer.items():
        parts = PurePosixPath(name).parts
        if any(part in ("app", "referencias", "private", "node_modules", "__pycache__") for part in parts):
            raise ValueError("A private source path entered the public package.")
        if ".sqlite" in name.lower() or name.lower().endswith((".db", ".env", ".log")):
            raise ValueError("A private data file entered the public package.")
        if key in data or key_text in data:
            raise ValueError("The access key must never enter the public package.")
    output.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix="tokyo-working-", suffix=".zip", dir=output.parent)
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(temporary, "w") as archive:
            for name, data in sorted(outer.items()):
                add_bytes(archive, ROOT + name, data)
        with zipfile.ZipFile(temporary) as archive:
            if archive.testzip() is not None or set(archive.namelist()) != {ROOT + name for name in outer}:
                raise ValueError("The generated distributable failed integrity verification.")
            for name, data in outer.items():
                if archive.read(ROOT + name) != data:
                    raise ValueError("A distributed file failed byte-for-byte verification.")
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return {**summary, "archive": str(output), "bytes": output.stat().st_size,
            "sha256": hashlib.sha256(output.read_bytes()).hexdigest(), "outer_files": len(outer)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Private complete working-app source directory.")
    parser.add_argument("--references-manifest", type=Path, help="Private JSON list of supplied attachments.")
    parser.add_argument("--key-file", type=Path, required=True, help="Private base64url 32-byte key file; never printed or packaged.")
    parser.add_argument("--output", type=Path, default=REPOSITORY / "downloads/Fluxo-de-Caixa-Tokyo-Trabalho.zip")
    parser.add_argument("--python-package", type=Path, default=Path(tempfile.gettempdir()) / "tokyo-portable-cache/python-3.13.16.nupkg")
    parser.add_argument("--wheel-cache", type=Path, default=Path(tempfile.gettempdir()) / "tokyo-working-wheels")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(json.dumps(build(args), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
