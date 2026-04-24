---
name: homebridge
description: Control smart home devices via Homebridge Config UI X. Use to list devices,
  turn things on/off, adjust brightness, color, temperature, or fan speed.
metadata:
  miniclaw:
    requires:
      env:
      - HOMEBRIDGE_URL
      - HOMEBRIDGE_USERNAME
      - HOMEBRIDGE_PASSWORD
---
# Homebridge Control

Control HomeKit-compatible smart home devices through the Homebridge Config UI X REST API.

## When to use
Use this skill when the user wants to:
- Turn lights, switches, or fans on or off
- Adjust brightness, color, or color temperature of lights
- Set thermostat target temperature
- List what devices or rooms are available
- Check the current state of a device

## Inputs

```yaml
type: object
properties:
  action:
    type: string
    enum: [list, rooms, get, set]
    description: >
      list = show all devices (optionally filtered by room or type),
      rooms = show rooms and which devices are in each,
      get = get current state of a named device,
      set = change a characteristic on a named device
  device_name:
    type: string
    description: Name of the device to get or control (fuzzy matched, case-insensitive). Required for get and set.
  room:
    type: string
    description: Filter list results by room name. Optional for list action.
  device_type:
    type: string
    description: Filter list results by type (e.g. Lightbulb, Switch, Thermostat). Optional for list action.
  characteristic:
    type: string
    description: >
      The characteristic to change. Required for set action.
      Common values: On, Brightness, Hue, Saturation, ColorTemperature, TargetTemperature, RotationSpeed, Active
  value:
    type: string
    description: >
      The value to set. Required for set action.
      Examples: true/false for On, 0-100 for Brightness, 0-360 for Hue, 10-38 for TargetTemperature
required:
  - action
```

## How to respond
- For list/rooms: summarise the devices naturally ("You have 3 lights in the living room")
- For get: describe the current state ("The desk lamp is on at 60 percent brightness")
- For set: confirm what was done ("Done, I turned off the kitchen light")
- If a device name doesn't match anything, say so and suggest listing devices
- Keep responses conversational, no markdown
