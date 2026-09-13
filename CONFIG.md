# One firmware, many modules

`main.py` is the same file on every smart switch module. Nothing in it is
edited per device — a serial typed into shared code is the same serial on
every module flashed with it, which is the one thing a serial must never be.

Everything that differs between modules lives in **`config.json`** on that
module's own flash. Setting up another module is two steps:

```
mpremote connect COM4 fs cp esp32/main.py :main.py        # unchanged, always
python esp32/setup_over_usb.py --port COM4 \
    --serial SW-000412 --name living_room
```

## What goes in config.json

`config.example.json` next to this file is a full one. It is **merged over the
firmware's defaults**, so it only has to carry what you are actually setting:

```json
{"serial": "SW-000412", "name": "living_room"}
```

| key | what it is |
|---|---|
| `serial` | the number on your sticker. Left empty, the module uses its MAC — so two unconfigured modules are still never the same module. |
| `name` | what it is called and spoken to. No spaces; use `_`. Left out, it is `buddy_switch_` + the last six of the serial. |
| `location` | free text, shown in the portal. |
| `switches` | the switch names, in GPIO order (1, 3, 4, 5, 6, 7). One to six. |
| `wifi_ssid` / `wifi_pass` | the network to join. `Buddy-Modules` is the Pi's own, which is where modules normally live. |
| `pi_url` | where to report in, e.g. `http://192.168.1.242:8000`. |
| `device_key` | the module's own key. Left empty, adoption generates one. |
| `provisioned` | `false` puts it in setup mode, waiting to be adopted from the portal. |
| `states` | written by the firmware, not by you — what each switch was last set to, so a power cut does not turn the house off. |

## Three ways to write it

* **Over USB** — `esp32/setup_over_usb.py`, above. With only `--serial` and
  `--name` it merges into whatever config the module already has, so it can be
  used to rename a working module without setting it up again.
* **From the Pi's portal** — the **S/N & name** button on the module's row,
  once it has been adopted. This is the normal route.
* **By hand** — write the JSON yourself and
  `mpremote connect COM4 fs cp my_config.json :config.json`.

## Serials are yours to choose, and the Pi checks them

The Pi refuses a serial another module already holds, and names the one that
has it. If two ever do slip through, both rows show **SHARED WITH ANOTHER
MODULE** rather than one quietly vanishing: the modules are told apart by
their MAC, which cannot be typed and cannot collide, and which every module
reports alongside whatever serial you gave it.

## A factory reset clears it

Holding GPIO 0 to ground for three seconds deletes `config.json` — serial,
name, switches and all. The module comes back unclaimed, using its MAC again.
