# Enclosure CadQuery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a parametric CadQuery Python project that generates STL + STEP files for the three printed parts (compute base, mic dome, plunger) of the Kaizen voice-assistant enclosure described in `docs/superpowers/specs/2026-05-16-enclosure-design.md`.

**Architecture:** Single Python package `hardware/enclosure/` with a parameters module, one module per printed part, an export script, and pytest checks that validate each generated solid is manifold and has expected bounding-box dimensions. CAD parameters from the spec live in `params.py` so the XVF3800 placeholders (mounting holes, USB-C position, LED ring radii) can be edited in one place after the v0 print fit-check.

**Tech Stack:** Python 3.11+, CadQuery 2.4+, pytest, pip venv.

---

## Spec reference

All section numbers below refer to `docs/superpowers/specs/2026-05-16-enclosure-design.md`.

## File structure

Created under `hardware/enclosure/`:

```
hardware/enclosure/
├── README.md             # quickstart: install, build, output paths
├── params.py             # all parametric dimensions
├── compute_base.py       # build_compute_base() -> cadquery.Workplane
├── mic_dome.py           # build_mic_dome() -> cadquery.Workplane
├── plunger.py            # build_plunger() -> cadquery.Workplane
├── build.py              # CLI: renders all 3 parts, exports STL + STEP to output/
├── test_enclosure.py     # pytest: validity + dimension assertions
├── requirements.txt      # cadquery, pytest
├── .gitignore            # output/, __pycache__/, .venv/
└── output/               # generated STL/STEP (gitignored)
```

Each part module exports one function returning a `cadquery.Workplane` solid. `build.py` ties them together; `test_enclosure.py` imports each function and runs assertions without writing files.

---

## Task 1: Project setup and parameters module

**Files:**
- Create: `hardware/enclosure/.gitignore`
- Create: `hardware/enclosure/requirements.txt`
- Create: `hardware/enclosure/params.py`
- Create: `hardware/enclosure/README.md`
- Create: `hardware/enclosure/output/.gitkeep`

- [ ] **Step 1.1: Create directory structure and .gitignore**

```bash
mkdir -p hardware/enclosure/output
touch hardware/enclosure/output/.gitkeep
```

Write `hardware/enclosure/.gitignore`:

```
output/*
!output/.gitkeep
__pycache__/
*.pyc
.venv/
.pytest_cache/
```

- [ ] **Step 1.2: Write requirements.txt**

Write `hardware/enclosure/requirements.txt`:

```
cadquery>=2.4
pytest>=7.0
```

- [ ] **Step 1.3: Set up venv and install dependencies**

```bash
cd hardware/enclosure
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -c "import cadquery; print(cadquery.__version__)"
```

Expected: prints a version string `2.x.x` and exits clean.

- [ ] **Step 1.4: Write params.py with all parametric dimensions from spec**

Write `hardware/enclosure/params.py`:

```python
"""
Parametric dimensions for the Kaizen voice-assistant enclosure.

All units are millimeters unless otherwise noted. Source: spec
docs/superpowers/specs/2026-05-16-enclosure-design.md.

Parameters marked XVF3800 PLACEHOLDER are estimates until measured
directly off the board. Update after a v0 fit-check print.
"""

# --- Compute base outer dimensions ---
BASE_X = 95.0
BASE_Y = 95.0
BASE_Z = 52.0
WALL = 2.0
FLOOR = 2.4

# --- Mic dome outer dimensions ---
DOME_OD = 113.0
DOME_HEIGHT = 18.0
DOME_WALL = 2.0
DOME_TOP_THICKNESS = 2.0          # solid (non-diffuser) regions
DOME_DIFFUSER_THICKNESS = 0.6     # over the LED ring
DOME_LIP_THICKNESS = 3.0          # flange that overhangs the base

# --- Pi 5 footprint (used for standoff placement, port positions) ---
PI_X = 85.0
PI_Y = 56.0
PI_MOUNT_PATTERN_X = 58.0
PI_MOUNT_PATTERN_Y = 49.0
PI_MOUNT_HOLE_D = 2.5             # M2.5 standoff press-fit (params: STANDOFF_FIT)
PI_STANDOFF_HEIGHT = 6.0          # above base floor
PI_STANDOFF_OD = 5.0              # printed boss outer diameter
STANDOFF_FIT = -0.2               # interference for press-fit brass standoffs

# Pi is placed centered along the long axis, offset toward GPIO side
PI_OFFSET_X = 0.0                 # X-centered in base
PI_OFFSET_Y = -8.0                # shifted toward left (GPIO) face

# --- AI HAT+ stack (no part to print, just clearance budget) ---
PI_TOP_TO_HAT_TOP = 17.6          # cooler height + HAT PCB thickness
HAT_TO_HAILO_TOP = 5.0
COMPUTE_STACK_CLEARANCE = 5.0     # air above Hailo
COMPUTE_CHAMBER_HEIGHT = (
    PI_STANDOFF_HEIGHT + 1.6      # PCB
    + 15.0                        # cooler+components topside
    + HAT_TO_HAILO_TOP
    + COMPUTE_STACK_CLEARANCE
)  # ~30mm

# --- Integrated divider (top of compute chamber, floor of mic chamber) ---
DIVIDER_THICKNESS = 1.5
DIVIDER_Z = FLOOR + COMPUTE_CHAMBER_HEIGHT  # absolute Z of divider top

# --- Port cutouts (Pi 5 standard positions, +0.5mm clearance per edge) ---
PORT_CLEARANCE = 0.5
PORTS_RIGHT_W = 53.0              # USB block + Ethernet
PORTS_RIGHT_H = 17.0
HDMI_W = 7.0
HDMI_H = 4.0
USBC_W = 10.0
USBC_H = 4.0
AUDIO_JACK_D = 6.5
MICROSD_W = 14.0
MICROSD_H = 2.0

# --- Power button plunger ---
PLUNGER_HEAD_D = 6.0
PLUNGER_HEAD_T = 2.0
PLUNGER_STEM_D = 4.0
PLUNGER_STEM_LEN = 8.0            # initial guess; tune in v2
PLUNGER_HOLE_D = 4.4              # slip fit on stem

# --- Vents ---
VENT_SLOT_W = 1.0
VENT_SLOT_H = 12.0
VENT_SLOT_PITCH = 3.0
SIDE_VENT_COUNT = 7
SIDE_VENT_HEIGHT = 30.0
BOTTOM_VENT_COVERAGE = 0.7        # fraction of floor used for vent grid

# --- Dome → base attachment ---
DOME_SCREW_COUNT = 4
DOME_SCREW_PCD = 88.0             # pitch-circle diameter; inside dome OD, outside base footprint
DOME_SCREW_D = 3.0
DOME_SCREW_BOSS_OD = 6.0
DOME_SCREW_BOSS_HEIGHT = 4.0      # rises above divider

# --- Feet ---
FOOT_HEIGHT = 4.0
FOOT_OD = 8.0
FOOT_INSET = 8.0                  # from each corner

# --- XVF3800 PLACEHOLDERS (revise after fit-check) ---
XVF_OD = 106.0
XVF_PCB_T = 1.6
XVF_STANDOFF_HEIGHT = 5.0
XVF_MOUNT_COUNT = 4
XVF_MOUNT_PCD = 80.0
XVF_MOUNT_HOLE_D = 2.5
XVF_USBC_ANGLE_DEG = 0.0          # 0 = +X radial direction
XVF_LED_RING_OUTER_R = 45.0
XVF_LED_RING_INNER_R = 38.0
XVF_MIC_R = 48.0
XVF_MIC_ANGLES_DEG = [0.0, 90.0, 180.0, 270.0]
XVF_MIC_PORT_D = 3.0

# --- Cable pass-through ---
CABLE_GROMMET_D = 10.0
CABLE_GROMMET_X = 35.0            # offset from divider center (in dead-space corner)
CABLE_GROMMET_Y = -35.0
```

- [ ] **Step 1.5: Write README.md quickstart**

Write `hardware/enclosure/README.md`:

````markdown
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
````

- [ ] **Step 1.6: Commit**

```bash
git add hardware/enclosure/
git commit -m "feat(enclosure): scaffold cadquery project with parameters

Add hardware/enclosure/ with venv-managed cadquery dependency,
parametric dimensions module, and quickstart README. Parameters
derived from spec 2026-05-16-enclosure-design.md."
```

---

## Task 2: Compute base — shell + Pi standoffs + integrated divider

**Files:**
- Create: `hardware/enclosure/compute_base.py`
- Create: `hardware/enclosure/test_enclosure.py`

- [ ] **Step 2.1: Write the first failing test**

Write `hardware/enclosure/test_enclosure.py`:

```python
"""Pytest checks for enclosure parts. Tests validate that each solid
is manifold and has the expected bounding box per the spec."""

import pytest
from compute_base import build_compute_base


def _bbox(solid):
    bb = solid.val().BoundingBox()
    return (bb.xlen, bb.ylen, bb.zlen)


def test_compute_base_is_valid():
    part = build_compute_base()
    assert part.val().isValid(), "compute_base must be a valid manifold solid"


def test_compute_base_bounding_box():
    part = build_compute_base()
    xlen, ylen, zlen = _bbox(part)
    # Allow 1mm tolerance for boss/feature overshoot
    assert 94.0 <= xlen <= 96.0, f"compute_base X = {xlen}"
    assert 94.0 <= ylen <= 96.0, f"compute_base Y = {ylen}"
    assert 50.0 <= zlen <= 56.0, f"compute_base Z = {zlen}"


def test_compute_base_has_volume():
    part = build_compute_base()
    assert part.val().Volume() > 10000, "compute_base volume too small"
```

- [ ] **Step 2.2: Run the test to verify it fails**

```bash
cd hardware/enclosure
source .venv/bin/activate
pytest test_enclosure.py -v
```

Expected: 3 failures, all with `ModuleNotFoundError: No module named 'compute_base'`.

- [ ] **Step 2.3: Implement minimum compute_base.py to pass bbox tests**

Write `hardware/enclosure/compute_base.py`:

```python
"""Compute base: outer shell, Pi standoffs, integrated mic-chamber divider.

Single function `build_compute_base()` returns a cadquery.Workplane solid
representing the complete compute base part (no separate dome).
"""

import cadquery as cq

import params as p


def _outer_shell():
    """95 x 95 x 52 box, hollowed from the top, with a 1.5mm divider near top."""
    box = cq.Workplane("XY").box(p.BASE_X, p.BASE_Y, p.BASE_Z, centered=(True, True, False))

    # Hollow the compute chamber (from floor up to underside of divider)
    chamber_z = p.DIVIDER_Z - p.FLOOR
    chamber = (
        cq.Workplane("XY")
        .workplane(offset=p.FLOOR)
        .box(
            p.BASE_X - 2 * p.WALL,
            p.BASE_Y - 2 * p.WALL,
            chamber_z,
            centered=(True, True, False),
        )
    )
    box = box.cut(chamber)

    # Hollow the mic chamber pocket (from divider top up to base top)
    mic_chamber_z = p.BASE_Z - p.DIVIDER_Z - p.DIVIDER_THICKNESS
    if mic_chamber_z > 0:
        mic_pocket = (
            cq.Workplane("XY")
            .workplane(offset=p.DIVIDER_Z + p.DIVIDER_THICKNESS)
            .box(
                p.BASE_X - 2 * p.WALL,
                p.BASE_Y - 2 * p.WALL,
                mic_chamber_z,
                centered=(True, True, False),
            )
        )
        box = box.cut(mic_pocket)
    return box


def _pi_standoffs(base):
    """Add 4 cylindrical bosses for Pi M2.5 standoff press-fit."""
    half_x = p.PI_MOUNT_PATTERN_X / 2
    half_y = p.PI_MOUNT_PATTERN_Y / 2
    positions = [
        (p.PI_OFFSET_X + dx, p.PI_OFFSET_Y + dy)
        for dx in (-half_x, half_x)
        for dy in (-half_y, half_y)
    ]
    for x, y in positions:
        boss = (
            cq.Workplane("XY")
            .workplane(offset=p.FLOOR)
            .center(x, y)
            .circle(p.PI_STANDOFF_OD / 2)
            .extrude(p.PI_STANDOFF_HEIGHT)
        )
        base = base.union(boss)
        # Drill a press-fit hole for the brass standoff
        hole = (
            cq.Workplane("XY")
            .workplane(offset=p.FLOOR)
            .center(x, y)
            .circle((p.PI_MOUNT_HOLE_D + p.STANDOFF_FIT) / 2)
            .extrude(p.PI_STANDOFF_HEIGHT + 0.01)
        )
        base = base.cut(hole)
    return base


def build_compute_base():
    base = _outer_shell()
    base = _pi_standoffs(base)
    return base


if __name__ == "__main__":
    part = build_compute_base()
    cq.exporters.export(part, "output/compute_base.step")
    cq.exporters.export(part, "output/compute_base.stl")
    print("Exported output/compute_base.{step,stl}")
```

- [ ] **Step 2.4: Run tests, verify pass**

```bash
pytest test_enclosure.py -v
```

Expected: 3 passing tests.

- [ ] **Step 2.5: Commit**

```bash
git add hardware/enclosure/compute_base.py hardware/enclosure/test_enclosure.py
git commit -m "feat(enclosure): compute base shell with Pi standoffs and divider

Adds the outer 95x95x52mm shell hollowed into compute and mic chambers
separated by a 1.5mm integrated divider, plus 4 press-fit Pi M2.5
standoff bosses. Pytest covers validity and bounding box."
```

---

## Task 3: Compute base — port cutouts and plunger hole

**Files:**
- Modify: `hardware/enclosure/compute_base.py`
- Modify: `hardware/enclosure/test_enclosure.py`

- [ ] **Step 3.1: Add a failing test for port cutout count**

Append to `hardware/enclosure/test_enclosure.py`:

```python
def test_compute_base_has_port_cutouts():
    """After port cutouts, the part should have substantially more faces
    than a plain shell (each cutout adds 5-6 faces)."""
    part = build_compute_base()
    # Shell-only had ~18 faces; with 7+ cutouts expect >40
    n_faces = len(part.val().Faces())
    assert n_faces > 40, f"expected >40 faces after port cutouts, got {n_faces}"
```

- [ ] **Step 3.2: Run test to verify it fails**

```bash
pytest test_enclosure.py::test_compute_base_has_port_cutouts -v
```

Expected: FAIL — face count below 40.

- [ ] **Step 3.3: Add port cutouts to compute_base.py**

In `compute_base.py`, add new helpers above `build_compute_base()`:

```python
def _port_cutout_right(base):
    """Single rect cutout for 2x USB-A 3.0 + 2x USB-A 2.0 + GbE on the right face."""
    w = p.PORTS_RIGHT_W + 2 * p.PORT_CLEARANCE
    h = p.PORTS_RIGHT_H + 2 * p.PORT_CLEARANCE
    z = p.FLOOR + p.PI_STANDOFF_HEIGHT + 1.6 + 1.0  # PCB top + a bit
    cutout = (
        cq.Workplane("YZ")
        .workplane(offset=p.BASE_X / 2 - p.WALL)
        .center(p.PI_OFFSET_Y, z + h / 2)
        .rect(h, w)  # YZ plane: first arg = Y span, second = Z span — w spans Y here
        .extrude(p.WALL + 1.0)
    )
    return base.cut(cutout)


def _port_cutouts_front(base):
    """2x micro-HDMI, USB-C, 3.5mm audio jack on the +Y short face."""
    z_pcb_top = p.FLOOR + p.PI_STANDOFF_HEIGHT + 1.6
    z = z_pcb_top + 1.5  # connectors sit ~1.5mm above PCB

    # Positions along X (front face), relative to Pi center
    # Per Pi 5 silkscreen: HDMI0, HDMI1, USB-C, audio (left-to-right looking from front)
    positions_w_h = [
        (-21.0, p.HDMI_W, p.HDMI_H),
        (-11.0, p.HDMI_W, p.HDMI_H),
        (+3.0, p.USBC_W, p.USBC_H),
        (+22.0, p.AUDIO_JACK_D, p.AUDIO_JACK_D),
    ]
    for offset_x, w, h in positions_w_h:
        # Front face is the +Y wall (looking at -Y direction)
        cutout = (
            cq.Workplane("XZ")
            .workplane(offset=p.BASE_Y / 2 - p.WALL)
            .center(p.PI_OFFSET_X + offset_x, z + h / 2)
            .rect(w + 2 * p.PORT_CLEARANCE, h + 2 * p.PORT_CLEARANCE)
            .extrude(p.WALL + 1.0)
        )
        base = base.cut(cutout)
    return base


def _microsd_cutout(base):
    """microSD slot on the back face (-Y), flush with Pi PCB underside."""
    z = p.FLOOR + p.PI_STANDOFF_HEIGHT - 1.0  # microSD is under the PCB
    cutout = (
        cq.Workplane("XZ")
        .workplane(offset=-p.BASE_Y / 2 + p.WALL - 1.0)
        .center(p.PI_OFFSET_X, z + p.MICROSD_H / 2)
        .rect(p.MICROSD_W + 2 * p.PORT_CLEARANCE, p.MICROSD_H + 2 * p.PORT_CLEARANCE)
        .extrude(p.WALL + 2.0)
    )
    return base.cut(cutout)


def _plunger_hole(base):
    """Through-hole for the power-button plunger on the front face."""
    z_pcb_top = p.FLOOR + p.PI_STANDOFF_HEIGHT + 1.6
    z = z_pcb_top + 4.0  # rough Pi 5 button height above PCB
    # Pi 5 power button is near USB-C corner — offset along X
    x = p.PI_OFFSET_X + 12.0
    cutout = (
        cq.Workplane("XZ")
        .workplane(offset=p.BASE_Y / 2 - p.WALL - 1.0)
        .center(x, z)
        .circle(p.PLUNGER_HOLE_D / 2)
        .extrude(p.WALL + 2.0)
    )
    return base.cut(cutout)
```

Then modify `build_compute_base()`:

```python
def build_compute_base():
    base = _outer_shell()
    base = _pi_standoffs(base)
    base = _port_cutout_right(base)
    base = _port_cutouts_front(base)
    base = _microsd_cutout(base)
    base = _plunger_hole(base)
    return base
```

- [ ] **Step 3.4: Run all tests, verify pass**

```bash
pytest test_enclosure.py -v
```

Expected: 4 passing tests.

- [ ] **Step 3.5: Render preview STEP and visually inspect**

```bash
python compute_base.py
ls output/
```

Expected: `output/compute_base.step` and `output/compute_base.stl` exist, both >0 bytes. Open the STEP in a viewer (FreeCAD, OnShape, or kicad-step-viewer) and confirm cutouts are on the correct faces. If a cutout is on the wrong face, adjust the workplane direction in the helper.

- [ ] **Step 3.6: Commit**

```bash
git add hardware/enclosure/compute_base.py hardware/enclosure/test_enclosure.py
git commit -m "feat(enclosure): port cutouts and plunger hole on compute base

Right-face USB+Ethernet block, front-face HDMI/USB-C/audio,
back-face microSD slot, front-face plunger through-hole."
```

---

## Task 4: Compute base — vents, dome screw bosses, cable pass-through, mic acoustic ports

**Files:**
- Modify: `hardware/enclosure/compute_base.py`
- Modify: `hardware/enclosure/test_enclosure.py`

- [ ] **Step 4.1: Add failing tests for vent + boss features**

Append to `test_enclosure.py`:

```python
def test_compute_base_has_vents():
    """Side and bottom vents should add many small face groups (slot grids)."""
    part = build_compute_base()
    n_faces = len(part.val().Faces())
    # After vents we should be well past 60 faces
    assert n_faces > 60, f"expected >60 faces after vents added, got {n_faces}"


def test_compute_base_volume_reasonable():
    """Total material volume should be 50-120cm^3 (50000-120000 mm^3) — a
    rough sanity check that we haven't accidentally hollowed everything
    or left it solid."""
    part = build_compute_base()
    vol = part.val().Volume()
    assert 50_000 < vol < 120_000, f"unexpected volume {vol} mm^3"
```

- [ ] **Step 4.2: Run tests, verify failures**

```bash
pytest test_enclosure.py::test_compute_base_has_vents test_enclosure.py::test_compute_base_volume_reasonable -v
```

Expected: `test_compute_base_has_vents` fails (face count still low or just barely above 60); `test_compute_base_volume_reasonable` may pass or fail depending on current solid mass.

- [ ] **Step 4.3: Add vent, boss, cable pass-through, mic port helpers**

In `compute_base.py`, add helpers above `build_compute_base()`:

```python
def _side_vents(base):
    """Vertical slot vents on the -X (GPIO) face — 7 slots, 1x30mm, 3mm pitch."""
    z_center = p.FLOOR + p.COMPUTE_CHAMBER_HEIGHT / 2
    total_width = (p.SIDE_VENT_COUNT - 1) * p.VENT_SLOT_PITCH
    for i in range(p.SIDE_VENT_COUNT):
        y = -total_width / 2 + i * p.VENT_SLOT_PITCH
        cutout = (
            cq.Workplane("YZ")
            .workplane(offset=-p.BASE_X / 2 + p.WALL - 1.0)
            .center(y, z_center)
            .rect(p.VENT_SLOT_W, p.SIDE_VENT_HEIGHT)
            .extrude(p.WALL + 2.0)
        )
        base = base.cut(cutout)
    return base


def _bottom_vents(base):
    """Slot grid in the bottom plate."""
    # Avoid the 4 standoff bosses by limiting to inner region
    inner_x = p.PI_MOUNT_PATTERN_X * 0.45
    inner_y = p.PI_MOUNT_PATTERN_Y * 0.45
    slots_x = int((2 * inner_x) / p.VENT_SLOT_PITCH)
    slots_y_per_strip = 4  # 4 slots arranged across the strip width
    pitch = p.VENT_SLOT_PITCH
    for ix in range(slots_x):
        x = -inner_x + ix * pitch + pitch / 2
        if abs(x) > inner_x - 1:
            continue
        for iy in range(slots_y_per_strip):
            y = -inner_y + iy * (2 * inner_y / (slots_y_per_strip - 1))
            cutout = (
                cq.Workplane("XY")
                .center(x, y)
                .rect(p.VENT_SLOT_W, p.VENT_SLOT_H)
                .extrude(p.FLOOR + 0.1)
            )
            base = base.cut(cutout)
    return base


def _dome_screw_bosses(base):
    """4 screw bosses rising from the divider top to clamp the dome lip."""
    import math
    pcd_r = p.DOME_SCREW_PCD / 2
    for i in range(p.DOME_SCREW_COUNT):
        angle = i * (2 * math.pi / p.DOME_SCREW_COUNT) + math.pi / 4  # 45° offset
        x = pcd_r * math.cos(angle)
        y = pcd_r * math.sin(angle)
        # Only place bosses that fit within the base footprint (with margin)
        if abs(x) > p.BASE_X / 2 - p.DOME_SCREW_BOSS_OD / 2 - 1:
            continue
        if abs(y) > p.BASE_Y / 2 - p.DOME_SCREW_BOSS_OD / 2 - 1:
            continue
        boss = (
            cq.Workplane("XY")
            .workplane(offset=p.DIVIDER_Z + p.DIVIDER_THICKNESS)
            .center(x, y)
            .circle(p.DOME_SCREW_BOSS_OD / 2)
            .extrude(p.DOME_SCREW_BOSS_HEIGHT)
        )
        base = base.union(boss)
        # Tap hole (no thread — self-tapping M3 into plastic)
        hole = (
            cq.Workplane("XY")
            .workplane(offset=p.DIVIDER_Z + p.DIVIDER_THICKNESS)
            .center(x, y)
            .circle(p.DOME_SCREW_D / 2 * 0.9)  # 90% of nominal for self-tap
            .extrude(p.DOME_SCREW_BOSS_HEIGHT + 0.1)
        )
        base = base.cut(hole)
    return base


def _cable_passthrough(base):
    """Ø10mm hole in the divider for the USB-A→USB-C cable."""
    cutout = (
        cq.Workplane("XY")
        .workplane(offset=p.DIVIDER_Z)
        .center(p.CABLE_GROMMET_X, p.CABLE_GROMMET_Y)
        .circle(p.CABLE_GROMMET_D / 2)
        .extrude(p.DIVIDER_THICKNESS + 0.1)
    )
    return base.cut(cutout)


def _mic_acoustic_ports(base):
    """4x Ø3mm direct acoustic openings in the divider, aligned with XVF3800 mics."""
    import math
    for angle_deg in p.XVF_MIC_ANGLES_DEG:
        angle = math.radians(angle_deg)
        x = p.XVF_MIC_R * math.cos(angle)
        y = p.XVF_MIC_R * math.sin(angle)
        # Only cut if the mic position lands within the divider footprint
        if abs(x) > p.BASE_X / 2 - p.WALL - 1:
            continue
        if abs(y) > p.BASE_Y / 2 - p.WALL - 1:
            continue
        cutout = (
            cq.Workplane("XY")
            .workplane(offset=p.DIVIDER_Z)
            .center(x, y)
            .circle(p.XVF_MIC_PORT_D / 2)
            .extrude(p.DIVIDER_THICKNESS + 0.1)
        )
        base = base.cut(cutout)
    return base


def _xvf_mount_bosses(base):
    """Standoff bosses on the divider for the XVF3800 board."""
    import math
    pcd_r = p.XVF_MOUNT_PCD / 2
    for i in range(p.XVF_MOUNT_COUNT):
        angle = i * (2 * math.pi / p.XVF_MOUNT_COUNT)
        x = pcd_r * math.cos(angle)
        y = pcd_r * math.sin(angle)
        if abs(x) > p.BASE_X / 2 - p.WALL - 2:
            continue
        if abs(y) > p.BASE_Y / 2 - p.WALL - 2:
            continue
        boss = (
            cq.Workplane("XY")
            .workplane(offset=p.DIVIDER_Z + p.DIVIDER_THICKNESS)
            .center(x, y)
            .circle((p.XVF_MOUNT_HOLE_D + 2.0) / 2)
            .extrude(p.XVF_STANDOFF_HEIGHT)
        )
        base = base.union(boss)
        hole = (
            cq.Workplane("XY")
            .workplane(offset=p.DIVIDER_Z + p.DIVIDER_THICKNESS)
            .center(x, y)
            .circle(p.XVF_MOUNT_HOLE_D / 2 * 0.9)
            .extrude(p.XVF_STANDOFF_HEIGHT + 0.1)
        )
        base = base.cut(hole)
    return base
```

Update `build_compute_base()`:

```python
def build_compute_base():
    base = _outer_shell()
    base = _pi_standoffs(base)
    base = _port_cutout_right(base)
    base = _port_cutouts_front(base)
    base = _microsd_cutout(base)
    base = _plunger_hole(base)
    base = _side_vents(base)
    base = _bottom_vents(base)
    base = _dome_screw_bosses(base)
    base = _cable_passthrough(base)
    base = _mic_acoustic_ports(base)
    base = _xvf_mount_bosses(base)
    return base
```

- [ ] **Step 4.4: Run tests, verify pass**

```bash
pytest test_enclosure.py -v
```

Expected: all 6 tests pass. If `test_compute_base_volume_reasonable` fails because volume is too high or too low, adjust the bottom vent count or the BASE_Z dimension and re-run.

- [ ] **Step 4.5: Render and visually inspect**

```bash
python compute_base.py
```

Open `output/compute_base.step` in a viewer. Verify:
- Side vents on -X face
- Bottom vent grid visible looking up at the base floor
- 4 dome screw bosses on the divider top
- Cable pass-through hole in the divider
- Mic ports in the divider near the outer edge

- [ ] **Step 4.6: Commit**

```bash
git add hardware/enclosure/compute_base.py hardware/enclosure/test_enclosure.py
git commit -m "feat(enclosure): vents, dome bosses, cable port, mic ports

Side+bottom slot vents for airflow, 4 M3 screw bosses for dome
clamping, USB cable pass-through and 4 acoustic mic ports through
the integrated divider, plus XVF3800 mounting standoff bosses."
```

---

## Task 5: Mic dome

**Files:**
- Create: `hardware/enclosure/mic_dome.py`
- Modify: `hardware/enclosure/test_enclosure.py`

- [ ] **Step 5.1: Add failing dome tests**

Append to `test_enclosure.py`:

```python
from mic_dome import build_mic_dome


def test_mic_dome_is_valid():
    part = build_mic_dome()
    assert part.val().isValid(), "mic_dome must be a valid manifold solid"


def test_mic_dome_bounding_box():
    part = build_mic_dome()
    xlen, ylen, zlen = _bbox(part)
    assert 112.0 <= xlen <= 114.0, f"mic_dome X = {xlen}"
    assert 112.0 <= ylen <= 114.0, f"mic_dome Y = {ylen}"
    assert 17.0 <= zlen <= 19.0, f"mic_dome Z = {zlen}"


def test_mic_dome_screw_holes():
    """4 screw clearance holes should produce extra faces in the lip."""
    part = build_mic_dome()
    assert len(part.val().Faces()) > 8, "dome should have lip cutouts"
```

- [ ] **Step 5.2: Run tests to verify failures**

```bash
pytest test_enclosure.py -v
```

Expected: 3 new failures with `ModuleNotFoundError: No module named 'mic_dome'`.

- [ ] **Step 5.3: Implement mic_dome.py**

Write `hardware/enclosure/mic_dome.py`:

```python
"""Mic dome cap. Sits on top of the compute base, screw-clamped via 4 M3.

The dome is a cylindrical cap (DOME_OD diameter, DOME_HEIGHT tall) with:
- Solid outer wall, DOME_WALL thick
- Solid top region except for a thin-walled (DOME_DIFFUSER_THICKNESS) annulus
  over the XVF3800 LED ring for light diffusion
- A horizontal lip overhanging the base by ~9mm, with 4 counter-bored screw holes
"""

import math

import cadquery as cq

import params as p


def _solid_cylinder():
    """Outer cap blank: a cylinder with the bottom open."""
    cap = (
        cq.Workplane("XY")
        .circle(p.DOME_OD / 2)
        .extrude(p.DOME_HEIGHT)
    )
    # Hollow the inside, leaving DOME_TOP_THICKNESS of top wall
    inside_h = p.DOME_HEIGHT - p.DOME_TOP_THICKNESS
    inside = (
        cq.Workplane("XY")
        .circle((p.DOME_OD / 2) - p.DOME_WALL)
        .extrude(inside_h)
    )
    cap = cap.cut(inside)
    return cap


def _thin_diffuser_annulus(cap):
    """Cut the top down to DOME_DIFFUSER_THICKNESS in a ring over the LED ring."""
    thinning_depth = p.DOME_TOP_THICKNESS - p.DOME_DIFFUSER_THICKNESS
    if thinning_depth <= 0:
        return cap
    # Subtract a ring from the inside of the top wall
    outer = (
        cq.Workplane("XY")
        .workplane(offset=p.DOME_HEIGHT - p.DOME_TOP_THICKNESS)
        .circle(p.XVF_LED_RING_OUTER_R)
        .circle(p.XVF_LED_RING_INNER_R)
        .extrude(thinning_depth + 0.01)
    )
    return cap.cut(outer)


def _screw_holes(cap):
    """4 counter-bored through-holes around the lip for M3 flatheads."""
    pcd_r = p.DOME_SCREW_PCD / 2
    for i in range(p.DOME_SCREW_COUNT):
        angle = i * (2 * math.pi / p.DOME_SCREW_COUNT) + math.pi / 4
        x = pcd_r * math.cos(angle)
        y = pcd_r * math.sin(angle)
        # Through-hole
        through = (
            cq.Workplane("XY")
            .center(x, y)
            .circle((p.DOME_SCREW_D + 0.4) / 2)
            .extrude(p.DOME_HEIGHT + 0.1)
        )
        cap = cap.cut(through)
        # Countersink on top (flathead M3: ~6mm head OD, 1.8mm deep)
        csk = (
            cq.Workplane("XY")
            .workplane(offset=p.DOME_HEIGHT - 1.8)
            .center(x, y)
            .circle(3.0)
            .extrude(1.9)
        )
        cap = cap.cut(csk)
    return cap


def build_mic_dome():
    cap = _solid_cylinder()
    cap = _thin_diffuser_annulus(cap)
    cap = _screw_holes(cap)
    return cap


if __name__ == "__main__":
    part = build_mic_dome()
    cq.exporters.export(part, "output/mic_dome.step")
    cq.exporters.export(part, "output/mic_dome.stl")
    print("Exported output/mic_dome.{step,stl}")
```

- [ ] **Step 5.4: Run tests, verify pass**

```bash
pytest test_enclosure.py -v
```

Expected: 9 tests pass (6 from previous + 3 dome tests).

- [ ] **Step 5.5: Render and visually inspect**

```bash
python mic_dome.py
```

Open `output/mic_dome.step`. Verify:
- Ø113mm cylindrical cap
- Open bottom
- 4 countersunk holes around lip
- Visible thinning ring in the top where the LED diffuser will sit

- [ ] **Step 5.6: Commit**

```bash
git add hardware/enclosure/mic_dome.py hardware/enclosure/test_enclosure.py
git commit -m "feat(enclosure): mic dome cap with LED diffuser ring

Ø113mm × 18mm dome with hollowed interior, thin-wall LED diffuser
annulus over the XVF3800 LED ring, and 4 countersunk M3 holes
around the lip for dome→base attachment."
```

---

## Task 6: Plunger

**Files:**
- Create: `hardware/enclosure/plunger.py`
- Modify: `hardware/enclosure/test_enclosure.py`

- [ ] **Step 6.1: Add failing plunger tests**

Append to `test_enclosure.py`:

```python
from plunger import build_plunger


def test_plunger_is_valid():
    part = build_plunger()
    assert part.val().isValid()


def test_plunger_bounding_box():
    part = build_plunger()
    xlen, ylen, zlen = _bbox(part)
    # Largest XY dim = head diameter; Z = stem + head thickness
    assert 5.5 <= xlen <= 6.5
    assert 5.5 <= ylen <= 6.5
    expected_z = p.PLUNGER_STEM_LEN + p.PLUNGER_HEAD_T
    assert (expected_z - 0.5) <= zlen <= (expected_z + 0.5)
```

(Add `import params as p` at top of test file if not already imported.)

- [ ] **Step 6.2: Run tests, verify failures**

```bash
pytest test_enclosure.py -v
```

Expected: 2 new failures with `ModuleNotFoundError: No module named 'plunger'`.

- [ ] **Step 6.3: Implement plunger.py**

Write `hardware/enclosure/plunger.py`:

```python
"""Power-button plunger: small printed part that passes through the front face
of the compute base and presses the Pi 5 onboard tactile power button."""

import cadquery as cq

import params as p


def build_plunger():
    head = (
        cq.Workplane("XY")
        .circle(p.PLUNGER_HEAD_D / 2)
        .extrude(p.PLUNGER_HEAD_T)
    )
    stem = (
        cq.Workplane("XY")
        .workplane(offset=p.PLUNGER_HEAD_T)
        .circle(p.PLUNGER_STEM_D / 2)
        .extrude(p.PLUNGER_STEM_LEN)
    )
    return head.union(stem)


if __name__ == "__main__":
    part = build_plunger()
    cq.exporters.export(part, "output/plunger.step")
    cq.exporters.export(part, "output/plunger.stl")
    print("Exported output/plunger.{step,stl}")
```

- [ ] **Step 6.4: Run all tests, verify pass**

```bash
pytest test_enclosure.py -v
```

Expected: 11 tests pass.

- [ ] **Step 6.5: Commit**

```bash
git add hardware/enclosure/plunger.py hardware/enclosure/test_enclosure.py
git commit -m "feat(enclosure): power-button plunger part

Ø6mm head + Ø4mm stem captive plunger for the Pi 5 onboard power
button. Stem length parameterized for v2 tuning."
```

---

## Task 7: Build script and final assembly check

**Files:**
- Create: `hardware/enclosure/build.py`
- Modify: `hardware/enclosure/test_enclosure.py`

- [ ] **Step 7.1: Add failing test for build script outputs**

Append to `test_enclosure.py`:

```python
import os
import subprocess


def test_build_script_emits_all_six_files(tmp_path, monkeypatch):
    """Running build.py should write 3 .step + 3 .stl into output/."""
    here = os.path.dirname(os.path.abspath(__file__))
    monkeypatch.chdir(here)
    # Clean previous outputs
    for fname in ("compute_base.step", "compute_base.stl",
                  "mic_dome.step", "mic_dome.stl",
                  "plunger.step", "plunger.stl"):
        fpath = os.path.join(here, "output", fname)
        if os.path.exists(fpath):
            os.remove(fpath)

    result = subprocess.run(["python", "build.py"], capture_output=True, text=True)
    assert result.returncode == 0, f"build.py failed: {result.stderr}"

    for fname in ("compute_base.step", "compute_base.stl",
                  "mic_dome.step", "mic_dome.stl",
                  "plunger.step", "plunger.stl"):
        fpath = os.path.join(here, "output", fname)
        assert os.path.exists(fpath), f"missing {fname}"
        assert os.path.getsize(fpath) > 1000, f"{fname} suspiciously small"
```

- [ ] **Step 7.2: Run test to verify failure**

```bash
pytest test_enclosure.py::test_build_script_emits_all_six_files -v
```

Expected: FAIL — `build.py` doesn't exist yet.

- [ ] **Step 7.3: Write build.py**

Write `hardware/enclosure/build.py`:

```python
"""Render all three enclosure parts and export STL + STEP to output/.

Usage:
    python build.py
"""

import os

import cadquery as cq

from compute_base import build_compute_base
from mic_dome import build_mic_dome
from plunger import build_plunger


OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
PARTS = {
    "compute_base": build_compute_base,
    "mic_dome": build_mic_dome,
    "plunger": build_plunger,
}


def main():
    os.makedirs(OUT, exist_ok=True)
    for name, builder in PARTS.items():
        part = builder()
        step_path = os.path.join(OUT, f"{name}.step")
        stl_path = os.path.join(OUT, f"{name}.stl")
        cq.exporters.export(part, step_path)
        cq.exporters.export(part, stl_path)
        print(f"  rendered {name}: {step_path}, {stl_path}")
    print("Done.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 7.4: Run all tests, verify pass**

```bash
pytest test_enclosure.py -v
```

Expected: 12 tests pass.

- [ ] **Step 7.5: Final render and visual review**

```bash
python build.py
ls -la output/
```

Open all three STEP files in a viewer (FreeCAD recommended — it imports STEP natively). Confirm:
- Compute base: all 4 corners present, port cutouts on expected faces, vents visible, divider with mic ports + cable hole + 4 dome bosses + 4 XVF bosses
- Mic dome: clean cylindrical cap with countersunk screws and diffuser ring
- Plunger: small mushroom shape

Optional: do an assembly preview in FreeCAD by importing all three and stacking them in the right Z positions to confirm there are no interferences.

- [ ] **Step 7.6: Commit**

```bash
git add hardware/enclosure/build.py hardware/enclosure/test_enclosure.py
git commit -m "feat(enclosure): build script renders all parts to output/

Single 'python build.py' call writes STEP and STL exports for
compute_base, mic_dome, and plunger. Integration test in
test_enclosure.py verifies all 6 output files are produced."
```

---

## Task 8: Slice the STLs and dry-run the print plan

This task has no code; it is the print-prep checklist. Mark each step done as you complete it.

- [ ] **Step 8.1: Open `output/compute_base.stl` in your slicer (PrusaSlicer / Cura / Bambu Studio).**
  - Orient: open side up (top of compute box facing up)
  - Layer height: 0.2mm
  - Walls: 3–4 perimeters
  - Infill: 20–25%
  - Supports: only on the dome screw bosses if they overhang (they should be near-vertical and not need supports)
  - Expected print time: 4–6 hr

- [ ] **Step 8.2: Open `mic_dome.stl`.**
  - Orient: top of dome down (build-plate side becomes the diffuser top — best surface finish)
  - Supports: needed for the dome lip overhang
  - Use white or natural PLA (translucent) so the LED ring shows through
  - Expected print time: 1.5–2.5 hr

- [ ] **Step 8.3: Open `plunger.stl`.**
  - Orient: stem up, head down
  - Tiny part, no supports needed
  - Expected print time: <10 min

- [ ] **Step 8.4: Print and assemble v0.**

- [ ] **Step 8.5: Fit-check XVF3800 against mic chamber.**
  - Confirm the board sits on the 4 standoff bosses and aligns to the screw holes
  - Confirm the USB-C connector aligns near the cable pass-through hole
  - Confirm the mic openings align with the divider acoustic ports
  - If any of these are off, measure the actual board and update the XVF3800 placeholders in `params.py`, re-run `python build.py`, reprint the divider/dome.

---

## Self-review notes

**Spec coverage check:**

- § 2 (hardware) — Pi/HAT/Hailo stack not printed; only their footprint constraints used → Tasks 2, 4 ✓
- § 3 (silhouette) — overall shell and dome dimensions → Tasks 2, 5 ✓
- § 4 (stack-up) — heights drive params → Task 1, 2 ✓
- § 5 (cutouts) — all four faces + plunger → Task 3 ✓
- § 6 (plunger) → Task 6 ✓
- § 7 (dome attachment) → Tasks 4, 5 ✓
- § 8 (XVF3800 mount, mic ports, cable) → Task 4 ✓
- § 9 (cable routing) — pass-through only; cable itself is sourced hardware → Task 4 ✓
- § 10 (thermal vents) → Task 4 ✓
- § 11 (acoustic) — mic ports + cable grommet hole; foam gasket is sourced hardware → Task 4 ✓
- § 12 (material/tolerance) — clearances in params.py → Task 1 ✓
- § 13 (BOM) — printed parts only; hardware is sourced separately → all tasks ✓
- § 14 (output format) — CadQuery → Tasks 1–7 ✓
- § 15 (open items / parameterization) → noted in README and Task 8.5 fit check ✓
- § 16 (out of scope) — wall mount, battery, speaker, display → respected ✓

**Type consistency check:**

- All builder functions named `build_<part>()` consistently across modules ✓
- All parameters live in `params.py` with `import params as p` everywhere ✓

**Placeholder scan:**

- XVF3800 dimensions are documented placeholders (per spec § 15), with initial values that produce a printable v0; Task 8.5 catches the fit-check loop ✓
- No "TBD" or "implement later" strings in tasks ✓
