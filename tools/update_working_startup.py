#!/usr/bin/env python3
"""Update only Tokyo's startup helpers, preserving the encrypted payload exactly.

No private source, package key or cryptography dependency is needed. The full
download is replaced atomically after byte-for-byte verification; a separate
small ZIP repairs an already-extracted Tokyo-Trabalho installation.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import zipfile

from build_portable import REPOSITORY, add_bytes, json_bytes

HELPERS = REPOSITORY / "tools" / "working"
ROOT = "Tokyo-Trabalho/"
PAYLOAD = ROOT + "pacote-tokyo.enc"
BUILD_INFO = ROOT + "BUILD_INFO.json"
REPLACED = {ROOT + name for name in (
    "INICIAR_TOKYO.cmd", "desbloquear_tokyo.py", "LEIA_PRIMEIRO.txt",
)} | {BUILD_INFO}


def source_helpers(directory: Path) -> dict[str, bytes]:
    files = {}
    for name in ("INICIAR_TOKYO.cmd", "desbloquear_tokyo.py", "LEIA_PRIMEIRO.txt"):
        data = (directory / name).read_bytes()
        if name.endswith(".cmd"):
            data = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        files[ROOT + name] = data
    return files


def temporary_zip(destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(prefix="tokyo-startup-", suffix=".zip", dir=destination.parent)
    os.close(handle)
    return Path(name)


def archive_summary(path: Path) -> dict:
    return {"archive": str(path), "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def update(full: Path, patch: Path, helpers: Path = HELPERS) -> dict:
    full, patch = full.resolve(), patch.resolve()
    if full == patch:
        raise ValueError("The full download and small correction ZIP must use different paths.")
    replacement = source_helpers(helpers)
    patch_files = {
        "INICIAR_TOKYO.cmd": replacement[ROOT + "INICIAR_TOKYO.cmd"],
        "desbloquear_tokyo.py": replacement[ROOT + "desbloquear_tokyo.py"],
        "LEIA_CORRECAO.txt": (helpers / "LEIA_CORRECAO.txt").read_bytes(),
    }
    full_temporary = temporary_zip(full)
    patch_temporary = temporary_zip(patch)
    try:
        with zipfile.ZipFile(full) as original:
            names = original.namelist()
            if len(names) != len(set(names)) or not REPLACED | {PAYLOAD} <= set(names):
                raise ValueError("The original working download is incomplete or has duplicate members.")
            if original.testzip() is not None:
                raise ValueError("The original working download failed CRC verification.")
            payload_sha256 = hashlib.sha256(original.read(PAYLOAD)).hexdigest()
            metadata = json.loads(original.read(BUILD_INFO))
            if metadata.get("protected_payload_sha256") != payload_sha256:
                raise ValueError("The original payload differs from its recorded SHA256.")
            metadata["startup_revision"] = 2
            replacement[BUILD_INFO] = json_bytes(metadata)
            original_hashes = {name: hashlib.sha256(original.read(name)).digest() for name in names}
            with zipfile.ZipFile(full_temporary, "w") as updated:
                updated.comment = original.comment
                for item in original.infolist():
                    data = replacement.get(item.filename)
                    if data is None:
                        data = original.read(item)
                    updated.writestr(copy.copy(item), data, compresslevel=9)
            with zipfile.ZipFile(full_temporary) as updated:
                if updated.namelist() != names or updated.testzip() is not None:
                    raise ValueError("The corrected working download failed member or CRC verification.")
                for name in names:
                    data = updated.read(name)
                    if name in replacement:
                        if data != replacement[name]:
                            raise ValueError("A corrected startup helper differs from its source.")
                    elif hashlib.sha256(data).digest() != original_hashes[name]:
                        raise ValueError("An application, runtime or payload member was modified.")
                if updated.read(PAYLOAD) != original.read(PAYLOAD):
                    raise ValueError("The encrypted payload was modified.")
            with zipfile.ZipFile(patch_temporary, "w") as correction:
                for name, data in patch_files.items():
                    add_bytes(correction, name, data)
            with zipfile.ZipFile(patch_temporary) as correction:
                if correction.testzip() is not None or set(correction.namelist()) != set(patch_files):
                    raise ValueError("The small correction ZIP failed member or CRC verification.")
                if any(correction.read(name) != data for name, data in patch_files.items()):
                    raise ValueError("A small correction helper differs from its source.")
        # Both replacements are ready and checked before publishing either one.
        # os.replace keeps the prior full download intact on any earlier error.
        os.replace(patch_temporary, patch)
        os.replace(full_temporary, full)
    finally:
        full_temporary.unlink(missing_ok=True)
        patch_temporary.unlink(missing_ok=True)
    return {
        "startup_revision": 2, "full": archive_summary(full), "correction": archive_summary(patch),
        "protected_payload_sha256": payload_sha256, "encrypted_payload_unchanged": True,
        "unchanged_members_verified": len(names) - len(REPLACED),
        "replaced_members": len(REPLACED), "correction_members": len(patch_files),
        "key_required": False, "database_or_runtime_modified": False,
        "windows_native_execution_tested": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update", type=Path,
                        default=REPOSITORY / "downloads/Fluxo-de-Caixa-Tokyo-Trabalho.zip",
                        help="Existing full ZIP to update atomically.")
    parser.add_argument("--patch-out", type=Path,
                        default=REPOSITORY / "downloads/Tokyo-Correcao-Abertura.zip",
                        help="Small correction ZIP for the already-extracted application.")
    args = parser.parse_args()
    print(json.dumps(update(args.update, args.patch_out), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
