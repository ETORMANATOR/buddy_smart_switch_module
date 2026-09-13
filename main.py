# ESP32-C3 MicroPython — WiFi switch controller, 1 to 6 channels
# Save to the ESP32 as "main.py" (runs automatically on boot).
#
# --------------------------------------------- one firmware, many modules --
# This file is identical on every module and never edited per device. What
# differs between one module and the next — its id, its name, how many
# switches it has, what they are called, which WiFi it joins, its own access
# key — lives in config.json on that module's own flash, which survives a
# restart and a power cut.
#
# So setting up another module is: copy this file to it unchanged, then give
# it its own config.json. That file is merged over the defaults below, so it
# only has to carry what you are setting:
#
#     {"smart_switch_id": "SW-000412", "name": "living_room"}
#
# Three ways to put it there, all writing the same file:
#
#   * `python esp32/setup_over_usb.py --id SW-000412 --name living_room`
#     over the cable already plugged in — see esp32/config.example.json.
#   * The Pi's portal, once the module is adopted: the ID & name button.
#   * By hand: `mpremote fs cp my_config.json :config.json`.
#
# A module with neither set has no identity and will not report to the Pi:
# nothing here makes one up. An id typed into THIS file instead would be
# shared by every module flashed with it, which is why there is none.
#
# ---------------------------------------------------------------- the pins --
# On the ESP32-C3, DO NOT use: 11-17 (flash), 18/19 (USB), 20/21 (serial),
# and avoid 2, 8, 9 (strapping pins — they change how the chip boots).
# That leaves GPIO 0, 1, 3, 4, 5, 6, 7, 10.
#
# GPIO 0  is the reset button (to GND) — see FACTORY RESET below.
# GPIO 10 is the status LED.
# GPIO 1, 3, 4, 5, 6, 7 drive the relays, in that order, which is why six is
# the most switches one board can have.
#
# ------------------------------------------------------------ first power --
# A board with no config.json is unclaimed, and goes looking for the Pi:
#
#   1. It joins the Pi's own network, `Buddy-Modules`, whose name and password
#      are below. The Pi keeps that network up; it is where the modules live.
#   2. Having joined, it tells the Pi it exists — the Pi is the gateway on its
#      own network, so there is nothing to discover.
#   3. The portal lists it. You enter the setup key and a name, and it is
#      adopted — staying on this same network, with a device key of its own.
#      There is no handover, so there is no moment when nobody can reach it.
#
# If the Pi's network is not up, the board raises an access point of its own
# (`Buddy-Setup-XXXX`) so it is reachable by something, and keeps checking for
# the Pi every twenty seconds.
#
# Setting a board up over its USB cable — `esp32/setup_over_usb.py` — skips
# all of this and is the quickest route when the board is already plugged in.
#
# ------------------------------------------- what the radio will and won't do
# Measured on an ESP32-C3, MicroPython v1.27.0, against a Raspberry Pi
# running NetworkManager 1.52. Written down because every one of these looks
# like a broken board and none of them is:
#
#   * The Pi's setup network MUST be visible. `connect()` matches on beacons,
#     and a hidden access point broadcasts none — the board reports
#     STAT_NO_AP_FOUND (201) even while a scan can see the network sitting
#     there with an empty name. Tested with and without a password.
#
#   * It MUST be on 2.4 GHz. The C3 has no 5 GHz radio, and a Pi left to
#     choose its own band will happily pick one the board cannot hear.
#
#   * It MUST have a password. An open network is refused outright with
#     STAT_NO_AP_FOUND_W_COMPATIBLE_SECURITY (210), and this build's
#     `connect()` will not take a `security=` argument to lower that.
#
#   * OUR OWN ACCESS POINT MUST BE DOWN FIRST. An ESP32 running as both an
#     access point and a station has to keep both on one channel, so while
#     our AP is up the station cannot associate to a Pi that chose another.
#     This one is silent: the attempt simply sits at STAT_CONNECTING forever.
#
# Still unresolved: with all of the above right, the four-way handshake
# against NetworkManager's hostapd does not complete — 202 with a long
# passphrase, 1001 with a short one. The Pi advertises
# `WPA-PSK WPA-PSK-SHA256`, which is the likeliest culprit.
#
# --------------------------------------------------------- FACTORY RESET --
# Hold the button on GPIO 0 for three seconds. Everything goes: name, switch
# names, WiFi, keys. The board comes back up unclaimed.

import json
import os
import socket
import time

import binascii
import hashlib
import machine
import network
from machine import Pin

# The key that lets a Pi adopt an unclaimed board. It is the setup AP's
# password *and* the token the provisioning call must carry, so a board can
# only be claimed by someone who already knows it.
#
# Change this before flashing a fleet you care about — every board flashed
# from this file shares it, and it is the one secret that is not per-device.
PROVISION_KEY = "gZXw3wuGGXT96QGmNYk8"

# The Pi's own network, which is where a module lives — not a setup network
# it passes through. It joins this while unclaimed, is adopted on it, and
# stays on it for good.
#
# The module does the finding, not the Pi: an ESP32-C3's access point cannot
# be heard from the next room, and a Raspberry Pi's can. Looking for a name
# it already knows turns the problem round.
SETUP_HOTSPOT_SSID = "Buddy-Modules"
SETUP_HOTSPOT_PASS = PROVISION_KEY

# How long to keep trying the Pi's network before falling back to raising an
# access point of our own. The fallback matters: a board that can find neither
# is a board nobody can reach at all.
HOTSPOT_JOIN_SECONDS = 12

CONFIG_FILE = "config.json"

# Where `POST /update` fetches new firmware from when it is not told
# otherwise. Set it per module in config.json ("firmware_url"), or pass a url
# with the request - the Pi does, which is how one button updates a fleet.
#
# Written as a raw file URL, because that is what serves the file itself
# rather than a page about it.
FIRMWARE_URL = ""

# Kept beside the running firmware after an update, so a module that comes
# back broken can be put back the way it was over USB with one copy.
FIRMWARE_BACKUP = "main.bak"

# Relay GPIOs in the order switches are handed out. Six, and no more.
SWITCH_PINS = (1, 3, 4, 5, 6, 7)
MAX_SWITCHES = len(SWITCH_PINS)

RESET_PIN = 0
STATUS_LED_PIN = 10

# Most relay boards energise on HIGH. Low-trigger boards want these swapped.
RELAY_ON, RELAY_OFF = 1, 0

# How often the board tells the Pi it is alive. The Pi calls a device offline
# after missing a few of these, so it wants to be well under that.
HEARTBEAT_SECONDS = 20

# How long the reset button must be held. Long enough that a knock or a stray
# finger cannot wipe a board that is working.
RESET_HOLD_SECONDS = 3


# --------------------------------------------------------------- naming --

# ------------------------------------------------------------- identity --
#
# A module's id and its name are set by hand, in config.json, and nothing
# here derives them, cleans them up or invents a substitute. That is the
# point: what you wrote on the sticker, in the asset register and in the
# portal is the same string, character for character, because only one place
# ever decides it.
#
# The cost, accepted deliberately: a module with no config.json has no
# identity at all. It cannot report to the Pi, because there is nothing to
# report it as. It raises its setup access point and waits to be given one -
# over USB, or by being adopted from the portal. See esp32/CONFIG.md.


def smart_switch_id(config):
    """What this module is called in the asset register. Whatever you set."""
    return str(config.get("smart_switch_id", "")).strip()


def has_identity(config):
    """Whether this module has been given an id and a name to answer to."""
    return bool(smart_switch_id(config)) and bool(str(config.get("name", "")).strip())


# --------------------------------------------------------------- config --

def default_config():
    return {
        # Both empty, always. An id or a name written into this file is the
        # same id on every module flashed with it, which is the one thing an
        # id must never be. They are set per module, in config.json.
        "smart_switch_id": "",
        "name": "",
        "location": "",
        "switches": ["switch_%d" % (i + 1) for i in range(4)],
        "wifi_ssid": "",
        "wifi_pass": "",
        "pi_url": "",
        "device_key": "",
        "firmware_url": "",
        "provisioned": False,
        # What each switch was last set to. Kept so a module comes back after
        # a power cut the way the house was left, rather than with every
        # light off and somebody wondering why.
        "states": {},
    }


def load_config():
    try:
        with open(CONFIG_FILE) as handle:
            saved = json.load(handle)
    except (OSError, ValueError):
        return default_config()

    # Merged over the defaults rather than trusted wholesale: a config written
    # by an older firmware is missing whatever was added since, and a board
    # that will not boot because of that is a board someone has to unscrew.
    config = default_config()
    for key in config:
        if key in saved:
            config[key] = saved[key]
    config["switches"] = normalise_switches(config.get("switches"))
    config["name"] = str(config.get("name", "")).strip()
    return config


def save_config(config):
    tmp = CONFIG_FILE + ".tmp"
    with open(tmp, "w") as handle:
        json.dump(config, handle)
    try:
        os.remove(CONFIG_FILE)
    except OSError:
        pass
    os.rename(tmp, CONFIG_FILE)


def normalise_switches(names):
    """A clean, unique, in-range list of switch names."""
    if not isinstance(names, list):
        names = []
    out = []
    for index, raw in enumerate(names[:MAX_SWITCHES]):
        name = str(raw or "").strip() or "switch_%d" % (index + 1)
        # Local uniqueness only; the Pi is the one that can see every board
        # and so is the only thing that can enforce it across the fleet.
        while name in out:
            name = "%s_%d" % (name, index + 1)
        out.append(name)
    return out


def factory_reset():
    print("FACTORY RESET — clearing everything")
    try:
        os.remove(CONFIG_FILE)
    except OSError:
        pass
    time.sleep(0.5)
    machine.reset()


# ---------------------------------------------------------------- relays --

class Switches:
    """The relays, what each is doing, and what it was doing last time.

    [remember] is called whenever a switch changes, so the states outlive a
    power cut. It is given rather than reached for, because the relays should
    not need to know what a config file is.
    """

    def __init__(self, names, states=None, remember=None):
        self.names = []
        self.pins = {}
        self.state = {}
        self._remember = remember
        self.apply(names, states or {})

    def apply(self, names, states=None):
        """(Re)builds the relay set to match [names].

        Pins that are no longer used are driven off first — a switch that
        disappears from the config with its relay still closed would leave a
        light on that nothing can now turn off.
        """
        for name in self.names:
            if name not in names:
                self.pins[name].value(RELAY_OFF)

        # What was on before: whatever is running now, or on the first build
        # whatever was saved the last time this module had power.
        old_state = dict(self.state)
        if states:
            for name, was_on in states.items():
                old_state.setdefault(name, bool(was_on))

        self.names = list(names)[:MAX_SWITCHES]
        self.pins = {}
        self.state = {}
        for index, name in enumerate(self.names):
            pin = Pin(SWITCH_PINS[index], Pin.OUT, value=RELAY_OFF)
            self.pins[name] = pin
            was_on = old_state.get(name, False)
            self.state[name] = was_on
            pin.value(RELAY_ON if was_on else RELAY_OFF)

    def gpio_for(self, name):
        return SWITCH_PINS[self.names.index(name)]

    def set(self, name, on, save=True):
        if name not in self.pins:
            return False
        on = bool(on)
        # Written only when it actually changes. Flash has a finite number of
        # writes in it, and a switch told "off" while already off should not
        # spend one.
        changed = self.state.get(name) != on
        self.pins[name].value(RELAY_ON if on else RELAY_OFF)
        self.state[name] = on
        if changed and save and self._remember:
            self._remember(self.state)
        return True

    def set_all(self, on):
        # One write for the lot, rather than one per relay.
        changed = any(self.state.get(n) != bool(on) for n in self.names)
        for name in self.names:
            self.set(name, on, save=False)
        if changed and self._remember:
            self._remember(self.state)

    def rename(self, old, new):
        """Keeps the relay and its state, changes only what it is called."""
        if old not in self.pins:
            return False
        names = [new if n == old else n for n in self.names]
        was_on = self.state.get(old, False)
        self.apply(normalise_switches(names))
        cleaned = str(new or "").strip() or old
        if cleaned in self.pins:
            self.set(cleaned, was_on)
        return True


# ------------------------------------------------------------ networking --

def start_ap(config):
    """The setup network, for a board nobody has claimed yet.

    Reports what the radio actually ended up doing rather than what it was
    asked to do. The first version printed a confident banner before checking
    anything, so a board whose access point never came up looked identical to
    one broadcasting perfectly — and the Pi simply never found it.
    """
    # Named after the module when it has a name, and plainly when it does
    # not. Two un-named modules powered up in one room will raise the same
    # access point, which is a good reason to set them up one at a time.
    if config.get("name"):
        ssid = "Buddy-Setup-%s" % config["name"]
    else:
        ssid = "Buddy-Setup"

    # The station interface is put to sleep first. Leaving it active while the
    # access point starts makes some ESP32-C3 builds bring the AP up on the
    # station's channel, or not at all.
    try:
        sta = network.WLAN(network.STA_IF)
        if sta.active():
            sta.active(False)
            time.sleep(0.3)
    except Exception:
        pass

    ap = network.WLAN(network.AP_IF)
    ap.active(False)
    time.sleep(0.3)

    # Named before the radio comes up *and* after it: builds disagree about
    # which order works, and doing both is harmless.
    #
    # The attempts are kept quiet and only reported if the last one fails.
    # This chip refuses to be named before its radio is active, so the first
    # attempt always raises - printing that made every healthy boot carry an
    # error message, which is how a real one goes unread.
    secured = False
    trouble = []
    for when in ("before", "after"):
        if when == "after":
            ap.active(True)
            time.sleep(0.5)
        try:
            ap.config(essid=ssid, password=PROVISION_KEY, authmode=3)
            secured = True
            trouble = []
        except Exception as exc:
            try:
                ap.config(essid=ssid)
                trouble = ["access point is open - no password (%s)" % exc]
            except Exception as inner:
                trouble.append("could not name it %s the radio was active: %s"
                               % (when, inner))
    for line in trouble:
        print(line)

    if not ap.active():
        ap.active(True)
        time.sleep(0.5)

    # What the chip says, not what it was told.
    try:
        actual = ap.config("essid")
    except Exception:
        actual = "?"
    try:
        address = ap.ifconfig()[0]
    except Exception:
        address = "?"

    print("")
    print("=" * 52)
    if ap.active() and actual == ssid:
        print("  UNCLAIMED — waiting to be set up")
        print("  SSID     : %s%s" % (actual, "" if secured else "   (open!)"))
        print("  ADDRESS  : %s" % address)
        print("  Join it from the Pi's portal and enter the setup key.")
    else:
        # Said plainly, because this is the state that looks like a working
        # board and is not one.
        print("  ACCESS POINT DID NOT START")
        print("  active   : %s" % ap.active())
        print("  essid    : %s   (wanted %s)" % (actual, ssid))
        print("  Nothing will find this board until that says otherwise.")
    print("=" * 52)
    print("")
    return ap


def status_in_words(code):
    """What a station status code means, in a sentence rather than a number.

    Written out because these codes are the whole story when a module will
    not join, and looking them up is a job nobody does at the top of a
    ladder.
    """
    return {
        1000: "idle",
        1001: "still trying to associate - the access point is not answering",
        1010: "connected",
        201: "no access point of that name on the air here",
        202: "rejected: wrong password, or the handshake was not completed",
        203: "the access point refused the association",
        204: "the handshake timed out",
        210: "found it, but its security is one this chip cannot use",
        211: "its security is weaker than this chip will accept",
    }.get(code, "unknown status %s" % code)


def signal_of(wlan, ssid):
    """The Pi network's signal here, in dBm, or None if it is not on the air.

    Scanned before trying to join, because "could not join" and "could not
    hear" are different faults with different fixes, and a module that
    reports the wrong one sends somebody to change a password when what is
    needed is to move the module.
    """
    wanted = ssid.encode() if isinstance(ssid, str) else ssid
    best = None
    try:
        for found in wlan.scan():
            if found[0] == wanted and (best is None or found[3] > best):
                best = found[3]
    except Exception:
        return None
    return best


def join_setup_hotspot(timeout=HOTSPOT_JOIN_SECONDS):
    """Joins the Pi's own network, if it is in range. None when it is not.

    Reports what it saw either way. A module can hear a Pi long before a Pi
    can hear the module - the Pi's radio is the stronger of the two - so
    "the network is right there and the join failed" is a real state, and it
    means the module is too far away, not that anything is misconfigured.
    """
    # Our own access point comes down first, and this is not optional: an
    # ESP32 running as both an access point and a station must have the two
    # on the *same channel*, so while our AP is up the station simply cannot
    # associate to a Pi that chose a different one. Measured - with the AP
    # left up the attempt sat at "connecting" forever; with it down the very
    # next attempt reached authentication.
    try:
        ap = network.WLAN(network.AP_IF)
        if ap.active():
            ap.active(False)
            time.sleep(0.8)
    except Exception:
        pass

    wlan = network.WLAN(network.STA_IF)
    try:
        if wlan.isconnected():
            wlan.disconnect()
            time.sleep(0.3)
    except OSError:
        pass
    wlan.active(False)
    time.sleep(0.4)
    wlan.active(True)
    # The radio needs longer than feels reasonable before it can scan: at
    # half a second the first scan comes back empty even standing next to
    # the access point, which reads exactly like being out of range.
    time.sleep(2)

    rssi = signal_of(wlan, SETUP_HOTSPOT_SSID)
    if rssi is None:
        print("The Pi's network (%s) is not on the air here."
              % SETUP_HOTSPOT_SSID)
    else:
        print("The Pi's network is here at %d dBm%s"
              % (rssi, " - weak" if rssi < -75 else ""))

    print("Joining", end="")
    try:
        wlan.connect(SETUP_HOTSPOT_SSID, SETUP_HOTSPOT_PASS)
    except OSError as exc:
        print(" radio error: %s" % exc)
        return None

    waited = 0
    while not wlan.isconnected() and waited < timeout:
        time.sleep(0.5)
        waited += 0.5
        print(".", end="")

    if wlan.isconnected():
        print(" joined - %s" % wlan.ifconfig()[0])
        return wlan

    try:
        code = wlan.status()
    except Exception:
        code = None
    print(" no")
    print("  why: %s" % status_in_words(code))
    if rssi is not None:
        # The asymmetry that wastes the most time. Measured on this setup:
        # the module heard the Pi at -68 dBm while the Pi could not hear the
        # module at all, and every attempt sat at "associating".
        print("  The module can hear the Pi, so the Pi cannot hear the")
        print("  module: a Pi's radio carries much further than this one's.")
        print("  Move the module nearer the Pi, or put it on the house WiFi")
        print("  over USB instead - see esp32/CONFIG.md.")

    try:
        wlan.active(False)
    except OSError:
        pass
    return None


def announce_to_pi(wlan, config, switches):
    """Tells the Pi this board exists and is waiting to be set up.

    The Pi is the gateway on its own hotspot, so there is nothing to
    configure and nothing to discover — the board already knows the address
    of whatever handed it a lease.
    """
    try:
        gateway = wlan.ifconfig()[2]
    except Exception:
        return False

    payload = {
        "smart_switch_id": smart_switch_id(config),
        "ip": wlan.ifconfig()[0],
        "name": config["name"],
        "switch_count": len(switches.names),
        "max_switches": MAX_SWITCHES,
    }
    return post_json("http://%s:8000/api/esp32/announce" % gateway, payload)


def join_wifi(config, timeout=20, attempts=3):
    """Joins the saved network. None when it cannot."""
    ssid = config.get("wifi_ssid", "")
    if not ssid:
        return None

    for attempt in range(1, attempts + 1):
        wlan = network.WLAN(network.STA_IF)
        try:
            if wlan.isconnected():
                wlan.disconnect()
                time.sleep(0.3)
        except OSError:
            pass
        wlan.active(False)
        time.sleep(0.5)

        # The hostname has to be set while the interface is down. It is a
        # courtesy — a readable entry in the router's table — not something
        # the Pi relies on, because plenty of routers ignore it.
        try:
            network.hostname(config["name"])
        except AttributeError:
            try:
                wlan.config(dhcp_hostname=config["name"])
            except Exception:
                pass

        wlan.active(True)
        time.sleep(0.5)

        print("Joining '%s' (%d/%d)" % (ssid, attempt, attempts), end="")
        try:
            wlan.connect(ssid, config.get("wifi_pass", ""))
        except OSError as exc:
            print(" radio error: %s" % exc)
            time.sleep(2)
            continue

        waited = 0
        while not wlan.isconnected() and waited < timeout:
            time.sleep(0.5)
            waited += 0.5
            print(".", end="")

        if wlan.isconnected():
            print(" ok — %s" % wlan.ifconfig()[0])
            return wlan
        print(" failed (status %s)" % wlan.status())

    return None


def post_json(url, payload, timeout=4):
    """A POST, written on sockets so the board needs no extra library.

    Deliberately small and forgiving: this runs every heartbeat, and a Pi that
    is rebooting must not take the switches down with it.
    """
    try:
        rest = url.split("://", 1)[-1]
        hostport, _, path = rest.partition("/")
        host, _, port = hostport.partition(":")
        port = int(port) if port else 80
        path = "/" + path

        body = json.dumps(payload)
        request = (
            "POST %s HTTP/1.0\r\nHost: %s\r\n"
            "Content-Type: application/json\r\nContent-Length: %d\r\n"
            "Connection: close\r\n\r\n%s" % (path, hostport, len(body), body)
        )

        addr = socket.getaddrinfo(host, port)[0][-1]
        sock = socket.socket()
        sock.settimeout(timeout)
        sock.connect(addr)
        sock.send(request.encode())
        reply = sock.recv(256)
        sock.close()
        return b"200" in reply[:16]
    except Exception as exc:
        print("heartbeat failed: %s" % exc)
        return False


def fetch_to_file(url, path, timeout=20, limit=200000):
    """Downloads [url] into [path]. Returns (bytes, sha256-hex).

    Written on sockets like everything else here, so a module needs no
    library it might not have, and streamed in small pieces so a file larger
    than free memory still lands. The hash is computed on the way past: it is
    what lets the Pi say whether what arrived is what it meant to send.
    """
    scheme, _, rest = url.partition("://")
    hostport, _, tail = rest.partition("/")
    host, _, port = hostport.partition(":")
    secure = scheme == "https"
    port = int(port) if port else (443 if secure else 80)

    addr = socket.getaddrinfo(host, port)[0][-1]
    sock = socket.socket()
    sock.settimeout(timeout)
    sock.connect(addr)
    if secure:
        # No certificate check: MicroPython on this chip has no trust store
        # to check against. That is why the Pi sends a sha256 with the
        # request - see update() - rather than this being trusted on its own.
        import ssl
        sock = ssl.wrap_socket(sock, server_hostname=host)

    sock.write(("GET /%s HTTP/1.0\r\nHost: %s\r\n"
                "User-Agent: buddy-switch\r\n"
                "Connection: close\r\n\r\n" % (tail, hostport)).encode())

    # Headers first, then whatever of the body came with them.
    header = b""
    while b"\r\n\r\n" not in header:
        chunk = sock.read(256)
        if not chunk:
            raise OSError("the server closed before sending anything")
        header += chunk
        if len(header) > 8192:
            raise OSError("headers went on too long")
    head, _, body = header.partition(b"\r\n\r\n")

    status = head.split(b"\r\n")[0].split(b" ")
    code = int(status[1]) if len(status) > 1 else 0
    if code != 200:
        sock.close()
        raise OSError("the server said %d" % code)

    digest = hashlib.sha256()
    written = 0
    with open(path, "wb") as handle:
        while True:
            if body:
                written += len(body)
                if written > limit:
                    raise OSError("firmware is larger than %d bytes" % limit)
                digest.update(body)
                handle.write(body)
            body = sock.read(512)
            if not body:
                break
    sock.close()
    return written, binascii.hexlify(digest.digest()).decode()


def looks_like_firmware(path):
    """Whether what arrived is plausibly this program.

    A truncated download or a login page saved over main.py is a module that
    does not come back, and one that is screwed to a wall is one somebody has
    to unscrew. Cheap to check, expensive to skip.
    """
    try:
        if os.stat(path)[6] < 5000:
            return False
    except OSError:
        return False
    with open(path) as handle:
        text = handle.read()
    return ("def main():" in text
            and "SWITCH_PINS" in text
            and text.rstrip().endswith("main()"))


# ------------------------------------------------------------- responses --

def json_response(payload):
    return 200, json.dumps(payload)


def state_payload(config, switches, ip):
    return {
        "name": config["name"],
        "location": config.get("location", ""),
        "ip": ip,
        "smart_switch_id": smart_switch_id(config),
        "max_switches": MAX_SWITCHES,
        "switches": [
            {
                "name": name,
                "gpio": switches.gpio_for(name),
                "on": switches.state.get(name, False),
            }
            for name in switches.names
        ],
    }


# -------------------------------------------------------------- the API --

class Server:
    def __init__(self, config, switches):
        self.config = config
        self.switches = switches
        self.ip = "192.168.4.1"
        self.reboot_after_reply = False

    # -- helpers ----------------------------------------------------------

    def authorised(self, params, body):
        key = params.get("key") or body.get("key") or ""
        expected = self.config.get("device_key") or PROVISION_KEY
        return key == expected

    def save(self):
        save_config(self.config)

    # -- routes -----------------------------------------------------------

    def hello(self):
        """The one endpoint that needs no key.

        It says what the board is and whether anyone has claimed it, and
        nothing that would help a stranger do anything to it. Without this
        there is no way to find a board that has not been set up yet.
        """
        return json_response({
            "device": "buddy-switch",
            "name": self.config["name"],
            "smart_switch_id": smart_switch_id(self.config),
            "provisioned": bool(self.config.get("provisioned")),
            "switch_count": len(self.switches.names),
            "max_switches": MAX_SWITCHES,
        })

    def provision(self, body):
        if body.get("key") != PROVISION_KEY:
            return 401, json.dumps({"error": "wrong setup key"})

        ssid = str(body.get("ssid", "")).strip()
        if not ssid:
            return 400, json.dumps({"error": "no wifi ssid given"})

        self.config["wifi_ssid"] = ssid
        self.config["wifi_pass"] = str(body.get("pass", ""))
        self.config["pi_url"] = str(body.get("pi_url", "")).strip()
        if body.get("name"):
            self.config["name"] = str(body["name"]).strip()
        if body.get("smart_switch_id"):
            self.config["smart_switch_id"] = str(body["smart_switch_id"]).strip()
        if body.get("location"):
            self.config["location"] = str(body["location"])[:60]
        if body.get("switches"):
            self.config["switches"] = normalise_switches(body["switches"])
        elif body.get("switch_count"):
            count = max(1, min(MAX_SWITCHES, int(body["switch_count"])))
            self.config["switches"] = ["switch_%d" % (i + 1) for i in range(count)]

        # A key of its own, now that there is something worth protecting: the
        # setup key opened the door once, and from here the Pi uses this
        # instead. The Pi makes it, because the module no longer makes
        # anything about itself up - and a module that is handed no key would
        # otherwise be left with the setup key as its only protection, which
        # every module shares.
        key = str(body.get("device_key", "")).strip()
        if not key:
            return 400, json.dumps({
                "error": "no device key supplied; the Pi must send one",
            })
        self.config["device_key"] = key
        self.config["provisioned"] = True
        self.save()

        self.reboot_after_reply = True
        return json_response({
            "ok": True,
            "name": self.config["name"],
            "device_key": self.config["device_key"],
            "switches": self.config["switches"],
        })

    def rename(self, body):
        """Renames the module, a switch, or both — and remembers it."""
        changed = []

        if "smart_switch_id" in body:
            self.config["smart_switch_id"] = str(body["smart_switch_id"]).strip()
            changed.append("smart_switch_id")

        if body.get("name"):
            self.config["name"] = str(body["name"]).strip()
            changed.append("device")

        renames = body.get("switches") or {}
        if isinstance(renames, dict):
            for old, new in renames.items():
                if self.switches.rename(old, str(new or "").strip() or old):
                    changed.append(old)
            self.config["switches"] = list(self.switches.names)
            self.config["states"] = dict(self.switches.state)

        if not changed:
            return 400, json.dumps({"error": "nothing to rename"})

        self.save()
        return json_response({"ok": True, "renamed": changed,
                              "name": self.config["name"],
                              "smart_switch_id": smart_switch_id(self.config),
                              "switches": self.config["switches"]})

    def update(self, body):
        """Fetches new firmware and restarts onto it.

        The Pi normally sends both the url and the sha256 of what it fetched
        from that url itself; with a hash, anything that arrives different is
        refused. Without one the download is taken on trust, which over https
        with no trust store on this chip is weaker than it sounds - so it
        says so rather than pretending.
        """
        url = (str(body.get("url", "")).strip()
               or str(self.config.get("firmware_url", "")).strip()
               or FIRMWARE_URL)
        if not url:
            return 400, json.dumps({
                "error": "no firmware url: send one, or set firmware_url",
            })

        wanted = str(body.get("sha256", "")).strip().lower()
        staged = "main.new"
        try:
            size, digest = fetch_to_file(url, staged)
        except Exception as exc:
            return 502, json.dumps({"error": "download failed: %s" % exc})

        if wanted and digest != wanted:
            return 409, json.dumps({
                "error": "what arrived is not what was sent",
                "expected": wanted, "got": digest,
            })
        if not wanted:
            print("update: no sha256 given, taking the download on trust")

        if not looks_like_firmware(staged):
            return 422, json.dumps({
                "error": "that download does not look like firmware",
                "bytes": size,
            })

        # The running copy is kept, so a module that comes back wrong can be
        # put back with one file copy rather than a reflash.
        try:
            os.remove(FIRMWARE_BACKUP)
        except OSError:
            pass
        try:
            os.rename("main.py", FIRMWARE_BACKUP)
        except OSError:
            pass
        os.rename(staged, "main.py")

        self.reboot_after_reply = True
        return json_response({"ok": True, "bytes": size, "sha256": digest,
                              "from": url})

    def configure_switches(self, body):
        """Sets how many switches this board has, or what they are called."""
        if body.get("switches"):
            names = normalise_switches(body["switches"])
        elif body.get("count") is not None:
            count = int(body["count"])
            if count < 1 or count > MAX_SWITCHES:
                return 400, json.dumps({
                    "error": "this board has %d usable relay pins" % MAX_SWITCHES,
                })
            names = normalise_switches(
                [self.switches.names[i] if i < len(self.switches.names)
                 else "switch_%d" % (i + 1) for i in range(count)]
            )
        else:
            return 400, json.dumps({"error": "give switches or count"})

        self.switches.apply(names, self.config.get("states"))
        self.config["switches"] = list(self.switches.names)
        self.config["states"] = dict(self.switches.state)
        self.save()
        return json_response(state_payload(self.config, self.switches, self.ip))

    def handle(self, method, path, body):
        if "?" in path:
            path, _, query = path.partition("?")
            params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
        else:
            params = {}

        if path in ("/hello", "/"):
            return self.hello()

        if path == "/provision":
            return self.provision(body)

        if not self.authorised(params, body):
            return 401, json.dumps({"error": "invalid or missing key"})

        if path in ("/status", "/info"):
            return json_response(state_payload(self.config, self.switches, self.ip))

        if path == "/rename":
            return self.rename(body)

        if path == "/switches":
            return self.configure_switches(body)

        if path == "/update":
            return self.update(body)

        if path == "/factory_reset":
            factory_reset()   # does not return

        parts = [p for p in path.split("/") if p]
        if len(parts) == 2 and parts[1] in ("on", "off"):
            target, on = parts[0], parts[1] == "on"
            if target == "all":
                self.switches.set_all(on)
                return json_response(state_payload(self.config, self.switches, self.ip))
            if self.switches.set(target, on):
                return json_response(state_payload(self.config, self.switches, self.ip))
            return 404, json.dumps({"error": "no switch called '%s'" % target})

        return 404, json.dumps({"error": "unknown path"})


# ---------------------------------------------------------------- main --

def read_request(conn):
    """The method, path and JSON body of one request."""
    raw = conn.recv(1024).decode()
    if not raw:
        return None, None, {}
    head, _, rest = raw.partition("\r\n\r\n")
    first = head.split("\r\n")[0].split(" ")
    method = first[0] if first else "GET"
    path = first[1] if len(first) > 1 else "/"
    body = {}
    if rest.strip():
        try:
            body = json.loads(rest)
        except ValueError:
            body = {}
    return method, path, body


def watch_reset_button(button, held_since):
    """Returns when the hold started, or None while the button is up.

    Polled from the accept loop rather than driven by an interrupt: an IRQ
    that fires while the socket is mid-reply is a good way to corrupt a
    response, and three seconds is not a deadline worth racing for.
    """
    if button is None or button.value() == 1:      # pull-up: 1 is released
        return None
    now = time.time()
    if held_since is None:
        return now
    if now - held_since >= RESET_HOLD_SECONDS:
        factory_reset()
    return held_since


def main():
    config = load_config()

    def remember(states):
        """Writes the switch states back to flash as they change."""
        config["states"] = dict(states)
        try:
            save_config(config)
        except OSError as exc:
            # A full or failing flash must not stop the relays working.
            print("could not save switch states: %s" % exc)

    switches = Switches(config["switches"], config.get("states"), remember)

    led = Pin(STATUS_LED_PIN, Pin.OUT, value=0) if STATUS_LED_PIN is not None else None
    try:
        button = Pin(RESET_PIN, Pin.IN, Pin.PULL_UP)
    except ValueError:
        button = None

    server = Server(config, switches)

    if not has_identity(config):
        # Not an error, and not fatal: this is a module fresh out of the box.
        # But it cannot report to the Pi, because there is nothing to report
        # it as, so it says so once rather than looking like a radio fault.
        print("")
        print("=" * 52)
        print("  NO IDENTITY SET")
        print("  smart_switch_id : %s"
              % (smart_switch_id(config) or "(not set)"))
        print("  name            : %s"
              % (config.get("name") or "(not set)"))
        print("  Set both in config.json - over USB with")
        print("  esp32/setup_over_usb.py, or by being adopted from the")
        print("  portal. Until then this module will not report to the Pi.")
        print("=" * 52)
        print("")

    wlan = join_wifi(config) if config.get("provisioned") else None
    waiting_on_hotspot = False

    if wlan is None and not config.get("provisioned"):
        # Unclaimed. Go looking for the Pi rather than waiting to be found.
        wlan = join_setup_hotspot()
        if wlan is not None:
            waiting_on_hotspot = True
            announce_to_pi(wlan, config, switches)

    if wlan is not None:
        server.ip = wlan.ifconfig()[0]
        if led:
            led.value(1)
        if waiting_on_hotspot:
            print("")
            print("=" * 52)
            print("  UNCLAIMED — on the Pi's setup network")
            print("  ADDRESS : %s" % server.ip)
            print("  It should now be listed in the portal under")
            print("  Switch boards. Click it and enter the setup key.")
            print("=" * 52)
            print("")
        else:
            banner(config, switches, server.ip)
    else:
        # Neither the Pi's network nor our own credentials. Raise an access
        # point so the board is reachable by *something*, and keep trying the
        # Pi from the loop below.
        start_ap(config)
        server.ip = "192.168.4.1"

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", 80))
    sock.listen(4)
    sock.settimeout(1)          # so the loop can watch the button and the clock
    print("listening on %s:80" % server.ip)

    held_since = None
    last_beat = 0

    while True:
        held_since = watch_reset_button(button, held_since)

        if time.time() - last_beat >= HEARTBEAT_SECONDS:
            last_beat = time.time()

            if wlan is not None and config.get("pi_url") and has_identity(config):
                # Claimed and on the house network: report where we are, so
                # the Pi can keep calling us by name however the address moves.
                payload = state_payload(config, switches, server.ip)
                payload["key"] = config.get("device_key", "")
                post_json(config["pi_url"].rstrip("/") + "/api/esp32/register",
                          payload)

            elif waiting_on_hotspot and wlan is not None:
                # Unclaimed, on the Pi's network: keep saying so, or the
                # portal forgets us while somebody is still deciding.
                announce_to_pi(wlan, config, switches)

            elif not config.get("provisioned"):
                # Unclaimed and on our own access point. The Pi's hotspot may
                # have come up since we last looked — it is usually switched
                # on at the moment somebody presses "Find new boards".
                found = join_setup_hotspot(timeout=6)
                if found is not None:
                    wlan = found
                    waiting_on_hotspot = True
                    server.ip = wlan.ifconfig()[0]
                    announce_to_pi(wlan, config, switches)
                    print("joined the Pi's setup network — %s" % server.ip)

        try:
            conn, _ = sock.accept()
        except OSError:
            continue            # the one-second timeout; back round the loop

        try:
            method, path, body = read_request(conn)
            if path is None:
                continue
            code, reply = server.handle(method, path, body)
            conn.send("HTTP/1.1 %d OK\r\nContent-Type: application/json\r\n"
                      "Connection: close\r\n\r\n%s" % (code, reply))
        except Exception as exc:
            print("request error: %s" % exc)
        finally:
            conn.close()

        if server.reboot_after_reply:
            # Provisioning changes which network this board belongs on, so it
            # has to come up again — but only after the Pi has its answer,
            # which holds the new device key.
            print("restarting (provisioned, or updated)")
            time.sleep(1)
            machine.reset()


def banner(config, switches, ip):
    print("")
    print("=" * 52)
    print("  NAME     : %s" % config["name"])
    print("  LOCATION : %s" % (config.get("location") or "-"))
    print("  IP       : %s   (the Pi calls it by name, not this)" % ip)
    print("  PI       : %s" % (config.get("pi_url") or "not set"))
    print("-" * 52)
    print("  SWITCH                GPIO")
    for name in switches.names:
        print("  %-20s  %d" % (name, switches.gpio_for(name)))
    print("=" * 52)
    print("")


main()
