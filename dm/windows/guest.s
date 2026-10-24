// Synthetic AArch64 demo, not a Switch executable or Horizon ABI.
// Code 0x100000 (RX), framebuffer 0x200000 (RW), stack 0x300000 (RW).
// SVC 0x7f01: log unsigned x0; SVC 0x7f02: present(x0=pointer,x1=size,
// x2=width,x3=height,x4=stride), RGBA8; SVC 0x7f03: exit(x0=status).
// Arithmetic proof: sum(1..100) = 5050, kept in x20.
mov x20, #0
mov x5, #100
sum_loop:
add x20, x20, x5
subs x5, x5, #1
b.ne sum_loop
mov x0, x20
svc #0x7f01

mov x10, #0x200000
mov x11, x10
mov w6, #0
row_loop:
mov w5, #0
pixel_loop:
// R = (5*x) mod 256; G = (3*y) mod 256; B = (7*(x XOR y)) mod 256.
add w7, w5, w5, lsl #2
strb w7, [x11], #1
add w8, w6, w6, lsl #1
strb w8, [x11], #1
eor w9, w5, w6
lsl w7, w9, #3
sub w9, w7, w9
strb w9, [x11], #1
mov w7, #255
strb w7, [x11], #1
add w5, w5, #1
cmp w5, #256
b.ne pixel_loop
add w6, w6, #1
cmp w6, #160
b.ne row_loop
mov x0, x10
mov x1, #0x8000
movk x1, #2, lsl #16
mov x2, #256
mov x3, #160
mov x4, #1024
svc #0x7f02
mov x0, #0
svc #0x7f03
// Falling through or returning without exit is an error.
