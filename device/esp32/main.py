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
#   * `mpremote connect COM4 fs cp my_config.json :config.json`, over the
#     cable already plugged in — see config.example.json next to this file.
#   * Adoption from the Pi's portal, which hands the module a network, an
#     address to report to and a key of its own — but never a name.
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
#   1. It advertises over Bluetooth (BLE) for BLE_PROVISION_SECONDS, so the Pi
#      can find and provision it directly with no WiFi involved at all -
#      proximity is the only thing that lets somebody claim it this way, the
#      same role Buddy-Modules' shared password played before. See
#      ble_provision() below.
#   2. If nothing claims it over BLE in time, it falls back to joining
#      `Buddy-Modules`, whose name and password are below - hosted by its own
#      bridge device, not the Pi's own radio, so this network being up says
#      nothing about where the Pi itself actually is.
#   3. Having joined that, it tells the Pi it exists, at pi_url (below) -
#      resolved over mDNS if that is a ".local" name, since the gateway on
#      this network is the bridge, not the Pi.
#   4. The portal lists it. You enter the setup key and a name, and it is
#      adopted — staying on this same network, with a device key of its own.
#      There is no handover, so there is no moment when nobody can reach it.
#
# If the Pi's network is not up, the board raises an access point of its own
# (`Buddy-Setup-XXXX`) so it is reachable by something, and keeps checking for
# the Pi every twenty seconds.
#
# Writing a config.json over the USB cable skips all of this, and is the
# quickest route when the module is already plugged in — see CONFIG.md.
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
# Hold the button on GPIO 0 for three seconds. The WiFi and the key it was
# given go, so the board comes back up unclaimed. Its name, its id and its
# switch states are not what a reset means to undo, and stay.

import json
import os
import socket
import time

import binascii
import bluetooth
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

# What this device is. Reported on every check-in so the Pi can tell one kind
# of module from another without guessing from the shape of the reply - a
# fleet gets more than one kind of hardware in it eventually, and the day it
# does is not the day to start asking.
DEVICE_TYPE = "esp32"

# What this build is. Reported on every check-in, so the portal can say which
# modules are behind rather than only that an update happened.
#
# Bumped by hand when the firmware changes in a way a module would notice.
# The Pi reads this same line out of the copy it fetched from GitHub, which is
# how "update available" is decided - so the string has to stay easy to find:
# one line, plain quotes, nothing computed.
FIRMWARE_VERSION = "1.11.4"

# Where `POST /update` fetches new firmware from when it is not told
# otherwise. Set it per module in config.json ("firmware_url"), or pass a url
# with the request - the Pi does, which is how one button updates a fleet.
#
# Written as a raw file URL, because that is what serves the file itself
# rather than a page about it.
# The repository, laid out a folder per kind of hardware. The type is part
# of the path rather than a label on the file: an ESP8266 handed an ESP32
# build is a module on a wall that does not come back.
FIRMWARE_BASE_URL = ("https://raw.githubusercontent.com/ETORMANATOR/"
                     "buddy_smart_switch_module/main/device")
FIRMWARE_URL = "%s/%s/main.py" % (FIRMWARE_BASE_URL, DEVICE_TYPE)

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
HEARTBEAT_SECONDS = 10

# A heartbeat gone unanswered this many times in a row, while the radio still
# insists it is connected, means the radio is wrong: the association survived
# but whatever actually carries packets to the Pi did not, which sitting still
# never fixes. One miss is a busy Pi or a lost packet - normal, and not worth
# acting on. This many in a row, on a network that swears it is still up, is
# the fault the RF association trouble turns into after the fact: not "never
# joins" but "joined, then quietly stopped carrying anything." The fix is the
# same either way - drop the association and let the loop below rejoin from
# nothing, rather than trusting a link that has proven it is not one.
FAILED_HEARTBEATS_BEFORE_RECONNECT = 3

# Heartbeat ticks in a row with no network at all - neither the board's own
# WiFi nor the Pi's Buddy-Modules hotspot - before trying a full reboot. A
# dropped association is what the reconnect above is for; this is the next
# rung up, for a radio that will not even rejoin from nothing, which
# power-cycling the board has been the only reliable fix for. Twelve misses
# at HEARTBEAT_SECONDS (10) each is two minutes - requested explicitly,
# short enough that a board stuck off the network is not left dark for long,
# at the cost of being more willing than before to reboot through a
# genuinely brief outage (the Pi restarting, someone mid-setup on it) rather
# than waiting it out.
NO_NETWORK_TICKS_BEFORE_REBOOT = 12

# How long the reset button must be held. Long enough that a knock or a stray
# finger cannot wipe a board that is working.
RESET_HOLD_SECONDS = 3

# How long an unclaimed board advertises over Bluetooth before giving up and
# falling back to join_setup_hotspot(). BLE and WiFi share one radio on this
# chip, so this has to actually end rather than run alongside anything else -
# see ble_provision() below.
#
# Long enough for an actual person: scanning, reading the result, typing a
# name and clicking Connect easily takes more than the 20 seconds this was
# first measured at - a board that gives up and starts hunting for
# Buddy-Modules mid-adoption is one BleakDeviceNotFoundError away from a
# confusing "it was right there a second ago."
BLE_PROVISION_SECONDS = 120


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


def device_type(config=None):
    """What kind of device this is. Overridable, but rarely worth it."""
    if config:
        chosen = str(config.get("device_type", "")).strip()
        if chosen:
            return chosen
    return DEVICE_TYPE


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
        "device_type": DEVICE_TYPE,
        "smart_switch_id": "",
        "name": "",
        # How many relays, not what they are called. The names follow the
        # count - switch1..switchN - and the Pi labels them for people.
        "switch_count": 6,
        # Empty, not "Buddy-Modules" - is_provisioned() below treats any
        # non-empty wifi_ssid as this board's own house network and tries
        # join_wifi() with it before ever falling back to
        # join_setup_hotspot(). "Buddy-Modules" here used to make an
        # unclaimed board (or one with no config.json at all) look
        # provisioned onto Buddy-Modules with a blank password - which
        # join_wifi() then spent up to three attempts x 20 seconds failing
        # to join, before falling back to the one path that was ever going
        # to work. join_setup_hotspot() already knows the real SSID and
        # password for Buddy-Modules; this default has no reason to guess
        # at them too, wrongly.
        "wifi_ssid": "",
        "wifi_pass": "",
        "pi_url": "http://buddy.local:8000",
        "device_key": "",
        "firmware_url": "",
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
    # A list is accepted and counted: a config written when switches had
    # names should not leave a module with none.
    wanted = config.get("switch_count")
    if isinstance(wanted, list):
        wanted = len(wanted)
    config["switch_count"] = len(switch_names(wanted))
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


def switch_names(count):
    """switch1 .. switchN, in relay order.

    Generated rather than stored, so they cannot drift from the relays they
    name and cannot be duplicated. What a person calls a switch is a label,
    and labels live on the Pi.
    """
    try:
        count = int(count)
    except (TypeError, ValueError):
        count = 1
    count = max(1, min(MAX_SWITCHES, count))
    return ["switch%d" % (index + 1) for index in range(count)]


def is_provisioned(config):
    """Whether this module has a network of its own to join.

    Derived, not stored. As a stored flag it could say no while wifi_ssid
    said yes, and then a module sits raising its own access point next to
    the network it was given.
    """
    return bool(str(config.get("wifi_ssid", "")).strip())


def apply_provision(config, body):
    """Writes WiFi credentials and a device key into [config] and saves it -
    the one thing both the HTTP /provision route and BLE provisioning do,
    kept in one place rather than two so they cannot quietly drift apart.

    Raises ValueError, with a message fit to hand straight back to whoever
    called it, on anything wrong with [body].
    """
    ssid = str(body.get("ssid", "")).strip()
    if not ssid:
        raise ValueError("no wifi ssid given")

    key = str(body.get("device_key", "")).strip()
    if not key:
        raise ValueError("no device key supplied; the Pi must send one")

    # A network, somewhere to report to, and a key. Nothing about what this
    # module is: its id, its name and how many relays it has are set on the
    # module itself and are not adoption's to rewrite.
    config["wifi_ssid"] = ssid
    config["wifi_pass"] = str(body.get("pass", ""))
    config["pi_url"] = str(body.get("pi_url", "")).strip()
    config["device_key"] = key
    save_config(config)


# What a factory reset keeps. The asset number and the name are the module's
# identity, set by hand and painted on the case; a reset button that wiped
# them would mean a cable and a laptop before the module could be set up
# again. How many relays it has is a fact about the wiring, not about who
# owns it, so that stays too.
#
# States are deliberately NOT kept. Removing a module from the Pi is meant to
# leave it safe to find powered on with nobody watching - on a shelf, mid
# reassignment to a different room - and "whatever it happened to be
# switching" is not a safe default for that, only "off" is.
KEPT_THROUGH_RESET = ("device_type", "smart_switch_id", "name", "switch_count")


def factory_reset(switches=None):
    """Forgets whose module this is, and switches everything off.

    The network and the key it was given go. Its identity stays, because
    that is not something being reset means to undo - a module that came
    back nameless could not even be adopted again without a USB cable.

    [switches] is optional only so this can still be called with nothing
    wired up; every real call site has one. Passing it means every relay is
    physically driven off before the reboot below, rather than left exactly
    as it was until the reboot's own re-init catches up a moment later -
    removing a module from the Pi is exactly the moment nobody may be
    watching what it is switching.
    """
    print("FACTORY RESET - forgetting the network and the key, "
         "switching everything off")
    if switches is not None:
        switches.set_all(False)

    kept = {}
    try:
        config = load_config()
        for key in KEPT_THROUGH_RESET:
            if config.get(key):
                kept[key] = config[key]
    except Exception:
        pass

    try:
        os.remove(CONFIG_FILE)
    except OSError:
        pass

    # Written either way, even when kept is empty - a reset must never leave
    # config.json missing. Loading nothing back later is not the same as
    # finding nothing there to load: the first is load_config() reading an
    # empty file and correctly falling in behind default_config(); the
    # second is a board that inspecting config.json over USB shows nothing
    # at all, with no way to tell "reset" from "never touched this file".
    if kept:
        print("  keeping: %s" % ", ".join("%s=%s" % (k, kept[k])
                                          for k in sorted(kept)))
    try:
        with open(CONFIG_FILE, "w") as handle:
            json.dump(kept, handle)
    except OSError as exc:
        print("  could not write config.json: %s" % exc)

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

# ------------------------------------------------------------ networking --

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
    """Joins the Pi's own network, if it is in range. None when it is not."""
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
    
    # --- POWER SAVING FIX ---
    try:
        wlan.config(pm=wlan.PM_NONE)
    except Exception:
        pass
    time.sleep(2)

    rssi = signal_of(wlan, SETUP_HOTSPOT_SSID)
    if rssi is None:
        print("The Pi's network (%s) is not on the air here." % SETUP_HOTSPOT_SSID)
    else:
        print("The Pi's network is here at %d dBm%s" % (rssi, " - weak" if rssi < -75 else ""))

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
        print("  The module can hear the Pi, so the Pi cannot hear the")
        print("  module: a Pi's radio carries much further than this one's.")
        print("  Move the module nearer the Pi, or put it on the house WiFi")
        print("  over USB instead - see esp32/CONFIG.md.")

    try:
        wlan.active(False)
    except OSError:
        pass
    return None


# ------------------------------------------------------------- bluetooth --
#
# An unclaimed board's first move: advertise over BLE and see whether the
# Pi provisions it that way before ever touching WiFi at all. Proximity is
# the only thing that lets somebody claim a board this way - the same role
# Buddy-Modules' shared password played before, and no WiFi-AP compatibility
# is involved on either side, since Bluetooth pairing has nothing to do with
# WiFi security ciphers.
#
# BLE and WiFi share one radio on this chip (time-division multiplexed), so
# this is deliberately sequential, not concurrent: advertising stops for
# good, one way or another, before join_setup_hotspot() or join_wifi() ever
# touches the radio again.

_BLE_SERVICE_UUID = bluetooth.UUID("b17d0001-8888-4c39-9a35-6f6b1a2b3c4d")
_BLE_INFO_UUID = bluetooth.UUID("b17d0002-8888-4c39-9a35-6f6b1a2b3c4d")
_BLE_CRED_UUID = bluetooth.UUID("b17d0003-8888-4c39-9a35-6f6b1a2b3c4d")
_BLE_STATUS_UUID = bluetooth.UUID("b17d0004-8888-4c39-9a35-6f6b1a2b3c4d")

# The GATT server's own per-characteristic buffer defaults to a few bytes
# regardless of the negotiated MTU - gatts_set_buffer() below is what
# actually allows a write this long. Measured: without it, a credentials
# write silently arrives truncated at ~20 bytes with no error on either
# side, which looks exactly like a malformed payload rather than what it
# actually is.
_BLE_CHAR_BUFFER = 256

_IRQ_CENTRAL_DISCONNECT = 2
_IRQ_GATTS_WRITE = 3


def _ble_advertisement(name):
    name_bytes = name.encode()
    payload = bytearray()
    payload += bytes((len(name_bytes) + 1, 0x09)) + name_bytes          # AD: complete local name
    payload += bytes((17, 0x07)) + bytes(_BLE_SERVICE_UUID)             # AD: 128-bit service UUID
    return bytes(payload)


def ble_provision(config, switches, timeout=BLE_PROVISION_SECONDS):
    """Advertises this board over BLE and waits up to [timeout] seconds to
    be provisioned. True the moment credentials are accepted and saved -
    config.json is already written when this returns, same as after an
    HTTP /provision; the caller still has to reboot onto the new network.
    """
    ble = bluetooth.BLE()
    ble.active(True)
    ble.config(mtu=_BLE_CHAR_BUFFER)

    info_char = (_BLE_INFO_UUID, bluetooth.FLAG_READ)
    cred_char = (_BLE_CRED_UUID, bluetooth.FLAG_WRITE)
    status_char = (_BLE_STATUS_UUID, bluetooth.FLAG_READ | bluetooth.FLAG_NOTIFY)
    service = (_BLE_SERVICE_UUID, (info_char, cred_char, status_char))
    ((info_handle, cred_handle, status_handle),) = \
        ble.gatts_register_services((service,))
    ble.gatts_set_buffer(cred_handle, _BLE_CHAR_BUFFER)

    ble.gatts_write(info_handle, json.dumps({
        "smart_switch_id": smart_switch_id(config),
        "device_type": device_type(config),
        "firmware_version": FIRMWARE_VERSION,
        "name": config.get("name", ""),
        "switch_count": len(switches.names),
    }).encode())
    ble.gatts_write(status_handle, b"idle")

    received = []
    name = "Buddy-%s" % binascii.hexlify(machine.unique_id())[-4:].decode()
    adv_payload = _ble_advertisement(name)

    def irq(event, data):
        if event == _IRQ_GATTS_WRITE:
            _conn_handle, attr_handle = data
            if attr_handle == cred_handle:
                received.append(ble.gatts_read(cred_handle))
        elif event == _IRQ_CENTRAL_DISCONNECT:
            # BLE advertising stops the moment a central connects, and
            # nothing else here starts it again - measured live: a scan
            # that only connects to read the info characteristic (to show
            # a board's identity before anyone claims it) leaves the board
            # silently unadvertised and unfindable for the rest of this
            # boot, long before credentials ever arrive. Only a central
            # that actually wrote credentials should be allowed to end
            # advertising for good.
            if not received:
                ble.gap_advertise(100_000, adv_payload)

    ble.irq(irq)

    ble.gap_advertise(100_000, adv_payload)
    print("BLE advertising as %s (%ds to be provisioned)" % (name, timeout))

    deadline = time.time() + timeout
    while time.time() < deadline and not received:
        time.sleep(0.2)

    ok = False
    if received:
        ble.gatts_write(status_handle, b"received")
        try:
            apply_provision(config, json.loads(received[0]))
            ble.gatts_write(status_handle, b"ok")
            ok = True
        except Exception as exc:
            print("BLE provision failed: %s" % exc)
            try:
                ble.gatts_write(status_handle, ("error: %s" % exc).encode()[:_BLE_CHAR_BUFFER])
            except Exception:
                pass

    try:
        ble.gap_advertise(None)
    except Exception:
        pass
    ble.active(False)
    return ok


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

        try:
            network.hostname(config["name"])
        except AttributeError:
            try:
                wlan.config(dhcp_hostname=config["name"])
            except Exception:
                pass

        wlan.active(True)
        
        # --- POWER SAVING FIX ---
        try:
            wlan.config(pm=wlan.PM_NONE)
        except Exception:
            pass
        time.sleep(1)

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


def announce_to_pi(wlan, config, switches):
    """Tells the Pi this board exists and is waiting to be set up.

    Buddy-Modules is hosted by its own bridge device now, not the Pi's own
    radio - the gateway on this network is that bridge, not the Pi, so
    guessing the Pi's address from wlan.ifconfig()[2] would announce to the
    wrong device entirely. pi_url (resolved over mDNS if it is a ".local"
    name - see resolve_host()) is the one address that is actually right
    regardless of which device is hosting this network.
    """
    payload = {
        "device_type": device_type(config),
        "firmware_version": FIRMWARE_VERSION,
        "smart_switch_id": smart_switch_id(config),
        "ip": wlan.ifconfig()[0],
        "name": config["name"],
        "switch_count": len(switches.names),
        "max_switches": MAX_SWITCHES,
    }
    pi_url = config.get("pi_url") or "http://buddy.local:8000"
    return post_json(pi_url.rstrip("/") + "/api/esp32/announce", payload)




_MDNS_ADDR = ("224.0.0.251", 5353)
_mdns_cache = {}


def _dns_encode_name(name):
    encoded = b""
    for part in name.split("."):
        encoded += bytes([len(part)]) + part.encode()
    return encoded + b"\x00"


def _dns_skip_name(buf, offset):
    """Advances past one (possibly compressed) DNS name without decoding
    it - all this needs to know is where the name ends, not what it says.
    """
    while True:
        length = buf[offset]
        if length == 0:
            return offset + 1
        if length & 0xC0 == 0xC0:
            return offset + 2
        offset += 1 + length


def _dns_first_a_record(reply):
    """Pulls the first IPv4 address out of an mDNS reply's answer section,
    or None if there is not one. Only ever asked one question at a time,
    so the first A record in the answers is the one that matters - no
    need to match it back against the name asked for.
    """
    ancount = (reply[6] << 8) | reply[7]
    if ancount == 0:
        return None
    offset = _dns_skip_name(reply, 12) + 4  # past the echoed question
    for _ in range(ancount):
        offset = _dns_skip_name(reply, offset)
        rtype = (reply[offset] << 8) | reply[offset + 1]
        rdlength = (reply[offset + 8] << 8) | reply[offset + 9]
        offset += 10
        if rtype == 1 and rdlength == 4:  # A record
            return ".".join(str(b) for b in reply[offset:offset + 4])
        offset += rdlength
    return None


def resolve_mdns(name, timeout=2):
    """Resolves a ".local" hostname to an IPv4 address over mDNS.

    socket.getaddrinfo() on this board only ever speaks ordinary DNS, and a
    router's own DNS server has never heard of "buddy.local" either - that
    name only means anything over multicast, a different mechanism this
    board otherwise has no way to speak. Sent with the "QU" bit set (the
    top bit of the question's class) so an ordinary mDNS responder
    (avahi-daemon, on the Pi) answers with a direct unicast reply, rather
    than this needing to join the multicast group just to see one.
    """
    question = _dns_encode_name(name) + b"\x00\x01\x80\x01"
    packet = b"\x42\x42\x00\x00\x00\x01\x00\x00\x00\x00\x00\x00" + question

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(packet, _MDNS_ADDR)
        for _ in range(3):
            reply, _ = sock.recvfrom(512)
            ip = _dns_first_a_record(reply)
            if ip:
                return ip
    except Exception as exc:
        print("mdns resolve failed for %s: %s" % (name, exc))
    finally:
        sock.close()
    return None


def resolve_host(host):
    """Turns a ".local" name into the IPv4 address it currently answers at,
    cached so every heartbeat does not repeat a multicast query of its own.
    Anything else (a literal IP, or a real DNS name) passes through
    untouched - there is nothing here for socket.getaddrinfo() to need help
    with. See forget_resolved_host() for what happens when a cached address
    stops working.
    """
    if not host.endswith(".local"):
        return host
    cached = _mdns_cache.get(host)
    if cached:
        return cached
    resolved = resolve_mdns(host)
    if resolved:
        _mdns_cache[host] = resolved
        return resolved
    return host


def forget_resolved_host(host):
    """Drops a cached mDNS answer once it stops working, so the next call
    re-resolves instead of retrying the same dead address forever - this
    is exactly what a Pi that fails over from Ethernet to WiFi backup (or
    back) looks like from a module's side: same name, different address.
    """
    _mdns_cache.pop(host, None)


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

        resolved = resolve_host(host)
        try:
            addr = socket.getaddrinfo(resolved, port)[0][-1]
            sock = socket.socket()
            sock.settimeout(timeout)
            sock.connect(addr)
        except OSError:
            # A cached mDNS answer that no longer connects is exactly what
            # the Pi moving to a different address looks like - dropping it
            # is what lets the very next heartbeat re-resolve instead of
            # retrying the same dead address every HEARTBEAT_SECONDS.
            forget_resolved_host(host)
            raise
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

    try:
        addr = socket.getaddrinfo(resolve_host(host), port)[0][-1]
        sock = socket.socket()
        sock.settimeout(timeout)
        sock.connect(addr)
    except OSError:
        forget_resolved_host(host)
        raise
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

    Read in pieces rather than as one string - measured on this chip mid
    update: the heap is already holding the socket this download is a reply
    to, and a single ~44KB allocation for the whole file failed on it
    ("memory allocation failed, allocating 42752 bytes"). The exception was
    never caught this deep, so it unwound past the point where any HTTP
    response gets sent at all - the Pi saw a connection closed with nothing
    on it, which is a worse failure than the 422 this was supposed to guard
    against.
    """
    try:
        size = os.stat(path)[6]
    except OSError:
        return False
    if size < 5000:
        return False

    found_main_def = False
    found_switch_pins = False
    overlap = b""
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(512)
            if not chunk:
                break
            window = overlap + chunk
            if b"def main():" in window:
                found_main_def = True
            if b"SWITCH_PINS" in window:
                found_switch_pins = True
            # Enough of this chunk's tail to catch either marker landing
            # split across the boundary with the next one.
            overlap = chunk[-16:]
    if not (found_main_def and found_switch_pins):
        return False

    # The one check that needs the end of the file - read just that end,
    # not everything before it.
    tail_len = min(size, 64)
    with open(path, "rb") as handle:
        handle.seek(size - tail_len)
        tail = handle.read(tail_len)
    return tail.rstrip().endswith(b"main()")


# ------------------------------------------------------------- responses --

_status_led = None


def wink_twice():
    """Two quick blinks for a request, then back to the resting state.

    Called on the way into handling any request, so the light shows the
    module being talked to. It restores solid rather than guessing: a request
    only reaches here when the module is up, and up-and-adopted is solid.
    """
    if _status_led is None:
        return
    for _ in range(2):
        _status_led.value(0)
        time.sleep(0.08)
        _status_led.value(1)
        time.sleep(0.08)


def json_response(payload):
    return 200, json.dumps(payload)


def state_payload(config, switches, ip):
    return {
        "name": config["name"],
        "device_type": device_type(config),
        "firmware_version": FIRMWARE_VERSION,
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
        self.ip = ""            # nothing to answer on until it joins
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
            "device_type": device_type(self.config),
            "firmware_version": FIRMWARE_VERSION,
            "name": self.config["name"],
            "smart_switch_id": smart_switch_id(self.config),
            "provisioned": is_provisioned(self.config),
            "switch_count": len(self.switches.names),
            "max_switches": MAX_SWITCHES,
        })

    def provision(self, body):
        if body.get("key") != PROVISION_KEY:
            return 401, json.dumps({"error": "wrong setup key"})

        try:
            apply_provision(self.config, body)
        except ValueError as exc:
            return 400, json.dumps({"error": str(exc)})

        self.reboot_after_reply = True
        return json_response({
            "ok": True,
            "name": self.config["name"],
            "device_key": self.config["device_key"],
            "switches": list(self.switches.names),
        })

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
                              "from": url, "was": FIRMWARE_VERSION})

    def configure_switches(self, body):
        """Sets how many relays this module drives."""
        if body.get("count") is None:
            return 400, json.dumps({"error": "give a count"})

        count = int(body["count"])
        if count < 1 or count > MAX_SWITCHES:
            return 400, json.dumps({
                "error": "this module has %d usable relay pins" % MAX_SWITCHES,
            })

        self.config["switch_count"] = count
        self.switches.apply(switch_names(count), self.config.get("states"))
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

        if path == "/switches":
            return self.configure_switches(body)

        if path == "/update":
            return self.update(body)

        if path == "/factory_reset":
            factory_reset(self.switches)   # does not return

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


def watch_reset_button(button, held_since, switches):
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
        factory_reset(switches)
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

    switches = Switches(switch_names(config["switch_count"]),
                        config.get("states"), remember)

    global _status_led
    led = Pin(STATUS_LED_PIN, Pin.OUT, value=0) if STATUS_LED_PIN is not None else None
    _status_led = led
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
        print("  Set both in config.json, over USB:")
        print("    mpremote fs cp my_config.json :config.json")
        print("  Until then this module will not report to the Pi.")
        print("=" * 52)
        print("")

    wlan = join_wifi(config) if is_provisioned(config) else None
    waiting_on_hotspot = False

    if wlan is None:
        # Either unclaimed, or claimed and unable to join the network it was
        # given. Both look the same from here and both have the same answer:
        # go and find the Pi. A module stranded on its own access point is a
        # module the one command that would fix it cannot reach.
        if is_provisioned(config):
            print("could not join '%s' - falling back to the Pi's network"
                  % config.get("wifi_ssid", ""))
        elif ble_provision(config, switches):
            print("provisioned over Bluetooth - restarting onto the new network")
            time.sleep(1)
            # machine.reset() alone was measured, live, to not be enough: the
            # WiFi join right after kept failing the handshake with a real
            # router (repeatedly cycling STAT_CONNECTING/STAT_WRONG_PASSWORD)
            # even with known-correct credentials on a network every other
            # device joins fine - purely from having briefly used BLE this
            # same boot, before ever touching WiFi. A short deep-sleep/wake
            # cycle instead of a plain reset does a more thorough hardware
            # re-init on this chip and reliably cleared it in that same
            # testing - a bare reset does not seem to fully release whatever
            # radio state BLE leaves behind.
            machine.deepsleep(100)

        wlan = join_setup_hotspot()
        if wlan is not None:
            waiting_on_hotspot = True
            announce_to_pi(wlan, config, switches)

    if wlan is not None:
        server.ip = wlan.ifconfig()[0]
        if waiting_on_hotspot:
            print("")
            print("=" * 52)
            print("  UNCLAIMED — on the Pi's setup network")
            print("  ADDRESS : %s" % server.ip)
            print("  It should now be listed in the portal under Smart")
            print("  switch modules > Find new module. Connect it there.")
            print("=" * 52)
            print("")
        else:
            banner(config, switches, server.ip)
    else:
        # Nothing found. The module keeps looking from the loop below - there
        # is one setup network, it is the Pi's, and raising a second one of
        # our own only ever added a name to be confused by: the Pi cannot
        # join it. Measured, both directions, with the board on the desk
        # beside the Pi.
        print("")
        print("=" * 52)
        print("  UNCLAIMED - no network yet")
        print("  Looking for '%s' every %d seconds."
              % (SETUP_HOTSPOT_SSID, HEARTBEAT_SECONDS))
        print("  It should already be up - nothing to open on the Pi any")
        print("  more. If it never appears, see esp32/CONFIG.md for the")
        print("  USB setup route.")
        print("=" * 52)
        print("")

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", 80))
    sock.listen(4)
    sock.settimeout(1)          # so the loop can watch the button and the clock
    print("listening on %s:80" % (server.ip or "no address yet"))

    held_since = None
    last_beat = 0
    failed_heartbeats = 0
    no_network_ticks = 0
    blink = False               # toggles each pass, for the setup blink

    while True:
        held_since = watch_reset_button(button, held_since, switches)

        # The resting state of the light, set every pass so the blink keeps
        # going while nothing else happens. The loop turns over about once a
        # second (the socket timeout below), a slow readable blink.
        #
        #   off      no network
        #   blink    on Buddy-Modules, waiting to be adopted (setup indicator)
        #   solid    on the house network handed over by the Pi, set up
        if led:
            if wlan is None:
                led.value(0)
            elif waiting_on_hotspot:
                blink = not blink
                led.value(1 if blink else 0)
            else:
                led.value(1)

        if time.time() - last_beat >= HEARTBEAT_SECONDS:
            last_beat = time.time()

            # Still actually on a network? The connection object survives
            # its access point disappearing, so this is the only way to
            # tell - and without it a module whose network went down keeps
            # posting into nothing for good.
            if wlan is not None and not wlan.isconnected():
                print("dropped off '%s'" % (config.get("wifi_ssid")
                                            or "the network"))
                wlan = None
                waiting_on_hotspot = False

            if wlan is None:
                # Our own network first - it is the one we belong on, and the
                # Pi's setup network may not even exist most of the time. It
                # comes and goes with whoever is adopting modules.
                if is_provisioned(config):
                    wlan = join_wifi(config, attempts=1)
                if wlan is not None:
                    waiting_on_hotspot = False
                else:
                    wlan = join_setup_hotspot(timeout=6)
                    waiting_on_hotspot = wlan is not None

                if wlan is not None:
                    server.ip = wlan.ifconfig()[0]
                    print("back on the air - %s" % server.ip)
                    no_network_ticks = 0
                    if waiting_on_hotspot:
                        announce_to_pi(wlan, config, switches)

            if wlan is None:
                no_network_ticks += 1
                print("still no network (%d heartbeat%s in a row)"
                      % (no_network_ticks, "" if no_network_ticks == 1 else "s"))
                if no_network_ticks >= NO_NETWORK_TICKS_BEFORE_REBOOT:
                    print("no network for %d heartbeats - rebooting to reset "
                          "the radio" % no_network_ticks)
                    time.sleep(1)
                    machine.reset()

            elif waiting_on_hotspot:
                # On the setup network, waiting to be adopted. Keep saying
                # hello, or the portal forgets us while somebody is still
                # deciding. Not adopted yet, so the light stays blinking.
                announce_to_pi(wlan, config, switches)

            elif config.get("pi_url") and has_identity(config):
                # Where we belong: report in, so the Pi can keep calling us
                # by name however the address moves. Whether the report
                # landed is what the solid light means.
                payload = state_payload(config, switches, server.ip)
                payload["key"] = config.get("device_key", "")
                reached = post_json(
                    config["pi_url"].rstrip("/") + "/api/esp32/register",
                    payload)

                if reached:
                    failed_heartbeats = 0
                else:
                    failed_heartbeats += 1
                    print("heartbeat: %d unanswered in a row"
                          % failed_heartbeats)
                    if failed_heartbeats >= FAILED_HEARTBEATS_BEFORE_RECONNECT:
                        print("radio still says connected but the Pi never "
                              "answers - dropping the association so the "
                              "next pass can rejoin from nothing")
                        failed_heartbeats = 0
                        try:
                            wlan.disconnect()
                        except OSError:
                            pass
                        wlan = None
                        waiting_on_hotspot = False

        try:
            conn, _ = sock.accept()
        except OSError:
            continue            # the one-second timeout; back round the loop

        # Every request is handled to completion before the next accept() -
        # one socket, one thread, so this loop is already the queue: whoever
        # is waiting simply waits for their turn. What was missing is a
        # bound on how long a turn can take. An accepted connection has no
        # timeout of its own by default, so a client that connects and then
        # sends nothing - a stalled network, a bare port probe - left recv()
        # blocking forever, which held up not just that request but every
        # request behind it, the heartbeat, and the reset button. A few
        # seconds is generous for a real HTTP client and short enough that
        # one bad connection cannot indefinitely stall the ones queued
        # behind it.
        conn.settimeout(5)

        try:
            method, path, body = read_request(conn)
            if path is None:
                continue
            wink_twice()                 # show we were called, then back to solid
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
    print("  TYPE     : %s   (firmware %s)"
          % (device_type(config), FIRMWARE_VERSION))
    print("  ID       : %s" % (smart_switch_id(config) or "-"))
    print("  IP       : %s   (the Pi calls it by name, not this)" % ip)
    print("  PI       : %s" % (config.get("pi_url") or "not set"))
    print("-" * 52)
    print("  SWITCH                GPIO")
    for name in switches.names:
        print("  %-20s  %d" % (name, switches.gpio_for(name)))
    print("=" * 52)
    print("")


main()
