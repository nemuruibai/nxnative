// Own freestanding, position-independent AArch64 homebrew entry at NRO + 0x80.
// No libnx init, imports, relocations, private SVCs, graphics or filesystem.
// Fixed *relative* section displacements match generate_homebrew.py.
// Assertion failure executes BRK #1, which the host treats as a guest fault.
start:
    cmn x1, #1
    b.ne failed
    ldr x2, [x0]
    cbnz x2, failed
    mov x2, sp
    tst x2, #15
    b.ne failed
    adr x19, start
    sub x19, x19, #0x80
    add x20, x19, #0x2000
    ldr x2, [x20]
    mov x3, #0x1234
    cmp x2, x3
    b.ne failed
    add x21, x19, #0x3000
    ldr x2, [x21]
    cbnz x2, failed
    add x0, x19, #0x1000
    add x0, x0, #0x40
    mov x1, #16
    svc #0x27
    cbnz w0, failed
    mov x0, #0 // Deliberately not an output pointer: raw kernel ABI.
    mov x1, #0x200000
    svc #1
    cbnz w0, failed
    mov x22, x1
    ldr x2, [x22]
    cbnz x2, failed
    mov x2, #0x5a
    strb w2, [x22]
    ldrb w3, [x22]
    cmp w2, w3
    b.ne failed
    mov x0, x21
    mov x1, #0 // PageInfo is returned in W1, not through this pointer.
    add x2, x22, #17
    svc #6
    cbnz w0, failed
    cbnz w1, failed
    ldr x2, [x21]
    cmp x2, x22
    b.ne failed
    ldr x2, [x21, #8]
    mov x3, #0x200000
    cmp x2, x3
    b.ne failed
    ldr w2, [x21, #16]
    cmp w2, #5
    b.ne failed
    ldr w2, [x21, #24]
    cmp w2, #3
    b.ne failed
    svc #0x1e
    mov x23, x0
    svc #0x1e
    cmp x0, x23
    b.lo failed
    add x0, x19, #0x1000
    add x0, x0, #0x50
    mov x1, #17
    svc #0x27
    cbnz w0, failed
    mrs x24, tpidrro_el0
    cbz x24, failed
    mov x0, #0 // Not a C-wrapper output pointer.
    add x1, x19, #0x1000
    add x1, x1, #0x80 // sm: NUL-terminated port name.
    svc #0x1f
    cbnz w0, failed
    cbz w1, failed
    mov w25, w1
    bl clear_ipc
    mov w2, #4
    mov w3, #10
    movk w3, #0x8000, lsl #16
    stp w2, w3, [x24]
    mov w2, #1
    str w2, [x24, #8] // PID descriptor; kernel substitutes its own PID.
    mov x2, #0xdead
    str x2, [x24, #12]
    mov w2, #0x4653
    movk w2, #0x4943, lsl #16
    str w2, [x24, #0x20] // SFCI, command 0, raw u64 placeholder zero.
    mov w0, w25
    svc #0x21
    cbnz w0, failed
    bl check_response
    bl clear_ipc
    mov w2, #4
    mov w3, #10
    stp w2, w3, [x24]
    mov w2, #0x4653
    movk w2, #0x4943, lsl #16
    str w2, [x24, #0x10]
    mov w2, #1
    str w2, [x24, #0x18] // SM GetService command 1.
    mov x2, #0x6573
    movk x2, #0x74, lsl #16
    str x2, [x24, #0x20] // Eight-byte service name: set.
    mov w0, w25
    svc #0x21
    cbnz w0, failed
    bl check_response
    ldr w2, [x24, #4]
    mov w3, #4
    movk w3, #0x8000, lsl #16
    cmp w2, w3
    b.ne failed
    ldr w2, [x24, #8]
    cmp w2, #0x20 // Exactly one outgoing moved handle.
    b.ne failed
    ldr w26, [x24, #12]
    cbz w26, failed
    cmp w26, w25
    b.eq failed
    mov w0, w25
    svc #0x16 // Closing SM must leave the set session usable.
    cbnz w0, failed
    bl clear_ipc
    mov w2, #4
    mov w3, #8
    stp w2, w3, [x24]
    mov w2, #0x4653
    movk w2, #0x4943, lsl #16
    str w2, [x24, #0x10] // set GetLanguageCode command 0.
    mov w0, w26
    svc #0x21
    cbnz w0, failed
    bl check_response
    ldr x2, [x24, #0x20]
    mov x3, #0x6e65 // Expected en-US, asserted by guest instructions.
    movk x3, #0x552d, lsl #16
    movk x3, #0x53, lsl #32
    cmp x2, x3
    b.ne failed
    add x0, x19, #0x1000
    add x0, x0, #0x90
    mov x1, #14
    svc #0x27
    cbnz w0, failed
    mov w0, w26
    svc #0x16
    cbnz w0, failed
    mov x0, #99 // ExitProcess has no status argument; this is ignored.
    svc #7
failed:
    brk #1
clear_ipc:
    mov x4, x24
    mov w5, #16
clear_loop:
    stp xzr, xzr, [x4], #16
    subs w5, w5, #1
    b.ne clear_loop
    ret
check_response:
    ldr w2, [x24]
    cbnz w2, failed
    ldr w2, [x24, #0x10]
    mov w3, #0x4653
    movk w3, #0x4f43, lsl #16
    cmp w2, w3
    b.ne failed
    ldr w2, [x24, #0x14]
    cbnz w2, failed
    ldr x2, [x24, #0x18] // Both service Result and token must be zero.
    cbnz x2, failed
    ret
