# Kaizen Voice-Assistant Enclosure — Design Spec

**Date:** 2026-05-16
**Status:** Design draft (awaiting user review before code)
**Output target:** Parametric CadQuery (`.py`) → STL + STEP exports for FDM prototype and MJF production

---

## 1. Goal

A single-piece desktop voice-assistant enclosure for Kaizen on Raspberry Pi 5. Acoustically isolates the ReSpeaker XVF3800 microphone array from the Pi's active cooler and the AI HAT+ thermal load. Looks like a finished product, not a hobby project.

**Form factor:** desktop puck — always-on, plugged in, sits on a shelf.

## 2. Hardware to enclose

| Item | Dimensions | Notes |
|---|---|---|
| Raspberry Pi 5, 8GB | 85 × 56 × 17mm (with cooler) | Standard mounting: M2.5 × 4, 58 × 49mm pattern |
| Pi 5 Active Cooler (official) | sits on CPU, ~14mm tall above PCB | Auto-controlled by Pi firmware |
| Raspberry Pi AI HAT+ (with Hailo) | 65 × 56.5 × 1.6mm PCB | Stacks above Pi via 16mm M2.5 standoffs (kit-supplied), connects to Pi PCIe via FFC |
| Hailo M.2 module on AI HAT+ | M.2 2230, ~5mm above HAT PCB | Pre-installed before assembly |
| ReSpeaker XVF3800 USB 4-Mic Array (circular) | Ø ~106mm × ~9mm tall | USB-C connection, RGB LED ring, 4 mics around edge. Bare board (no Seeed case) |
| USB-A→USB-C cable, ~10cm | low-profile / right-angle preferred | Internal: Pi USB-A → XVF3800 USB-C |

## 3. Form & silhouette

Saucer-on-pedestal:

```
      ┌──────────────────────────┐    Ø113mm mic dome
      │ ░ diffuser ring (LEDs) ░ │    (overhangs base by ~9mm/side)
      │  • • mic openings • •    │
      └──────────────────────────┘
            ┌─────────────────┐
            │                 │      95 × 95mm compute box
            │  AI HAT + Pi 5  │      (square footprint, top-down)
            │                 │
            └─┬─────────────┬─┘
              ▼ 4× feet ▼
```

- **Compute base:** 95 × 95 × ~52mm tall. Single piece. Contains the Pi + AI HAT+ stack in its lower chamber, and has an **integrated mic chamber floor** as a horizontal divider near the top — that floor is the mounting surface for the XVF3800.
- **Mic dome:** Ø113mm × ~18mm tall, separate cap (no floor — just an outer wall + diffuser top). Lifts off the compute base to expose the XVF3800.
- **Parting line:** horizontal, along the top rim of the compute base — hidden by the saucer overhang.
- **Total height:** ~74mm (52mm base + 18mm dome + 4mm feet).

The mic disc is wider than the compute box — overhang gives clear acoustic line-of-sight for the 4 mics and visually breaks up the form. The integrated divider keeps the dome simple (no internal floor) and makes the compute chamber acoustically sealed from the mic chamber by a printed wall, not a screw-clamped interface.

## 4. Stack-up (interior, bottom-up)

| Layer | Height (mm) | Cumulative |
|---|---|---|
| Floor + foot clearance | 4 | 4 |
| Pi mounting standoff (M2.5 × 6mm) | 6 | 10 |
| Pi 5 PCB | 1.6 | 11.6 |
| Pi 5 components + active cooler | 15 | 26.6 |
| (AI HAT+ standoffs straddle cooler — kit's 16mm) | — | — |
| AI HAT+ PCB top surface | — | ~19.2 |
| Hailo M.2 module | 5 | 24.2 |
| Air gap above stack | ~5 | 29 |
| Integrated mic chamber floor (part of compute base, 1.5mm) | 1.5 | 30.5 |
| XVF3800 standoffs + PCB | 5 + 1.6 | 37.1 |
| XVF3800 components + air | ~8 | 45 |
| Dome top (incl. 0.6mm diffuser) | ~3 | 48 |

Compute base exterior height: ~52mm (2.4mm floor + 29mm compute chamber + 1.5mm divider + ~19mm mic chamber wall = up to dome lip). Dome exterior: ~18mm (cap only, sits on top of base). Total exterior: ~74mm including 4mm feet.

## 5. Cutouts & access (compute box)

Pi oriented with GPIO header on **left** long face, ports on **right**, USB-C/HDMI on **front**, microSD on **back**.

| Face | Cutout | Notes |
|---|---|---|
| Right (long) | One rect ~53 × 17mm | 2× USB-A 3.0, 2× USB-A 2.0, GbE in single opening |
| Front (short) | 2× micro-HDMI (7 × 4mm), USB-C (10 × 4mm), 3.5mm jack (Ø6.5) | Individual per Pi footprint |
| Back (short) | Slot ~14 × 2mm | microSD, flush with floor (slot is on Pi underside) |
| Left (long, GPIO) | No port cutouts | Used for exhaust vent slots instead |
| Front | Ø6mm plunger hole | Power button (see § 6) |

All port cutouts apply +0.5mm clearance on each side.

## 6. Power button (printed plunger)

- Pi 5's onboard tactile power button is on the topside of the PCB near USB-C
- Plunger: 6mm head, 4mm Ø stem, length **parameterized** (initial guess 8mm reach)
- Captive design — head wider than through-hole, can't pull out
- Tunes in v2 print after fit check

## 7. Mic dome → compute base attachment

- 4× printed M3 screw bosses integrated into the **top rim of the compute base** (the mic chamber wall), positioned just inside the dome's outer wall path
- Mic dome has matching counter-bored holes (M3 flathead countersink) through its outer-wall flange
- 4× M3 × 12mm flathead screws clamp dome to base, going down through the dome lip into the base bosses
- Optional 3mm foam gasket strip on the base rim under the dome lip for acoustic seal
- Screw heads sit recessed in the underside of the dome lip, hidden from normal viewing angles
- Dome can be removed without disturbing the compute stack — services the XVF3800 only

## 8. XVF3800 mounting (parameterized — locked after fit test)

These dimensions are **placeholders** until measured directly or extracted from Seeed STP via deeper geometry parsing. Initial values let us print v0 and measure the real ones.

| Parameter | Initial value | Source |
|---|---|---|
| `xvf_diameter` | 106mm | Bbox of Seeed STP (vertex-filtered) |
| `xvf_thickness` | 1.6mm | Standard PCB |
| `xvf_mount_count` | 4 | Assumed standard |
| `xvf_mount_pcd` | 80mm | Initial guess; revise after fit |
| `xvf_mount_hole_d` | M2.5 (2.7mm clearance) | Standard |
| `xvf_usbc_pos_angle` | 0° (radial, edge) | Revise after fit |
| `xvf_led_ring_outer_r` | 45mm | Estimate from disc diameter |
| `xvf_led_ring_inner_r` | 38mm | Estimate |
| `xvf_mic_radius` | 48mm | Mics near outer edge |
| `xvf_mic_angles` | [0°, 90°, 180°, 270°] | Standard symmetric layout |

Mic chamber floor has:
- 4× M2.5 receiver bosses for board mounting (5mm tall, board sits 5mm above floor)
- 1× Ø10mm USB cable pass-through (positioned at `xvf_usbc_pos_angle`)
- 4× Ø3mm mic acoustic ports (aligned with `xvf_mic_angles` on `xvf_mic_radius`)

Dome top has:
- Thin-wall (0.6mm) diffuser annulus over the LED ring
- Solid 2mm wall everywhere else

## 9. USB cable routing

- USB-A end → upper-rear Pi USB-A 2.0 port (preserves USB 3.0 for external use)
- Cable runs up the GPIO-side dead space corner of the compute box
- Passes through grommeted Ø10mm hole in mic chamber floor
- USB-C end → XVF3800 board
- ~10cm total length needed with bend allowance

## 10. Thermal design

```
  ┌─ Exhaust: slot vents on rear arc of dome shoulder (~60° hidden)
  │  ▲ warm air rises
  │  │ compute chamber (AI HAT + Pi 5 + active cooler)
  │  │ active cooler exhausts horizontally
  │  │ side slot vents on GPIO face give it a path
  │  ▼ cool intake
  └─ Bottom plate: hex-grid intake; 4× 4mm feet lift case
```

- **Bottom intake:** slot grid (1 × 12mm slots, 3mm pitch), full footprint minus screw boss zones
- **Side exhaust:** 7× vertical slots, 30mm × 1mm, centered on GPIO face
- **Dome shoulder exhaust:** arc of slots on rear 60° of dome lip — hidden by overhang
- **Active cooler control:** stock Pi firmware (auto-PWM)
- Expected interior temp under inference load: 35–45°C, well below PLA softening (60°C)

## 11. Acoustic design

- Mic chamber floor is solid (1.5mm) except for: 4× mounting bosses, 1× cable pass-through, 4× mic acoustic ports
- USB pass-through gets a foam grommet to block compute-chamber noise
- Optional foam strip around dome lip seals dome-to-base joint
- Mic acoustic ports are direct openings under each XVF3800 mic — short, straight path, no resonant chambers

## 12. Material & manufacturing

User handles material/vendor choice. Design tolerances target:
- **FDM** (PLA/PETG): clearances +0.4mm, press-fit -0.2mm interference
- **MJF nylon** (e.g. via JLC/PCBWay): subtract 0.1mm from all clearances

Recommended for prototype: PLA via FDM, ~7–9 hr print time, ~$2–3 material.
Recommended for production-feel: MJF nylon, $50–80 estimated.

## 13. BOM

**Printed parts:**
1. Compute base (95 × 95 × 58mm, open-top shell)
2. Mic dome (Ø113 × 17mm, separate)
3. Power-button plunger

**Hardware (sourced separately):**
- 4× M2.5 × 6mm brass standoff (Pi → base floor, press-fit)
- 4× M2.5 × 6mm screw (through base into Pi)
- 4× M3 × 12mm flathead screw (dome → base)
- 3–4× M2.5 standoff for XVF3800 (count/size parameterized)
- 1× USB-A male → USB-C male cable, ~10cm
- 4× rubber adhesive feet, Ø8 × 2mm
- Optional: foam acoustic gasket strip

Already on hand:
- Pi 5, AI HAT+ kit (with cooler, 16mm standoffs, FFC), XVF3800 board

## 14. CAD output format

**CadQuery** (Python parametric CAD). Single `.py` file with all parameters at the top, exports:
- `compute_base.stl` + `compute_base.step`
- `mic_dome.stl` + `mic_dome.step`
- `plunger.stl` + `plunger.step`

Rationale: matches Python-primary stack, real CAD kernel (clean fillets/chamfers), parametric file makes the placeholder XVF3800 dimensions trivially editable after fit-check.

## 15. Open items (resolved in v2 after first print)

- XVF3800 exact mounting hole pattern, USB-C position, LED ring radii, mic positions (currently parameterized)
- Pi 5 power-button plunger stem length (currently 8mm — tune by feel)
- Whether dome→base clamp needs the foam gasket or printed lip is sufficient

## 16. Out of scope

- Wall mounting
- Battery / portable operation
- Display or front-panel LEDs (XVF3800 ring is the only light)
- Speaker — assumed external (Bluetooth or USB)
- Manufacturer selection
