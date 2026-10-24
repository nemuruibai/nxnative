# Windows Horizon IPC implementation ledger

## Brief and decisions (2026-10-25, before implementation)

Implement the authorized own freestanding ARM64 NRO path: ConnectToNamedPort
`sm:` -> RegisterClient -> GetService `set` -> GetLanguageCode -> CloseHandle
for both sessions -> normal ExitProcess. Preserve earlier heap/query/tick checks
and the separate synthetic framebuffer prototype. No firmware or game inputs,
full libnx startup, scheduler, arbitrary third-party compatibility or commits.

Use synchronous host dispatch, a per-execution bounded monotonically allocated
positive handle table, per-SM-session registration, and a zero RW TLS page at
0x33000000 exposed through TPIDRRO_EL0. Only the first 0x100 bytes are IPC;
the remaining TLS fields are not implemented. The synthetic kernel PID is 1.
Support CMIF request type 4/version 0/token 0, zero descriptors/incoming handles,
optional PID for SM registration, and one outgoing moved set handle. Parse all
declared extents before dispatch/allocation. Unsupported framing/services stop
with explicit runtime errors rather than invented successful stubs. Default
language en-US; explicit supported configuration permits causal mutation tests.

## Primary references verified before implementation

- [libnx svc.s](https://github.com/switchbrew/libnx/blob/master/nx/source/kernel/svc.s):
  raw ConnectToNamedPort X1 name/W1 handle, SendSyncRequest W0, CloseHandle W0.
- [HIPC](https://github.com/switchbrew/libnx/blob/master/nx/include/switch/sf/hipc.h)
  and [CMIF](https://github.com/switchbrew/libnx/blob/master/nx/include/switch/sf/cmif.h):
  bit layouts, PID then handle arrays, 16-byte alignment, SFCI/SFCO headers;
  libnx reserves alignment allowance so raw size need not equal payload minimum.
- [SM](https://github.com/switchbrew/libnx/blob/master/nx/source/services/sm.c):
  legacy CMIF registration includes a PID descriptor and raw u64 placeholder;
  modern libnx may use TIPC, which this subset does not support.
- [set](https://github.com/switchbrew/libnx/blob/master/nx/source/services/set.c):
  GetLanguageCode command 0 returns u64.
- [ARM64 TLS](https://github.com/Atmosphere-NX/Atmosphere/blob/master/libraries/libvapours/include/vapours/svc/arch/arm64/svc_thread_local_region.hpp):
  0x100-byte message buffer in 0x200-byte thread local region, TPIDRRO_EL0.
- [port SVC](https://github.com/Atmosphere-NX/Atmosphere/blob/master/libraries/libmesosphere/source/svc/kern_svc_port.cpp)
  and [object names](https://github.com/Atmosphere-NX/Atmosphere/blob/master/libraries/libmesosphere/include/mesosphere/kern_k_object_name.hpp):
  bounded C string, 12-byte maximum; pointer and termination validation.
- [server sessions](https://github.com/Atmosphere-NX/Atmosphere/blob/master/libraries/libmesosphere/source/kern_k_server_session.cpp):
  kernel IPC transport is separate from service dispatch; this prototype handles
  only its explicitly documented descriptor-free synchronous subset.
- [SM CMIF vectors](https://github.com/Atmosphere-NX/Atmosphere/blob/master/stratosphere/sm/source/sm_user_service.cpp)
  and [registration](https://github.com/Atmosphere-NX/Atmosphere/blob/master/stratosphere/sm/source/sm_user_service.hpp):
  independent canonical byte vectors and per-session initialized state.
- [SVC Results](https://github.com/Atmosphere-NX/Atmosphere/blob/master/libraries/libvapours/include/vapours/results/svc_results.hpp):
  module 1, OutOfHandles 105=0xd201, InvalidHandle 114=0xe401,
  InvalidPointer 115=0xe601, OutOfRange 119=0xee01, NotFound 121=0xf201.
- [SM Results](https://github.com/Atmosphere-NX/Atmosphere/blob/master/libraries/libvapours/include/vapours/results/sm_results.hpp):
  module 21, InvalidClient 2=0x415.
- [SF Results](https://github.com/Atmosphere-NX/Atmosphere/blob/master/libraries/libvapours/include/vapours/results/sf_results.hpp):
  module 10, CMIF UnknownCommandId 221=0x1ba0a.

## Implementation and evidence

Implemented in `dm/windows/ipc.py` (bounded parser, session table, SM/set dispatch)
and `horizon.py` (TLS mapping/register, three additional raw SVC hooks, reporting).
The existing five SVCs remain. `homebrew.s` retains all earlier checks and now
constructs real CMIF packets through TPIDRRO_EL0, checks transport and SFCO header/
service Result, checks the moved handle and u64 language, closes SM before using
set, prints the IPC checkpoint, closes set and calls ExitProcess. Its assembled
code is 660 bytes (below 3968); the file is 12288 bytes and retains MOD0/DT_NULL.
`generate_homebrew.py` adds only the own fixture's port/checkpoint rodata.
`homebrew.hex` was reproduced through optional Keystone 0.9.2.

`scripts/run_windows_homebrew.py` adds the LANGUAGE setting/--language and decoded
IPC console lines. The existing .cmd needs no change: default/external selection,
Settings/Spyder F5, argument forwarding, pause convention and exit status remain.
README and both Windows documents describe eight SVCs and the deliberately narrow
IPC/TLS scope. No dependencies were installed or changed.

### Runtime evidence

All final command records, exact argv/cwd/exit status and captured stdout/stderr:
`out/windows-homebrew-ipc-evidence/commands.json`. The companion `verify.py` is a
reproducible verification driver. All nine recorded commands met expectations.

| Check | Command / outcome |
| --- | --- |
| Full suite, once after implementation settled | `.venv-windows/Scripts/python.exe -m unittest discover -s dm/windows/tests -v`: **49 PASS**, 31.132 s, no skips; original 34 plus 15 meaningful IPC tests. |
| Real default .cmd | `scripts/run_windows_homebrew.cmd --output E:\nxnavite-win\out\windows-homebrew-ipc-evidence\default`: exit 0, 336 instructions, normal svcExitProcess, error null. |
| External NRO from C:\Windows | Absolute .cmd path with `--guest E:\nxnavite-win\out\windows-homebrew-ipc-evidence\alternate-ja.nro --language ja --output E:\nxnavite-win\out\windows-homebrew-ipc-evidence\external`: exit 0, 336 instructions, normal svcExitProcess, error null. |
| Causal negative | Default guest with `--language ja`: expected exit 1, guest BRK interrupt 7 at 0x100002c8, no kernel exit. The service returned ja before the guest rejected it. |
| Original first-frame GUI | `scripts/run_windows_prototype.py --auto-close 1 --output ...\firstframe-gui`: exit 0; cpu_success=true, display_success=true, 533436 instructions. |
| Original first-frame headless | `scripts/run_windows_prototype.py --headless --output ...\firstframe-headless`: exit 0; cpu_success=true, display_success=false as expected, 533436 instructions. |
| Source reproduction | `-m dm.windows.generate_homebrew --check --output ...\reproduced.nro`: 660 matching source bytes; generated NRO byte-identical to default file. |
| Compile | `-m compileall -q dm/windows scripts/run_windows_homebrew.py`: exit 0. |
| Whitespace | `git diff --check`: exit 0. |

Default report: `out/windows-homebrew-ipc-evidence/default/report.json`.
The exact runtime call sequence was:

1. OutputDebugString, SetHeapSize, QueryMemory, GetSystemTick twice,
   OutputDebugString (all earlier checks retained).
2. ConnectToNamedPort: X0=0, X1=0x10001080 (`sm:`), W0=0, W1=0x100.
3. SendSyncRequest(0x100): SM command 0, PID descriptor present (guest value
   0xdead ignored), synthetic kernel PID 1, transport 0, service Result 0.
4. SendSyncRequest(0x100): SM command 1, name set, transport 0, service Result 0,
   HIPC one moved handle 0x101.
5. CloseHandle(0x100): Result 0.
6. SendSyncRequest(0x101): set command 0, transport 0, service Result 0,
   returned bytes `656e2d5553000000` (u64 0x53552d6e65, en-US).
7. OutputDebugString `homebrew: ipc\n`; CloseHandle(0x101): Result 0;
   ExitProcess normally. Final live_handles=[] and lifecycle open/open/close/close.

Reports retain bounded exact 256-byte request/response hex and decoded offsets,
commands, PID, language, moved handle, transport and service Results separately.
TLS is 0x33000000, mapped 4096 RW bytes, IPC extent 256, declared ARM64 thread
local region 512 with other fields explicitly unimplemented. Every successful
IPC response write is exactly 256 bytes, never beyond the command buffer.

### Test independence and review

`dm/windows/tests/test_ipc.py` uses explicit independent request vectors and
machine-word MOV/STR/SVC guests rather than importing the runtime response builder.
It verifies canonical response fields against literal protocol values, complete
file execution, alternate image/language expectations, missing/malformed PID and
raw placeholder, short service payload/header, SFCI/version/token, raw-count and
descriptor overflow, incoming copy/move descriptors, reserved fields, close/
control/context/TIPC rejection, registration isolation/repeat, unknown commands,
explicit unsupported service, invalid/double/stale/pseudo handles, outgoing move,
capacity failure/recovery with unchanged request/no leak, TLS+0x100 sentinel,
real MRS TLS access, raw X0/X1 ABI, high X0 handle bits and bytewise port reads
including a valid NUL at the very last mapped byte. The existing source test
matches assembly -> checked bytes -> full NRO. Alternate minimal NRO and CLI
tests retain proof that success has no fixture identity/log/checksum gate.

Self-review walked the parser arithmetic against primary layouts, request extent
checks before state mutation/allocation, fixed response extents, session lifetime,
raw ABI width and guest assertion flow. No review tool or `/review` entry point
is exposed in this worker session; independent Lead review remains pending.
The full suite and smokes above passed after the final code/test changes; no
repeated broad suite was run. The earlier targeted IPC suite ran 14 tests before
the final capacity test and additional malformed subcases were added.

### Artifact SHA-256

| Artifact | SHA-256 |
| --- | --- |
| dm/windows/homebrew.s | 3abd6f1490377e14176c57bbd840098b056c90d65416fd13f8f8e09d56fee5a7 |
| dm/windows/homebrew.hex | f53d359342cc0c552b720e2be76bf2799bab3986cbedb444a79b1a7b8b3678bf |
| default/selftest.nro and reproduced.nro | 0ff85c1a27bfaf3414f7f9712a646e1480304f89fafa61109cbff275f0b6bf35 |
| alternate-ja.nro | 874bdc0db38b77d48f57d18c0d2d33d93ea2cf8aea45a1d4328064e2b13d892e |

### Scope and remaining limitations

Success is still only normal ExitProcess with no runtime error. The host does
not require logs, image hashes, expected language, arithmetic or closed handles
to mark success; the own guest asserts those observations itself. An assertion
failure can leave a live handle in the diagnostic snapshot; the per-execution
table is then discarded and cannot leak into another run. No real server-side
guest, scheduler, blocking/deferred service requests, IPC buffers/domains/TIPC,
full libnx startup, arbitrary third-party NROs or game support is claimed. There
has been no Switch hardware test. The synthetic PID and language are explicit
prototype configuration, independent of Windows settings. Unsupported protocol
framing is a runtime error, not a fabricated Horizon service Result.

Existing `.twotower/ledger-horizon-homebrew.md` was already modified at task start
and was not edited by this worker. Earlier output/evidence folders remain. No
cleanup, commit, push, branch/remote changes or Apple host/build/shader changes
were performed. New persistent artifacts are ignored `out/windows-homebrew-ipc*`.

## Independent review correction: TLS memory type (2026-10-25)

The Lead's independent review returned one P2 finding and withheld acceptance:
TLS at 0x33000000 was mapped with memory type 0x0B (MappedMemory), while
[libnx svc.h](https://github.com/switchbrew/libnx/blob/master/nx/include/switch/kernel/svc.h)
defines MemType_ThreadLocal as 0x0C. The reviewer independently reproduced real
MRS TPIDRRO_EL0 -> QueryMemory returning type 11 instead of 12. No other protocol
or code blockers were reported. Review evidence supplied by the Lead: previous
full 49-test suite PASS in 29.365 s and diff check PASS; real default .cmd PASS
with 336 instructions at `out/windows-homebrew-ipc-lead`, and alternate-ja external
.cmd from C:\Windows PASS at `out/windows-homebrew-ipc-lead-external`. Those are
pre-correction observations, not final acceptance of this revision.

Changed only the TLS mapping's memory type to 0x0C in `horizon.py`; the stack
retains type 0x0B. Corrected the stack/TLS wording in `docs/windows-homebrew.md`.
Added `test_mrs_tls_query_memory_reports_thread_local_mapping`: an independent
machine-word guest executes real MRS X2, TPIDRRO_EL0, raw QueryMemory into writable
guest memory, and OutputDebugString of the entire 40-byte MemoryInfo. The test
checks the actual QueryMemory address register, Result/PageInfo, and all eight
structure fields against `(0x33000000, 4096, 12, 0, 3, 0, 0, 0)` (base, size,
ThreadLocal type, attributes, RW permissions and zero counts/padding).

The new regression was run before the mapping fix and failed precisely on
`(855638016, 4096, 11, 0, 3, 0, 0, 0)` versus expected type 12. Evidence:
`out/windows-homebrew-ipc-tls-review/regression-before.txt`. After the fix, targeted
IPC tests passed: 16 tests, 0.043 s. Targeted/full-suite/diff-check command records
and captured logs are under `out/windows-homebrew-ipc-tls-review/`.

Final correction validation: the full suite ran once after this correction and
passed **50 tests in 28.831 s**, no skips; `git diff --check` passed (exit 0).
All three commands in `out/windows-homebrew-ipc-tls-review/commands.json` returned
0. The prior source/fixture bytes were unchanged by this correction. Self-review
confirmed the narrow mapping/doc/test change and causal pre-fix regression.
Independent re-review and final IPC acceptance remain **pending** with the Lead.

## Final acceptance (2026-10-25)

**PASS — milestone accepted after independent re-review.** Execution channels
were an Orca Worker for implementation and a native subagent for independent
review. The native reviewer re-reviewed the sole P2 TLS correction and reported
PASS with no additional blockers. It confirmed the old causal failure (type 11
versus 12), the revised ThreadLocal type 12, and the narrow mapping/doc/test scope.

Validation attribution is explicit: the implementation Worker ran the final
full **50-test suite PASS in 28.831 s**. The independent native reviewer had run
the earlier **49-test suite PASS in 29.365 s**, then independently ran the new
guest-level MRS -> QueryMemory -> OutputDebugString regression **1 PASS in
0.007 s** during re-review. These are distinct runs; the reviewer did not claim
to run the final full 50-test suite. The Lead accepted both evidence sets and
the earlier real default/external .cmd smoke results recorded above.

This finalization changes only this ledger. No runtime/test changes, broad test
rerun, commit, push or cleanup were performed. The user's modified homebrew
ledger and all existing evidence remain preserved. The milestone is ready to
archive.
