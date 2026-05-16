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


def test_compute_base_has_port_cutouts():
    """After port cutouts, the part should have substantially more faces
    than a plain shell (each cutout adds 5-6 faces)."""
    part = build_compute_base()
    # Shell-only had ~18 faces; with 7+ cutouts expect >40
    n_faces = len(part.val().Faces())
    assert n_faces > 40, f"expected >40 faces after port cutouts, got {n_faces}"
