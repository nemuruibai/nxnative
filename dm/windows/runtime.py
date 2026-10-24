"""Bounded Unicorn execution and the explicitly synthetic demo SVC ABI."""
from dataclasses import dataclass
import hashlib
import math
from pathlib import Path
import time

import unicorn as uc
from unicorn import arm64_const as arm

CODE = 0x100000
CODE_SIZE = 0x1000
DATA = 0x200000
DATA_SIZE = 0x40000
STACK = 0x300000
STACK_SIZE = 0x10000
LOG, PRESENT, EXIT = 0x7F01, 0x7F02, 0x7F03


@dataclass(frozen=True)
class Frame:
    width: int
    height: int
    stride: int
    rgba: bytes  # tightly packed snapshot, excluding guest row padding

    @property
    def rgb(self):
        return bytes(v for i, v in enumerate(self.rgba) if i % 4 != 3)


def fixture_code():
    return bytes.fromhex(Path(__file__).with_name("guest.hex").read_text(encoding="ascii"))


def execute(code=None, *, instruction_limit=1_000_000, time_limit=10.0):
    """Return (JSON-compatible report, optional frame); guest failures never escape.

    Only this module knows Unicorn. No host routine computes demo pixels.
    The raw-code option is for trusted synthetic fixtures, not a security sandbox.
    """
    if code is None:
        code = fixture_code()
    report = {
        "schema": 1, "backend": f"Unicorn {uc.__version__} ARM64",
        "abi": "synthetic-demo-0x7f01-0x7f03", "cpu_success": False,
        "display_success": False, "display_status": "not_requested",
        "guest_exit_status": None, "process_exit_status": 1,
        "instructions": 0, "svc_calls": [], "logs": [], "error": None,
        "guest_sha256": hashlib.sha256(code).hexdigest(),
    }
    frame = None
    if not code or len(code) % 4 or len(code) > CODE_SIZE:
        report["error"] = "invalid_code: expected 4-byte-aligned code of 4..4096 bytes"
        return report, frame
    if not isinstance(instruction_limit, int) or instruction_limit < 1 or not (
            math.isfinite(time_limit) and 0 < time_limit <= 60):
        report["error"] = "invalid_budget: instructions >= 1 and 0 < seconds <= 60 required"
        return report, frame
    engine = uc.Uc(uc.UC_ARCH_ARM64, uc.UC_MODE_ARM)
    engine.mem_map(CODE, CODE_SIZE, uc.UC_PROT_READ | uc.UC_PROT_EXEC)
    engine.mem_write(CODE, code)
    engine.mem_map(DATA, DATA_SIZE, uc.UC_PROT_READ | uc.UC_PROT_WRITE)
    engine.mem_map(STACK, STACK_SIZE, uc.UC_PROT_READ | uc.UC_PROT_WRITE)
    engine.reg_write(arm.UC_ARM64_REG_SP, STACK + STACK_SIZE)
    engine.reg_write(arm.UC_ARM64_REG_LR, CODE + len(code))
    started = time.monotonic()

    def fail(reason):
        if report["error"] is None:
            report["error"] = reason
        engine.emu_stop()

    def on_code(_engine, address, size, _user):
        if address < CODE or address + size > CODE + len(code):
            fail(f"invalid_pc: 0x{address:x} outside guest code")
        elif report["instructions"] >= instruction_limit:
            fail("budget_instructions: guest exceeded instruction limit")
        elif time.monotonic() - started >= time_limit:
            fail("budget_time: guest exceeded wall-clock limit")
        else:
            report["instructions"] += 1

    def on_memory(_engine, access, address, size, value, _user):
        fail(f"guest_memory_fault: access={access} address=0x{address:x} size={size}")
        return False

    def on_interrupt(_engine, interrupt, _user):
        nonlocal frame
        pc = engine.reg_read(arm.UC_ARM64_REG_PC)
        # Unicorn ARM64 advances PC by four before delivering an SVC interrupt.
        site = pc - 4
        if interrupt != 2 or not CODE <= site <= CODE + len(code) - 4:
            fail(f"guest_interrupt: unsupported interrupt {interrupt} at 0x{pc:x}")
            return
        word = int.from_bytes(engine.mem_read(site, 4), "little")
        if word & 0xFFE0001F != 0xD4000001:
            fail(f"guest_interrupt: interrupt {interrupt} did not originate from SVC")
            return
        svc = (word >> 5) & 0xFFFF
        values = [engine.reg_read(reg) for reg in (arm.UC_ARM64_REG_X0,
                  arm.UC_ARM64_REG_X1, arm.UC_ARM64_REG_X2,
                  arm.UC_ARM64_REG_X3, arm.UC_ARM64_REG_X4)]
        report["svc_calls"].append({"id": svc, "site": site, "resume_pc": pc,
                                    "args": values})
        if svc == LOG:
            # Bound diagnostics even for a guest looping on log.
            if len(report["logs"]) >= 32:
                fail("log_limit: more than 32 log calls")
            else:
                report["logs"].append(values[0])
        elif svc == PRESENT:
            pointer, size, width, height, stride = values
            if frame is not None:
                fail("invalid_present: only one frame allowed")
            elif not (1 <= width <= 1024 and 1 <= height <= 1024
                      and width * 4 <= stride <= width * 4 + 4096
                      and stride % 4 == 0 and size == stride * height
                      and DATA <= pointer and size <= DATA_SIZE
                      and pointer + size <= DATA + DATA_SIZE):
                fail("invalid_present: pointer/size/width/height/stride outside framebuffer ABI")
            else:
                raw = bytes(engine.mem_read(pointer, size))
                pixels = b"".join(raw[y*stride:y*stride+width*4] for y in range(height))
                frame = Frame(width, height, stride, pixels)
                report["frame"] = {"width": width, "height": height,
                                   "guest_stride": stride, "format": "RGBA8",
                                   "bytes": len(pixels),
                                   "sha256": hashlib.sha256(pixels).hexdigest()}
        elif svc == EXIT:
            report["guest_exit_status"] = values[0]
            if values[0] != 0:
                fail(f"guest_exit: nonzero status {values[0]}")
            engine.emu_stop()
        else:
            fail(f"unknown_svc: 0x{svc:04x}")

    engine.hook_add(uc.UC_HOOK_CODE, on_code)
    engine.hook_add(uc.UC_HOOK_INTR, on_interrupt)
    engine.hook_add(uc.UC_HOOK_MEM_INVALID, on_memory)
    try:
        engine.emu_start(CODE, CODE + len(code), timeout=max(1, int(time_limit * 1_000_000)))
    except uc.UcError as error:
        fail(f"guest_fault: {error}")
    report["elapsed_seconds"] = round(time.monotonic() - started, 6)
    report["pc"] = engine.reg_read(arm.UC_ARM64_REG_PC)
    actual_sum = engine.reg_read(arm.UC_ARM64_REG_X20)
    report["cpu_check"] = {"name": "sum_1_to_100", "expected": 5050,
                           "actual": actual_sum, "passed": actual_sum == 5050}
    if report["error"] is None:
        if engine.query(uc.UC_QUERY_TIMEOUT):
            report["error"] = "budget_time: Unicorn execution timed out"
        elif report["guest_exit_status"] is None:
            report["error"] = "missing_exit: guest returned or fell through before exit"
        elif frame is None:
            report["error"] = "missing_present: guest exited without a frame"
        elif actual_sum != 5050:
            report["error"] = "cpu_check: ARM64 sum did not equal 5050"
        else:
            report["cpu_success"] = True
            report["process_exit_status"] = 0
    return report, frame
