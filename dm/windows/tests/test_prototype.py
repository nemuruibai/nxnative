"""Regression checks execute real machine words; no assembler dependency."""
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from dm.windows.display import write_bmp
from dm.windows.runtime import CODE, DATA, LOG, PRESENT, EXIT, execute, fixture_code

ROOT = Path(__file__).resolve().parents[3]


def words(*values):
    return b"".join(struct.pack("<I", value) for value in values)


def mov(register, value, shift=0):
    # AArch64 MOVZ Xd, #imm16, LSL #shift.
    return words(0xD2800000 | ((shift//16) << 21) | (value << 5) | register)


def svc(number):
    return words(0xD4000001 | (number << 5))


class RuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT / "out").mkdir(exist_ok=True)
        cls.code = fixture_code()
        cls.result, cls.frame = execute(cls.code)

    def assert_failure(self, code, prefix, **kwargs):
        result, _ = execute(code, **kwargs)
        self.assertFalse(result["cpu_success"])
        self.assertEqual(result["process_exit_status"], 1)
        self.assertTrue(result["error"].startswith(prefix), result)
        return result

    def test_cpu_and_every_pixel_against_independent_formula(self):
        self.assertTrue(self.result["cpu_success"], self.result)
        self.assertEqual(self.result["cpu_check"]["actual"], sum(range(101)))
        self.assertEqual(self.result["logs"], [5050])
        self.assertEqual(self.result["instructions"], 533436)
        self.assertEqual((self.frame.width, self.frame.height, self.frame.stride),
                         (256, 160, 1024))
        expected = bytes(channel for y in range(160) for x in range(256)
                         for channel in ((5*x) % 256, (3*y) % 256,
                                         (7*(x ^ y)) % 256, 255))
        self.assertEqual(self.frame.rgba, expected)
        self.assertEqual(hashlib.sha256(expected).hexdigest(),
                         "be047d33b397d1a415776898f430734c171b6d4a49a079aaf47b9d4735dd29c9")

    def test_guest_color_mutation_changes_output(self):
        # ADD W7,W5,W5,LSL #2 -> LSL #1: red changes from 5*x to 3*x.
        before, after = words(0x0B0508A7), words(0x0B0504A7)
        self.assertEqual(self.code.count(before), 1)
        result, frame = execute(self.code.replace(before, after))
        self.assertTrue(result["cpu_success"], result)
        self.assertEqual(frame.rgba[4], 3)
        self.assertEqual(self.frame.rgba[4], 5)
        self.assertNotEqual(result["frame"]["sha256"], self.result["frame"]["sha256"])

    def test_svc_interrupt_pc_is_next_instruction(self):
        result = self.assert_failure(mov(0, 42) + svc(LOG) + mov(0, 0) + svc(EXIT),
                                     "missing_present")
        self.assertEqual(result["logs"], [42])
        self.assertEqual(result["svc_calls"][0]["site"], CODE + 4)
        self.assertEqual(result["svc_calls"][0]["resume_pc"], CODE + 8)
        self.assertEqual([c["id"] for c in self.result["svc_calls"]], [LOG, PRESENT, EXIT])

    def test_unknown_svc_and_non_svc_interrupt(self):
        self.assert_failure(svc(0x7777), "unknown_svc")
        self.assert_failure(words(0xD4200000), "guest_interrupt")  # BRK #0
        self.assert_failure(words(0), "guest_interrupt")  # UDF #0

    def test_memory_protections_and_unmapped_access(self):
        self.assert_failure(words(0xF9400000), "guest_memory_fault")  # LDR X0,[X0=0]
        self.assert_failure(mov(0, CODE >> 16, 16) + words(0xF9000000),
                            "guest_memory_fault")  # STR X0,[RX code]
        self.assert_failure(mov(0, DATA >> 16, 16) + words(0xD61F0000),
                            "guest_memory_fault")  # BR X0 into non-executable data

    def test_frame_abi_rejects_bad_dimensions_ranges_and_sizes(self):
        present = self.code.index(svc(PRESENT))
        for name, overrides in (
            ("null_pointer", mov(0, 0)),
            ("code_pointer", mov(0, CODE >> 16, 16)),
            ("past_data_end", mov(0, 0x23, 16)),
            ("size_mismatch", mov(1, 4)),
            ("zero_width", mov(2, 0)),
            ("huge_width", mov(2, 1025)),
            ("zero_height", mov(3, 0)),
            ("huge_height", mov(3, 1025)),
            ("short_stride", mov(4, 1020)),
            ("unaligned_stride", mov(4, 1025)),
        ):
            with self.subTest(name=name):
                code = self.code[:present] + overrides + self.code[present:]
                self.assert_failure(code, "invalid_present")

    def test_missing_present_exit_and_nonzero_exit(self):
        self.assert_failure(mov(0, 0) + svc(EXIT), "missing_present")
        self.assert_failure(words(0xD65F03C0), "missing_exit")  # RET to end sentinel
        self.assert_failure(words(0xD503201F), "missing_exit")  # NOP, falls through
        self.assert_failure(mov(0, 7) + svc(EXIT), "guest_exit")
        self.assert_failure(self.code[:-4], "missing_exit")  # present then no exit
        self.assert_failure(self.code.replace(svc(EXIT), svc(PRESENT)), "invalid_present")

    def test_wrong_arithmetic_is_rejected(self):
        # Loop starts at 99 rather than 100; still draws the whole frame.
        self.assert_failure(self.code.replace(mov(5, 100), mov(5, 99)), "cpu_check")

    def test_instruction_and_time_budget_exhaustion(self):
        infinite_loop = words(0x14000000)  # B .
        result = self.assert_failure(infinite_loop, "budget_instructions", instruction_limit=17)
        self.assertEqual(result["instructions"], 17)
        self.assert_failure(infinite_loop, "budget_time", time_limit=0.001)
        self.assert_failure(self.code, "budget_instructions", instruction_limit=10)

    def test_log_flood_and_branch_outside_loaded_code(self):
        self.assert_failure(svc(LOG) + words(0x17FFFFFF), "log_limit")  # B back to SVC
        self.assert_failure(words(0x14000002), "invalid_pc")  # B past 4-byte image

    def test_bad_binary_and_budget_inputs(self):
        for code in (b"", b"x", b"\0" * 4100):
            self.assert_failure(code, "invalid_code")
        self.assert_failure(self.code, "invalid_budget", instruction_limit=0)
        self.assert_failure(self.code, "invalid_budget", time_limit=0)
        self.assert_failure(self.code, "invalid_budget", time_limit=float("nan"))
        self.assert_failure(self.code, "invalid_budget", time_limit=float("inf"))

    def test_padded_rows_copy_only_visible_pixels(self):
        present = self.code.index(svc(PRESENT))
        result, frame = execute(self.code[:present] + mov(2, 255) + self.code[present:])
        self.assertTrue(result["cpu_success"], result)
        expected = b"".join(self.frame.rgba[y*1024:y*1024+255*4] for y in range(160))
        self.assertEqual(frame.rgba, expected)

    def test_bmp_contains_guest_pixels_in_viewable_format(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "out") as folder:
            path = Path(folder) / "frame.bmp"
            write_bmp(self.frame, path)
            data = path.read_bytes()
        self.assertEqual(data[:2], b"BM")
        self.assertEqual(struct.unpack_from("<ii", data, 18), (256, 160))
        self.assertEqual(struct.unpack_from("<I", data, 2)[0], len(data))
        for x, y in ((0, 0), (1, 0), (119, 81), (255, 159)):
            offset = 54 + (159-y)*768 + x*3
            pixel = self.frame.rgba[(y*256+x)*4:(y*256+x)*4+3]
            self.assertEqual(data[offset:offset+3], pixel[::-1])


class LauncherTests(unittest.TestCase):
    def test_display_failure_keeps_cpu_success_and_fails_process(self):
        from scripts import run_windows_prototype as launcher

        (ROOT / "out").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / "out") as folder:
            with patch("dm.windows.display.show_frame", side_effect=RuntimeError("test desktop unavailable")):
                with patch("builtins.print"):
                    status = launcher.main(["--no-headless", "--output", folder])
            report = json.loads((Path(folder) / "report.json").read_text())
            self.assertEqual(status, 1)
            self.assertTrue(report["cpu_success"])
            self.assertFalse(report["display_success"])
            self.assertEqual(report["display_status"], "failed")

    @unittest.skipUnless(os.name == "nt", "Windows batch launcher")
    def test_cmd_forwards_failure_status_from_other_directory(self):
        (ROOT / "out").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / "out") as folder:
            run = subprocess.run([str(ROOT / "scripts/run_windows_prototype.cmd"),
                                  "--headless", "--instruction-limit", "1",
                                  "--output", folder], cwd=folder,
                                 capture_output=True, text=True, timeout=20)
            self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
            self.assertIn("budget_instructions", run.stderr)

    def test_headless_cli_and_failure_exit_report_from_other_directory(self):
        (ROOT / "out").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / "out") as folder:
            args = [sys.executable, str(ROOT / "scripts/run_windows_prototype.py"),
                    "--headless", "--output", folder]
            run = subprocess.run(args, cwd=folder, capture_output=True, text=True, timeout=20)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            report = json.loads((Path(folder) / "report.json").read_text())
            self.assertTrue(report["cpu_success"])
            self.assertFalse(report["display_success"])
            self.assertTrue((Path(folder) / "frame.bmp").exists())
            run = subprocess.run(args + ["--instruction-limit", "1"], cwd=folder,
                                 capture_output=True, text=True, timeout=20)
            self.assertEqual(run.returncode, 1)
            self.assertIn("[RED]", run.stderr)
            self.assertFalse((Path(folder) / "frame.bmp").exists())
            report = json.loads((Path(folder) / "report.json").read_text())
            self.assertEqual(report["process_exit_status"], 1)
            self.assertFalse(report["cpu_success"])


if __name__ == "__main__":
    (ROOT / "out").mkdir(exist_ok=True)
    unittest.main()
