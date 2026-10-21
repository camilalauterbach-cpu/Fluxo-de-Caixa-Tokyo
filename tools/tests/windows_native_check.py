"""Native startup smoke check: public code and random fixtures only, no real key."""
from __future__ import annotations
import argparse, base64, ctypes, hashlib, importlib.util, json, os, shutil
import socket, subprocess, sys, tempfile, time
from ctypes import wintypes
from pathlib import Path


def load_helper(root):
    spec = importlib.util.spec_from_file_location('tokyo_native_unlock', root / 'desbloquear_tokyo.py')
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    return helper


def kill_tree(process):
    if process.poll() is None:
        subprocess.run(['taskkill.exe', '/PID', str(process.pid), '/T', '/F'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        process.wait(timeout=5)


def check_gui(root):
    process = subprocess.Popen([sys.executable, '-I', '-B', str(Path(__file__).resolve()),
                                '--root', str(root), '--ask-key'], stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        user32 = ctypes.WinDLL('user32', use_last_error=True)
        user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        user32.FindWindowW.restype = wintypes.HWND
        user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                              wintypes.LPARAM, wintypes.UINT, wintypes.UINT,
                                              ctypes.POINTER(ctypes.c_size_t)]
        caption = 'Tokyo - Native QA ' + str(process.pid)
        deadline = time.monotonic() + 5
        window = None
        while time.monotonic() < deadline and process.poll() is None:
            window = user32.FindWindowW(None, caption)
            if window:
                break
            time.sleep(0.1)
        if not window:
            return {'status': 'skipped', 'reason': 'WinForms window unavailable on this runner desktop'}
        controls = {}
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def inspect(handle, _):
            class_name, text = ctypes.create_unicode_buffer(256), ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(handle, class_name, 256)
            user32.GetWindowTextW(handle, text, 256)
            if 'EDIT' in class_name.value.upper():
                controls['edit'] = handle
            if text.value == 'Abrir Tokyo':
                controls['button'] = handle
            return True
        callback = callback_type(inspect)
        user32.EnumChildWindows.argtypes = [wintypes.HWND, callback_type, wintypes.LPARAM]
        user32.EnumChildWindows(window, callback, 0)
        assert set(controls) == {'edit', 'button'}, 'Native key controls are missing'
        fixture = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip('=')
        buffer, result = ctypes.create_unicode_buffer(fixture), ctypes.c_size_t()
        assert user32.SendMessageTimeoutW(controls['edit'], 0x000C, 0, ctypes.cast(buffer, ctypes.c_void_p).value,
                                         2, 1500, ctypes.byref(result)), 'Native paste failed'
        assert user32.SendMessageTimeoutW(controls['button'], 0x00F5, 0, 0, 2, 1500,
                                         ctypes.byref(result)), 'Native submit failed'
        output, _ = process.communicate(timeout=5)
        assert process.returncode == 0, 'Native key helper failed'
        assert json.loads(output)['digest'] == hashlib.sha256(fixture.encode()).hexdigest(), 'Native pasted key changed'
        assert fixture.encode() not in output, 'Fixture key must not be logged'
        return {'status': 'passed'}
    finally:
        kill_tree(process)


def check_console(root):
    with tempfile.TemporaryDirectory(prefix='tokyo console (QA) ') as temporary:
        launcher = Path(temporary) / 'INICIAR_TOKYO.cmd'
        shutil.copyfile(root / 'INICIAR_TOKYO.cmd', launcher)
        # No Python is copied: exercise the real missing-runtime branch.
        command = '"' + os.environ['ComSpec'] + '" /D /C ""' + str(launcher) + '""'
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            time.sleep(2)
            assert process.poll() is None, 'CMD closed instead of preserving the error window'
            output, _ = process.communicate(input=b'exit\r\n', timeout=5)
            assert b'Python incluido nao foi encontrado' in output, 'Expected missing-runtime message was not reached'
            assert b'Esta janela continua aberta' in output, 'Persistent-console message missing'
        finally:
            kill_tree(process)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--ask-key', action='store_true')
    args = parser.parse_args()
    assert sys.platform == 'win32', 'This check requires actual Windows'
    helper = load_helper(args.root)
    if args.ask_key:
        helper.WINDOWS_KEY_DIALOG = helper.WINDOWS_KEY_DIALOG.replace(
            'Tokyo - Primeira abertura', 'Tokyo - Native QA ' + str(os.getpid()))
        key = helper.ask_key()
        print(json.dumps({'digest': hashlib.sha256(key.encode()).hexdigest()}))
        return
    assert sys.flags.isolated, 'Run the bundled Python with -I'
    checks = []
    print('::notice title=Windows native stage::native_crypto', flush=True)
    AESGCM, _ = helper.crypto_primitives()
    key, nonce, fixture = os.urandom(32), os.urandom(12), b'public synthetic native fixture'
    encoded = base64.urlsafe_b64encode(key).decode().rstrip('=')
    payload = helper.MAGIC + nonce + AESGCM(key).encrypt(nonce, fixture, helper.AAD)
    assert helper.decrypt(payload, encoded) == fixture, 'Native cryptography round-trip failed'
    checks.append('bundled_native_crypto')
    print('::notice title=Windows native stage::port_guards', flush=True)
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    helper.require_stopped(port)
    checks.append('free_port_accepted')
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0)); listener.listen()
        try:
            helper.require_stopped(listener.getsockname()[1])
        except helper.UnlockError:
            pass
        else:
            raise AssertionError('Occupied port was accepted')
    checks.append('occupied_port_rejected')
    print('::notice title=Windows native stage::powershell_parser', flush=True)
    script = base64.b64encode(helper.WINDOWS_KEY_DIALOG.encode()).decode()
    parse = "$t=$null;$e=$null;[void][System.Management.Automation.Language.Parser]::ParseInput([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('" + script + "')),[ref]$t,[ref]$e);if($e.Count){exit 1}"
    command = base64.b64encode(parse.encode('utf-16le')).decode()
    parsed = subprocess.run(['powershell.exe', '-NoLogo', '-NoProfile', '-EncodedCommand', command],
                            capture_output=True, timeout=10)
    assert parsed.returncode == 0, 'Native PowerShell parser rejected the dialog'
    checks.append('native_powershell_syntax')
    print('::notice title=Windows native stage::real_key_dialog', flush=True)
    gui = check_gui(args.root)
    print('::notice title=Windows native stage::persistent_console', flush=True)
    check_console(args.root)
    checks.append('persistent_cmd_missing_runtime')
    print(json.dumps({'passed': checks, 'key_dialog': gui, 'real_payload_decrypted': False,
                      'real_key_read': False, 'platform': sys.platform, 'python': sys.version.split()[0]}))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        details = type(error).__name__ + ': ' + str(error)
        if error.__cause__ is not None:
            details += '; cause=' + type(error.__cause__).__name__ + ': ' + str(error.__cause__)
        print('::error title=Windows native startup::' + details.replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A'), flush=True)
        raise
