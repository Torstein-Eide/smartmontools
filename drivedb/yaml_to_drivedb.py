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
import datetime
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("PyYAML is required: pip install pyyaml")

try:
    import jinja2
except ImportError:
    sys.exit("Jinja2 is required: pip install jinja2")

FOOTER = """\
/*
}; // builtin_knowndrives[]
 */"""


def build_header(preamble) -> str:
    """Render the file header via Jinja2.

    Only the intro (copyright/license) section is read from preamble.
    The struct documentation is hardcoded in drivedb_header.j2.
    """
    template_path = Path(__file__).parent / "drivedb_header.j2"
    if not template_path.exists():
        sys.exit(f"ERROR: template not found: {template_path}")

    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(template_path.parent)),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )

    if not isinstance(preamble, dict):
        preamble = {}

    ctx = {
        'intro': preamble.get('intro', {}),
        'year_end': f"{datetime.date.today().year % 100:02d}",
    }
    return env.get_template("drivedb_header.j2").render(ctx).rstrip('\n')


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
    """Returns (entry_str, preamble) where preamble is a dict or list."""
    value = data.get("value", "")
    if not value:
        sys.stderr.write(f"WARNING: {path}: missing 'value'\n")
    entry = emit_entry(
        modelfamily=f"VERSION: {value}",
        modelregexp="-",
        firmwareregexp="-",
        warningmsg="Version information",
        presets_list=[],
    )
    # preamble may be a dict (new structured format) or a list (legacy raw blocks)
    preamble = data.get("preamble", {})
    return entry, preamble


def _get_list(data, key):
    return [str(v) for v in data.get(key, []) if v is not None]


def _vendorattr_to_preset(va) -> str:
    """Reconstruct a -v preset string from a vendorattribute dict."""
    if not isinstance(va, dict):
        return str(va)
    parts = [str(va.get('id', ''))]
    if 'format' in va:
        parts.append(str(va['format']))
    if 'name' in va:
        parts.append(str(va['name']))
    if 'byteorder' in va:
        parts.append(str(va['byteorder']))
    return f"-v {','.join(parts)}"


def _get_all_presets(data) -> list:
    """Return all preset strings: -v flags, -F flags, -d flag, then remaining presets."""
    result = []
    for va in data.get('vendorattributes', []):
        result.append(_vendorattr_to_preset(va))
    for fb in data.get('firmwarebug', []):
        if fb:
            result.append(f'-F {fb}')
    dt = data.get('devicetype')
    if dt:
        result.append(f'-d {dt}')
    result.extend(_get_list(data, 'presets'))
    return result


def load_default(data, path):
    return emit_entry(
        modelfamily="DEFAULT",
        modelregexp="-",
        firmwareregexp="-",
        warningmsg="Default settings",
        presets_list=_get_all_presets(data),
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
        presets_list=_get_all_presets(data),
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
        presets_list=_get_all_presets(data),
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
    """Returns (entries, preamble) where preamble is the list from version.yaml."""
    entries = []
    preamble = []

    # 1. _meta: version first, then default
    meta_dir = yaml_root / "_meta"
    version_path = meta_dir / "version.yaml"
    if version_path.exists():
        result = parse_file(version_path, yaml_root)
        if result:
            entry, preamble = result  # load_version returns (entry, preamble)
            entries.append(entry)
    else:
        sys.stderr.write(f"WARNING: {version_path} not found\n")

    default_path = meta_dir / "default.yaml"
    if default_path.exists():
        entry = parse_file(default_path, yaml_root)
        if entry:
            entries.append(entry)
    else:
        sys.stderr.write(f"WARNING: {default_path} not found\n")

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

    return entries, preamble


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

    entries, preamble = collect_entries(yaml_root)
    header = build_header(preamble)

    out_lines = [header, ""]
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
