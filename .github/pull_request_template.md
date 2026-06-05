## What this PR changes

<!-- Drive family and models covered -->

<!-- Source of SMART data: own device / user-reported issue / manufacturer datasheet -->

<!-- Firmware versions tested, if applicable -->

---

## DriveDB entry checklist

- [ ] YAML file placed under `drivedb/yaml/ata/<vendor>/` or `drivedb/yaml/usb/<vendor>/`
- [ ] One file per drive family; filename is the `modelfamily` in kebab-case
- [ ] `modelregexp` verified against the exact model string from `smartctl -i`
- [ ] `presets` validated against `smartctl -P showall` and `smartctl -a` output
- [ ] `drivedb.h` regenerated: `python3 drivedb/yaml_to_drivedb.py > drivedb/drivedb.h`
- [ ] Round-trip verified: `python3 drivedb/verify_drivedb.py`
- [ ] YAML valid: `yamllint drivedb/yaml/`
- [ ] CI passes (drivedb workflow)

See [CONTRIBUTING.md](../CONTRIBUTING.md) for the full step-by-step guide.
