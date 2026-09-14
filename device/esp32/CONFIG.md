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
| `pi_url` | where to report in, e.g. `http://192.168.1.60:8000`. |
| `device_key` | the module's own key. Written by the Pi when it adopts the module. |
| `firmware_url` | where `POST /update` fetches from when it is not told otherwise. |
| `states` | written by the firmware, not by you — what each switch was last set to, so a power cut does not turn the house off. |

There is no `provisioned` flag and no `location`. A module is provisioned when
it has a `wifi_ssid` to join — a stored flag could say no while the network
said yes, and then a module sits looking for the Pi's setup network next to
the one it was actually given. Where a module is, is a note the Pi keeps: the
module has never had a use for it.

**Both `smart_switch_id` and `name` are required** before a module will report
to the Pi. A module with neither says so at boot and waits: it has no identity
to report, and nothing on it will make one up. Two unconfigured modules
reporting as the same nameless thing would overwrite each other, which is the
failure this refuses to have.

## One network: Buddy-Modules

The Pi keeps one WiFi network up, always: **`Buddy-Modules`**, password
compiled into the firmware (the setup key). An unclaimed board joins it on
its own — the Pi is the gateway on its own network, so there is nothing for
either side to discover — and announces itself. The portal's **Find new
module** tab lists it; enter the setup key there and it is adopted, handed
your house WiFi, and moves there for good.

There is no handover and no window to catch: the setup network is simply
always there, and an adopted module is on your house WiFi, not on anything
the Pi hosts. That is also why the setup key is not treated as secret in this
repository — it is compiled into published firmware, so anyone who reads this
repo can join `Buddy-Modules`. It carries no internet and routes nowhere, and
adoption still needs the key; it is a door that is always there rather than
one opened for ten minutes at a time.

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

The normal route is the portal's **Update** button on a Connected BSSM card,
which does this for you: the Pi fetches from GitHub, checks the file is
firmware rather than a 404 page, and tells the module to download it **from
the Pi** with the sha256 of what the Pi actually got. The module refuses
anything whose hash differs, refuses anything that does not look like
firmware, and keeps the old copy as `main.bak`.

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

## A factory reset — over the portal, or the button

The portal's **Remove** button on a Connected BSSM card, and holding GPIO 0
to ground for three seconds on the board itself, do the same thing: the
network and the device key go, so the board comes back up unclaimed and
ready to be adopted again. Its `smart_switch_id`, its `name` and how many
relays it has all **stay** — a reset is about who owns the module, not what
it is. See `KEPT_THROUGH_RESET` in `main.py`.

Every relay is switched **off**, and that is not kept either. Removing a
module is meant to leave it safe to find powered on with nobody watching —
on a shelf, mid reassignment to a different room — and whatever it happened
to be switching at the time is not a safe default for that.

Removing it from the portal also asks the module itself to reset, over the
network, with its current key — best-effort, since that request never gets a
reply (the module resets before one goes out). A module that is genuinely
unreachable at the time cannot be reset remotely; it keeps its old
configuration until somebody plugs it in and holds the button.
