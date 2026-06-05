# Contributing a drive entry to drivedb

This guide covers adding or correcting a drive entry in the smartmontools drive database.

## Prerequisites

- smartmontools installed (`smartctl` in PATH)
- Python 3.6+
- `yamllint` for YAML validation (`pip install yamllint`)

## 1. Gather drive information

Run these commands and keep the output handy:

```bash
# Basic identification
smartctl -i /dev/sdX

# Full SMART data
smartctl -a /dev/sdX

# Show all known presets for matching drives (if any)
smartctl -P showall /dev/sdX
```

Key fields from `smartctl -i`:
- **Device Model** — used in `modelregexp`
- **Firmware Version** — used in `firmwareregexp` (leave empty to match all)
- **USB ID** — for USB bridges

## 2. Find or create the YAML file

Drive entries live under `drivedb/yaml/`:

```
drivedb/yaml/
  ata/<vendor-kebab-case>/<family-kebab-case>.yaml   # ATA/SATA drives
  usb/<vendor-kebab-case>/<product-kebab-case>.yaml  # USB-ATA bridges
```

Use **one file per drive family**. Name the file after the `modelfamily` value in
kebab-case, e.g. `wd-red.yaml`.

If your vendor directory does not exist yet, create it.

## 3. Write the entry

### ATA drive

```yaml
modelfamily: "WD Red"
modelregexp: "WDC WD(10|20|30|40)EFRX-[0-9A-Z]+"
firmwareregexp: ""
warningmsg: ""
presets:
  - "-v 190,tempminmax,Airflow_Temperature_Cel"
  - "-v 194,tempminmax,Temperature_Celsius"
```

### USB-ATA bridge

```yaml
device: ""
bridge: "JMicron JMS578"
modelregexp: "0x152d:0x0578"
bcddeviceregexp: ""
warningmsg: ""
presets:
  - "-d sat"
```

### Field reference

| Field | Required | Description |
|---|---|---|
| `modelfamily` | yes | Informal family name shown in smartctl output |
| `modelregexp` | yes | POSIX extended regex matched against "Device Model" |
| `firmwareregexp` | yes | Regex for firmware version, or `""` to match all |
| `warningmsg` | yes | Warning shown to user, or `""` |
| `presets` | yes | List of `-v` attribute flags and `-F` firmware bug flags |

Regex tips:
- Match the exact model string from `smartctl -i` — not a substring.
- Anchor with `^` / `$` only if needed; the field is matched as a full string.
- Use `(A|B)` for model variants in the same family.

## 4. Regenerate and verify

After editing or adding a YAML file, regenerate `drivedb.h` and verify the round-trip:

```bash
# Regenerate drivedb.h from all YAML sources
python3 drivedb/yaml_to_drivedb.py > drivedb/drivedb.h

# Verify the generated file matches the YAML content
python3 drivedb/verify_drivedb.py

# Validate YAML syntax
yamllint drivedb/yaml/
```

Test your drive against the regenerated database:

```bash
smartctl --drivedb=drivedb/drivedb.h -a /dev/sdX
```

The output should now show the correct attribute names and your entry in the "Drive found in smartmontools Database" line.

## 5. Open a pull request

1. Fork the repository and create a branch: `git checkout -b drivedb/<vendor>-<family>`
2. Commit the YAML file and the regenerated `drivedb.h`.
3. Open a PR — the PR template will guide you through the checklist.

For questions, use the [smartmontools mailing list](https://lists.sourceforge.net/lists/listinfo/smartmontools-support).
