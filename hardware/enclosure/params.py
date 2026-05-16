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
DOME_SCREW_BOSS_HEIGHT = 4.0           # legacy, retained for short XVF mount bosses if needed later
DOME_SCREW_BOSS_HEIGHT_FULL = BASE_Z - (DIVIDER_Z + DIVIDER_THICKNESS)  # rises from divider to top rim (~15.5mm)

# --- Feet ---
FOOT_HEIGHT = 4.0
FOOT_OD = 8.0
FOOT_INSET = 8.0                  # from each corner

# --- XVF3800 PLACEHOLDERS (revise after fit-check) ---
XVF_OD = 106.0
XVF_PCB_T = 1.6
XVF_STANDOFF_HEIGHT = 5.0
XVF_MOUNT_COUNT = 4
XVF_MOUNT_PCD = DOME_SCREW_PCD  # combined dome+XVF mount for v0 (placeholder)
XVF_MOUNT_HOLE_D = 2.5
XVF_USBC_ANGLE_DEG = 0.0          # 0 = +X radial direction
XVF_LED_RING_OUTER_R = 45.0
XVF_LED_RING_INNER_R = 38.0
XVF_MIC_R = 48.0
XVF_MIC_ANGLES_DEG = [45.0, 135.0, 225.0, 315.0]  # placeholder; 45° offsets fit within divider at R=48
XVF_MIC_PORT_D = 3.0

# --- Cable pass-through ---
CABLE_GROMMET_D = 10.0
CABLE_GROMMET_X = 35.0            # offset from divider center (in dead-space corner)
CABLE_GROMMET_Y = -35.0
