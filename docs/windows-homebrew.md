# Windows freestanding Horizon homebrew milestone

This separate Windows x64 path runs a self-authored, position-independent AArch64
homebrew NRO through Unicorn 2.1.4 and handles **five real Horizon SVC numbers and
register conventions**. It is a bounded, single-thread kernel subset. The default
program checks initialized data, zero bss, entry arguments/stack alignment, heap
allocation/store/load, QueryMemory and tick ordering, prints two checkpoints, then
calls ExitProcess. Failed assertions execute `BRK #1` and fail the run.

This is a freestanding assembly homebrew with standard NRO0 and MOD0 headers,
not a full libnx application. There has been no Switch hardware test. It provides
no IPC, handles, threading, TLS, scheduling, filesystem, services, input, audio,
graphics, NVN/Vulkan, NSO/NSP loading, or game compatibility. Arbitrary third-party
NROs and libnx initialization are unsupported. No firmware, game, keys or Nintendo
code is required. Guest code has no host-file API; Unicorn is not advertised as a
security sandbox. The Apple host/build/shader chain is unchanged.

## Setup and run (PowerShell)

Use 64-bit Python 3.11. Keep the environment and outputs on the checkout drive.
If the [first-frame prototype](windows-prototype.md) is already set up, reuse its
environment; no additional runtime package is needed.
Open PowerShell in your checkout, or use `Set-Location <your-checkout>` first.

```powershell
# Only if the environment does not already exist:
python -m venv .venv-windows
.\.venv-windows\Scripts\python.exe -m pip install --no-cache-dir --only-binary=:all: -r dm/windows/requirements.txt

# Build selftest.nro from checked bytes, load that file, run, save report:
.\scripts\run_windows_homebrew.cmd --output out/windows-homebrew

# Equivalent Python entry point:
.\.venv-windows\Scripts\python.exe scripts/run_windows_homebrew.py

# External freestanding NRO matching this exact subset:
.\scripts\run_windows_homebrew.cmd --guest E:\my-homebrew\example.nro --output out/windows-homebrew-external

# Deliberate bounded failure (exit status 1):
.\scripts\run_windows_homebrew.cmd --instruction-limit 1 --output out/windows-homebrew-limit
```

Double-click `scripts/run_windows_homebrew.cmd` to run the default homebrew. With
no arguments, it pauses after both success and failure so the console is readable.
With arguments it does not pause, forwards all arguments, and returns Python's exit
status. It resolves paths from its own location and works from another directory.
Relative `--guest`/`--output` paths are relative to the caller's working directory.

For Spyder F5, select `.venv-windows/Scripts/python.exe` as the interpreter and open
`scripts/run_windows_homebrew.py`. Edit its Settings block for guest/output/budgets;
terminal options override those settings. There is no Tk window on this path.

`report.json` separates loaded segments, entry registers, logs, SVC input/output
registers/results, execution budgets, errors and kernel exit. Console output quotes
debug strings so guest control characters cannot alter the terminal. Each stored
debug entry includes the exact raw bytes as hex, byte count, SHA-256 and decoded
UTF-8 text (replacement characters for invalid UTF-8). The runtime has no checksum,
image identity, expected text, framebuffer or arithmetic requirement for success:
**success means ExitProcess was reached with no host/runtime error**. ExitProcess
has no exit-status argument in the targeted libnx ABI; X0 is ignored. Host exit
statuses are 0 for success, 1 for execution/load/I/O failure, 2 for invalid command
arguments and 130 for interruption. A runtime failure replaces the previous report;
argument validation fails before output/report creation and can leave an old report.
The current command's exit status is authoritative. A previously built `selftest.nro`
may remain in an output folder; it is an input artifact, not proof of success.

## Build and reproduce

The checked-in `dm/windows/homebrew.s` is own AArch64 source. `homebrew.hex` contains
only its assembled code. `generate_homebrew.py` packs it into a usable standard NRO
with three file segments, a zero-initialized bss, a MOD0 and an Elf64_Dyn DT_NULL.
Default launches always use the NRO **file loader**, rather than a raw-code shortcut.
No devkitA64/clang or global installation is required.

```powershell
# Assemble only when reproducing/editing source; install into the existing E: venv:
.\.venv-windows\Scripts\python.exe -m pip install --no-cache-dir --only-binary=:all: -r dm/windows/requirements-fixtures.txt
.\.venv-windows\Scripts\python.exe -m dm.windows.generate_homebrew --check --output out/windows-homebrew-repro/selftest.nro

# After editing homebrew.s, regenerate checked code and produce the NRO:
.\.venv-windows\Scripts\python.exe -m dm.windows.generate_homebrew --regenerate --output out/windows-homebrew-repro/selftest.nro

# Pack checked bytes without any assembler dependency:
.\.venv-windows\Scripts\python.exe -m dm.windows.generate_homebrew --output out/windows-homebrew-build/selftest.nro

# Both the previous 16 prototype tests and new homebrew tests:
.\.venv-windows\Scripts\python.exe -m unittest discover -s dm/windows/tests -v
```

Optional regeneration is pinned to Keystone 0.9.2. Source correspondence is checked
by a test when that optional dependency exists; otherwise that one test is skipped.
Normal execution still only needs the pinned Unicorn runtime dependency.

## Supported NRO and memory layout

The loader validates NRO0 version 0, flags 0, exact declared file length, nonzero
page-aligned segment offsets/sizes, ordering/nonoverlap, and text at offset 0 with
an entry branch past the 0x80-byte header. File offsets are image-relative virtual
offsets. Gaps remain unmapped. File size is capped at 16 MiB and bss at 16 MiB
(maximum image plus bss 32 MiB). Data is copied and bss is zero-filled. Text is RX,
rodata R, data/bss RW; stack/heap RW and environment R. There is no mapping on fault.

The MOD0 must be inside a file segment, with a valid aligned dynamic-table pointer,
bss exactly following the data segment and matching the NRO bss size. Only a single
DT_NULL dynamic representation is accepted: no relocation/import/dynamic linking.
Nonzero unwind/module-object fields, libnx MOD0 extensions, NRO optional header
sections, AlignedHeader, assets/trailing bytes, NSO/ELF and unsupported versions or
flags are rejected explicitly. This is a deliberately small supported file subset,
not a claim to implement every standard NRO feature.

| Area | Address | Limit / entry behavior |
| --- | --- | --- |
| NRO | `0x10000000` | Text entry at image base, branch over NRO header |
| Heap | `0x20000000` | Initially unmapped; cap 64 MiB |
| Stack | `0x30000000` | 1 MiB, SP at top and 16-byte aligned |
| Environment | `0x31000000` | Read-only page; valid 24-byte ConfigEntry EndOfList |
| Return sentinel | `0x32000000` | LR points to unmapped address; return fails |
| Address space | `[0, 2^39)` | Unmapped gaps queryable; never silently mapped |

Homebrew entry receives X0 pointing at the terminated environment and X1=`UINT64_MAX`.
The minimal environment supplies no optional capabilities/loader services. A `RET`
is a fault, not process completion. This program uses ExitProcess explicitly.

## Implemented SVC subset

The following are **raw SVC register interfaces**. libnx C wrappers save output
pointers and store returned registers themselves; passing the C signature directly
to a kernel handler would be incorrect.

| Number / name | Inputs | Outputs and behavior |
| --- | --- | --- |
| `0x01 SetHeapSize` | X1=size | W0=Result, X1=heap base on success. X0 is not dereferenced. Size is 2 MiB aligned, including 0; misalignment returns `0xca01` InvalidSize. Above this runtime's 64 MiB cap returns `0xd001` OutOfMemory. Invalid requests do not mutate the heap. Grow/shrink preserves retained bytes, new/regrown bytes are zero. |
| `0x06 QueryMemory` | X0=writable MemoryInfo pointer, X2=address | W0=Result; W1=PageInfo=0 on success. Exactly 40 bytes `<QQIIIIII>`: base, size, type, attributes, permission, IPC count, device count, padding. Invalid destination returns `0xe601` InvalidPointer. X1 is not an output pointer. Reports whole mappings and unmapped gaps; addresses at/above 2^39 return the reserved region `[2^39, 2^64)`, type `0x10`, permission 0. |
| `0x07 ExitProcess` | No arguments | Stops process normally. No invented guest exit code. |
| `0x1e GetSystemTick` | No arguments | X0=19.2 MHz ticks from a monotonic high-resolution host clock, with per-run epoch; nondecreasing, not required to increase on each call. No Result. |
| `0x27 OutputDebugString` | X0=pointer, X1=explicit byte length | W0=Result. Reads exactly those bytes from readable guest memory, including embedded NULs. Invalid range returns `0xe601`; length 0 succeeds even for nullptr. |

MemoryInfo types are 8 for NRO text/rodata/environment, 9 for mutable NRO data/bss,
5 for heap, `0x0b` for stack, 0 for unmapped. Permissions are R=1, W=2, X=4.
Attributes and reference counts are zero in this subset.

Unknown SVCs fail with the number and PC. SleepThread, GetInfo and Break SVCs are
not implemented. Faults (including the fixture's BRK) stop the run. Default bounds
are 1,000,000 instructions and 5 seconds; configurable limits are 1..100,000,000
instructions and greater than 0 through 60 seconds. SVC trace is capped at 256 calls
and debug output at 16 KiB total. Exceeding a host resource bound stops the run with
a host error, rather than returning an invented Horizon Result. Even zero-length
debug floods are bounded by the SVC trace cap.

## Primary references

- [libnx SVC assembly wrappers](https://github.com/switchbrew/libnx/blob/master/nx/source/kernel/svc.s)
  establish raw register conventions and no-argument ExitProcess.
- [libnx SVC declarations and MemoryInfo](https://github.com/switchbrew/libnx/blob/master/nx/include/switch/kernel/svc.h)
  define numbers, types, permissions and the memory structure.
- [libnx startup](https://github.com/switchbrew/libnx/blob/master/nx/source/runtime/switch_crt0.s)
  and [homebrew environment](https://github.com/switchbrew/libnx/blob/master/nx/include/switch/runtime/env.h)
  describe entry arguments, MOD0 and ConfigEntry.
- [switch-tools elf2nro](https://github.com/switchbrew/switch-tools/blob/master/src/elf2nro.c)
  defines the standard NRO packing and segment alignment.
- [Atmosphere physical-memory SVCs](https://github.com/Atmosphere-NX/Atmosphere/blob/master/libraries/libmesosphere/source/svc/kern_svc_physical_memory.cpp),
  [page table](https://github.com/Atmosphere-NX/Atmosphere/blob/master/libraries/libmesosphere/source/kern_k_page_table_base.cpp)
  and [SVC Results](https://github.com/Atmosphere-NX/Atmosphere/blob/master/libraries/libvapours/include/vapours/results/svc_results.hpp)
  guide heap alignment, QueryMemory boundaries and Result encoding (description shifted left 9, module 1).
- [libnx counter helpers](https://github.com/switchbrew/libnx/blob/master/nx/include/switch/arm/counter.h)
  supply the nanosecond/tick ratio (12/625 = 19.2 MHz).

These references guide this limited implementation; they do not imply complete
Horizon fidelity or third-party compatibility. No upstream source is bundled into
the own fixture.
