"""Pack own freestanding source into a standard NRO0/MOD0 image.

Default builds a usable .nro from checked code bytes without an assembler.
--regenerate/--check assemble homebrew.s using optional keystone-engine==0.9.2.
Run from repository root: python -m dm.windows.generate_homebrew --check
"""
import argparse
import hashlib
from pathlib import Path
import struct

FOLDER = Path(__file__).resolve().parent
DEFAULT_OUTPUT = FOLDER.parents[1] / "out/windows-homebrew/selftest.nro"


def pack_nro(code, *, rodata=None, data=None, bss_size=0x1000):
    """Fixture packer, independent of loader validation; code starts at +0x80."""
    if not code or len(code) % 4 or len(code) > 0xF80:
        raise ValueError("code must contain 4..3968 aligned bytes")
    if bss_size < 0 or bss_size % 0x1000:
        raise ValueError("bss size must be page-aligned")
    image = bytearray(0x3000)
    struct.pack_into("<II8s", image, 0, 0x14000020, 0x1000, b"HOMEBREW")
    struct.pack_into("<4sIII", image, 0x10, b"NRO0", 0, len(image), 0)
    for i in range(3):
        struct.pack_into("<II", image, 0x20+i*8, i*0x1000, 0x1000)
    struct.pack_into("<I", image, 0x38, bss_size)
    image[0x40:0x60] = hashlib.sha256(code).digest()  # build identity only
    image[0x80:0x80+len(code)] = code
    struct.pack_into("<4siiiiii", image, 0x1000, b"MOD0", 0x20, 0x2000,
                     0x2000+bss_size, 0, 0, 0)
    # +0x1020: one zeroed Elf64_Dyn DT_NULL entry.
    rodata = b"homebrew: start\n" + b"homebrew: checks\n" if rodata is None else rodata
    data = struct.pack("<Q", 0x1234) if data is None else data
    if len(rodata) > 0xFC0 or len(data) > 0x1000:
        raise ValueError("fixture sections exceed one page")
    image[0x1040:0x1040+len(rodata)] = rodata
    image[0x2000:0x2000+len(data)] = data
    return bytes(image)


def fixture_code():
    return bytes.fromhex((FOLDER / "homebrew.hex").read_text(encoding="ascii"))


def assemble_source():
    from keystone import Ks, KS_ARCH_ARM64, KS_MODE_LITTLE_ENDIAN, __version__
    if __version__ != "0.9.2":
        raise ValueError("fixture regeneration requires keystone-engine==0.9.2")
    source = "\n".join(line.split("//")[0] for line in
                       (FOLDER / "homebrew.s").read_text(encoding="ascii").splitlines())
    encoded, _ = Ks(KS_ARCH_ARM64, KS_MODE_LITTLE_ENDIAN).asm(source, addr=0x80)
    return bytes(encoded)


def write_fixture(path=DEFAULT_OUTPUT):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pack_nro(fixture_code()))
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--regenerate", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.check or args.regenerate:
            code = assemble_source()
            if args.regenerate:
                (FOLDER / "homebrew.hex").write_text(
                    "\n".join(code[i:i+4].hex() for i in range(0, len(code), 4))+"\n",
                    encoding="ascii")
            if code != fixture_code():
                raise ValueError("homebrew.hex differs from homebrew.s; use --regenerate")
            print(f"Source matches checked code ({len(code)} bytes).")
        path = write_fixture(args.output)
        print(f"NRO: {path.resolve()} ({path.stat().st_size} bytes)")
        return 0
    except (ImportError, OSError, ValueError) as error:
        print(f"[RED] {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
