#!/usr/bin/env python3
"""
yaml_to_drivedb.py - generate drivedb.h from YAML source tree

Usage:
    python3 drivedb/yaml_to_drivedb.py [--output drivedb/drivedb.h]

Walk order:
    drivedb/yaml/_meta/version.yaml   -> VERSION entry  (first)
    drivedb/yaml/_meta/default.yaml   -> DEFAULT entry  (second)
    drivedb/yaml/ata/**/*.yaml        -> ATA entries    (alphabetical)
    drivedb/yaml/usb/**/*.yaml        -> USB entries    (alphabetical)
"""

import argparse
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("PyYAML is required: pip install pyyaml")

HEADER = """\
/*
 * drivedb.h - smartmontools drive database file
 *
 * Home page of code is: https://www.smartmontools.org
 *
 * Copyright (C) 2003-11 Philip Williams, Bruce Allen
 * Copyright (C) 2008-25 Christian Franke
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 *
 * THIS FILE IS GENERATED from the YAML sources under drivedb/yaml/.
 * Edit those files instead of editing this file directly.
 */

/*
 * Structure used to store drive database entries:
 *
 * struct drive_settings {
 *   const char * modelfamily;
 *   const char * modelregexp;
 *   const char * firmwareregexp;
 *   const char * warningmsg;
 *   const char * presets;
 * };
 */

/*
const drive_settings builtin_knowndrives[] = {
 */"""

FOOTER = """\
/*
}; // builtin_knowndrives[]
 */"""


def c_escape(s: str) -> str:
    """Escape a string for use as a C string literal."""
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def emit_entry(modelfamily, modelregexp, firmwareregexp, warningmsg, presets_list):
    """Return the C struct literal for one drive_settings entry."""
    fw = c_escape(firmwareregexp)
    wm = c_escape(warningmsg)
    mf = c_escape(modelfamily)
    mr = c_escape(modelregexp)

    lines = []
    lines.append(f'  {{ "{mf}",')
    lines.append(f'    "{mr}",')

    if wm:
        lines.append(f'    "{fw}", "{wm}",')
    else:
        lines.append(f'    "{fw}", "",')

    if not presets_list:
        lines.append('    ""')
    else:
        for i, token in enumerate(presets_list):
            suffix = " " if i < len(presets_list) - 1 else ""
            lines.append(f'    "{c_escape(token.strip())}{suffix}"')

    lines.append("  },")
    return "\n".join(lines)


def load_version(data, path):
    value = data.get("value", "")
    if not value:
        sys.stderr.write(f"WARNING: {path}: missing 'value'\n")
    return emit_entry(
        modelfamily=f"VERSION: {value}",
        modelregexp="-",
        firmwareregexp="-",
        warningmsg="Version information",
        presets_list=[],
    )


def _get_list(data, key):
    return [str(v) for v in data.get(key, []) if v is not None]


def load_default(data, path):
    return emit_entry(
        modelfamily="DEFAULT",
        modelregexp="-",
        firmwareregexp="-",
        warningmsg="Default settings",
        presets_list=_get_list(data, "presets"),
    )


ATA_REQUIRED = {"modelfamily", "modelregexp"}
USB_REQUIRED = {"device", "bridge", "modelregexp"}


def load_ata(data, path):
    missing = ATA_REQUIRED - data.keys()
    if missing:
        sys.stderr.write(f"WARNING: {path}: missing required fields: {missing}\n")
    return emit_entry(
        modelfamily=data.get("modelfamily", ""),
        modelregexp=data.get("modelregexp", ""),
        firmwareregexp=data.get("firmwareregexp", ""),
        warningmsg=data.get("warningmsg", ""),
        presets_list=_get_list(data, "presets"),
    )


def load_usb(data, path):
    missing = USB_REQUIRED - data.keys()
    if missing:
        sys.stderr.write(f"WARNING: {path}: missing required fields: {missing}\n")
    device = data.get("device", "")
    bridge = data.get("bridge", "")
    return emit_entry(
        modelfamily=f"USB: {device}; {bridge}",
        modelregexp=data.get("modelregexp", ""),
        firmwareregexp=data.get("bcddeviceregexp", ""),
        warningmsg=data.get("warningmsg", ""),
        presets_list=_get_list(data, "presets"),
    )


def parse_file(path: Path, yaml_root: Path):
    with path.open() as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        sys.stderr.write(f"WARNING: {path}: not a YAML mapping, skipping\n")
        return None

    entry_type = data.get("type", "")

    if entry_type in ("version", "default"):
        if entry_type == "version":
            return load_version(data, path)
        return load_default(data, path)

    # Determine section from path relative to yaml_root
    try:
        rel = path.relative_to(yaml_root)
    except ValueError:
        sys.stderr.write(f"WARNING: {path}: not under yaml_root {yaml_root}\n")
        return None

    section = rel.parts[0] if rel.parts else ""

    if section == "_meta":
        sys.stderr.write(f"WARNING: {path}: unknown meta type '{entry_type}'\n")
        return None
    if section == "ata":
        return load_ata(data, path)
    if section == "usb":
        return load_usb(data, path)

    sys.stderr.write(f"WARNING: {path}: unknown section '{section}', skipping\n")
    return None


def collect_entries(yaml_root: Path):
    entries = []

    # 1. _meta: version first, then default
    meta_dir = yaml_root / "_meta"
    for name in ("version.yaml", "default.yaml"):
        p = meta_dir / name
        if p.exists():
            entry = parse_file(p, yaml_root)
            if entry:
                entries.append(entry)
        else:
            sys.stderr.write(f"WARNING: {p} not found\n")

    # 2. ata/** sorted
    ata_dir = yaml_root / "ata"
    if ata_dir.is_dir():
        for p in sorted(ata_dir.rglob("*.yaml")):
            entry = parse_file(p, yaml_root)
            if entry:
                entries.append(entry)

    # 3. usb/** sorted
    usb_dir = yaml_root / "usb"
    if usb_dir.is_dir():
        for p in sorted(usb_dir.rglob("*.yaml")):
            entry = parse_file(p, yaml_root)
            if entry:
                entries.append(entry)

    return entries


def main():
    parser = argparse.ArgumentParser(description="Generate drivedb.h from YAML tree")
    parser.add_argument("--output", "-o", metavar="FILE",
                        help="Output file (default: stdout)")
    parser.add_argument("--yaml-root", metavar="DIR",
                        help="Root of YAML tree (default: drivedb/yaml next to this script)")
    args = parser.parse_args()

    script_dir = Path(__file__).parent
    yaml_root = Path(args.yaml_root) if args.yaml_root else script_dir / "yaml"

    if not yaml_root.is_dir():
        sys.exit(f"ERROR: YAML root not found: {yaml_root}")

    entries = collect_entries(yaml_root)

    out_lines = [HEADER, ""]
    for entry in entries:
        out_lines.append(entry)
    out_lines.append(FOOTER)
    output = "\n".join(out_lines) + "\n"

    if args.output:
        Path(args.output).write_text(output)
    else:
        sys.stdout.write(output)


if __name__ == "__main__":
    main()
