# One firmware, many modules

`main.py` is the same file on every smart switch module. Nothing in it is
edited per device, and nothing in it derives, cleans up or invents an
identity: an id typed into shared code is the same id on every module flashed
with it, which is the one thing an id must never be.

Everything that differs between modules lives in **`config.json`** on that
module's own flash.

```
mpremote connect COM4 fs cp main.py :main.py          # unchanged, always
mpremote connect COM4 fs cp my_config.json :config.json
mpremote connect COM4 reset
```

## What goes in config.json

It is **merged over the firmware's defaults**, so it only has to carry what
you are actually setting. This is a complete, working config:

```json
{"smart_switch_id": "SW-000412", "name": "living_room"}
```

| key | what it is |
|---|---|
| `device_type` | what kind of hardware this is — `esp32`. Reported on every check-in so a fleet with more than one kind of device in it never has to guess. |
| `smart_switch_id` | the id on your sticker and in your asset register. Yours to choose; the Pi shows it and matches on it. |
| `name` | the module's own name — what it answers to. No spaces; use `_`. |
| `switch_count` | how many relays, 1 to 6, in GPIO order (1, 3, 4, 5, 6, 7). They are called `switch1`..`switchN`; what a person calls them is a label on the Pi. |
| `wifi_ssid` / `wifi_pass` | the network to join. Adoption fills these in with the Pi's module network — see below. |
| `pi_url` | where to report in, e.g. `http://192.168.1.242:8000`. |
| `device_key` | the module's own key. Written by the Pi when it adopts the module. |
| `firmware_url` | where `POST /update` fetches from when it is not told otherwise. |
| `states` | written by the firmware, not by you — what each switch was last set to, so a power cut does not turn the house off. |

There is no `provisioned` flag and no `location`. A module is provisioned when
it has a `wifi_ssid` to join — a stored flag could say no while the network
said yes, and then a module sits raising its own access point next to the
network it was given. Where a module is, is a note the Pi keeps: the module
has never had a use for it.

**Both `smart_switch_id` and `name` are required** before a module will report
to the Pi. A module with neither says so at boot and waits: it has no identity
to report, and nothing on it will make one up. Two unconfigured modules
reporting as the same nameless thing would overwrite each other, which is the
failure this refuses to have.

## Two networks, taking turns

The Pi has one radio, and one radio holds up one access point — so its two
networks take turns rather than running together:

* **`Buddy-Switches`** is where adopted modules live. Its password is made on
  the Pi, kept there, and handed to a module when it is adopted. This is up
  almost always.
* **`Buddy-Modules`** is the setup network, and its password is the setup key
  compiled into the firmware. It exists only while somebody opens a setup
  window from the portal — ten minutes, and it closes itself.

That is what makes the setup key worth having: it opens a network that is
almost never there, and it is not the key to the network the modules live on.
An unclaimed module knows only that key, so the most it can reach is a window
somebody deliberately opened.

The cost of one radio, said plainly: while a window is open, adopted modules
cannot reach the Pi. They keep their switch states and keep looking, and they
come back on their own when it closes — but a voice command during those
minutes will not land.

## Names: the module's, and the Pi's

The module's name is set here and **nothing else changes it**; its switches
are called `switch1`..`switchN` and nothing changes those either. The module
has no rename endpoint at all - there is one place identity comes from, and
it is this file. Renaming in the portal sets a *label* on the Pi — what you call it,
what you say to it, what the portal shows — and the module never hears about
it. Both names work when addressing a module, so automation written against
the module's own name keeps working after somebody relabels it.

Adoption from the portal hands a module a network, an address to report to,
and a key of its own. It does not hand it a name.

## Updating the firmware

`POST /update` on the module fetches new firmware and restarts onto it:

```json
{"url": "https://raw.githubusercontent.com/ETORMANATOR/buddy_smart_switch_module/main/device/esp32/main.py",
 "sha256": "<hex digest of that file>"}
```

That is this repository, which keeps a folder per kind of hardware —
`device/esp32/`, `device/esp8266/` — and the module builds that path from its
own `device_type`. So `POST /update` with an empty body fetches the build for
what it actually is, and an ESP8266 can never be handed an ESP32 one.

The normal route is the portal's **update firmware** button, which does this
for you: the Pi fetches from GitHub, checks the file is firmware rather than a
404 page, and tells the module to download it **from the Pi** with the sha256
of what the Pi actually got. The module refuses anything whose hash differs,
refuses anything that does not look like firmware, and keeps the old copy as
`main.bak`.

The Pi builds the same path. Point it at a fork or a branch with
`BUDDY_FIRMWARE_BASE`, which is the `device/` folder rather than one file:

```
sudo systemctl edit buddy-backend
# Environment=BUDDY_FIRMWARE_BASE=https://raw.githubusercontent.com/<owner>/<repo>/<branch>/device
```

It must be a **raw** URL. The `github.com/...` address serves a web page about
the file; written over `main.py` that is a module that does not boot, and the
mistake looks exactly like a correct URL.

A Pi that cannot reach GitHub falls back to the copy it last fetched, and says
so — the result carries `"stale": true` — so a house with no internet can
still update its modules, and nobody reads that as "updated to the latest".

Why the round trip: this chip can do TLS but has no trust store to check a
certificate against, so "it came over https" means little on the module. The
Pi has one. So the Pi is what trusts GitHub, and the module trusts only a hash
from the Pi it already shares a key with.

## A factory reset clears it

Holding GPIO 0 to ground for three seconds deletes `config.json` — id, name,
switches and all. The module comes back with no identity, waiting to be given
one.
