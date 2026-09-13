# Buddy Smart Switch Module

Firmware for the relay modules in a Buddy system: an ESP running MicroPython,
driving up to six mains switches, talked to by name from a Raspberry Pi.

```
device/
  esp32/          ESP32-C3 — main.py, config.example.json, CONFIG.md
  esp8266/        ESP8266 — main.py, config.example.json, CONFIG.md
```

One folder per kind of hardware, and the folder name is the `device_type` a
module reports. That is not filing for its own sake: the update endpoint
builds its URL from it, so a module fetches
`device/<its own device_type>/main.py` and an ESP8266 can never be handed an
ESP32 build.

## Flashing one

The firmware is the same file on every module of that type. Nothing in it is
edited per device.

```
mpremote connect COM4 fs cp device/esp32/main.py :main.py
mpremote connect COM4 fs cp my_config.json :config.json
mpremote connect COM4 reset
```

A complete, working `config.json` is two fields:

```json
{"smart_switch_id": "SW-000412", "name": "living_room"}
```

Both are required. A module with neither says so at boot and will not report
to the Pi — it has no identity, and nothing on it invents one. The full list
of settings is in [device/esp32/CONFIG.md](device/esp32/CONFIG.md).

## What a module does

* Joins the network it was given, or goes looking for the Pi's when it has
  none — and falls back to looking if the network it was given has gone.
* Reports itself every twenty seconds: its id, its name, its type, its
  firmware version, its address and what every relay is doing.
* Remembers what each switch was set to, so a power cut does not come back
  with the house dark.
* Answers `/<switch>/on`, `/<switch>/off`, `/status` and `/update`, with a
  key only the Pi has.

## Identity is set by hand

A module's id and name come from its `config.json` and from nowhere else.
Nothing derives them, cleans them up or substitutes a default, and there is
no rename endpoint: what is on the sticker, in the asset register and in the
portal is one string, because only one place decides it.

Renaming from the Pi's portal changes a *label* the Pi keeps. The module
never hears about it and keeps answering to its own name — so automation
written against that name survives somebody relabelling things.

## Updating over the air

`POST /update` fetches new firmware and restarts onto it:

```json
{"url": "https://raw.githubusercontent.com/ETORMANATOR/buddy_smart_switch_module/main/device/esp32/main.py",
 "sha256": "<hex digest of that file>"}
```

The normal route is the portal's **Update** button on a Connected BSSM card.
The Pi fetches the build for that module's type, checks it is firmware
rather than a 404 page, and tells the module to download it **from the Pi**
with the sha256 of what the Pi actually got. The module refuses anything
whose hash differs, refuses anything that does not look like firmware, and
keeps the previous
copy as `main.bak`.

The round trip is deliberate. These chips can do TLS but have no trust store
to check a certificate against, so "it came over https" means little on the
module. The Pi has one. So the Pi is what trusts GitHub, and the module
trusts only a hash from the Pi it already shares a key with.

## Adding another kind of hardware

Add `device/<type>/main.py`, have it report that `device_type`, and the
update endpoint finds it without anything else changing — `device/esp8266/`
is exactly this, added after `device/esp32/` with no change needed anywhere
else. The pin map belongs in the firmware: the ESP8266 build's usable relay
pins are 4, 5, 12, 13, 14 — the ESP32-C3's list would land on its flash and
strapping pins, which is a module that does not boot.

`device/esp8266/main.py` was ported from the ESP32-C3 build rather than
written fresh — the MicroPython WiFi and socket APIs it depends on are the
same on both chips — but has not yet been flashed to real ESP8266 hardware
the way the ESP32-C3 one has been exercised at length. Treat its own header
comment's RF notes as inherited assumptions, not measurements, until someone
chases a real join failure on this chip the way the ESP32-C3 section's was
chased.
