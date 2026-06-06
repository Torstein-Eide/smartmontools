#!/usr/bin/env python3
"""
check_yaml.py - validate drivedb YAML source tree or generated drivedb.h

Usage:
    python3 drivedb/check_yaml.py [--yaml-root drivedb/yaml]
    python3 drivedb/check_yaml.py --drivedb drivedb/drivedb.h

Exits 0 on no errors, 1 if any validation error is found.
Each error is printed as:  path: field: message
"""

import argparse
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("PyYAML is required: pip install pyyaml")

try:
    from trieregex import TrieRegEx as _TrieRegEx
    _TRIE_AVAILABLE = True
except ImportError:
    _TRIE_AVAILABLE = False

# ---------------------------------------------------------------------------
# Validation tables (mirrors C tables in atacmds.cpp / knowndrives.cpp)
# ---------------------------------------------------------------------------

VALID_FORMATS = {
    "raw8", "raw16", "raw48", "hex48", "raw56", "hex56",
    "raw64", "hex64", "raw16(raw16)", "raw16(avg16)", "raw24(raw8)",
    "raw24/raw24", "raw24/raw32", "sec2hour", "min2hour",
    "halfmin2hour", "msec24hour32", "tempminmax", "temp10x",
}

VALID_FIRMWAREBUGS = {
    "none", "nologdir", "samsung", "samsung2", "samsung3", "xerrorlba",
}

BYTEORDER_CHARS = set("012345rvwz")

# ---------------------------------------------------------------------------
# Regexp suggestion
# ---------------------------------------------------------------------------

def suggest_regexp(models: list) -> str | None:
    """Return a trie-compressed regex that matches all strings in models.

    Returns None if trieregex is not installed or models is empty.
    """
    if not _TRIE_AVAILABLE or not models:
        return None
    return _TrieRegEx(*models).regex()


# ---------------------------------------------------------------------------
# Low-level checkers
# ---------------------------------------------------------------------------

def check_regexp(pattern, field, path):
    """Return error string if pattern is not a valid Python/POSIX ERE, else None."""
    try:
        re.compile(pattern)
        return None
    except re.error as exc:
        return f"{path}: {field}: invalid regexp {pattern!r}: {exc}"


def check_vendorattribute(va, idx, path):
    """Validate one vendorattribute dict; return list of error strings."""
    errors = []
    loc = f"{path}: vendorattributes[{idx}]"

    if not isinstance(va, dict):
        errors.append(f"{loc}: expected a mapping, got {type(va).__name__}")
        return errors

    # id range
    va_id = va.get("id")
    if va_id is None:
        errors.append(f"{loc}: missing 'id'")
    elif not isinstance(va_id, int) or not (1 <= va_id <= 255):
        errors.append(f"{loc}: id {va_id!r} must be an integer 1–255")

    # format base + embedded byteorder
    format_full = str(va.get("format", ""))
    base, _, byteorder_from_format = format_full.partition(":")
    if format_full and base not in VALID_FORMATS:
        errors.append(f"{loc}: format base {base!r} is not a known format")
    if byteorder_from_format:
        bad = set(byteorder_from_format) - BYTEORDER_CHARS
        if bad:
            errors.append(
                f"{loc}: format byteorder {byteorder_from_format!r} contains "
                f"invalid chars {sorted(bad)}"
            )

    # separate byteorder field
    byteorder_field = str(va.get("byteorder", ""))
    if byteorder_field:
        bad = set(byteorder_field) - BYTEORDER_CHARS
        if bad:
            errors.append(
                f"{loc}: byteorder {byteorder_field!r} contains invalid chars "
                f"{sorted(bad)}"
            )

    # name length
    name = va.get("name", "")
    if len(str(name)) > 23:
        errors.append(f"{loc}: name {name!r} exceeds 23 characters")

    return errors


# ---------------------------------------------------------------------------
# Entry-level checker
# ---------------------------------------------------------------------------

def check_entry_data(data, path, section):
    """Validate one YAML dict; section is 'ata', 'usb', or 'meta'.

    Returns list of error strings.
    """
    errors = []

    if section == "meta":
        return errors  # VERSION / DEFAULT are structural — skip

    if section == "ata":
        # modelregexp: required, non-empty, valid regex (list joined with |)
        mr_raw = data.get("modelregexp")
        compiled_mr = None
        if not mr_raw and mr_raw != 0:
            msg = f"{path}: modelregexp: missing or empty"
            suggestion = suggest_regexp([str(m) for m in data.get("test_model", [])])
            if suggestion:
                msg += f"\n  suggestion: {suggestion}"
            errors.append(msg)
        else:
            if isinstance(mr_raw, list):
                mr = "|".join(str(v) for v in mr_raw if v is not None)
            else:
                mr = str(mr_raw) if mr_raw is not None else ""
            if not mr:
                errors.append(f"{path}: modelregexp: empty after joining list")
            else:
                err = check_regexp(mr, "modelregexp", path)
                if err:
                    errors.append(err)
                else:
                    compiled_mr = re.compile(mr)

        # test_model: each entry must be fully matched by modelregexp
        for model in data.get("test_model", []):
            model = str(model)
            if compiled_mr is None:
                break
            if not compiled_mr.fullmatch(model):
                errors.append(
                    f"{path}: test_model: {model!r} does not match modelregexp"
                )

        # firmwareregexp: valid regex if non-empty
        fw = data.get("firmwareregexp", "")
        if fw:
            err = check_regexp(str(fw), "firmwareregexp", path)
            if err:
                errors.append(err)

        # vendorattributes
        for idx, va in enumerate(data.get("vendorattributes", [])):
            errors.extend(check_vendorattribute(va, idx, path))

        # firmwarebug
        for fb in data.get("firmwarebug", []):
            if fb not in VALID_FIRMWAREBUGS:
                errors.append(
                    f"{path}: firmwarebug: {fb!r} is not a known firmware bug value"
                )

    elif section == "usb":
        # modelregexp: required, non-empty, valid regex
        mr_raw = data.get("modelregexp")
        compiled_mr = None
        if not mr_raw and mr_raw != 0:
            msg = f"{path}: modelregexp: missing or empty"
            suggestion = suggest_regexp([str(m) for m in data.get("test_model", [])])
            if suggestion:
                msg += f"\n  suggestion: {suggestion}"
            errors.append(msg)
        else:
            mr = str(mr_raw) if mr_raw is not None else ""
            if not mr:
                errors.append(f"{path}: modelregexp: empty")
            else:
                err = check_regexp(mr, "modelregexp", path)
                if err:
                    errors.append(err)
                else:
                    compiled_mr = re.compile(mr)

        # test_model: each entry must be fully matched by modelregexp
        for model in data.get("test_model", []):
            model = str(model)
            if compiled_mr is None:
                break
            if not compiled_mr.fullmatch(model):
                errors.append(
                    f"{path}: test_model: {model!r} does not match modelregexp"
                )

        # bcddeviceregexp: valid regex if non-empty
        bcd = data.get("bcddeviceregexp", "")
        if bcd:
            err = check_regexp(str(bcd), "bcddeviceregexp", path)
            if err:
                errors.append(err)

        # devicetype: if present, must be a non-empty string
        dt = data.get("devicetype")
        if dt is not None and not str(dt).strip():
            errors.append(f"{path}: devicetype: present but empty")

    return errors


# ---------------------------------------------------------------------------
# Tree walkers
# ---------------------------------------------------------------------------

def check_yaml_tree(yaml_root: Path):
    """Walk the YAML tree and validate every entry. Returns list of error strings."""
    errors = []

    meta_dir = yaml_root / "_meta"
    ata_dir = yaml_root / "ata"
    usb_dir = yaml_root / "usb"

    for section, directory in [("meta", meta_dir), ("ata", ata_dir), ("usb", usb_dir)]:
        if not directory.exists():
            continue
        for path in sorted(directory.rglob("*.yaml")):
            try:
                with path.open() as f:
                    data = yaml.safe_load(f)
            except Exception as exc:
                errors.append(f"{path}: YAML parse error: {exc}")
                continue
            if not isinstance(data, dict):
                errors.append(f"{path}: expected a YAML mapping at top level")
                continue
            errors.extend(check_entry_data(data, str(path), section))

    return errors


def check_drivedb_h(h_path: Path):
    """Parse drivedb.h with drivedb_to_yaml.parse_entries and check each entry."""
    # Import parse_entries from the sibling module
    sys.path.insert(0, str(h_path.parent.parent / "drivedb"))
    try:
        from drivedb_to_yaml import parse_entries
    except ImportError as exc:
        return [f"{h_path}: cannot import drivedb_to_yaml: {exc}"]

    try:
        text = h_path.read_text()
    except OSError as exc:
        return [f"{h_path}: {exc}"]

    entries = parse_entries(text)
    errors = []

    for mf, mr, fw, wm, presets_str in entries:
        label = f"{h_path}[{mf!r}]"

        # Skip meta entries
        if mf.startswith("VERSION:") or mf == "DEFAULT":
            continue

        # Determine section from modelfamily prefix
        section = "usb" if mf.startswith("USB:") else "ata"

        # Reconstruct a minimal data dict from the 5-tuple for validation
        data = {"modelregexp": mr, "firmwareregexp": fw}

        if not mr:
            errors.append(f"{label}: modelregexp: missing or empty")
        else:
            err = check_regexp(mr, "modelregexp", label)
            if err:
                errors.append(err)

        if fw:
            err = check_regexp(fw, "firmwareregexp", label)
            if err:
                errors.append(err)

        if section == "ata":
            # Parse -v flags from presets string to check vendorattributes
            for i, token in enumerate(_parse_v_flags(presets_str)):
                errors.extend(_check_v_preset(token, i, label))
            # Parse -F flags
            for fb in _parse_f_flags(presets_str):
                if fb not in VALID_FIRMWAREBUGS:
                    errors.append(
                        f"{label}: firmwarebug: {fb!r} is not a known firmware bug value"
                    )

    return errors


def _parse_v_flags(presets_str: str) -> list:
    """Extract -v <spec> tokens from a presets string."""
    return re.findall(r'-v\s+(\S+)', presets_str)


def _parse_f_flags(presets_str: str) -> list:
    """Extract -F <bug> tokens from a presets string."""
    return re.findall(r'-F\s+(\S+)', presets_str)


def _check_v_preset(spec: str, idx: int, label: str) -> list:
    """Check a -v id,format[,name[,byteorder]] spec string."""
    errors = []
    parts = spec.split(",")
    loc = f"{label}: vendorattribute -v {spec!r}"

    # id
    try:
        va_id = int(parts[0])
        if not (1 <= va_id <= 255):
            errors.append(f"{loc}: id {va_id} must be 1–255")
    except (ValueError, IndexError):
        errors.append(f"{loc}: id {parts[0]!r} is not an integer")
        return errors

    if len(parts) < 2:
        return errors

    format_full = parts[1]
    base, _, byteorder_from_format = format_full.partition(":")
    if base not in VALID_FORMATS:
        errors.append(f"{loc}: format base {base!r} is not a known format")
    if byteorder_from_format:
        bad = set(byteorder_from_format) - BYTEORDER_CHARS
        if bad:
            errors.append(
                f"{loc}: format byteorder {byteorder_from_format!r} contains "
                f"invalid chars {sorted(bad)}"
            )

    if len(parts) >= 3:
        name = parts[2]
        if len(name) > 23:
            errors.append(f"{loc}: name {name!r} exceeds 23 characters")

    if len(parts) >= 4:
        bo = parts[3]
        bad = set(bo) - BYTEORDER_CHARS
        if bad:
            errors.append(
                f"{loc}: byteorder {bo!r} contains invalid chars {sorted(bad)}"
            )

    return errors


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    here = Path(__file__).parent
    parser = argparse.ArgumentParser(
        description="Validate drivedb YAML source tree or generated drivedb.h"
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--yaml-root",
        metavar="DIR",
        type=Path,
        default=here / "yaml",
        help="root of YAML tree (default: drivedb/yaml next to this script)",
    )
    group.add_argument(
        "--drivedb",
        metavar="FILE",
        type=Path,
        help="validate a generated drivedb.h instead of the YAML tree",
    )
    parser.add_argument(
        "--suggest",
        action="store_true",
        help="for every entry with test_model, print a suggested modelregexp",
    )
    args = parser.parse_args()

    if args.suggest:
        if not _TRIE_AVAILABLE:
            sys.exit("trieregex is required for --suggest: uv pip install trieregex")
        _run_suggest(args.yaml_root if not args.drivedb else None)
        return

    if args.drivedb:
        errors = check_drivedb_h(args.drivedb)
    else:
        errors = check_yaml_tree(args.yaml_root)

    for err in errors:
        print(err)

    sys.exit(1 if errors else 0)


def _run_suggest(yaml_root: Path):
    """Print suggested modelregexp for every YAML entry that has test_model."""
    if yaml_root is None:
        sys.exit("--suggest requires a YAML root (not supported with --drivedb)")

    for section, directory in [
        ("ata", yaml_root / "ata"),
        ("usb", yaml_root / "usb"),
    ]:
        if not directory.exists():
            continue
        for path in sorted(directory.rglob("*.yaml")):
            try:
                with path.open() as f:
                    data = yaml.safe_load(f)
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            models = [str(m) for m in data.get("test_model", []) if m]
            if not models:
                continue
            suggestion = suggest_regexp(models)
            print(f"{path}:")
            print(f"  test_model:   {models}")
            print(f"  suggestion:   {suggestion}")
            mr_raw = data.get("modelregexp")
            if mr_raw:
                if isinstance(mr_raw, list):
                    current = "|".join(str(v) for v in mr_raw if v is not None)
                else:
                    current = str(mr_raw)
                print(f"  current:      {current}")
            print()


if __name__ == "__main__":
    main()
