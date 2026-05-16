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
        # Countersink on top (M3 flathead: ~6mm OD, 1.8mm deep)
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
