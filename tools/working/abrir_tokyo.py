"""Open the unlocked Tokyo working package with its own local database."""
from __future__ import annotations

import argparse
import importlib.util
import json
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

BASE = Path(__file__).resolve().parent
APP = BASE / "app"
PRIVATE = BASE / "private"
DATABASE = PRIVATE / "tokyo.sqlite3"


def interrupt(signum, frame):
    raise KeyboardInterrupt


def identities():
    spec = importlib.util.spec_from_file_location("tokyo_launch_identity", APP / "server" / "identity.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build_id(APP), module.instance_id(DATABASE)


def health(opener, url):
    try:
        with opener.open(url + "/api/health", timeout=1) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError("A porta escolhida esta sendo usada por outro programa. Nenhum processo foi encerrado.") from error
    except (ConnectionError, urllib.error.URLError, TimeoutError):
        return None
    except (ValueError, json.JSONDecodeError) as error:
        raise RuntimeError("A resposta da porta escolhida nao pertence a esta instalacao Tokyo.") from error


def verify_health(value, expected_build, expected_instance):
    expected_version = json.loads((APP / "package.json").read_text(encoding="utf-8"))["version"]
    if not isinstance(value, dict) or value.get("application") != "Tokyo" or value.get("version") != expected_version or value.get("build_id") != expected_build or value.get("instance_id") != expected_instance:
        raise RuntimeError("A porta escolhida pertence a outra instalacao ou versao. Ela foi preservada; nenhuma base foi substituida.")


def stop_own_process(process):
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def main():
    if sys.version_info < (3, 12):
        raise RuntimeError("Este pacote requer Python 3.12 ou posterior. Use INICIAR_TOKYO.cmd para executar o Python incluido.")
    parser = argparse.ArgumentParser(description="Abrir a instalacao Tokyo Tokyo sem alterar a instalacao existente.")
    parser.add_argument("--port", type=int, default=8845)
    parser.add_argument("--no-browser", action="store_true", help="Nao abrir o navegador; usado na validacao automatica.")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        raise RuntimeError("A porta informada e invalida.")
    if not (APP / "dist" / "index.html").is_file():
        raise RuntimeError("Extraia todo o ZIP antes de abrir. A pasta app/dist nao foi encontrada.")
    PRIVATE.mkdir(parents=True, exist_ok=True)
    expected_build, expected_instance = identities()
    url = f"http://127.0.0.1:{args.port}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    current = health(opener, url)
    if current is not None:
        verify_health(current, expected_build, expected_instance)
        print("A instalacao Tokyo desta pasta ja esta aberta. Reutilizando sem alterar os dados.", flush=True)
        if not args.no_browser:
            webbrowser.open(url, new=2)
        print("Endereco neste computador: " + url, flush=True)
        return 0
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", args.port)) == 0:
            raise RuntimeError("A porta escolhida esta ocupada por outro programa. Nenhum processo foi encerrado.")
    process = None
    try:
        with (PRIVATE / "servidor.log").open("ab") as log:
            options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if sys.platform == "win32" else {"start_new_session": True}
            process = subprocess.Popen(
                [sys.executable, "-I", "-B", "-u", str(APP / "server" / "app.py"), "--port", str(args.port), "--db", str(DATABASE)],
                cwd=APP, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, **options,
            )
            for attempt in range(150):
                if process.poll() is not None:
                    raise RuntimeError("O servidor nao iniciou. Consulte private/servidor.log.")
                current = health(opener, url)
                if current is not None:
                    verify_health(current, expected_build, expected_instance)
                    break
                time.sleep(0.1)
            else:
                raise RuntimeError("O servidor demorou para iniciar. Consulte private/servidor.log.")
            print("TOKYO - versao de trabalho local", flush=True)
            print("Os dados usados sao os da base local desta pasta.", flush=True)
            print("Pronto. Endereco neste computador: " + url, flush=True)
            print("Mantenha esta janela aberta enquanto usa o aplicativo.", flush=True)
            print("Para encerrar, pressione Ctrl+C nesta janela. Seus dados serao preservados nesta pasta.", flush=True)
            if not args.no_browser:
                opened = webbrowser.open(url, new=2)
                if not opened:
                    print("O navegador nao abriu automaticamente. Copie o endereco acima para Chrome ou Edge.", flush=True)
            process.wait()
            if process.returncode:
                raise RuntimeError("O servidor foi encerrado. Consulte private/servidor.log.")
    except KeyboardInterrupt:
        print("\nEncerrando somente esta instalacao Tokyo. Dados locais preservados.", flush=True)
    finally:
        stop_own_process(process)
    return 0


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, interrupt)
    try:
        raise SystemExit(main())
    except Exception as error:
        print("Nao foi possivel abrir o Tokyo: " + str(error), flush=True)
        raise SystemExit(1)
