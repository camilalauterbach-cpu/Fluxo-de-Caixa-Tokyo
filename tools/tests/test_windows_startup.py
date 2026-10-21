"""Portable tests for first-start behavior; these do not execute Windows APIs."""
from __future__ import annotations

import base64
from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("tokyo_windows_startup", ROOT / "tools/working/desbloquear_tokyo.py")
startup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(startup)


class WindowsFirstStartTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="tokyo-startup-test-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.key = base64.urlsafe_b64encode(os.urandom(32)).decode("ascii").rstrip("=")

    def response(self, value=None, *, stdout=None, returncode=0):
        return subprocess.CompletedProcess([], returncode, json.dumps(value) if stdout is None else stdout, "")

    def ask_windows(self, completed, console_value=None):
        with patch.object(startup.sys, "platform", "win32"), patch.object(startup.subprocess, "run", return_value=completed) as run, patch("builtins.input", return_value=console_value) as console, redirect_stdout(io.StringIO()) as output:
            result = startup.ask_key()
        return result, run, console, output.getvalue()

    def read_log(self):
        path = self.base / "private/inicializacao.log"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def test_dialog_success_keeps_key_out_of_process_arguments_and_output(self):
        result, run, console, output = self.ask_windows(self.response({"status": "ok", "key": " " + self.key + "\r\n"}))
        self.assertEqual(result, self.key)
        console.assert_not_called()
        arguments, options = run.call_args
        command = arguments[0]
        self.assertEqual(command[0], "powershell.exe")
        self.assertIn("-STA", command)
        self.assertIn("-NoProfile", command)
        self.assertEqual(command[-1], startup.WINDOWS_KEY_DIALOG)
        self.assertNotIn(self.key, repr(run.call_args))
        self.assertNotIn(self.key, output)
        self.assertFalse(any("bypass" in argument.casefold() for argument in command))
        self.assertEqual(options["stdin"], subprocess.DEVNULL)
        self.assertEqual(options["stdout"], subprocess.PIPE)
        self.assertEqual(options["stderr"], subprocess.PIPE)
        self.assertFalse(options["check"])
        self.assertNotIn("shell", options)

    def test_dialog_cancel_does_not_fall_back_to_another_prompt(self):
        with patch.object(startup.sys, "platform", "win32"), patch.object(startup.subprocess, "run", return_value=self.response({"status": "cancelled"})), patch("builtins.input", side_effect=AssertionError("cancel must stop")):
            with self.assertRaises(startup.UnlockCancelled):
                startup.ask_key()

    def test_malformed_or_failed_dialog_falls_back_without_showing_child_output(self):
        variants = [
            self.response(stdout="not JSON " + self.key),
            self.response([self.key]),
            self.response({"status": "unknown", "key": self.key}),
            self.response({"status": "ok", "key": 123}),
            self.response({"status": "ok", "key": "invalid-key"}),
            self.response({"status": "ok", "key": self.key}, returncode=1),
        ]
        for completed in variants:
            with self.subTest(response_type=type(completed.stdout).__name__, returncode=completed.returncode):
                result, _, console, output = self.ask_windows(completed, console_value=self.key)
                self.assertEqual(result, self.key)
                console.assert_called_once()
                self.assertNotIn(self.key, output)
                self.assertNotIn(completed.stdout, output)

    def test_missing_powershell_or_decoding_failure_uses_console(self):
        errors = [FileNotFoundError("synthetic missing PowerShell"), UnicodeDecodeError("utf8", b"\xff", 0, 1, "synthetic invalid child output")]
        for error in errors:
            with self.subTest(kind=type(error).__name__):
                with patch.object(startup.sys, "platform", "win32"), patch.object(startup.subprocess, "run", side_effect=error), patch("builtins.input", return_value=self.key), redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(startup.ask_key(), self.key)
                self.assertNotIn(self.key, output.getvalue())

    def test_cooked_console_input_consumes_entire_crlf_and_preserves_next_line(self):
        incoming = io.StringIO("  " + self.key + "  \r\nNEXT-LINE\n")
        with patch.object(startup.sys, "platform", "linux"), patch.object(startup.sys, "stdin", incoming), redirect_stdout(io.StringIO()) as output, patch.object(startup.subprocess, "run", side_effect=AssertionError("console path cannot launch PowerShell")):
            self.assertEqual(startup.ask_key(), self.key)
        self.assertEqual(incoming.read(), "NEXT-LINE\n")
        self.assertNotIn(self.key, output.getvalue())

    def test_crypto_failure_occurs_before_prompt_or_application_changes(self):
        (self.base / startup.PAYLOAD_NAME).write_bytes(startup.MAGIC + b"0" * 12 + b"0" * 16)
        existing = self.base / "private"
        existing.mkdir()
        (existing / "tokyo.sqlite3").write_bytes(b"synthetic existing database")
        with patch.object(startup, "require_stopped"), patch.object(startup, "crypto_primitives", side_effect=startup.UnlockError("Synthetic crypto unavailable")), patch.object(startup, "ask_key", side_effect=AssertionError("components must be checked first")) as ask:
            with self.assertRaises(startup.UnlockError):
                startup.unlock(base=self.base)
        ask.assert_not_called()
        self.assertEqual((existing / "tokyo.sqlite3").read_bytes(), b"synthetic existing database")
        self.assertFalse((self.base / "app").exists())
        self.assertEqual([entry["stage"] for entry in self.read_log()], ["verificando_componentes"])

    def test_success_returns_zero_and_records_no_sensitive_data(self):
        with patch.object(startup, "BASE", self.base), patch.object(startup, "unlock", return_value={"installed": False, "backup": None}), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(startup.main(), 0)
        self.assertEqual([entry["stage"] for entry in self.read_log()], ["inicio", "ja_preparado"])
        self.assertNotIn(self.key, output.getvalue())

    def test_cancel_and_keyboard_interrupt_return_two_without_exception_text(self):
        for error in [startup.UnlockCancelled(self.key), KeyboardInterrupt(self.key)]:
            with self.subTest(kind=type(error).__name__):
                with patch.object(startup, "BASE", self.base), patch.object(startup, "unlock", side_effect=error), redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(startup.main(), 2)
                self.assertNotIn(self.key, output.getvalue())
                entry = self.read_log()[-1]
                self.assertEqual(entry["stage"], "cancelado")
                self.assertEqual(entry["error_type"], type(error).__name__)
                self.assertNotIn(self.key, json.dumps(self.read_log()))

    def test_unexpected_error_returns_one_without_logging_exception_details(self):
        error = OSError("synthetic sensitive data " + self.key)
        error.winerror = 126
        with patch.object(startup, "BASE", self.base), patch.object(startup, "unlock", side_effect=error), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(startup.main(), 1)
        self.assertNotIn(self.key, output.getvalue())
        self.assertIn("private/inicializacao.log", output.getvalue())
        log = self.read_log()
        self.assertNotIn(self.key, json.dumps(log))
        self.assertEqual(log[-1]["stage"], "falha")
        self.assertEqual(log[-1]["error_type"], "OSError")
        self.assertEqual(log[-1]["winerror"], 126)
        self.assertEqual(set(log[-1]), {"utc", "stage", "error_type", "winerror"})


if __name__ == "__main__":
    unittest.main()
