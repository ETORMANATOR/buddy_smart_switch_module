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
| `smart_switch_id` | the id on your sticker and in your asset register. Yours to choose; the Pi shows it and matches on it. |
| `name` | the module's own name — what it answers to. No spaces; use `_`. |
| `location` | free text, shown in the portal. |
| `switches` | the switch names, in GPIO order (1, 3, 4, 5, 6, 7). One to six. |
| `wifi_ssid` / `wifi_pass` | the network to join. `Buddy-Modules` is the Pi's own, which is where modules normally live. |
| `pi_url` | where to report in, e.g. `http://192.168.1.242:8000`. |
| `device_key` | the module's own key. Written by the Pi when it adopts the module. |
| `firmware_url` | where `POST /update` fetches from when it is not told otherwise. |
| `provisioned` | `false` puts it in setup mode, waiting to be adopted from the portal. |
| `states` | written by the firmware, not by you — what each switch was last set to, so a power cut does not turn the house off. |

**Both `smart_switch_id` and `name` are required** before a module will report
to the Pi. A module with neither says so at boot and waits: it has no identity
to report, and nothing on it will make one up. Two unconfigured modules
reporting as the same nameless thing would overwrite each other, which is the
failure this refuses to have.

## Names: the module's, and the Pi's

The module's name and its switch names are set here and **nothing else changes
them**. Renaming in the portal sets a *label* on the Pi — what you call it,
what you say to it, what the portal shows — and the module never hears about
it. Both names work when addressing a module, so automation written against
the module's own name keeps working after somebody relabels it.

Adoption from the portal hands a module a network, an address to report to,
and a key of its own. It does not hand it a name.

## Updating the firmware

`POST /update` on the module fetches new firmware and restarts onto it:

```json
{"url": "https://raw.githubusercontent.com/ETORMANATOR/buddy_smart_switch_module/main/main.py",
 "sha256": "<hex digest of that file>"}
```

That is this repository — <https://github.com/ETORMANATOR/buddy_smart_switch_module>
— and it is the module's own default, so `POST /update` with an empty body
fetches from there.

The normal route is the portal's **update firmware** button, which does this
for you: the Pi fetches from GitHub, checks the file is firmware rather than a
404 page, and tells the module to download it **from the Pi** with the sha256
of what the Pi actually got. The module refuses anything whose hash differs,
refuses anything that does not look like firmware, and keeps the old copy as
`main.bak`.

The Pi uses the same URL by default. Point it at a fork or a branch with
`BUDDY_FIRMWARE_URL`:

```
sudo systemctl edit buddy-backend
# Environment=BUDDY_FIRMWARE_URL=https://raw.githubusercontent.com/<owner>/<repo>/<branch>/main.py
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
