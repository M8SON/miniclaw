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


def _port_cutout_right(base):
    """Single rect cutout for 2x USB-A 3.0 + 2x USB-A 2.0 + GbE on the right face."""
    w = p.PORTS_RIGHT_W + 2 * p.PORT_CLEARANCE
    h = p.PORTS_RIGHT_H + 2 * p.PORT_CLEARANCE
    z = p.FLOOR + p.PI_STANDOFF_HEIGHT + 1.6 + 1.0  # PCB top + a bit
    cutout = (
        cq.Workplane("YZ")
        .workplane(offset=p.BASE_X / 2 - p.WALL)
        .center(p.PI_OFFSET_Y, z + h / 2)
        .rect(h, w)
        .extrude(p.WALL + 1.0)
    )
    return base.cut(cutout)


def _port_cutouts_front(base):
    """2x micro-HDMI, USB-C, 3.5mm audio jack on the +Y short face."""
    z_pcb_top = p.FLOOR + p.PI_STANDOFF_HEIGHT + 1.6
    z = z_pcb_top + 1.5  # connectors sit ~1.5mm above PCB

    # Positions along X relative to Pi center: HDMI0, HDMI1, USB-C, audio
    positions_w_h = [
        (-21.0, p.HDMI_W, p.HDMI_H),
        (-11.0, p.HDMI_W, p.HDMI_H),
        (+3.0, p.USBC_W, p.USBC_H),
        (+22.0, p.AUDIO_JACK_D, p.AUDIO_JACK_D),
    ]
    for offset_x, w, h in positions_w_h:
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
    x = p.PI_OFFSET_X + 12.0
    cutout = (
        cq.Workplane("XZ")
        .workplane(offset=p.BASE_Y / 2 - p.WALL - 1.0)
        .center(x, z)
        .circle(p.PLUNGER_HOLE_D / 2)
        .extrude(p.WALL + 2.0)
    )
    return base.cut(cutout)


def build_compute_base():
    base = _outer_shell()
    base = _pi_standoffs(base)
    base = _port_cutout_right(base)
    base = _port_cutouts_front(base)
    base = _microsd_cutout(base)
    base = _plunger_hole(base)
    return base


if __name__ == "__main__":
    part = build_compute_base()
    cq.exporters.export(part, "output/compute_base.step")
    cq.exporters.export(part, "output/compute_base.stl")
    print("Exported output/compute_base.{step,stl}")
