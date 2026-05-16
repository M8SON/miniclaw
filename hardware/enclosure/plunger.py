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
