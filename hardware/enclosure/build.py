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
