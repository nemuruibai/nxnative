"""Strict, bounded loader for relocation-free AArch64 NRO version 0 files.

Format references: switch-tools/src/elf2nro.c and libnx switch_crt0.s.
This subset deliberately rejects dynamic linking and appended assets.
"""
from dataclasses import dataclass
from pathlib import Path
import struct

PAGE = 0x1000
MAX_IMAGE = 16 * 1024 * 1024
MAX_BSS = 16 * 1024 * 1024


class NroError(ValueError):
    pass


@dataclass(frozen=True)
class Segment:
    name: str
    offset: int
    size: int
    permission: int


@dataclass(frozen=True)
class Nro:
    image: bytes
    segments: tuple
    bss_size: int
    mod_offset: int
    dynamic_offset: int


def parse_nro(image):
    image = bytes(image)

    def require(condition, message):
        if not condition:
            raise NroError(message)

    require(0x80 <= len(image) <= MAX_IMAGE, "invalid NRO file length")
    require(image[0x10:0x14] == b"NRO0", "unsupported format: expected NRO0")
    version, size, flags = struct.unpack_from("<III", image, 0x14)
    require(version == 0, "unsupported NRO version")
    require(flags == 0, "unsupported NRO flags (including AlignedHeader)")
    require(size == len(image), "NRO size mismatch; truncated files/assets unsupported")
    segments = tuple(Segment(name, *struct.unpack_from("<II", image, 0x20+i*8), perm)
                     for i, (name, perm) in enumerate((("text", 5), ("rodata", 1), ("data", 3))))
    end = 0
    for segment in segments:
        require(segment.size > 0 and segment.offset % PAGE == 0 and segment.size % PAGE == 0,
                "NRO segments must have nonzero page-aligned offsets/sizes")
        require(segment.offset >= end and segment.offset + segment.size <= size,
                "NRO segments overlap or exceed file")
        end = segment.offset + segment.size
    require(segments[0].offset == 0 and segments[0].size >= 0x84,
            "NRO text must start at zero and include the entry")
    require(end == size, "unsupported trailing NRO data")
    bss, reserved = struct.unpack_from("<II", image, 0x38)
    require(bss <= MAX_BSS and bss % PAGE == 0, "invalid/oversized NRO bss")
    require(reserved == 0 and image[0x60:0x80] == bytes(0x20),
            "unsupported NRO reserved fields/API/dynamic sections")

    def readable(offset, length):
        return any(s.offset <= offset and offset + length <= s.offset + s.size
                   for s in segments)

    branch, mod = struct.unpack_from("<II", image)
    require(branch & 0xFC000000 == 0x14000000, "NRO entry must branch over header")
    displacement = branch & 0x3FFFFFF
    if displacement & 0x2000000:
        displacement -= 0x4000000
    target = displacement * 4
    require(0x80 <= target < segments[0].size, "NRO entry branch outside text")
    require(mod % 4 == 0 and readable(mod, 28), "MOD0 outside a loaded segment")
    require(image[mod:mod+4] == b"MOD0", "missing MOD0")
    dynamic, bs, be, eh_start, eh_end, module = struct.unpack_from("<iiiiii", image, mod+4)
    require(mod + bs == size and mod + be == size + bss,
            "unsupported MOD0 bss range (must follow data segment)")
    require(eh_start == eh_end == 0 and module == 0,
            "unsupported MOD0 unwind/module object")
    require(image[mod+28:mod+32] not in (b"LNY0", b"LNY1", b"LNY2"),
            "unsupported libnx MOD0 extensions")
    dynamic += mod
    require(dynamic % 8 == 0 and readable(dynamic, 16), "MOD0 dynamic table outside image")
    require(image[dynamic:dynamic+16] == bytes(16),
            "unsupported dynamic entries: only DT_NULL accepted; no relocations/imports")
    return Nro(image, segments, bss, mod, dynamic)


def load_nro(path):
    with Path(path).open("rb") as stream:
        return parse_nro(stream.read(MAX_IMAGE + 1))
