"""Pytest checks for enclosure parts. Tests validate that each solid
is manifold and has the expected bounding box per the spec."""

import pytest
from compute_base import build_compute_base
from mic_dome import build_mic_dome


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


def test_compute_base_has_port_cutouts():
    """After port cutouts, the part should have substantially more faces
    than a plain shell (each cutout adds 5-6 faces)."""
    part = build_compute_base()
    # Shell-only had ~18 faces; with 7+ cutouts expect >40
    n_faces = len(part.val().Faces())
    assert n_faces > 40, f"expected >40 faces after port cutouts, got {n_faces}"


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
