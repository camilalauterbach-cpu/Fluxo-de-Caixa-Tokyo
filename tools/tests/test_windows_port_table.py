"""Exercise the Windows listener-table contract without executing Windows APIs."""
from __future__ import annotations

import ctypes
import importlib.util
from pathlib import Path
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("tokyo_windows_ports", ROOT / "tools/working/desbloquear_tokyo.py")
ports = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ports)


def listener_table(family, listening_ports):
    size, offset = (24, 8) if family == 2 else (56, 20)
    data = bytearray(struct.pack("<I", len(listening_ports)))
    for port in listening_ports:
        row = bytearray(size)
        struct.pack_into("!H", row, offset, port)
        # Real MIB rows carry state LISTEN (2) and owner PID. They are not
        # ctypes.Structure instances, avoiding host-Linux DWORD-size artifacts.
        struct.pack_into("<I", row, 0 if family == 2 else 48, 2)
        struct.pack_into("<I", row, size - 4, 1234)
        data.extend(row)
    return bytes(data)


class FakeTcpTable:
    def __init__(self, tables=None, *, errors=None, resize_family=None, bad_size=None):
        self.tables = tables or {2: listener_table(2, []), 23: listener_table(23, [])}
        self.errors = errors or {}
        self.resize_family = resize_family
        self.bad_size = bad_size
        self.resized = False
        self.calls = []

    def __call__(self, buffer, size_pointer, sorted_rows, family, table_class, reserved):
        size = ctypes.cast(size_pointer, ctypes.POINTER(ctypes.c_uint32)).contents
        self.calls.append((buffer is not None, family, table_class, reserved, size.value))
        if (family, buffer is not None) in self.errors:
            return self.errors[(family, buffer is not None)]
        data = self.tables.get(family, listener_table(family, []))
        if buffer is None:
            size.value = self.bad_size if self.bad_size is not None else (4 if self.resize_family == family else len(data))
            return 122
        if self.resize_family == family and not self.resized:
            self.resized = True
            size.value = len(data)
            return 122
        if len(buffer) < len(data):
            raise AssertionError("Production code did not resize the native buffer")
        ctypes.memmove(buffer, data, len(data))
        size.value = len(data)
        return 0


class WindowsPortTableTests(unittest.TestCase):
    def mock_api(self, api):
        return patch.object(ctypes, "WinDLL", return_value=SimpleNamespace(GetExtendedTcpTable=api), create=True)

    def test_empty_ipv4_and_ipv6_tables_report_free_port(self):
        api = FakeTcpTable()
        with self.mock_api(api) as load:
            self.assertFalse(ports.windows_port_listening(8845))
        load.assert_called_once_with("iphlpapi.dll", use_last_error=True)
        self.assertEqual([call[:4] for call in api.calls], [(False, 2, 3, 0), (True, 2, 3, 0), (False, 23, 3, 0), (True, 23, 3, 0)])
        self.assertEqual(ctypes.sizeof(api.restype), 4)
        self.assertIs(api.argtypes[1]._type_, ctypes.c_uint32)

    def test_ipv4_target_is_found_after_a_different_listener(self):
        api = FakeTcpTable({2: listener_table(2, [8844, 8845]), 23: listener_table(23, [])})
        with self.mock_api(api):
            self.assertTrue(ports.windows_port_listening(8845))
        self.assertEqual({call[1] for call in api.calls}, {2})

    def test_ipv6_target_is_found_using_its_distinct_row_layout(self):
        api = FakeTcpTable({2: listener_table(2, [8844]), 23: listener_table(23, [443, 8845])})
        with self.mock_api(api):
            self.assertTrue(ports.windows_port_listening(8845))

    def test_other_listening_ports_do_not_block_tokyo(self):
        api = FakeTcpTable({2: listener_table(2, [80, 8844]), 23: listener_table(23, [443, 8846])})
        with self.mock_api(api):
            self.assertFalse(ports.windows_port_listening(8845))

    def test_insufficient_buffer_retries_with_the_updated_dword_size(self):
        api = FakeTcpTable({2: listener_table(2, [8845]), 23: listener_table(23, [])}, resize_family=2)
        with self.mock_api(api):
            self.assertTrue(ports.windows_port_listening(8845))
        self.assertTrue(api.resized)
        self.assertEqual([(call[0], call[4]) for call in api.calls], [(False, 0), (True, 4), (True, 28)])

    def test_api_errors_fail_safely_instead_of_reporting_a_free_port(self):
        for family, fetch, code in [(2, False, 5), (2, True, 5), (23, False, 5), (23, True, 5), (2, False, 50)]:
            with self.subTest(family=family, fetch=fetch, code=code):
                with self.mock_api(FakeTcpTable(errors={(family, fetch): code})):
                    with self.assertRaises(ports.UnlockError):
                        ports.windows_port_listening(8845)

    def test_missing_ipv6_stack_is_allowed_when_ipv4_has_no_listener(self):
        with self.mock_api(FakeTcpTable(errors={(23, False): 50})):
            self.assertFalse(ports.windows_port_listening(8845))

    def test_truncated_counted_rows_are_rejected_for_both_address_families(self):
        for family in (2, 23):
            with self.subTest(family=family):
                tables = {2: listener_table(2, []), 23: listener_table(23, [])}
                tables[family] = struct.pack("<I", 2)
                with self.mock_api(FakeTcpTable(tables)):
                    with self.assertRaises(ports.UnlockError):
                        ports.windows_port_listening(8845)

    def test_invalid_native_buffer_sizes_are_rejected(self):
        for size in (0, 3, 8 * 1024 * 1024 + 1):
            with self.subTest(size=size):
                api = FakeTcpTable(bad_size=size)
                with self.mock_api(api):
                    with self.assertRaises(ports.UnlockError):
                        ports.windows_port_listening(8845)
                self.assertEqual(len(api.calls), 1)

    def test_windows_guard_uses_tables_and_never_connects_to_test_free_ports(self):
        for listening in (False, True):
            with self.subTest(listening=listening):
                api = FakeTcpTable({2: listener_table(2, [8845] if listening else []), 23: listener_table(23, [])})
                with self.mock_api(api), patch.object(ports.sys, "platform", "win32"), patch.object(ports.socket, "socket", side_effect=AssertionError("Windows must not use connect_ex")):
                    if listening:
                        with self.assertRaises(ports.UnlockError):
                            ports.require_stopped(8845)
                    else:
                        ports.require_stopped(8845)


if __name__ == "__main__":
    unittest.main()
