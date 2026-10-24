#!/usr/bin/env python3
"""Run a freestanding AArch64 NRO using the Windows Horizon SVC/CMIF subset.

Spyder: select .venv-windows/Scripts/python.exe, open this file, press F5.
Terminal arguments override Settings; the script never installs dependencies.
"""
from pathlib import Path
import argparse
import json
import math
import sys

# ---- Settings (also used by Spyder F5) ----
GUEST = ""  # empty = build and load own checked-in homebrew fixture
OUTPUT = ""  # empty = <repository>/out/windows-homebrew
INSTRUCTION_LIMIT = 1_000_000
TIME_LIMIT = 5.0
LANGUAGE = "en-US"  # set/GetLanguageCode; own selftest asserts en-US
ROOT = Path(__file__).resolve().parents[1]


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(2, f"[RED] {message}\n")


def main(argv=None):
    parser = Parser(description=__doc__)
    parser.add_argument("--guest", type=Path, default=Path(GUEST) if GUEST else None,
                        help="external NRO matching the documented freestanding subset")
    parser.add_argument("--output", type=Path, default=Path(OUTPUT) if OUTPUT else
                        ROOT / "out/windows-homebrew")
    parser.add_argument("--instruction-limit", type=int, default=INSTRUCTION_LIMIT)
    parser.add_argument("--time-limit", type=float, default=TIME_LIMIT)
    parser.add_argument("--language", default=LANGUAGE,
                        help="set service language code (own selftest expects en-US)")
    args = parser.parse_args(argv)
    if not 1 <= args.instruction_limit <= 100_000_000:
        parser.error("--instruction-limit must be between 1 and 100000000")
    if not math.isfinite(args.time_limit) or not 0 < args.time_limit <= 60:
        parser.error("--time-limit must be greater than 0 and at most 60")
    sys.path.insert(0, str(ROOT))
    report = {"schema": 1, "success": False, "kernel_exit": False,
              "process_exit_status": 1, "error": None}
    try:
        args.output.mkdir(parents=True, exist_ok=True)
        try:
            from dm.windows.horizon import execute_nro
            from dm.windows.generate_homebrew import write_fixture
        except ImportError as error:
            raise RuntimeError("Dependency unavailable. Use .venv-windows/Scripts/python.exe; "
                               "see docs/windows-homebrew.md. " + str(error)) from error
        guest = args.guest if args.guest is not None else write_fixture(args.output / "selftest.nro")
        report = execute_nro(guest, instruction_limit=args.instruction_limit,
                             time_limit=args.time_limit, language=args.language)
        report["guest_path"] = str(guest.resolve())
        report["guest_selection"] = "external" if args.guest is not None else "own_selftest"
        for segment in report.get("loaded_segments", []):
            print(f"LOAD {segment['name']}: 0x{segment['base']:x} "
                  f"bytes={segment['memory_bytes']} perm={segment['permission']}")
        for call in report.get("svc_calls", []):
            inputs = ",".join(f"x{i}=0x{v:x}" for i, v in enumerate(call['in'][:3]))
            outputs = ",".join(f"x{i}=0x{v:x}" for i, v in enumerate(call['out'][:2]))
            print(f"SVC 0x{call['id']:02x} {call['name']} pc=0x{call['pc']:x}: "
                  f"{inputs} -> {outputs}")
            if "ipc" in call:
                ipc = call["ipc"]
                print(f"IPC {ipc.get('service')} command={ipc.get('command')} "
                      f"transport={ipc.get('transport_result')} "
                      f"service_result={ipc.get('service_result')} "
                      f"moved_handle={ipc.get('moved_handle')}")
        for log in report.get("logs", []):
            # repr avoids guest control characters altering the host terminal.
            print(f"DEBUG ({log['size']} bytes): {log['text']!r}")
        if report["success"]:
            print(f"KERNEL EXIT: svcExitProcess; {report['instructions']} instructions. "
                  "No fixture-specific host success check.")
        else:
            print(f"[RED] {report['error']}", file=sys.stderr)
    except KeyboardInterrupt:
        report.update(error="interrupted", success=False, process_exit_status=130)
        print("[RED] Interrupted.", file=sys.stderr)
    except Exception as error:
        report.update(error=str(error), success=False, process_exit_status=1)
        print(f"[RED] {error}", file=sys.stderr)
    try:
        path = args.output / "report.json"
        path.write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
        print(f"Report: {path.resolve()}")
    except OSError as error:
        print(f"[RED] Cannot save report: {error}", file=sys.stderr)
        return 1
    return report["process_exit_status"]


if __name__ == "__main__":
    raise SystemExit(main())
