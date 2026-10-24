# Windows Horizon homebrew ledger

## 2026-10-25

- Authorized scope: a separate NRO loader and five-SVC Horizon runtime for own
  freestanding AArch64 homebrew on Windows x64. Existing synthetic framebuffer
  behavior and its 16 tests remain intact. No Apple host/build/shader-chain changes,
  game/firmware/key downloads, global installs, commit or push.
- Primary references checked: libnx `svc.s`, `svc.h`, `switch_crt0.s`, `env.h`,
  `counter.h`; switch-tools `elf2nro.c`; Atmosphere `kern_svc_physical_memory.cpp`,
  `kern_k_page_table_base.cpp` QueryInfo and `svc_results.hpp`. Links and exact
  limitations are in `docs/windows-homebrew.md`.
- Decision: strict NRO0 version 0/flags 0 with RX/R/RW file segments and bss,
  validated MOD0 with DT_NULL only, explicit rejection of unsupported dynamic
  linking/relocations/assets/header extensions. Bounded file reads, 16 MiB file
  plus at most 16 MiB bss; no fault-driven mapping. Entry X0 is a read-only terminated
  24-byte homebrew environment, X1=-1, SP aligned, LR an unmapped return sentinel.
- Raw Horizon SVC subset: SetHeapSize 0x01, QueryMemory 0x06, ExitProcess 0x07,
  GetSystemTick 0x1e, OutputDebugString 0x27. No SleepThread/GetInfo. SetHeapSize
  uses X1 and returns W0/X1; QueryMemory writes 40 bytes and returns W0/W1 rather
  than writing an X1 pointer. Invalid arguments return real Results. ExitProcess
  has no guest status in the target libnx ABI; X0=99 is ignored in the own fixture.
- Memory: image 0x10000000, heap 0x20000000 (64 MiB cap, 2 MiB alignment), stack
  0x30000000 (1 MiB), environment 0x31000000, 39-bit address space. Heap retained
  bytes survive resizing and new/regrown bytes are zero. QueryMemory reports full
  mappings/gaps and a reserved region beyond the address space. Host clock is
  monotonic `perf_counter_ns`, scaled to 19.2 MHz with a per-run epoch. Windows
  Python 3.11's coarse `monotonic_ns` was observed to remain unchanged during one
  short run, so the high-resolution clock was selected and its conversion tested.
- Own source `dm/windows/homebrew.s` is 264 assembled bytes. Checked `homebrew.hex`
  is packed into a usable 12,288-byte NRO by `generate_homebrew.py`; normal launches
  build the NRO and pass its file path through the loader. Assertion failures use
  BRK, not success markers. Host success is only valid ExitProcess with no error;
  no fixed checksum/frame/sum/log expectation is used by the runtime.
- Setup: reused `.venv-windows` and pinned Unicorn 2.1.4 on E:. Installed optional
  `keystone-engine==0.9.2` only into that venv via
  `python -m pip install --disable-pip-version-check --no-cache-dir --only-binary=:all: -r dm/windows/requirements-fixtures.txt`.
  No global installation. Source reproduction passed with
  `.\.venv-windows\Scripts\python.exe -m dm.windows.generate_homebrew --check --output out/windows-homebrew-repro/selftest.nro`.
- Verification: `.\.venv-windows\Scripts\python.exe -m unittest discover -s dm/windows/tests -v`
  passed **34 tests** (18 homebrew + original 16, 31.558 s), no skips with the
  optional assembler installed. Saved output: `out/windows-homebrew-verification/tests.txt`.
  Covers malformed/truncated/version/segment/bss/MOD0 NROs, source reproduction,
  RX/R/NX/unmapped faults, wrapper-shaped raw ABI, heap invalid/grow/shrink/zero,
  QueryMemory ptr/layout/full ranges/outside-AS, debug explicit lengths/NUL/null/
  invalid pointer/hash, ticks, unknown SVC/BRK/return, count/time/trace/log limits,
  alternate generic NRO with no fixture-specific requirements, CLI success/failure
  reports, and mutation of initialized data causing the own guest's BRK assertion.
  Fresh test setup creates `out` before using temporary directories.
- Own smoke: file-loaded selftest exits normally after 66 instructions and SVCs
  0x27, 0x01, 0x06, 0x1e, 0x1e, 0x27, 0x07. Debug checkpoints are exact 16/17-byte
  strings. Launcher from `C:\Windows` with absolute `--guest` and `--output` passed;
  evidence at `out/windows-homebrew-launcher/report.json`.
- Lead's independent smoke: Python launcher exit 0 at `out/windows-homebrew-lead/`;
  actual .cmd from `C:\Windows` using its external NRO exit 0 at
  `out/windows-homebrew-lead-external/`. Preserve these ignored evidence folders.
- `python -m compileall -q dm/windows scripts/run_windows_homebrew.py` and
  `git diff --check` passed. No `/review` command/tool is exposed in this worker;
  manually reviewed bounds, permissions, raw registers, loader and CLI. Independent
  review was handled independently by the lead and passed.
- Independent reviewer final evidence: all 34 tests passed in 31.580 s, no skips;
  original 16 prototype tests/code remain unchanged and 18 tests cover this milestone.
  Reviewer checked NRO/MOD0/raw ABI against official sources and outside-address-space
  QueryMemory/zero-length debug against Atmosphere. Final review **PASS**, with no
  material blockers and implementation/documentation scope matching the milestone.
  `out/windows-homebrew-verification/verification-report.json` records exact source,
  assembled-code and NRO SHA-256 values, code/NRO byte lengths and both test timings.
  Final independent approval received on 2026-10-25; no implementation changes or
  broad test rerun were needed. Original first-frame ledger is preserved.
- Limits: single-thread, own freestanding source only; not full Horizon/libnx,
  arbitrary homebrew support, Switch hardware validation or game compatibility.
