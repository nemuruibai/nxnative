# Windows ARM64 first-frame prototype

This standalone prototype runs real synthetic AArch64 machine code through
Unicorn on Windows x64 and shows the code's framebuffer in a Tk window. On the
Ryzen host this is CPU emulation/translation, rather than native ARM execution.
It does not use games, keys, firmware, extracted assets, or a game profile.

A separate [freestanding homebrew path](windows-homebrew.md) now loads own NRO
files using five real Horizon SVCs. This synthetic demo and its private ABI remain
independent and unchanged.

The Apple host and its graphics/shader build remain independent. This milestone
is not an NSO/NSP loader, Horizon implementation, NVN/Vulkan backend, game
compatibility claim, or performance proof. The displayed pixels are a **software
framebuffer**; they do not validate the RTX GPU or Switch graphics emulation.

## Setup once (PowerShell, repository root)

Requires 64-bit Python 3.11 with Tk (the normal python.org Windows installation).
Keep the environment and outputs on the repository drive, not the home drive.
No global packages are installed. The launcher never installs or downloads.

Open PowerShell in your checkout, or use `Set-Location <your-checkout>` first.

```powershell
python -m venv .venv-windows
.\.venv-windows\Scripts\python.exe -m pip install --no-cache-dir --only-binary=:all: -r dm/windows/requirements.txt
```

The tested dependency is pinned to `unicorn==2.1.4`. Tk comes with Python and is
not a pip package. See the [Unicorn API tutorial](https://www.unicorn-engine.org/docs/tutorial.html)
and [official PyPI package](https://pypi.org/project/unicorn/2.1.4/).

## Run and inspect

Double-click `scripts/run_windows_prototype.cmd` after setup. The window stays
open until closed. The command resolves its own location, so it also works from
another working directory, and passes command-line arguments and exit status.
It pauses on an error only when run without arguments, for double-click use.

```powershell
# Real window; automatically close one second after a verified draw
.\scripts\run_windows_prototype.cmd --auto-close 1 --output out/windows-prototype-gui

# CPU + saved image only; this does not claim display success
.\.venv-windows\Scripts\python.exe scripts/run_windows_prototype.py --headless

# Regressions, including mutated instructions and malformed guests
.\.venv-windows\Scripts\python.exe -m unittest discover -s dm/windows/tests -v
```

Default artifacts are `out/windows-prototype/frame.bmp` and `report.json`, even
when started from elsewhere. A supplied relative `--output` is relative to the
calling working directory. The BMP can be opened in Windows Photos or Paint.
The JSON distinguishes `cpu_success` from `display_success`, records the SVC
arguments and instruction count, and hashes the exact packed RGBA guest frame.
The BMP stores those same RGB values as bottom-up BGR without alpha.

Successful headless runs deliberately have `display_success: false` and
`display_status: not_requested`. A GUI run records success only after the window
and image widget are mapped, Tk has processed drawing events, and sampled Tk
photo pixels match the guest snapshot. This checks the application's display
path; it is not independent screenshot verification of the desktop compositor.
The JSON is saved after display verification and again on close. Display errors
leave CPU success separate and return nonzero. A pending GUI report has nonzero
process status until display is verified. Closing the window normally is success.

The script supports Spyder F5: select `.venv-windows/Scripts/python.exe` as the
interpreter and edit the Settings block at the top of the script. Terminal or
Spyder run-configuration arguments override those values. An unavailable
dependency/desktop or invalid guest prints `[RED] ...` and exits nonzero; ordinary
CLI errors return 2, runtime/display errors 1, and interruption 130.

Argument-validation errors (for example, `--time-limit 0`) exit before artifact
cleanup and leave any previous frame/report untouched. The current process exit
status is authoritative; existing artifacts alone do not prove the latest run
succeeded. Runtime failures replace the report and remove the previous frame.

## Guest and synthetic ABI

`dm/windows/guest.s` is the human-readable source and `guest.hex` the checked-in
little-endian instruction bytes (144 bytes). The launcher does not assemble code.
The guest loops to sum 1 through 100 into X20 (5050), logs it, then executes nested
pixel loops with arithmetic, branches and byte stores. For every `(x,y)` in the
256 x 160 frame it writes `R=5*x mod 256`, `G=3*y mod 256`,
`B=7*(x XOR y) mod 256`, and `A=255`. No host routine draws test pixels. The fixed
RGBA SHA-256 is
`be047d33b397d1a415776898f430734c171b6d4a49a079aaf47b9d4735dd29c9`.
The tests independently derive every expected pixel, and changing the guest's
red arithmetic instruction changes the frame hash.

| Mapping | Range | Guest permissions |
|---|---|---|
| Code | `0x100000..0x100fff` | Read + execute |
| Framebuffer/data | `0x200000..0x23ffff` | Read + write |
| Stack | `0x300000..0x30ffff` | Read + write |

SP starts at the stack top; LR points just past the guest to catch early returns.
Only the loaded code bytes may execute. Code is immutable and data/stack are
non-executable. There is no auto-mapping on a guest memory fault.

These SVC immediates are **private synthetic demo calls, unrelated to Horizon**:

| SVC | Registers | Host action |
|---|---|---|
| `0x7f01` | X0 = unsigned integer | Append bounded diagnostic log (maximum 32) |
| `0x7f02` | X0 = pointer, X1 = size, X2 = width, X3 = height, X4 = stride | Validate and copy one RGBA8 snapshot |
| `0x7f03` | X0 = exit status | Stop; only zero is success |

Present requires width/height 1..1024, stride divisible by four, between width*4
and width*4+4096, size exactly stride*height, and the entire buffer within the
256 KiB data map. Padding is omitted from the saved snapshot. Multiple presents
are rejected. The SVC hook checks interrupt number 2 and the actual SVC encoding
at PC-4; a tiny regression verifies Unicorn's exception PC convention. Other
interrupts, unknown SVCs, permission faults, out-of-range accesses, invalid code,
missing present/exit, failed arithmetic or a nonzero guest exit fail explicitly.

Defaults are 1,000,000 guest instructions and 10 seconds. An instruction hook
enforces the count and time deadline; Unicorn also gets a native time limit.
`--instruction-limit N` and `--time-limit SECONDS` override these (maximum
100,000,000 instructions and 60 seconds). Exhaustion is an error, not a frame.
`--guest path.bin` accepts only a trusted synthetic raw binary, 4..4096 bytes and
a multiple of four, under this exact ABI and sum check. Reads are bounded. This
is a development tool, not an isolation boundary for untrusted binaries.

## Optional reproducible assembly

Keystone is optional and absent from runtime requirements. To regenerate or check
the bytes in a local development environment:

```powershell
.\.venv-windows\Scripts\python.exe -m pip install --no-cache-dir keystone-engine==0.9.2
.\.venv-windows\Scripts\python.exe -m dm.windows.generate_guest --check
# After intentionally editing guest.s:
.\.venv-windows\Scripts\python.exe -m dm.windows.generate_guest
```

The generator strips comments and assembles the whole source at `0x100000`;
`--check` compares the result with the checked-in fixture. No system assembler
is required. Expected-output regressions must be updated deliberately if the
source changes the demonstration.

## Future seams

`runtime.py` owns CPU execution, mappings and the synthetic service dispatch;
`Frame` is an immutable copied snapshot; `display.py` owns BMP/Tk presentation;
the script owns options and reports. A future executable loader, real service
model, graphics command backend and faster CPU backend each need separate
design and validation. None can be inferred from this first frame.
