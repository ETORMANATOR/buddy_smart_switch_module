# BSSM pin reference

Which GPIO does what, on each board this firmware runs on. Both `main.py`
files (`device/esp32/main.py`, `device/esp8266/main.py`) carry this same
information in their own header comments — this file exists so it can be
read without opening either one, and so wiring a new board doesn't mean
re-deriving it from scratch.

## ESP32-C3

**Do not use:** GPIO 11-17 (wired to the flash chip), 18/19 (USB), 20/21
(the serial UART - TX/RX, needed for flashing and for anything printed to
be visible at all), and avoid 2, 8, 9 (strapping pins - they change how the
chip boots depending on their state at power-on). That leaves GPIO 0, 1, 3,
4, 5, 6, 7, 10 - eight pins, one reset button, one LED, six relays.

| GPIO | Role |
|---|---|
| 0 | Reset button, to GND. Hold 3s for factory reset. |
| 1, 3, 4, 5, 6, 7 | Relays, switch1..switch6 in that order. |
| 10 | Status LED. |

## ESP8266

**Do not use:** GPIO 6-11 (wired to the flash chip on almost every module
sold), 1/3 (the USB-serial UART - TX/RX). GPIO 0, 2 and 15 are read at boot
to choose which mode the chip starts in, so anything wired to them has to
already be in the right state at power-on, not just once software is
running - **do not power the board on with the reset button held down.**

| GPIO | Role |
|---|---|
| 2 | Reset button, to GND. Must read HIGH at boot (an internal pull-up holds it there until actually pressed). Hold 3s for factory reset. |
| 4, 5, 12, 13, 14 | Relays, switch1..switch5 in that order - one fewer than the ESP32-C3 build, since GPIO 0 is left spare here rather than pressed into service. |
| 15 | Status LED. Must read LOW at boot - wire the LED (and any series resistor) so nothing holds this pin high before `main.py` configures it as an output. |

Five relays, not six: this chip has one less usable, boot-safe GPIO than
the ESP32-C3 once the flash and UART pins are excluded.

## The Pi's own pins

See `backend/PI_PROVISIONING.md`'s "Pin reference" section — the Pi side
of this project (the physical factory-reset button, and appliance GPIO)
lives in that repo, not this one.
