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
    mov x0, #99 // ExitProcess has no status argument; this is ignored.
    svc #7
failed:
    brk #1
