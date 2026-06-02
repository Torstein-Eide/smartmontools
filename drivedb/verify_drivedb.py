#!/usr/bin/env python3
"""
verify_drivedb.py - compare two drivedb.h files for content equivalence

Usage:
    python3 drivedb/verify_drivedb.py original.h generated.h

Parses both files, normalises each entry, then compares as unordered sets.
Exits 0 if content is identical, 1 if differences are found.

Typical workflow:
    python3 drivedb/drivedb_to_yaml.py drivedb/drivedb.h --out-dir /tmp/yaml-out
    python3 drivedb/yaml_to_drivedb.py --yaml-root /tmp/yaml-out --output /tmp/generated.h
    python3 drivedb/verify_drivedb.py drivedb/drivedb.h /tmp/generated.h
"""

import sys
from pathlib import Path

# Reuse the parser from drivedb_to_yaml
sys.path.insert(0, str(Path(__file__).parent))
from drivedb_to_yaml import parse_entries


import re
from collections import Counter


def normalise_presets(raw: str) -> str:
    """Canonical presets string: each flag token separated by a single space."""
    tokens = re.split(r'(?<!\S)(?=-[vFd]\s)', raw.strip())
    return ' '.join(t.strip() for t in tokens if t.strip())


def normalise_modelfamily(mf: str) -> str:
    """Strip spaces around the semicolon in USB modelfamily strings."""
    if mf.startswith("USB:"):
        # "USB: Foo ; Bar" -> "USB: Foo; Bar"
        rest = mf[4:].strip()
        parts = rest.split(";", 1)
        device = parts[0].strip()
        bridge = parts[1].strip() if len(parts) > 1 else ""
        return f"USB: {device}; {bridge}"
    return mf.strip()


def normalise_entry(entry: tuple) -> tuple:
    modelfamily, modelregexp, firmwareregexp, warningmsg, presets = entry
    return (
        normalise_modelfamily(modelfamily),
        modelregexp.strip(),
        firmwareregexp.strip(),
        warningmsg.strip(),
        normalise_presets(presets),
    )


def load(path: Path) -> Counter:
    """Parse a drivedb.h and return a Counter of normalised 5-tuples.

    Using a Counter handles duplicate entries (same modelfamily + modelregexp)
    without silently discarding them.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    raw = parse_entries(text)
    return Counter(normalise_entry(e) for e in raw)


def main():
    if len(sys.argv) != 3:
        sys.exit("Usage: verify_drivedb.py <original.h> <generated.h>")

    orig_path = Path(sys.argv[1])
    gen_path = Path(sys.argv[2])

    for p in (orig_path, gen_path):
        if not p.exists():
            sys.exit(f"ERROR: file not found: {p}")

    print(f"Parsing {orig_path} …")
    orig = load(orig_path)
    print(f"Parsing {gen_path} …")
    gen = load(gen_path)

    # Counter subtraction: entries that appear more times in one than the other
    only_in_orig = list((orig - gen).elements())
    only_in_gen  = list((gen - orig).elements())

    fields = ("modelfamily", "modelregexp", "firmwareregexp", "warningmsg", "presets")

    print(f"\nEntries in original : {sum(orig.values())}")
    print(f"Entries in generated: {sum(gen.values())}")

    ok = True

    if only_in_orig:
        ok = False
        print(f"\n[MISSING from generated] ({len(only_in_orig)} entries):")
        for e in sorted(only_in_orig):
            print(f"  modelfamily : {e[0]!r}")
            print(f"  modelregexp : {e[1]!r}")
            print()

    if only_in_gen:
        ok = False
        print(f"\n[EXTRA in generated] ({len(only_in_gen)} entries):")
        for e in sorted(only_in_gen):
            print(f"  modelfamily : {e[0]!r}")
            print(f"  modelregexp : {e[1]!r}")
            print()

    if ok:
        print("\nOK — content is identical (order ignored).")
        sys.exit(0)
    else:
        print("\nFAIL — differences found.")
        sys.exit(1)


if __name__ == "__main__":
    main()
