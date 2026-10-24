"""Single-thread Horizon SVC subset on Unicorn, independent of the demo ABI.

Not a security boundary for untrusted native inputs. No guest host-file access.
"""
import hashlib
import math
import struct
import time

import unicorn as uc
from unicorn import arm64_const as arm

from .nro import PAGE, Nro, load_nro
from .ipc import IPC_SIZE, IpcError, Services, MAX_SESSIONS, OUT_OF_RANGE

IMAGE = 0x10000000
HEAP = 0x20000000
HEAP_CAP = 64 * 1024 * 1024
HEAP_ALIGN = 2 * 1024 * 1024
STACK = 0x30000000
STACK_SIZE = 1024 * 1024
ENV = 0x31000000
RETURN = 0x32000000  # deliberately unmapped; returning is not ExitProcess
TLS = 0x33000000
AS_END = 1 << 39
INVALID_SIZE = 0xCA01
OUT_OF_MEMORY = 0xD001
INVALID_POINTER = 0xE601
MAX_TRACE = 256
MAX_LOG_BYTES = 16 * 1024
REGS = (arm.UC_ARM64_REG_X0, arm.UC_ARM64_REG_X1, arm.UC_ARM64_REG_X2,
        arm.UC_ARM64_REG_X3, arm.UC_ARM64_REG_X4, arm.UC_ARM64_REG_X5,
        arm.UC_ARM64_REG_X6, arm.UC_ARM64_REG_X7)
NAMES = {1: "SetHeapSize", 6: "QueryMemory", 7: "ExitProcess",
         0x16: "CloseHandle", 0x1E: "GetSystemTick", 0x1F: "ConnectToNamedPort",
         0x21: "SendSyncRequest", 0x27: "OutputDebugString"}


def execute_nro(nro, *, instruction_limit=1_000_000, time_limit=5.0,
                language="en-US", session_capacity=MAX_SESSIONS):
    """Run a validated NRO or file path; success means ExitProcess without errors.

    SVC register convention is the raw kernel ABI, not libnx's C wrappers.
    Invalid guest arguments return Horizon Results; host bounds stop execution.
    """
    report = {"schema": 1, "backend": f"Unicorn {uc.__version__} ARM64",
              "abi": "Horizon-AArch64-eight-svc-cmif-subset", "kernel_exit": False,
              "termination": None, "success": False, "process_exit_status": 1,
              "instructions": 0, "svc_calls": [], "logs": [], "error": None,
              "loaded_segments": [], "heap_size": 0,
              "budgets": {"instructions": instruction_limit, "seconds": time_limit,
                          "svc_trace": MAX_TRACE, "debug_bytes": MAX_LOG_BYTES}}
    if (not isinstance(instruction_limit, int) or not 1 <= instruction_limit <= 100_000_000
            or not math.isfinite(time_limit) or not 0 < time_limit <= 60):
        report["error"] = "invalid_budget"
        return report
    try:
        services = Services(language, session_capacity)
    except ValueError as error:
        report["error"] = str(error)
        return report
    try:
        # Even supplied Nro objects are revalidated, so metadata cannot bypass checks.
        from .nro import parse_nro
        nro = parse_nro(nro.image) if isinstance(nro, Nro) else load_nro(nro)
    except (OSError, ValueError) as error:
        report["error"] = f"nro_load: {error}"
        return report
    report["image_sha256"] = hashlib.sha256(nro.image).hexdigest()
    engine = uc.Uc(uc.UC_ARCH_ARM64, uc.UC_MODE_ARM)
    regions = []  # [base, size, MemoryType, Permission, label]

    def map_region(base, size, kind, perm, label, data=b""):
        engine.mem_map(base, size, perm)
        if data:
            engine.mem_write(base, data)
        regions.append([base, size, kind, perm, label])

    for s in nro.segments:
        # NRO executable mappings are ModuleCodeStatic/Mutable (8/9).
        extent = s.size + (nro.bss_size if s.name == "data" else 0)
        map_region(IMAGE+s.offset, extent, 9 if s.name == "data" else 8,
                   s.permission, s.name, nro.image[s.offset:s.offset+s.size])
        report["loaded_segments"].append({"name": s.name, "base": IMAGE+s.offset,
                                         "file_bytes": s.size, "memory_bytes": extent,
                                         "permission": s.permission})
    map_region(STACK, STACK_SIZE, 0xB, 3, "stack")
    # Homebrew ABI ConfigEntry {u32 key, u32 flags, u64 value[2]}, EndOfList=0.
    map_region(ENV, PAGE, 8, 1, "environment", bytes(24))
    map_region(TLS, PAGE, 0xC, 3, "tls")
    engine.reg_write(arm.UC_ARM64_REG_TPIDRRO_EL0, TLS)
    report["tls"] = {"base": TLS, "mapped_bytes": PAGE, "ipc_bytes": IPC_SIZE,
                     "thread_local_region_bytes": 0x200, "other_fields_implemented": False}
    report["services"] = {"language": language, "synthetic_pid": 1,
                          "session_capacity": session_capacity,
                          "handle_lifecycle": services.lifecycle}
    engine.reg_write(REGS[0], ENV)
    engine.reg_write(REGS[1], (1 << 64)-1)
    engine.reg_write(arm.UC_ARM64_REG_SP, STACK+STACK_SIZE)
    engine.reg_write(arm.UC_ARM64_REG_LR, RETURN)
    report["entry"] = {"pc": IMAGE, "x0": ENV, "x1": (1 << 64)-1,
                       "sp": STACK+STACK_SIZE, "lr": RETURN}
    # perf_counter is monotonic and high resolution on Windows Python 3.11;
    # monotonic_ns can advance only once per Windows timer quantum there.
    started = time.perf_counter_ns()
    log_bytes = 0

    def fail(message):
        if report["error"] is None:
            report["error"] = message
        engine.emu_stop()

    def accessible(pointer, size, permission):
        if not 0 <= pointer < AS_END or size < 0 or pointer+size > AS_END:
            return False
        cursor = pointer
        for base, length, _kind, perm, _label in sorted(regions):
            if base <= cursor < base+length:
                if perm & permission != permission:
                    return False
                cursor = min(pointer+size, base+length)
                if cursor == pointer+size:
                    return True
        return False

    def query(address):
        if address >= AS_END:
            return AS_END, (1 << 64)-AS_END, 0x10, 0
        low, high = 0, AS_END
        for base, size, kind, perm, _label in sorted(regions):
            if base <= address < base+size:
                return base, size, kind, perm
            if base+size <= address:
                low = base+size
            elif base > address:
                high = base
                break
        return low, high-low, 0, 0

    def code_hook(_engine, address, size, _user):
        if report["instructions"] >= instruction_limit:
            fail("budget_instructions: instruction limit reached")
        elif time.perf_counter_ns()-started >= time_limit*1e9:
            fail("budget_time: wall-clock limit reached")
        else:
            report["instructions"] += 1

    def memory_hook(_engine, access, address, size, _value, _user):
        fail(f"guest_memory_fault: access={access} address=0x{address:x} size={size}")
        return False

    def interrupt_hook(_engine, interrupt, _user):
        nonlocal log_bytes
        pc = engine.reg_read(arm.UC_ARM64_REG_PC)
        site = pc-4
        if interrupt != 2 or not accessible(site, 4, 5):
            fail(f"guest_interrupt: interrupt={interrupt} pc=0x{pc:x}")
            return
        word = int.from_bytes(engine.mem_read(site, 4), "little")
        if word & 0xFFE0001F != 0xD4000001:
            fail(f"guest_interrupt: non-SVC at 0x{site:x}")
            return
        number = (word >> 5) & 0xFFFF
        if len(report["svc_calls"]) >= MAX_TRACE:
            fail(f"host_trace_limit: svc=0x{number:x} pc=0x{site:x}")
            return
        args = [engine.reg_read(r) for r in REGS]
        trace = {"id": number, "name": NAMES.get(number, "unsupported"),
                 "pc": site, "resume_pc": pc, "in": args, "out": None}
        report["svc_calls"].append(trace)
        result = 0
        if number == 1:
            size = args[1]
            old = report["heap_size"]
            if size % HEAP_ALIGN:
                result = INVALID_SIZE
            elif size > HEAP_CAP:
                result = OUT_OF_MEMORY
            else:
                if size < old:
                    engine.mem_unmap(HEAP+size, old-size)
                elif size > old:
                    engine.mem_map(HEAP+old, size-old, 3)
                regions[:] = [r for r in regions if r[4] != "heap"]
                if size:
                    regions.append([HEAP, size, 5, 3, "heap"])
                report["heap_size"] = size
                engine.reg_write(REGS[1], HEAP)
            engine.reg_write(REGS[0], result)
        elif number == 6:
            if not accessible(args[0], 40, 2):
                result = INVALID_POINTER
            else:
                base, size, kind, perm = query(args[2])
                engine.mem_write(args[0], struct.pack("<QQIIIIII", base, size, kind,
                                                    0, perm, 0, 0, 0))
                engine.reg_write(REGS[1], 0)  # PageInfo, never a guest pointer
            engine.reg_write(REGS[0], result)
        elif number == 7:
            report["kernel_exit"] = True
            report["termination"] = "svcExitProcess"
            engine.emu_stop()  # target libnx ABI has no exit-status argument
        elif number == 0x1E:
            tick = (time.perf_counter_ns()-started)*19_200_000//1_000_000_000
            engine.reg_write(REGS[0], tick)
        elif number == 0x1F:
            name = bytearray()
            handle = None
            for offset in range(12):
                if not accessible(args[1]+offset, 1, 1):
                    result = INVALID_POINTER
                    break
                byte = engine.mem_read(args[1]+offset, 1)[0]
                if byte == 0:
                    result, handle = services.connect(bytes(name))
                    break
                name.append(byte)
            else:
                result = OUT_OF_RANGE
            trace["port_hex"] = name.hex()
            engine.reg_write(REGS[0], result)
            engine.reg_write(REGS[1], handle if handle is not None else 0)
        elif number == 0x16:
            result = services.close(args[0])
            engine.reg_write(REGS[0], result)
        elif number == 0x21:
            # This fixed-size mapped TLS buffer is the only implicit IPC memory.
            request = bytes(engine.mem_read(TLS, IPC_SIZE))
            trace["ipc"] = {"request_hex": request.hex(), "response_hex": None}
            try:
                result, packet, decoded = services.dispatch(args[0], request)
                trace["ipc"].update(decoded, transport_result=result)
                if packet is not None:
                    engine.mem_write(TLS, packet)
                    trace["ipc"]["response_hex"] = packet.hex()
            except IpcError as error:
                fail(str(error))
            engine.reg_write(REGS[0], result)
        elif number == 0x27:
            pointer, size = args[:2]
            if size and not accessible(pointer, size, 1):
                result = INVALID_POINTER
            elif size > MAX_LOG_BYTES-log_bytes:
                fail("host_log_limit: debug output byte budget reached")
            else:
                raw = bytes(engine.mem_read(pointer, size)) if size else b""
                log_bytes += size
                report["logs"].append({"size": size, "hex": raw.hex(),
                                       "text": raw.decode("utf-8", errors="replace"),
                                       "sha256": hashlib.sha256(raw).hexdigest()})
            engine.reg_write(REGS[0], result)
        else:
            fail(f"unsupported_svc: 0x{number:04x} pc=0x{site:x}")
        trace["out"] = [engine.reg_read(r) for r in REGS]
        if number in (1, 6, 0x16, 0x1F, 0x21, 0x27) and report["error"] is None:
            trace["result"] = result

    engine.hook_add(uc.UC_HOOK_CODE, code_hook)
    engine.hook_add(uc.UC_HOOK_MEM_INVALID, memory_hook)
    engine.hook_add(uc.UC_HOOK_INTR, interrupt_hook)
    try:
        engine.emu_start(IMAGE, AS_END, timeout=max(1, int(time_limit*1_000_000)))
    except uc.UcError as error:
        fail(f"guest_fault: {error}")
    if report["error"] is None and not report["kernel_exit"]:
        fail("budget_time: Unicorn timeout" if engine.query(uc.UC_QUERY_TIMEOUT)
             else "missing_exit: execution stopped without ExitProcess")
    report["pc"] = engine.reg_read(arm.UC_ARM64_REG_PC)
    report["services"]["live_handles"] = sorted(services.sessions)
    report["elapsed_seconds"] = (time.perf_counter_ns()-started)/1e9
    report["success"] = report["kernel_exit"] and report["error"] is None
    report["process_exit_status"] = 0 if report["success"] else 1
    return report
