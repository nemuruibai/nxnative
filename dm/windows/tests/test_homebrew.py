"""Run independent machine-word guests against the real SVC register ABI."""
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from dm.windows.generate_homebrew import assemble_source, fixture_code, pack_nro, write_fixture
from dm.windows.horizon import (AS_END, ENV, HEAP, HEAP_ALIGN, HEAP_CAP, IMAGE,
                                INVALID_POINTER, INVALID_SIZE, MAX_LOG_BYTES,
                                MAX_TRACE, OUT_OF_MEMORY, execute_nro)
from dm.windows.nro import MAX_BSS, MAX_IMAGE, NroError, load_nro, parse_nro

ROOT = Path(__file__).resolve().parents[3]


def setUpModule():
    (ROOT / "out").mkdir(exist_ok=True)


def words(*values):
    return struct.pack("<"+"I"*len(values), *values)


def mov(register, value):
    """Independent MOVZ/MOVK encoder, no assembler/test dependency."""
    value &= (1 << 64)-1
    return b"".join(words((0xD2800000 if shift == 0 else 0xF2800000) |
                          (shift//16 << 21) | ((value >> shift & 0xFFFF) << 5) | register)
                    for shift in range(0, 64, 16))


def svc(number):
    return words(0xD4000001 | number << 5)


def debug(pointer, length):
    return mov(0, pointer)+mov(1, length)+svc(0x27)


def query(address, pointer=IMAGE+0x3000):
    return mov(0, pointer)+mov(1, 0)+mov(2, address)+svc(6)


def heap(size):
    return mov(0, 0)+mov(1, size)+svc(1)


def run(code, **kwargs):
    return execute_nro(parse_nro(pack_nro(code+svc(7))), **kwargs)


class LoaderTests(unittest.TestCase):
    def setUp(self):
        self.image = pack_nro(fixture_code())

    def mutate(self, offset, value):
        data = bytearray(self.image)
        struct.pack_into("<I", data, offset, value)
        return data

    def test_source_to_checked_bytes_reproducibility(self):
        try:
            import keystone
        except ImportError:
            self.skipTest("optional requirements-fixtures.txt needed for source reproduction")
        self.assertEqual(keystone.__version__, "0.9.2")
        self.assertEqual(assemble_source(), fixture_code())
        self.assertEqual(pack_nro(assemble_source()), self.image)

    def test_file_loader_and_fixture_structure(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "out") as folder:
            path = write_fixture(Path(folder)/"homebrew.nro")
            image = load_nro(path)
        self.assertEqual(image.image[8:16], b"HOMEBREW")
        self.assertEqual(image.mod_offset, 0x1000)
        self.assertEqual(image.dynamic_offset, 0x1020)
        self.assertEqual(image.bss_size, 0x1000)
        self.assertEqual([(s.offset, s.size, s.permission) for s in image.segments],
                         [(0, 4096, 5), (4096, 4096, 1), (8192, 4096, 3)])
        # Independently decode standard NRO and MOD0 fields, not packer identity.
        self.assertEqual(struct.unpack_from("<4sIII", image.image, 16),
                         (b"NRO0", 0, 0x3000, 0))
        self.assertEqual(struct.unpack_from("<4siiiiii", image.image, 0x1000),
                         (b"MOD0", 0x20, 0x2000, 0x3000, 0, 0, 0))

    def test_truncation_format_and_size_caps(self):
        for data in (b"", self.image[:127], self.image[:-1], b"NSO0"+self.image[4:],
                     bytes(MAX_IMAGE+1), self.image+b"ASET"):
            with self.subTest(size=len(data)), self.assertRaises(NroError):
                parse_nro(data)

    def test_rejects_bad_headers_segments_bss_and_mod0(self):
        for offset, value in ((0x14, 1), (0x1C, 1), (0x18, 2), (0x20, 4096),
                              (0x28, 0), (0x28, 4097), (0x2C, 0), (0x30, 4096),
                              (0x34, 0x3000), (0x38, MAX_BSS+4096), (0x38, 1),
                              (0x3C, 1), (0x60, 1), (4, 0x4000), (0, 0xD65F03C0),
                              (0, 0x14000001), (0x1000, 0), (0x1004, 0x4000),
                              (0x1008, 0), (0x100C, 0), (0x1010, 1), (0x1018, 1),
                              (0x1020, 7)):
            with self.subTest(offset=offset, value=value), self.assertRaises(NroError):
                parse_nro(self.mutate(offset, value))

    def test_file_read_is_bounded_and_nro_object_revalidated(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "out") as folder:
            path = Path(folder)/"oversized.nro"
            with path.open("wb") as stream:
                stream.truncate(MAX_IMAGE+1)
            self.assertFalse(execute_nro(path)["success"])
        from dataclasses import replace
        forged = replace(parse_nro(self.image), image=b"invalid")
        self.assertIn("nro_load:", execute_nro(forged)["error"])


class KernelTests(unittest.TestCase):
    def assert_success(self, report):
        self.assertTrue(report["success"], report)
        self.assertTrue(report["kernel_exit"])
        self.assertEqual(report["termination"], "svcExitProcess")
        self.assertIsNone(report["error"])

    def test_own_homebrew_and_entry_abi(self):
        result = execute_nro(parse_nro(pack_nro(fixture_code())))
        self.assert_success(result)
        self.assertEqual([v["id"] for v in result["svc_calls"]],
                         [39, 1, 6, 30, 30, 39, 31, 33, 33, 22, 33, 39, 22, 7])
        self.assertEqual([v["text"] for v in result["logs"]],
                         ["homebrew: start\n", "homebrew: checks\n", "homebrew: ipc\n"])
        self.assertEqual(result["entry"]["x0"], ENV)
        self.assertEqual(result["entry"]["x1"], (1 << 64)-1)
        self.assertEqual(result["entry"]["sp"] % 16, 0)
        self.assertNotIn("guest_exit_status", result)

    def test_fixture_checks_are_causal_guest_assertions(self):
        image = bytearray(pack_nro(fixture_code()))
        image[0x2000] ^= 1  # Break initialized-data assertion in the guest itself.
        result = execute_nro(parse_nro(image))
        self.assertFalse(result["success"])
        self.assertFalse(result["kernel_exit"])
        self.assertTrue(result["error"].startswith("guest_interrupt"), result)
        self.assertEqual(result["svc_calls"], [])

    def test_alternate_minimal_nro_has_no_fixture_identity_requirement(self):
        result = run(mov(0, 999))  # ExitProcess ignores X0.
        self.assert_success(result)
        self.assertEqual(len(result["svc_calls"]), 1)
        self.assertEqual(result["logs"], [])
        self.assertEqual(result["heap_size"], 0)

    def test_heap_errors_recover_and_do_not_dereference_x0(self):
        result = run(heap(HEAP_ALIGN)+heap(1)+heap(HEAP_CAP+HEAP_ALIGN)+query(HEAP))
        self.assert_success(result)
        calls = result["svc_calls"]
        self.assertEqual([c["out"][0] for c in calls[:4]],
                         [0, INVALID_SIZE, OUT_OF_MEMORY, 0])
        self.assertEqual(calls[0]["in"][0], 0)
        self.assertEqual(calls[0]["out"][1], HEAP)
        self.assertEqual(result["heap_size"], HEAP_ALIGN)

    def test_heap_grow_shrink_preserves_and_zeroes_new_bytes(self):
        # Store one byte in retained range, one in range removed by shrink.
        code = heap(2*HEAP_ALIGN)+mov(4, HEAP)+mov(5, 0xAB)+words(0x39000085)
        code += mov(4, HEAP+HEAP_ALIGN)+words(0x39000085)
        code += heap(HEAP_ALIGN)+heap(2*HEAP_ALIGN)
        result = run(code+debug(HEAP, 8)+debug(HEAP+HEAP_ALIGN, 8)+heap(0)+query(HEAP))
        self.assert_success(result)
        self.assertEqual(bytes.fromhex(result["logs"][0]["hex"]), b"\xab"+bytes(7))
        self.assertEqual(bytes.fromhex(result["logs"][1]["hex"]), bytes(8))
        self.assertEqual(result["heap_size"], 0)

    def test_query_memory_structure_regions_gaps_and_outside_address_space(self):
        addresses = [IMAGE+7, IMAGE+4096, IMAGE+8192, HEAP+7, 0, HEAP+HEAP_ALIGN,
                     AS_END, (1 << 64)-1]
        code = heap(HEAP_ALIGN)
        for address in addresses:
            code += query(address)+debug(IMAGE+0x3000, 40)
        result = run(code)
        self.assert_success(result)
        info = [struct.unpack("<QQIIIIII", bytes.fromhex(log["hex"]))
                for log in result["logs"]]
        self.assertEqual(info[0], (IMAGE, 4096, 8, 0, 5, 0, 0, 0))
        self.assertEqual(info[1], (IMAGE+4096, 4096, 8, 0, 1, 0, 0, 0))
        self.assertEqual(info[2], (IMAGE+8192, 8192, 9, 0, 3, 0, 0, 0))
        self.assertEqual(info[3], (HEAP, HEAP_ALIGN, 5, 0, 3, 0, 0, 0))
        self.assertEqual(info[4], (0, IMAGE, 0, 0, 0, 0, 0, 0))
        self.assertEqual(info[5][:3], (HEAP+HEAP_ALIGN, 0x30000000-HEAP-HEAP_ALIGN, 0))
        self.assertEqual(info[6], (AS_END, (1 << 64)-AS_END, 16, 0, 0, 0, 0, 0))
        self.assertEqual(info[6], info[7])
        self.assertTrue(all(c["out"][1] == 0 for c in result["svc_calls"] if c["id"] == 6))

    def test_query_invalid_output_pointer_returns_result_then_guest_recovers(self):
        pointers = [0, IMAGE, IMAGE+0x1000, IMAGE+0x4000-20, (1 << 64)-16]
        code = b"".join(query(HEAP, pointer) for pointer in pointers)
        result = run(code+query(0))
        self.assert_success(result)
        self.assertEqual([c["result"] for c in result["svc_calls"] if c["id"] == 6],
                         [INVALID_POINTER]*len(pointers)+[0])

    def test_libnx_shaped_wrappers_stack_out_pointers(self):
        # Mirror svc.s wrappers: save C output pointer, SVC, store X1/W1.
        code = mov(0, IMAGE+0x3000)+mov(1, HEAP_ALIGN)
        code += words(0xF81F0FE0)+svc(1)+words(0xF84107E2, 0xF9000041)
        code += debug(IMAGE+0x3000, 8)
        code += mov(0, IMAGE+0x3010)+mov(1, IMAGE+0x3008)+mov(2, HEAP+1)
        code += words(0xF81F0FE1)+svc(6)+words(0xF84107E2, 0xB9000041)
        code += debug(IMAGE+0x3008, 4)+debug(IMAGE+0x3010, 40)
        result = run(code)
        self.assert_success(result)
        self.assertEqual(bytes.fromhex(result["logs"][0]["hex"]), struct.pack("<Q", HEAP))
        self.assertEqual(bytes.fromhex(result["logs"][1]["hex"]), bytes(4))
        self.assertEqual(struct.unpack("<QQIIIIII", bytes.fromhex(result["logs"][2]["hex"]))[:3],
                         (HEAP, HEAP_ALIGN, 5))

    def test_explicit_debug_lengths_zero_null_and_bad_ranges(self):
        raw = b"a\0b\xffc"
        image = pack_nro(debug(IMAGE+0x1040, 4)+debug(0, 0)+debug(0, 1)+
                         debug((1 << 64)-1, 2)+svc(7), rodata=raw)
        result = execute_nro(parse_nro(image))
        self.assert_success(result)
        self.assertEqual([log["hex"] for log in result["logs"]], [raw[:4].hex(), ""])
        self.assertEqual(result["logs"][0]["sha256"], hashlib.sha256(raw[:4]).hexdigest())
        self.assertEqual([c["result"] for c in result["svc_calls"] if c["id"] == 39],
                         [0, 0, INVALID_POINTER, INVALID_POINTER])

    def test_tick_non_decreasing_and_frequency(self):
        result = run(svc(30)+svc(30))
        ticks = [c["out"][0] for c in result["svc_calls"][:2]]
        self.assert_success(result)
        self.assertGreaterEqual(ticks[1], ticks[0])
        with patch("dm.windows.horizon.time.perf_counter_ns", side_effect=lambda: 123456789):
            result = run(svc(30))
        self.assertEqual(result["svc_calls"][0]["out"][0], 0)
        # Isolate the syscall read from code hooks using a clock advanced per call.
        clock = iter(range(0, 100_000_000, 1_000_000))
        with patch("dm.windows.horizon.time.perf_counter_ns", side_effect=lambda: next(clock)):
            result = run(svc(30))
        self.assertEqual(result["svc_calls"][0]["out"][0] % 19200, 0)
        self.assertGreater(result["svc_calls"][0]["out"][0], 0)

    def test_permissions_unmapped_return_brk_unknown_and_no_lazy_mapping(self):
        cases = [(mov(4, IMAGE)+mov(5, 1)+words(0x39000085), "guest_memory_fault"),
                 (mov(4, IMAGE+4096)+words(0x39000085), "guest_memory_fault"),
                 (mov(4, HEAP)+words(0x39400085), "guest_memory_fault"),
                 (mov(4, IMAGE+8192)+words(0xD61F0080), "guest_memory_fault"),
                 (words(0xD65F03C0), "guest_memory_fault"),
                 (words(0xD4200020), "guest_interrupt"),
                 (svc(0x29), "unsupported_svc: 0x0029")]
        for code, prefix in cases:
            with self.subTest(prefix=prefix):
                result = run(code)
                self.assertFalse(result["success"])
                self.assertTrue(result["error"].startswith(prefix), result)

    def test_instruction_time_and_diagnostic_bounds(self):
        for code, kwargs, prefix in [
                (words(0x14000000), {"instruction_limit": 10}, "budget_instructions"),
                (words(0x14000000), {"time_limit": 0.001}, "budget_time"),
                (svc(30)+words(0x17FFFFFF), {}, "host_trace_limit"),
                (heap(HEAP_ALIGN)+debug(HEAP, MAX_LOG_BYTES+1), {}, "host_log_limit")]:
            with self.subTest(prefix=prefix):
                result = run(code, **kwargs)
                self.assertFalse(result["success"])
                self.assertTrue(result["error"].startswith(prefix), result)
                self.assertLessEqual(len(result["svc_calls"]), MAX_TRACE)
        result = run(svc(7), instruction_limit=1)
        self.assertFalse(result["success"])

    def test_cli_artifacts_external_and_failure_status(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "out") as folder:
            output = Path(folder)
            command = [sys.executable, str(ROOT / "scripts/run_windows_homebrew.py"),
                       "--output", str(output)]
            process = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertTrue((output / "selftest.nro").is_file())
            self.assertTrue(json.loads((output / "report.json").read_text())["success"])
            external = output / "alternate.nro"
            external.write_bytes(pack_nro(mov(0, 4321)+svc(7)))
            process = subprocess.run(command+["--guest", str(external)], capture_output=True, text=True)
            self.assertEqual(process.returncode, 0, process.stderr)
            external.write_bytes(pack_nro(svc(0x7FFF)))
            process = subprocess.run(command+["--guest", str(external)], capture_output=True, text=True)
            self.assertEqual(process.returncode, 1)
            self.assertIn("[RED] unsupported_svc", process.stderr)
            self.assertFalse(json.loads((output / "report.json").read_text())["success"])


if __name__ == "__main__":
    unittest.main()
