"""Optional fixture regeneration: install keystone-engine==0.9.2 separately.

The launcher reads checked-in guest.hex; it never needs an assembler.
Run from the repository root: python -m dm.windows.generate_guest --check
"""
import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    from keystone import Ks, KS_ARCH_ARM64, KS_MODE_LITTLE_ENDIAN

    folder = Path(__file__).resolve().parent
    source = "\n".join(line.split("//")[0] for line in
                       (folder / "guest.s").read_text(encoding="ascii").splitlines())
    encoded, _ = Ks(KS_ARCH_ARM64, KS_MODE_LITTLE_ENDIAN).asm(source, addr=0x100000)
    code = bytes(encoded)
    fixture = folder / "guest.hex"
    if args.check:
        if bytes.fromhex(fixture.read_text(encoding="ascii")) != code:
            raise SystemExit("[RED] guest.hex differs from guest.s; regenerate it.")
        print(f"Guest source matches checked fixture ({len(code)} bytes).")
    else:
        fixture.write_text("\n".join(code[i:i+4].hex() for i in range(0, len(code), 4))
                           + "\n", encoding="ascii")
        print(f"Wrote {fixture} ({len(code)} bytes).")


if __name__ == "__main__":
    main()
