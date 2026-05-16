# Kaizen Enclosure CAD

Parametric CadQuery models for the Kaizen voice-assistant case.

See spec: `docs/superpowers/specs/2026-05-16-enclosure-design.md`.

## Quickstart

```bash
cd hardware/enclosure
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Run tests
pytest -v

# Generate STL + STEP for all parts
python build.py
ls output/
```

Outputs land in `output/`:
- `compute_base.stl` + `.step`
- `mic_dome.stl` + `.step`
- `plunger.stl` + `.step`

## Tweaking dimensions

All parameters live in `params.py`. Edit and re-run `python build.py`.

The XVF3800-related parameters near the bottom of `params.py` are placeholders
(see spec § 8 and § 15) and should be remeasured after a v0 fit-check print.
