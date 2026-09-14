"""Apple TV Touch — clickpad gestures with controllable timing.

Home Assistant's `remote.send_command` for Apple TV only exposes HID *buttons*
with a fixed 1-second hold (pyatv's default). On tvOS that is long enough to
open the context menu AND trip tvOS's key-repeat on the menu that just opened
(observed 2026-09-12: pause/play churn after a held select in Music).

This integration borrows the apple_tv integration's live pyatv connection and
exposes the *touch* surface of the Siri Remote instead: a clickpad click, a
press/hold/release with a caller-chosen duration, and swipes. Touch clicks
are what tvOS apps expect from the Siri Remote (e.g. play/pause on Music's
Now Playing, select elsewhere).

Services (all take an optional `entity_id` of any apple_tv entity to pick the
box; with a single Apple TV it is auto-selected):
  atv_touch.click        — clickpad click (single / double)
  atv_touch.hold         — press, wait `hold_ms`, release (default 600 ms)
  atv_touch.swipe        — swipe between two points over `duration_ms`
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, entity_registry as er

from pyatv.const import Protocol, TouchAction
from pyatv.protocols.companion.api import HidCommand

DOMAIN = "atv_touch"
APPLE_TV_DOMAIN = "apple_tv"
_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

CENTER = 500  # pyatv touchpad coordinates run 0..1000 on both axes

CLICK_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_ENTITY_ID): cv.entity_id,
        vol.Optional("action", default="single"): vol.In(["single", "double"]),
        vol.Optional("x", default=CENTER): vol.All(vol.Coerce(int), vol.Range(min=0, max=1000)),
        vol.Optional("y", default=CENTER): vol.All(vol.Coerce(int), vol.Range(min=0, max=1000)),
    }
)
HOLD_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_ENTITY_ID): cv.entity_id,
        vol.Optional("hold_ms", default=600): vol.All(vol.Coerce(int), vol.Range(min=50, max=5000)),
        vol.Optional("x", default=CENTER): vol.All(vol.Coerce(int), vol.Range(min=0, max=1000)),
        vol.Optional("y", default=CENTER): vol.All(vol.Coerce(int), vol.Range(min=0, max=1000)),
    }
)
BUTTONS = {
    "select": HidCommand.Select,
    "menu": HidCommand.Menu,
    "home": HidCommand.Home,
    "up": HidCommand.Up,
    "down": HidCommand.Down,
    "left": HidCommand.Left,
    "right": HidCommand.Right,
    "play_pause": HidCommand.PlayPause,
}
BUTTON_HOLD_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_ENTITY_ID): cv.entity_id,
        vol.Optional("button", default="select"): vol.In(sorted(BUTTONS)),
        vol.Optional("hold_ms", default=700): vol.All(vol.Coerce(int), vol.Range(min=50, max=5000)),
    }
)
SWIPE_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_ENTITY_ID): cv.entity_id,
        vol.Required("start_x"): vol.All(vol.Coerce(int), vol.Range(min=0, max=1000)),
        vol.Required("start_y"): vol.All(vol.Coerce(int), vol.Range(min=0, max=1000)),
        vol.Required("end_x"): vol.All(vol.Coerce(int), vol.Range(min=0, max=1000)),
        vol.Required("end_y"): vol.All(vol.Coerce(int), vol.Range(min=0, max=1000)),
        vol.Optional("duration_ms", default=300): vol.All(vol.Coerce(int), vol.Range(min=50, max=5000)),
    }
)


def _resolve_atv(hass: HomeAssistant, entity_id: str | None):
    """Find the live pyatv object behind an apple_tv entity (or the only box)."""
    entries = [
        e
        for e in hass.config_entries.async_entries(APPLE_TV_DOMAIN)
        if e.state is ConfigEntryState.LOADED
    ]
    if entity_id:
        reg = er.async_get(hass)
        ent = reg.async_get(entity_id)
        if ent is None or ent.platform != APPLE_TV_DOMAIN:
            raise HomeAssistantError(f"{entity_id} is not an apple_tv entity")
        entries = [e for e in entries if e.entry_id == ent.config_entry_id]
    if not entries:
        raise HomeAssistantError("No loaded Apple TV config entry matches")
    if len(entries) > 1:
        raise HomeAssistantError(
            "Several Apple TVs are configured — pass entity_id to pick one"
        )
    manager = entries[0].runtime_data
    atv = getattr(manager, "atv", None)
    if atv is None:
        raise HomeAssistantError("Apple TV is not connected right now")
    return atv


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Register the touch services."""

    async def _click(call: ServiceCall) -> None:
        # pyatv's own `touch.click()` places the clickpad click at
        # (TOUCHPAD_WIDTH, TOUCHPAD_HEIGHT) = the bottom-RIGHT CORNER of the
        # pad. tvOS reads a click at the pad's edge like a Siri Remote edge
        # press (a directional click), so in grid UIs (YouTube, Plex) the
        # "select" sometimes just moved focus and needed several presses
        # (observed 2026-09-14: three OKs to start a YouTube video). Same
        # sequence as pyatv — HID Select button down/up + a touch Click — but
        # the touch lands at the CENTRE of the pad (or the caller's x/y).
        atv = _resolve_atv(hass, call.data.get(ATTR_ENTITY_ID))
        rc = atv.remote_control.get(Protocol.Companion)
        api = getattr(rc, "api", None)
        if api is None:
            raise HomeAssistantError(
                "Apple TV is not connected over the Companion protocol"
            )
        x, y = call.data["x"], call.data["y"]
        count = 2 if call.data["action"] == "double" else 1
        _LOGGER.debug("touch click x%d at %d,%d", count, x, y)
        for _ in range(count):
            await api._send_command("_hidC", {"_hBtS": 1, "_hidC": HidCommand.Select.value})
            await asyncio.sleep(0.02)
            await api._send_command("_hidC", {"_hBtS": 2, "_hidC": HidCommand.Select.value})
            await api.hid_event(x, y, TouchAction.Click)

    async def _hold(call: ServiceCall) -> None:
        atv = _resolve_atv(hass, call.data.get(ATTR_ENTITY_ID))
        x, y, ms = call.data["x"], call.data["y"], call.data["hold_ms"]
        _LOGGER.debug("touch hold %d ms at %d,%d", ms, x, y)
        await atv.touch.action(x, y, TouchAction.Press)
        try:
            await asyncio.sleep(ms / 1000)
        finally:
            await atv.touch.action(x, y, TouchAction.Release)

    async def _swipe(call: ServiceCall) -> None:
        atv = _resolve_atv(hass, call.data.get(ATTR_ENTITY_ID))
        d = call.data
        _LOGGER.debug("touch swipe %s", dict(d))
        await atv.touch.swipe(
            d["start_x"], d["start_y"], d["end_x"], d["end_y"], d["duration_ms"]
        )

    async def _button_hold(call: ServiceCall) -> None:
        # A held HID *button* (not a touch): this is what tvOS apps read as a
        # long-press for item context menus (YouTube, Plex, ...). pyatv's own
        # hold is a fixed 1 s, long enough to trip tvOS key-repeat; here the
        # press/release timing is the caller's.
        atv = _resolve_atv(hass, call.data.get(ATTR_ENTITY_ID))
        rc = atv.remote_control.get(Protocol.Companion)
        api = getattr(rc, "api", None)
        if api is None:
            raise HomeAssistantError(
                "Apple TV is not connected over the Companion protocol"
            )
        cmd, ms = BUTTONS[call.data["button"]], call.data["hold_ms"]
        _LOGGER.debug("button hold %s for %d ms", cmd, ms)
        await api.hid_command(True, cmd)
        try:
            await asyncio.sleep(ms / 1000)
        finally:
            await api.hid_command(False, cmd)

    hass.services.async_register(DOMAIN, "button_hold", _button_hold, schema=BUTTON_HOLD_SCHEMA)
    hass.services.async_register(DOMAIN, "click", _click, schema=CLICK_SCHEMA)
    hass.services.async_register(DOMAIN, "hold", _hold, schema=HOLD_SCHEMA)
    hass.services.async_register(DOMAIN, "swipe", _swipe, schema=SWIPE_SCHEMA)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Nothing per-entry: the services above do all the work."""
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return True
