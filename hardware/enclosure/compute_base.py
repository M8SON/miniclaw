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
