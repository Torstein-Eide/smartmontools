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
# Tokeniser
# ---------------------------------------------------------------------------

def _unescape(s: str) -> str:
    """Convert C escape sequences inside a string literal body to a Python string."""
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


def _tokenise(text: str) -> list:
    """Tokenise drivedb.h, preserving comments.

    Returns a list of tokens:
      '{', '}', ','
      ('str', value)      — one string literal (NOT concatenated with neighbours)
      ('comment', text)   — standalone comment line

    Inline comments (// on the same line as a preceding string token) are
    embedded into that string token as '  # comment_text' rather than being
    yielded as separate comment tokens.  Block comments /* ... */ are dropped
    entirely (they are file-level boilerplate, not entry annotations).
    """
    tokens = []
    i = 0
    n = len(text)
    line = 0            # 0-based current line counter
    last_str_line = -1  # line on which the most recent ('str', …) token was seen

    while i < n:
        c = text[i]

        if c == '\n':
            line += 1
            i += 1
            continue

        if c in ' \t\r':
            i += 1
            continue

        if c == '/' and i + 1 < n:
            if text[i + 1] == '/':
                # Line comment
                i += 2
                start = i
                while i < n and text[i] != '\n':
                    i += 1
                comment_text = text[start:i].strip()
                # Inline if the last string token is on the same line
                if (last_str_line == line
                        and tokens
                        and isinstance(tokens[-1], tuple)
                        and tokens[-1][0] == 'str'):
                    tokens[-1] = ('str', tokens[-1][1] + '  # ' + comment_text)
                elif comment_text:
                    tokens.append(('comment', comment_text))
                continue

            if text[i + 1] == '*':
                # Block comment — drop entirely
                i += 2
                while i < n:
                    if text[i] == '\n':
                        line += 1
                    if text[i] == '*' and i + 1 < n and text[i + 1] == '/':
                        i += 2
                        break
                    i += 1
                continue

        if c == '"':
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
            tokens.append(('str', _unescape(''.join(buf))))
            last_str_line = line
            continue

        if c in '{},':
            tokens.append(c)
            i += 1
            continue

        i += 1  # unknown char — skip

    return tokens


def _strip_annotation(s: str) -> str:
    """Strip '  # inline-comment' from a preset string (for backward compat)."""
    idx = s.find('  # ')
    return s[:idx].rstrip() if idx != -1 else s


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def _parse_with_notes(text: str) -> list:
    """Parse drivedb.h into 7-tuples:
    (modelfamily, modelregexp, firmwareregexp, warningmsg,
     presets_list, disabled_presets_list, notes_list)

    presets_list          — active preset strings; may have '  # comment' appended
    disabled_presets_list — presets commented out with //"-v ..." syntax
    notes_list            — all other standalone comments from within or just before the entry
    """
    tokens = _tokenise(text)
    entries = []
    n = len(tokens)
    i = 0
    pending_notes = []  # comments collected between entries

    while i < n:
        tok = tokens[i]

        if isinstance(tok, tuple) and tok[0] == 'comment':
            pending_notes.append(tok[1])
            i += 1
            continue

        if tok != '{':
            i += 1
            continue
        i += 1  # consume '{'

        entry_notes = pending_notes[:]
        pending_notes.clear()

        # Read 4 comma-separated fields.  Each field is built from one or more
        # adjacent string tokens (C concatenation) until the next comma or '}'.
        fields = []
        for _ in range(4):
            if i < n and tokens[i] == ',':
                i += 1
            # collect comments before this field
            while i < n and isinstance(tokens[i], tuple) and tokens[i][0] == 'comment':
                entry_notes.append(tokens[i][1])
                i += 1
            # concatenate adjacent strings → one field value
            field_val = ''
            while i < n and tokens[i] not in (',', '}'):
                t = tokens[i]
                if isinstance(t, tuple) and t[0] == 'str':
                    field_val += t[1]
                    i += 1
                elif isinstance(t, tuple) and t[0] == 'comment':
                    entry_notes.append(t[1])
                    i += 1
                else:
                    i += 1
            fields.append(field_val)

        if len(fields) < 4:
            while i < n and tokens[i] != '}':
                i += 1
            if i < n:
                i += 1
            continue

        # Skip comma before presets field
        if i < n and tokens[i] == ',':
            i += 1

        # Collect presets: each string token is one list entry
        presets = []
        disabled = []
        while i < n and tokens[i] != '}':
            t = tokens[i]
            if isinstance(t, tuple) and t[0] == 'str':
                presets.append(t[1].rstrip())  # strip trailing whitespace
                i += 1
            elif isinstance(t, tuple) and t[0] == 'comment':
                ctext = t[1]
                if ctext.startswith('"'):
                    # //"-v ..." — a commented-out preset
                    disabled.append(_unescape(ctext.strip('"').rstrip()))
                else:
                    entry_notes.append(ctext)
                i += 1
            else:
                i += 1

        if i < n and tokens[i] == '}':
            i += 1

        entries.append((*fields[:4], presets, disabled, entry_notes))

    return entries


def parse_entries(text: str) -> list:
    """Backward-compatible: parse drivedb.h into 5-tuples, stripping all comments.

    Used by verify_drivedb.py.
    """
    result = []
    for entry in _parse_with_notes(text):
        mf, mr, fw, wm, presets, disabled, notes = entry
        clean = ' '.join(_strip_annotation(p) for p in presets)
        result.append((mf, mr, fw, wm, clean))
    return result


# ---------------------------------------------------------------------------
# Slug helpers
# ---------------------------------------------------------------------------

def slugify(s: str) -> str:
    """Convert a string to a lowercase hyphen-separated filename slug."""
    s = s.lower()
    s = re.sub(r'[^a-z0-9]+', '-', s)
    s = s.strip('-')
    return s or 'unknown'


# Canonical manufacturer names for known aliases.
# Key: slugified first word(s) as produced by the raw heuristic.
# Value: canonical directory name to use instead.
MANUFACTURER_ALIASES = {
    "wd":               "western-digital",  # "WD Blue/Red/Green ..."
    "western":          "western-digital",  # "Western Digital ..."
    "jmicron-maxiotek": "jmicron",          # "JMicron/Maxiotek ..."
    "intel-solidigm":   "intel",            # "Intel/Solidigm ..."
}


# Canonical directory names for USB bridge/device first-word slugs.
USB_DIR_ALIASES = {
    "western": "western-digital",  # device "Western Digital" entries with no bridge
    "usb3":    "generic",          # "USB3 to SATA" catch-all entry
}


def manufacturer_slug(modelfamily: str) -> str:
    """Return the canonical manufacturer directory slug for an ATA modelfamily."""
    first = modelfamily.split()[0] if modelfamily.split() else modelfamily
    raw = slugify(first)
    return MANUFACTURER_ALIASES.get(raw, raw)


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
# YAML output
# ---------------------------------------------------------------------------

def _extract_raw_entries(text: str) -> list:
    """Return the raw C source text of every top-level { … } block, in order."""
    entries = []
    depth = 0
    start = -1
    i = 0
    n = len(text)
    in_string = False
    in_line_comment = False
    in_block_comment = False

    while i < n:
        c = text[i]
        if in_line_comment:
            if c == '\n':
                in_line_comment = False
        elif in_block_comment:
            if c == '*' and i + 1 < n and text[i + 1] == '/':
                in_block_comment = False
                i += 1
        elif in_string:
            if c == '\\' and i + 1 < n:
                i += 1  # skip escaped char
            elif c == '"':
                in_string = False
        else:
            if c == '/' and i + 1 < n:
                if text[i + 1] == '/':
                    in_line_comment = True
                    i += 1
                elif text[i + 1] == '*':
                    in_block_comment = True
                    i += 1
            elif c == '"':
                in_string = True
            elif c == '{':
                if depth == 0:
                    start = i
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0 and start >= 0:
                    entries.append(text[start:i + 1])
                    start = -1
        i += 1
    return entries


def _scalar(s: str) -> str:
    """Return PyYAML's scalar representation for a string (single line, no suffix)."""
    return yaml.dump(s, default_flow_style=False, allow_unicode=True).splitlines()[0]


def write_yaml(path: Path, data: dict, notes: list = None,
               disabled: list = None, raw_source: str = None) -> None:
    """Write a YAML file.

    All documentation goes as # comment lines at the top:
      - notes:    free-form comments from the C source
      - disabled: commented-out presets (//"-v ...") from the C source

    Active presets that had an inline C comment are written as:
      - -v 174,raw48,Host_Reads_MiB  # ] guessed (ticket #342)
    i.e. a real YAML inline comment, not embedded in the string value.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w') as f:
        for note in (notes or []):
            if note.strip():
                f.write(f'# {note.strip()}\n')
        for dp in (disabled or []):
            if dp.strip():
                f.write(f'# default: {dp.strip()}\n')
        if raw_source:
            f.write('# source:\n')
            for line in raw_source.splitlines():
                f.write(f'#   {line}\n')
        if notes or disabled or raw_source:
            f.write('\n')

        # Write all fields except presets using yaml.dump per field
        presets = data.pop('presets', [])
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True,
                  sort_keys=False, width=120)

        # Write presets manually so inline annotations become real YAML comments
        if not presets:
            f.write('presets: []\n')
        else:
            f.write('presets:\n')
            for p in presets:
                if '  # ' in p:
                    val, annotation = p.split('  # ', 1)
                    f.write(f'  - {_scalar(val.rstrip())}  # {annotation}\n')
                else:
                    f.write(f'  - {_scalar(p)}\n')


def _build_data(base: dict, presets: list) -> dict:
    """Assemble the YAML data dict (active presets only)."""
    data = dict(base)
    data['presets'] = presets
    return data


# ---------------------------------------------------------------------------
# Entry routing
# ---------------------------------------------------------------------------

def route_entry(entry, out_dir: Path, used_slugs: dict, raw_source: str = None) -> None:
    mf, mr, fw, wm, presets, disabled, notes = entry

    # VERSION entry
    if mf.startswith("VERSION:"):
        version = mf.split(":", 1)[1].strip()
        path = out_dir / "_meta" / "version.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        write_yaml(path, {"type": "version", "value": version})
        return

    # DEFAULT entry
    if mf == "DEFAULT":
        base = {
            "type": "default",
            "modelfamily": "DEFAULT",
            "modelregexp": "-",
            "firmwareregexp": "-",
            "warningmsg": wm,
        }
        path = out_dir / "_meta" / "default.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        write_yaml(path, _build_data(base, presets), notes=notes, disabled=disabled, raw_source=raw_source)
        return

    # USB entry
    if mf.startswith("USB:"):
        rest = mf[4:].strip()
        parts = rest.split(";", 1)
        device = parts[0].strip()
        bridge = parts[1].strip() if len(parts) > 1 else ""

        bridge_word = bridge.split()[0] if bridge.split() else ""
        device_word = device.split()[0] if device.split() else ""
        raw_dir = (slugify(bridge_word) if bridge_word
                   else (slugify(device_word) if device_word else "unknown"))
        dir_slug = USB_DIR_ALIASES.get(raw_dir, raw_dir)
        dev_slug = slugify(device) if device else slugify(mr)

        section_dir = out_dir / "usb" / dir_slug
        key = str(section_dir)
        used_slugs.setdefault(key, set())
        path = unique_path(section_dir, dev_slug or "device", used_slugs[key])

        base = {
            "device": device,
            "bridge": bridge,
            "modelregexp": mr,
            "bcddeviceregexp": fw,
            "warningmsg": wm,
        }
        write_yaml(path, _build_data(base, presets), notes=notes, disabled=disabled, raw_source=raw_source)
        return

    # ATA entry
    mfr = manufacturer_slug(mf)
    fam = family_slug(mf)

    section_dir = out_dir / "ata" / mfr
    key = str(section_dir)
    used_slugs.setdefault(key, set())
    path = unique_path(section_dir, fam, used_slugs[key])

    base = {
        "modelfamily": mf,
        "modelregexp": mr,
        "firmwareregexp": fw,
        "warningmsg": wm,
    }
    write_yaml(path, _build_data(base, presets), notes=notes, disabled=disabled, raw_source=raw_source)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Convert drivedb.h to YAML tree")
    parser.add_argument("input", nargs="?", default="drivedb/drivedb.h",
                        metavar="drivedb.h", help="Input drivedb.h (default: drivedb/drivedb.h)")
    parser.add_argument("--out-dir", "-o", default="drivedb/yaml",
                        metavar="DIR", help="Output YAML root (default: drivedb/yaml)")
    parser.add_argument("--include-source", "-s", action="store_true",
                        help="Embed the original C source of each entry as # source: comments")
    args = parser.parse_args()

    src = Path(args.input)
    if not src.exists():
        sys.exit(f"ERROR: input not found: {src}")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    text = src.read_text(encoding="utf-8", errors="replace")
    entries = _parse_with_notes(text)

    if not entries:
        sys.exit("ERROR: no entries parsed — check input file format")

    raw_entries = _extract_raw_entries(text) if args.include_source else []
    if args.include_source and len(raw_entries) != len(entries):
        sys.stderr.write(
            f"WARNING: raw entry count ({len(raw_entries)}) != parsed count ({len(entries)}); "
            "source comments may be misaligned\n"
        )

    used_slugs: dict = {}
    for idx, entry in enumerate(entries):
        raw = raw_entries[idx] if idx < len(raw_entries) else None
        try:
            route_entry(entry, out_dir, used_slugs, raw_source=raw)
        except Exception as e:
            sys.stderr.write(f"WARNING: failed to write entry '{entry[0][:60]}': {e}\n")

    total = len(entries)
    ata = sum(1 for e in entries
              if not e[0].startswith(("VERSION:", "USB:")) and e[0] != "DEFAULT")
    usb = sum(1 for e in entries if e[0].startswith("USB:"))
    print(f"Converted {total} entries ({ata} ATA, {usb} USB) to {out_dir}/")


if __name__ == "__main__":
    main()
