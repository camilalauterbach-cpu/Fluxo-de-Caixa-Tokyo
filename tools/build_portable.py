#!/usr/bin/env python3
"""Build Tokyo's Windows portable ZIP from demo/ without packaging user data."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

REPOSITORY = Path(__file__).resolve().parents[1]
HELPERS = REPOSITORY / "tools" / "portable"
EXCLUDED_DIRECTORIES = {
    ".git", ".cache", ".venv", "venv", "node_modules", "private", "runtime",
    "validation", "__pycache__", "test-results", "playwright-report",
}
EXCLUDED_SUFFIXES = {".db", ".pyc", ".pyo", ".log", ".wal", ".shm", ".zip", ".nupkg", ".bak", ".backup"}


def safe_relative(name: str) -> Path:
    parsed = PurePosixPath(name)
    if parsed.is_absolute() or ".." in parsed.parts or "\\" in name or not parsed.parts:
        raise ValueError(f"Unsafe archive path: {name}")
    return Path(*parsed.parts)


def permitted(path: Path) -> bool:
    if any(part in EXCLUDED_DIRECTORIES for part in path.parts):
        return False
    return not (
        path.name == ".env" or path.name.startswith(".env.")
        or path.suffix.lower() in EXCLUDED_SUFFIXES
        or ".sqlite" in path.name.lower()
    )


def source_files(app: Path) -> list[Path]:
    files = []
    for directory, subdirectories, names in os.walk(app):
        subdirectories[:] = sorted(d for d in subdirectories if d not in EXCLUDED_DIRECTORIES)
        base = Path(directory)
        for child in subdirectories:
            if (base / child).is_symlink():
                raise ValueError(f"Source symlink must not enter the package: {base / child}")
        for name in sorted(names):
            full = base / name
            relative = full.relative_to(app)
            if not permitted(relative):
                continue
            if full.is_symlink():
                raise ValueError(f"Source symlink must not enter the package: {full}")
            files.append(relative)
    return sorted(files)


def verify_manifest(app: Path, files: list[Path]) -> list[dict]:
    manifest = json.loads((app / "manifest.json").read_text(encoding="utf-8"))
    if not isinstance(manifest, list) or not manifest:
        raise ValueError("demo/manifest.json must contain the current nonempty file list.")
    included = set(files)
    seen = set()
    for entry in manifest:
        relative = safe_relative(entry["path"])
        if relative in seen:
            raise ValueError(f"Duplicate manifest path: {relative}")
        seen.add(relative)
        if relative not in included:
            raise ValueError(f"Manifest path is missing or excluded from the package: {relative}")
        data = (app / relative).read_bytes()
        if len(data) != entry["bytes"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise ValueError(f"Manifest mismatch; build and refresh the manifest first: {relative}")
    return manifest


def verified_python_package(package: Path, provenance: dict) -> bytes:
    if not package.exists():
        package.parent.mkdir(parents=True, exist_ok=True)
        url = provenance["download"]
        if not url.startswith("https://api.nuget.org/"):
            raise ValueError("Python must come from the pinned official HTTPS NuGet source.")
        with urllib.request.urlopen(url, timeout=60) as response:
            contents = response.read()
        verify_python_bytes(contents, provenance)
        temporary = package.with_suffix(package.suffix + ".download")
        temporary.write_bytes(contents)
        temporary.replace(package)
    contents = package.read_bytes()
    verify_python_bytes(contents, provenance)
    return contents


def verify_python_bytes(contents: bytes, provenance: dict) -> None:
    if provenance["publisher"] != "Python Software Foundation" or provenance["package_hash_algorithm"] != "SHA512":
        raise ValueError("Unexpected Python publisher or integrity algorithm.")
    digest = base64.b64encode(hashlib.sha512(contents).digest()).decode("ascii")
    if len(contents) != provenance["package_size"] or digest != provenance["package_hash_base64"]:
        raise ValueError("Python package integrity failed; the package was not used.")


def add_bytes(archive: zipfile.ZipFile, name: str, data: bytes) -> None:
    safe_relative(name)
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = (0o100755 if name.endswith(".exe") else 0o100644) << 16
    archive.writestr(info, data, compresslevel=9)


def json_bytes(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def build(app: Path, package: Path, output: Path, verify_only: bool) -> dict:
    if output.is_relative_to(app):
        raise ValueError("The portable ZIP must be written outside demo/.")
    files = source_files(app)
    manifest = verify_manifest(app, files)
    provenance = json.loads((HELPERS / "python-source.json").read_text(encoding="utf-8"))
    python_bytes = verified_python_package(package, provenance)
    summary = {
        "application_version": json.loads((app / "package.json").read_text(encoding="utf-8"))["version"],
        "manifest_entries": len(manifest), "source_files": len(files),
        "python_version": provenance["version"], "python_sha512_verified": True,
        "windows_native_execution_tested": False,
    }
    if verify_only:
        return {**summary, "verification_only": True, "archive_written": False}
    output.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix="tokyo-portable-", suffix=".zip", dir=output.parent)
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(temporary, "w") as archive:
            for relative in files:
                add_bytes(archive, "Tokyo-Teste/app/" + relative.as_posix(), (app / relative).read_bytes())
            for name in [
                "abrir_tokyo.py", "INICIAR_TOKYO.cmd", "LEIA_PRIMEIRO.txt",
                "importar_dados.py", "IMPORTAR_DADOS_REAIS.cmd",
            ]:
                data = (HELPERS / name).read_bytes()
                if name.endswith(".cmd"):
                    data = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
                add_bytes(archive, "Tokyo-Teste/" + name, data)
            import io
            runtime_hashes = []
            with zipfile.ZipFile(io.BytesIO(python_bytes)) as runtime:
                for item in sorted(runtime.infolist(), key=lambda item: item.filename):
                    if item.is_dir() or not item.filename.startswith("tools/"):
                        continue
                    relative = safe_relative(item.filename.removeprefix("tools/"))
                    data = runtime.read(item)
                    name = "python/" + relative.as_posix()
                    add_bytes(archive, "Tokyo-Teste/" + name, data)
                    runtime_hashes.append({"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
            add_bytes(archive, "Tokyo-Teste/ORIGEM_PYTHON.json", json_bytes(provenance))
            add_bytes(archive, "Tokyo-Teste/PYTHON_ARQUIVOS_SHA256.json", json_bytes(runtime_hashes))
            add_bytes(archive, "Tokyo-Teste/BUILD_INFO.json", json_bytes({
                **summary, "source": "demo/", "initial_seed_data": "synthetic",
                "source_manifest_sha256": hashlib.sha256((app / "manifest.json").read_bytes()).hexdigest(),
                "database_included": False, "runtime_files": len(runtime_hashes),
            }))
        with zipfile.ZipFile(temporary) as archive:
            if archive.testzip() is not None:
                raise ValueError("The generated archive failed its CRC integrity check.")
            for entry in manifest:
                data = archive.read("Tokyo-Teste/app/" + entry["path"])
                if hashlib.sha256(data).hexdigest() != entry["sha256"]:
                    raise ValueError(f"Packaged source differs from the manifest: {entry['path']}")
            for name in archive.namelist():
                parts = PurePosixPath(name).parts
                if "private" in parts or ".sqlite" in name.lower() or name.lower().endswith(".db"):
                    raise ValueError("A private data path entered the archive.")
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return {**summary, "archive": str(output), "bytes": output.stat().st_size,
            "sha256": hashlib.sha256(output.read_bytes()).hexdigest()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=REPOSITORY / "demo")
    parser.add_argument("--output", type=Path, default=REPOSITORY / "downloads" / "Fluxo-de-Caixa-Tokyo-Testar.zip")
    parser.add_argument("--python-package", type=Path, help="Existing official PSF NuGet package; SHA-512 is always verified.")
    parser.add_argument("--verify-only", action="store_true", help="Check source manifest and Python integrity without replacing the ZIP.")
    args = parser.parse_args()
    package = args.python_package or Path(tempfile.gettempdir()) / "tokyo-portable-cache" / "python-3.13.16.nupkg"
    result = build(args.source.resolve(), package.resolve(), args.output.resolve(), args.verify_only)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
