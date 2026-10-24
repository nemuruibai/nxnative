#!/usr/bin/env python3
"""Run the synthetic ARM64 first-frame demo. Terminal arguments override Settings.

Spyder: configure its interpreter as .venv-windows/Scripts/python.exe, open this
file and press F5. Nothing is installed automatically by this script.
"""
from pathlib import Path
import argparse
import json
import math
import sys

# ---- Settings (also used by Spyder F5) ----
HEADLESS = False
AUTO_CLOSE = 0.0  # seconds after a verified draw; 0 keeps the window open
OUTPUT = ""       # empty = <repository>/out/windows-prototype
GUEST = ""        # empty = checked-in synthetic fixture; otherwise trusted raw ARM64
INSTRUCTION_LIMIT = 1_000_000
TIME_LIMIT = 10.0

ROOT = Path(__file__).resolve().parents[1]


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(2, f"[RED] {message}\n")


def main(argv=None):
    parser = Parser(description=__doc__)
    parser.add_argument("--headless", action=argparse.BooleanOptionalAction, default=HEADLESS)
    parser.add_argument("--auto-close", type=float, default=AUTO_CLOSE, metavar="SECONDS")
    parser.add_argument("--output", type=Path, default=Path(OUTPUT) if OUTPUT else
                        ROOT / "out" / "windows-prototype")
    parser.add_argument("--guest", type=Path, default=Path(GUEST) if GUEST else None,
                        help="trusted synthetic raw ARM64 binary (not NSO/NSP)")
    parser.add_argument("--instruction-limit", type=int, default=INSTRUCTION_LIMIT)
    parser.add_argument("--time-limit", type=float, default=TIME_LIMIT, metavar="SECONDS")
    args = parser.parse_args(argv)
    if not math.isfinite(args.auto_close) or not 0 <= args.auto_close <= 3600:
        parser.error("--auto-close must be between 0 and 3600 seconds")
    if not math.isfinite(args.time_limit) or not 0 < args.time_limit <= 60:
        parser.error("--time-limit must be greater than 0 and at most 60 seconds")
    if not 1 <= args.instruction_limit <= 100_000_000:
        parser.error("--instruction-limit must be between 1 and 100000000")
    report = {"schema": 1, "cpu_success": False, "display_success": False,
              "process_exit_status": 1, "error": None}
    sys.path.insert(0, str(ROOT))

    def save_report():
        (args.output / "report.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8")

    def displayed():
        report["process_exit_status"] = 0
        save_report()

    try:
        args.output.mkdir(parents=True, exist_ok=True)
        # Remove only our previous frame, so a failed rerun cannot leave stale proof.
        (args.output / "frame.bmp").unlink(missing_ok=True)
        try:
            from dm.windows.runtime import execute, CODE_SIZE
            from dm.windows.display import show_frame, write_bmp
        except ImportError as error:
            raise RuntimeError("Dependency unavailable. Use .venv-windows/Scripts/python.exe; "
                               "see docs/windows-prototype.md for setup. " + str(error)) from error
        code = None
        if args.guest:
            # Limit reads even if a user accidentally selects a large game file.
            with args.guest.open("rb") as stream:
                code = stream.read(CODE_SIZE + 1)
        report, frame = execute(code, instruction_limit=args.instruction_limit,
                                time_limit=args.time_limit)
        report["mode"] = "headless" if args.headless else "gui"
        if not report["cpu_success"]:
            print(f"[RED] {report['error']}", file=sys.stderr)
        else:
            print(f"CPU SUCCESS: ARM64 sum={report['cpu_check']['actual']}; "
                  f"{report['instructions']} instructions; guest-produced "
                  f"{frame.width}x{frame.height} RGBA8 frame.", flush=True)
            write_bmp(frame, args.output / "frame.bmp")
            report["frame"]["artifact"] = str((args.output / "frame.bmp").resolve())
            if args.headless:
                print("Headless: frame saved; display verification was not requested.", flush=True)
            else:
                report["display_status"] = "pending"
                report["process_exit_status"] = 1
                save_report()
                show_frame(frame, report, auto_close=args.auto_close, on_display=displayed)
        save_report()
        print(f"Report: {(args.output / 'report.json').resolve()}", flush=True)
        return report["process_exit_status"]
    except KeyboardInterrupt:
        report.update(error="interrupted", process_exit_status=130)
        print("[RED] Interrupted.", file=sys.stderr)
    except Exception as error:
        # CLI boundary also captures missing Tk, unavailable desktop and I/O errors.
        report.update(error=str(error), process_exit_status=1)
        if report.get("cpu_success") and not args.headless:
            report["display_status"] = "failed"
        print(f"[RED] {error}", file=sys.stderr)
    try:
        save_report()
    except OSError as error:
        print(f"[RED] Cannot save report: {error}", file=sys.stderr)
    return report["process_exit_status"]


if __name__ == "__main__":
    raise SystemExit(main())
