"""Independent protocol vectors and ARM64 guests for the minimal IPC path."""
from pathlib import Path
import struct
import tempfile
import unittest

from dm.windows.generate_homebrew import fixture_code, pack_nro, write_fixture
from dm.windows.horizon import IMAGE, TLS, INVALID_POINTER, execute_nro
from dm.windows.ipc import (Services, IpcError, parse_request, INVALID_HANDLE,
                            OUT_OF_HANDLES, INVALID_CLIENT, UNKNOWN_COMMAND,
                            NOT_FOUND, OUT_OF_RANGE)
from dm.windows.nro import parse_nro
from test_homebrew import mov, svc, words, debug

ROOT = Path(__file__).resolve().parents[3]


def setUpModule():
    (ROOT / "out/windows-homebrew-ipc-tests").mkdir(parents=True, exist_ok=True)


def vector(kind):
    # Explicit independent libnx/Atmosphere byte layouts, not runtime builders.
    packet = bytearray(256)
    if kind == "register":
        struct.pack_into("<IIIQ", packet, 0, 4, 0x8000000A, 1, 0xBADF00D)
        struct.pack_into("<4IQ", packet, 0x20, 0x49434653, 0, 0, 0, 0xDEADBEEF)
    elif kind == "get":
        struct.pack_into("<II", packet, 0, 4, 10)
        struct.pack_into("<4I8s", packet, 0x10, 0x49434653, 0, 1, 0, b"set\0\0\0\0\0")
    elif kind == "language":
        struct.pack_into("<II", packet, 0, 4, 8)
        struct.pack_into("<4I", packet, 0x10, 0x49434653, 0, 0, 0)
    return packet


def put_packet(packet):
    code = mov(4, TLS)
    for offset in range(0, 256, 8):
        value, = struct.unpack_from("<Q", packet, offset)
        code += mov(5, value) if value else words(0xAA1F03E5)  # mov x5,xzr
        code += words(0xF9000000 | (offset//8 << 10) | (4 << 5) | 5)
    return code


def connect(pointer=IMAGE+0x1040):
    return mov(0, 0)+mov(1, pointer)+svc(0x1F)


def send(packet, handle=0x100):
    return put_packet(packet)+mov(0, handle)+svc(0x21)


def run_guest(code, *, rodata=b"sm:\0", **kwargs):
    return execute_nro(parse_nro(pack_nro(code+svc(7), rodata=rodata)), **kwargs)


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.services = Services()
        self.handle = self.services.connect(b"sm:")[1]

    def register(self, handle=None):
        return self.services.dispatch(self.handle if handle is None else handle, vector("register"))

    def test_independent_request_vectors_and_compact_responses(self):
        transport, reply, decoded = self.register()
        self.assertEqual((transport, decoded["kernel_pid"]), (0, 1))
        self.assertEqual(struct.unpack_from("<II", reply), (0, 6))
        self.assertEqual(struct.unpack_from("<4I", reply, 16), (0x4F434653, 0, 0, 0))
        transport, reply, decoded = self.services.dispatch(self.handle, vector("get"))
        moved = decoded["moved_handle"]
        self.assertEqual(struct.unpack_from("<4I", reply), (0, 0x80000004, 0x20, moved))
        self.assertEqual(transport, 0)
        self.assertEqual(self.services.close(self.handle), 0)
        transport, reply, decoded = self.services.dispatch(moved, vector("language"))
        self.assertEqual(transport, 0)
        self.assertEqual(struct.unpack_from("<II", reply), (0, 8))
        self.assertEqual(reply[32:40], b"en-US\0\0\0")
        self.assertEqual(len(reply), 256)

    def test_registration_isolation_repeat_and_untrusted_pid(self):
        other = self.services.connect(b"sm:")[1]
        self.register()
        self.register()  # re-registration succeeds
        self.assertEqual(self.services.sessions[self.handle].registered_pid, 1)
        transport, reply, decoded = self.services.dispatch(other, vector("get"))
        self.assertEqual((transport, decoded["service_result"]), (0, INVALID_CLIENT))
        self.assertEqual(struct.unpack_from("<I", reply, 24)[0], 0x415)
        self.assertEqual(len(self.services.sessions), 2)
        self.register(other)
        self.assertEqual(self.services.dispatch(other, vector("get"))[0], 0)

    def test_capacity_recovery_no_leak_and_stale_handles(self):
        services = Services(capacity=2)
        first = services.connect(b"sm:")[1]
        services.dispatch(first, vector("register"))
        second = services.connect(b"sm:")[1]
        self.assertEqual(services.connect(b"sm:"), (OUT_OF_HANDLES, None))
        transport, reply, _ = services.dispatch(first, vector("get"))
        self.assertEqual((transport, reply), (OUT_OF_HANDLES, None))
        self.assertEqual(len(services.sessions), 2)
        self.assertEqual(services.close(second), 0)
        self.assertEqual(services.close(second), INVALID_HANDLE)
        transport, reply, decoded = services.dispatch(first, vector("get"))
        moved = decoded["moved_handle"]
        self.assertEqual(transport, 0)
        self.assertGreater(moved, second)
        self.assertEqual(services.dispatch(second, vector("language"))[0], INVALID_HANDLE)
        self.assertEqual(services.close(moved), 0)
        self.assertEqual(services.close(first), 0)
        self.assertEqual(services.sessions, {})
        self.assertEqual(services.close(0xFFFF8001), INVALID_HANDLE)

    def test_malformed_framing_is_bounded_and_never_allocates(self):
        mutations = [("register", 8, 0), ("register", 4, 0x80000008),
                     ("register", 8, 3), ("register", 8, 0x20), ("register", 8, 0x200),
                     ("get", 4, 5), ("get", 4, 7), ("get", 16, 0), ("get", 20, 1),
                     ("get", 28, 1), ("get", 4, 1023), ("get", 0, 0xFFFF0004),
                     ("get", 0, 0x10004), ("get", 4, 0x400),
                     ("get", 4, 0x4000), ("get", 4, 0x100000),
                     ("get", 0, 2), ("get", 0, 5), ("get", 0, 6),
                     ("get", 0, 7), ("get", 0, 0x10)]
        for kind, offset, value in mutations:
            with self.subTest(kind=kind, offset=offset, value=value):
                packet = vector(kind)
                struct.pack_into("<I", packet, offset, value)
                before = self.services.next_handle
                with self.assertRaises(IpcError):
                    self.services.dispatch(self.handle, packet)
                self.assertEqual(self.services.next_handle, before)
                self.assertEqual(len(self.services.sessions), 1)
                self.assertIsNone(self.services.sessions[self.handle].registered_pid)
        with self.assertRaises(IpcError):
            parse_request(bytes(255))
        packet = vector("language")
        struct.pack_into("<I", packet, 4, 10)  # raw u64 present, PID missing
        with self.assertRaisesRegex(IpcError, "PID descriptor"):
            self.services.dispatch(self.handle, packet)

    def test_descriptor_overflow_checked_before_unsupported_descriptor(self):
        packet = vector("get")
        struct.pack_into("<I", packet, 0, 0xFFFF0004)
        with self.assertRaisesRegex(IpcError, "ipc_bounds"):
            parse_request(packet)

    def test_declared_raw_range_and_alignment_allowance(self):
        packet = vector("register")
        self.assertEqual(parse_request(packet).cmif_offset, 0x20)
        # Physical bytes exist after raw_end, but must not satisfy short payload.
        struct.pack_into("<I", packet, 4, 0x80000008)
        with self.assertRaisesRegex(IpcError, "raw u64"):
            self.services.dispatch(self.handle, packet)
        packet = vector("language")
        self.assertEqual(parse_request(packet).raw_end, 40)
        struct.pack_into("<I", packet, 4, 6)  # compact minimum also accepted
        self.assertEqual(parse_request(packet).raw_end, 32)
        struct.pack_into("<I", packet, 4, 5)
        with self.assertRaisesRegex(IpcError, "header outside"):
            parse_request(packet)

    def test_unknown_command_and_explicit_unsupported_service(self):
        packet = vector("language")
        struct.pack_into("<I", packet, 24, 999)
        result, reply, decoded = self.services.dispatch(self.handle, packet)
        self.assertEqual((result, decoded["service_result"]), (0, UNKNOWN_COMMAND))
        self.register()
        packet = vector("get")
        packet[32:40] = b"missing\0"
        with self.assertRaisesRegex(IpcError, "unsupported_service"):
            self.services.dispatch(self.handle, packet)
        self.assertEqual(len(self.services.sessions), 1)


class IpcGuestTests(unittest.TestCase):
    def test_mrs_tls_query_memory_reports_thread_local_mapping(self):
        destination = IMAGE+0x3000
        # Query the address supplied by real MRS, then read/output all MemoryInfo.
        code = mov(0, destination)+mov(1, 0)+words(0xD53BD062)+svc(6)
        report = run_guest(code+debug(destination, 40))
        self.assertTrue(report["success"], report)
        query = report["svc_calls"][0]
        self.assertEqual(query["in"][2], TLS)
        self.assertEqual(query["out"][:2], [0, 0])
        raw = bytes.fromhex(report["logs"][0]["hex"])
        self.assertEqual(len(raw), 40)
        self.assertEqual(struct.unpack("<QQIIIIII", raw),
                         (TLS, 0x1000, 0x0C, 0, 3, 0, 0, 0))

    def test_complete_file_path_and_reports(self):
        with tempfile.TemporaryDirectory(prefix="case-", dir=ROOT / "out/windows-homebrew-ipc-tests") as folder:
            path = write_fixture(Path(folder)/"selftest.nro")
            report = execute_nro(path)
        self.assertTrue(report["success"], report)
        ipc = [c["ipc"] for c in report["svc_calls"] if "ipc" in c]
        self.assertEqual([(c["service"], c["command"]) for c in ipc],
                         [("sm:", 0), ("sm:", 1), ("set", 0)])
        self.assertTrue(all(c["transport_result"] == c["service_result"] == 0 for c in ipc))
        self.assertTrue(all(len(c["request_hex"]) == len(c["response_hex"]) == 512 for c in ipc))
        self.assertEqual(report["services"]["live_handles"], [])
        self.assertEqual([c["event"] for c in report["services"]["handle_lifecycle"]],
                         ["open", "open", "close", "close"])

    def test_language_mutation_causes_guest_assert_and_alternate_expectation_succeeds(self):
        image = pack_nro(fixture_code())
        report = execute_nro(parse_nro(image), language="ja")
        self.assertFalse(report["success"])
        self.assertFalse(report["kernel_exit"])
        self.assertTrue(report["error"].startswith("guest_interrupt"))
        expected = mov(3, int.from_bytes(b"en-US", "little"))[:12]
        self.assertEqual(fixture_code().count(expected), 1)
        alternate = fixture_code().replace(expected, mov(3, int.from_bytes(b"ja", "little"))[:12])
        report = execute_nro(parse_nro(pack_nro(alternate)), language="ja")
        self.assertTrue(report["success"], report)
        self.assertNotEqual(report["image_sha256"], execute_nro(parse_nro(image))["image_sha256"])

    def test_independent_guest_tls_sentinel_high_handle_bits_and_closes(self):
        # Sentinel +0x100 must survive all IPC writes; MRS reads real TLS register.
        code = words(0xD53BD060)+mov(1, 16)+svc(0x27)
        code += mov(4, TLS+256)+mov(5, 0x12345678)+words(0xF9000085)
        code += connect()+send(vector("register"), (0xBAD << 32) | 0x100)
        code += send(vector("get"))+mov(0, 0x100)+svc(0x16)
        code += send(vector("language"), 0x101)+debug(TLS, 40)+debug(TLS+256, 8)
        code += mov(0, (0xBAD << 32) | 0x101)+svc(0x16)+mov(0, 0x101)+svc(0x16)
        code += mov(0, 0x100)+svc(0x21)
        report = run_guest(code)
        self.assertTrue(report["success"], report)
        self.assertEqual(report["logs"][0]["hex"], bytes(16).hex())
        self.assertEqual(bytes.fromhex(report["logs"][1]["hex"])[32:40], b"en-US\0\0\0")
        self.assertEqual(report["logs"][2]["hex"], struct.pack("<Q", 0x12345678).hex())
        self.assertEqual([c["result"] for c in report["svc_calls"] if c["id"] == 22], [0, 0, INVALID_HANDLE])
        self.assertEqual(report["svc_calls"][-2]["result"], INVALID_HANDLE)

    def test_raw_connect_abi_pointer_boundary_and_results(self):
        # Last four bytes of rodata; reading beyond NUL would enter writable data.
        rodata = bytearray(0xFC0)
        rodata[-4:] = b"sm:\0"
        code = connect(IMAGE+0x1FFC)+connect(0)+connect(IMAGE+0x3FFF)
        code += connect(IMAGE+0x1040)+connect(IMAGE+0x1050)
        rodata[:12] = b"abcdefghijkl"
        rodata[16:24] = b"unknown\0"
        report = run_guest(code, rodata=rodata)
        self.assertTrue(report["success"], report)
        self.assertEqual([c["result"] for c in report["svc_calls"][:-1]],
                         [0, INVALID_POINTER, 0xF201, OUT_OF_RANGE, NOT_FOUND])
        self.assertEqual(report["svc_calls"][0]["in"][0], 0)
        self.assertEqual(report["svc_calls"][0]["out"][1], 0x100)

    def test_port_terminator_at_end_of_all_mapped_memory(self):
        code = mov(4, IMAGE+0x3FFC)+mov(5, 0x3A6D73)+words(0xB9000085)
        code += connect(IMAGE+0x3FFC)
        report = run_guest(code)
        self.assertTrue(report["success"], report)
        self.assertEqual(report["svc_calls"][0]["result"], 0)
        code = mov(4, IMAGE+0x3FFF)+mov(5, 0x41)+words(0x39000085)
        report = run_guest(code+connect(IMAGE+0x3FFF))
        self.assertEqual(report["svc_calls"][0]["result"], INVALID_POINTER)

    def test_runtime_malformed_request_stops_without_response_or_allocation(self):
        packet = vector("register")
        struct.pack_into("<I", packet, 8, 0)
        report = run_guest(connect()+send(packet))
        self.assertFalse(report["success"])
        self.assertTrue(report["error"].startswith("ipc_"))
        self.assertIsNone(report["svc_calls"][-1]["ipc"]["response_hex"])
        self.assertEqual(report["services"]["live_handles"], [0x100])

    def test_runtime_capacity_transport_result_and_recovery(self):
        code = connect()+send(vector("register"))+send(vector("get"))
        code += debug(TLS, 256)+mov(0, 0x100)+svc(0x16)+connect()
        report = run_guest(code, session_capacity=1)
        self.assertTrue(report["success"], report)
        failed = [c for c in report["svc_calls"] if c["id"] == 33][-1]
        self.assertEqual(failed["result"], OUT_OF_HANDLES)
        self.assertIsNone(failed["ipc"]["response_hex"])
        self.assertEqual(report["logs"][0]["hex"], vector("get").hex())
        self.assertEqual(report["services"]["live_handles"], [0x101])

    def test_invalid_language_is_explicit(self):
        report = execute_nro(parse_nro(pack_nro(svc(7))), language="invented")
        self.assertFalse(report["success"])
        self.assertIn("unsupported_language", report["error"])


if __name__ == "__main__":
    unittest.main()
