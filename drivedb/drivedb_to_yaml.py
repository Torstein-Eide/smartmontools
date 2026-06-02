#!/usr/bin/env python3
"""
drivedb_to_yaml.py - convert drivedb.h to a YAML source tree

Usage:
    python3 drivedb/drivedb_to_yaml.py [drivedb/drivedb.h] [--out-dir drivedb/yaml]

Reads the C struct-literal format used by smartmontools drivedb.h and writes
one .yaml file per drive family into the tree:

    <out-dir>/_meta/version.yaml
    <out-dir>/_meta/default.yaml
    <out-dir>/ata/<manufacturer>/<family-slug>.yaml
    <out-dir>/usb/<bridge-slug>/<device-slug>.yaml

Manufacturer is derived from the first word of modelfamily (heuristic).
Duplicate slugs within the same directory get a numeric suffix (_2, _3, …).
"""

import argparse
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("PyYAML is required: pip install pyyaml")


# ---------------------------------------------------------------------------
# Tokeniser – mirrors the logic in knowndrives.cpp get_token()
# ---------------------------------------------------------------------------

def _strip_comments(text: str) -> str:
    """Remove C // and /* */ comments, preserving line count."""
    result = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] == '/' and i + 1 < n:
            if text[i + 1] == '/':
                # Line comment – skip to end of line
                while i < n and text[i] != '\n':
                    i += 1
                continue
            if text[i + 1] == '*':
                # Block comment – replace content with spaces to keep positions
                i += 2
                while i < n:
                    if text[i] == '*' and i + 1 < n and text[i + 1] == '/':
                        i += 2
                        break
                    i += 1
                continue
        result.append(text[i])
        i += 1
    return ''.join(result)


def _unescape(s: str) -> str:
    """Convert C escape sequences inside a string literal to Python string."""
    out = []
    i = 0
    while i < len(s):
        if s[i] == '\\' and i + 1 < len(s):
            c = s[i + 1]
            if c == 'n':
                out.append('\n')
            elif c == 't':
                out.append('\t')
            elif c == '\\':
                out.append('\\')
            elif c == '"':
                out.append('"')
            else:
                out.append('\\')
                out.append(c)
            i += 2
        else:
            out.append(s[i])
            i += 1
    return ''.join(out)


def tokenise(text: str):
    """Yield tokens: '{', '}', ',' or ('str', value) for string literals.

    Adjacent string literals are concatenated into one token, matching
    C compiler behaviour and the smartmontools parser.
    """
    text = _strip_comments(text)
    i = 0
    n = len(text)
    pending_str = None  # accumulated adjacent string literals

    while i < n:
        c = text[i]

        if c in ' \t\r\n':
            i += 1
            continue

        if c == '"':
            # String literal
            i += 1
            buf = []
            while i < n and text[i] != '"':
                if text[i] == '\\' and i + 1 < n:
                    buf.append(text[i])
                    buf.append(text[i + 1])
                    i += 2
                else:
                    buf.append(text[i])
                    i += 1
            i += 1  # closing "
            chunk = _unescape(''.join(buf))
            if pending_str is None:
                pending_str = chunk
            else:
                pending_str += chunk
            continue

        # Non-string token: flush any pending string first
        if pending_str is not None:
            yield ('str', pending_str)
            pending_str = None

        if c in '{},' :
            yield c
            i += 1
            continue

        # Unknown character – skip
        i += 1

    if pending_str is not None:
        yield ('str', pending_str)


# ---------------------------------------------------------------------------
# Parser – reads the token stream into drive_settings entries
# ---------------------------------------------------------------------------

def parse_entries(text: str):
    """Parse drivedb.h token stream into a list of 5-tuples."""
    tokens = list(tokenise(text))
    entries = []
    i = 0
    n = len(tokens)

    while i < n:
        tok = tokens[i]
        if tok != '{':
            i += 1
            continue
        i += 1

        fields = []
        while i < n and len(fields) < 5:
            tok = tokens[i]
            if tok == '}':
                break
            if tok == ',':
                i += 1
                continue
            if isinstance(tok, tuple) and tok[0] == 'str':
                fields.append(tok[1])
                i += 1
            else:
                i += 1

        if len(fields) == 5:
            entries.append(tuple(fields))
        elif fields:
            sys.stderr.write(f"WARNING: incomplete entry with {len(fields)} fields, skipping\n")

        # Consume closing '}'
        while i < n and tokens[i] != '}':
            i += 1
        i += 1  # skip '}'

    return entries


# ---------------------------------------------------------------------------
# Slug helpers
# ---------------------------------------------------------------------------

def slugify(s: str) -> str:
    """Convert a string to a lowercase hyphen-separated filename slug."""
    s = s.lower()
    s = re.sub(r'[^a-z0-9]+', '-', s)
    s = s.strip('-')
    return s or 'unknown'


def manufacturer_slug(modelfamily: str) -> str:
    """Heuristic: use the first word of modelfamily as manufacturer."""
    first = modelfamily.split()[0] if modelfamily.split() else modelfamily
    return slugify(first)


def family_slug(modelfamily: str) -> str:
    return slugify(modelfamily)


def unique_path(directory: Path, slug: str, used: set) -> Path:
    """Return a path that doesn't collide with already-used names."""
    candidate = slug
    n = 2
    while candidate in used:
        candidate = f"{slug}_{n}"
        n += 1
    used.add(candidate)
    return directory / f"{candidate}.yaml"


# ---------------------------------------------------------------------------
# YAML serialisation helpers
# ---------------------------------------------------------------------------

def _yaml_str(s: str) -> str:
    """Serialise a string value, using block style only when it contains newlines."""
    if '\n' in s:
        # Block scalar
        lines = s.split('\n')
        block = '\n'.join('  ' + l for l in lines)
        return f"|\n{block}"
    # Plain or quoted
    return yaml.dump(s, default_flow_style=False, allow_unicode=True).rstrip('\n...')


def write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w') as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True,
                  sort_keys=False, width=120)


# ---------------------------------------------------------------------------
# Entry routing
# ---------------------------------------------------------------------------

def presets_to_list(presets_str: str) -> list:
    """Split a flat presets string into a list of individual tokens.

    Each -v, -F, or -d option becomes one list item.
    """
    if not presets_str.strip():
        return []
    # Split on whitespace boundaries before each flag
    tokens = re.split(r'(?=\s+-[vFd]\s)', presets_str)
    result = []
    for t in tokens:
        t = t.strip()
        if t:
            result.append(t)
    return result


def route_entry(entry, out_dir: Path, used_slugs: dict) -> None:
    modelfamily, modelregexp, firmwareregexp, warningmsg, presets = entry

    # VERSION entry
    if modelfamily.startswith("VERSION:"):
        version = modelfamily.split(":", 1)[1].strip()
        path = out_dir / "_meta" / "version.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        write_yaml(path, {"type": "version", "value": version})
        return

    # DEFAULT entry
    if modelfamily == "DEFAULT":
        data = {
            "type": "default",
            "modelfamily": "DEFAULT",
            "modelregexp": "-",
            "firmwareregexp": "-",
            "warningmsg": warningmsg,
            "presets": presets_to_list(presets),
        }
        path = out_dir / "_meta" / "default.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        write_yaml(path, data)
        return

    # USB entry
    if modelfamily.startswith("USB:"):
        rest = modelfamily[4:].strip()
        parts = rest.split(";", 1)
        device = parts[0].strip()
        bridge = parts[1].strip() if len(parts) > 1 else ""

        # Directory: bridge slug if available, else device slug, else "unknown"
        dir_slug = slugify(bridge) if bridge else (slugify(device) if device else "unknown")
        dev_slug = slugify(device) if device else slugify(modelregexp)

        section_dir = out_dir / "usb" / dir_slug
        key = str(section_dir)
        if key not in used_slugs:
            used_slugs[key] = set()
        path = unique_path(section_dir, dev_slug or "device", used_slugs[key])

        data = {
            "device": device,
            "bridge": bridge,
            "modelregexp": modelregexp,
            "bcddeviceregexp": firmwareregexp,
            "warningmsg": warningmsg,
            "presets": presets_to_list(presets),
        }
        write_yaml(path, data)
        return

    # ATA entry
    mfr = manufacturer_slug(modelfamily)
    fam = family_slug(modelfamily)

    section_dir = out_dir / "ata" / mfr
    key = str(section_dir)
    if key not in used_slugs:
        used_slugs[key] = set()
    path = unique_path(section_dir, fam, used_slugs[key])

    data = {
        "modelfamily": modelfamily,
        "modelregexp": modelregexp,
        "firmwareregexp": firmwareregexp,
        "warningmsg": warningmsg,
        "presets": presets_to_list(presets),
    }
    write_yaml(path, data)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Convert drivedb.h to YAML tree")
    parser.add_argument("input", nargs="?", default="drivedb/drivedb.h",
                        metavar="drivedb.h", help="Input drivedb.h (default: drivedb/drivedb.h)")
    parser.add_argument("--out-dir", "-o", default="drivedb/yaml",
                        metavar="DIR", help="Output YAML root (default: drivedb/yaml)")
    args = parser.parse_args()

    src = Path(args.input)
    if not src.exists():
        sys.exit(f"ERROR: input not found: {src}")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    text = src.read_text(encoding="utf-8", errors="replace")
    entries = parse_entries(text)

    if not entries:
        sys.exit("ERROR: no entries parsed — check input file format")

    used_slugs: dict = {}
    for entry in entries:
        try:
            route_entry(entry, out_dir, used_slugs)
        except Exception as e:
            sys.stderr.write(f"WARNING: failed to write entry '{entry[0][:60]}': {e}\n")

    total = len(entries)
    ata = sum(1 for e in entries if not e[0].startswith(("VERSION:", "USB:")) and e[0] != "DEFAULT")
    usb = sum(1 for e in entries if e[0].startswith("USB:"))
    print(f"Converted {total} entries ({ata} ATA, {usb} USB) to {out_dir}/")


if __name__ == "__main__":
    main()
