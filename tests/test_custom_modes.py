"""Arlo custom modes (#39).

Shapes captured live 2026-10-22 from an account where a custom mode was made
in the iOS app. Custom modes hang off each gateway (a base station, or a
base-less camera), not the location:

    GET /hmsweb/automation/v3?locationId=… → data.customModes.properties =
        {"<gatewayId>": {"disarmed": {"name": ""},
                          "armed": {"devices": {…}, "name": ""},
                          "<uuid>": {"devices": {…}, "name": "Porco Dio"}}}

and a location is in one when its activeMode reads

    {"mode": "custom", "custom": {"<gatewayId>": "<mode key>"}}

The keys `armed`/`disarmed` are the pre-v3 modes Arlo migrates in with an
empty name; like pyaarlo, the key stands in for the name.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.eisenberg.coordinator import EisenbergCoordinator
from custom_components.eisenberg.select import SecurityModeSelect
from eisenberg.models import LocationState, custom_mode_labels

GW = "GATEWAY1"
UUID = "808a1433-b7ba-47fa-913a-4c0e1812b6fa"
LOC = "loc-1"

CUSTOM_MODES_RAW: dict[str, Any] = {
    GW: {
        "disarmed": {"name": ""},
        "armed": {"devices": {GW: {"motionStart": {"enabled": True}}}, "name": ""},
        UUID: {"devices": {GW: {"motionStart": {"enabled": True}}}, "name": "Night"},
    }
}


def _location() -> LocationState:
    loc = LocationState(location_id=LOC, gateway_device_ids=[GW], active_mode="standby")
    loc.custom_modes = custom_mode_labels(CUSTOM_MODES_RAW)
    return loc


def _coord(location: LocationState) -> EisenbergCoordinator:
    coord = EisenbergCoordinator.__new__(EisenbergCoordinator)
    coord.locations = {location.location_id: location}
    coord._devices = []
    coord.data = {}
    coord._push_to_entities = lambda: None  # type: ignore[method-assign]
    return coord


class TestCustomModeLabels:
    def test_named_mode_uses_its_name(self) -> None:
        assert custom_mode_labels(CUSTOM_MODES_RAW)[GW][UUID] == "Night"

    def test_nameless_migrated_modes_are_left_out(self) -> None:
        assert custom_mode_labels(CUSTOM_MODES_RAW)[GW] == {UUID: "Night"}

    def test_absent_section_is_empty(self) -> None:
        assert custom_mode_labels({}) == {}


class TestLocationModeLabel:
    def test_standard_mode_label_is_the_mode(self) -> None:
        loc = _location()
        assert loc.mode_label == "standby"

    def test_custom_mode_label_resolves_name(self) -> None:
        loc = _location()
        loc.set_mode("custom", {GW: UUID})
        assert loc.mode_label == "Night"

    def test_options_list_standard_then_named_custom(self) -> None:
        assert _location().mode_options == ["armAway", "armHome", "standby", "Night"]

    def test_active_nameless_mode_is_shown_by_key(self) -> None:
        """The app can still run a migrated mode; the select must show it."""
        loc = _location()
        loc.set_mode("custom", {GW: "armed"})
        assert loc.mode_label == "armed"
        assert loc.mode_options == ["armAway", "armHome", "standby", "Night", "armed"]

    def test_custom_target_maps_label_to_gateway_key(self) -> None:
        assert _location().custom_target("Night") == {GW: UUID}

    def test_custom_target_unknown_label_is_empty(self) -> None:
        assert _location().custom_target("Nope") == {}


class TestModeEvents:
    async def test_active_mode_custom_event_selects_named_mode(self) -> None:
        loc = _location()
        coord = _coord(loc)
        await coord._handle_active_mode(
            "u/USER/in/automation/activeMode/is",
            {
                "resource": "automation/activeMode",
                "locationId": LOC,
                "action": "is",
                "properties": {
                    "schemaVersion": 1,
                    "properties": {"mode": "custom", "custom": {GW: UUID}},
                    "revision": 1792647689392,
                },
            },
        )
        assert loc.mode_label == "Night"

    async def test_feed_mode_change_with_custom_key_does_not_clobber(self) -> None:
        """The feed names only one gateway's key; activeMode is the authority."""
        loc = _location()
        loc.set_mode("custom", {GW: UUID})
        coord = _coord(loc)
        await coord._handle_feed(
            "u/USER/in/feed/live",
            {
                "resource": "feedNotification",
                "type": "modeChange",
                "deviceId": GW,
                "locationId": LOC,
                "activeMode": UUID,
                "utcCreatedDate": 1792647689593,
                "action": "new",
            },
        )
        assert loc.mode_label == "Night"

    async def test_custom_modes_update_refreshes_labels(self) -> None:
        loc = _location()
        coord = _coord(loc)
        renamed = {GW: {UUID: {"name": "Late night"}}}
        await coord._handle_custom_modes(
            "u/USER/in/automation/customModes/is",
            {
                "resource": "automation/customModes",
                "locationId": LOC,
                "action": "is",
                "properties": {"schemaVersion": 1, "properties": renamed, "revision": 2},
            },
        )
        assert loc.custom_target("Late night") == {GW: UUID}


class TestSetCustomMode:
    async def _set(self, option: str) -> tuple[list[tuple[str, str, int, Any]], LocationState]:
        loc = _location()
        loc.mode_revision = 5
        coord = _coord(loc)
        calls: list[tuple[str, str, int, Any]] = []

        async def _set_active_mode(
            location_id: str, mode: str, revision: int, custom: dict[str, str] | None
        ) -> Any:
            calls.append((location_id, mode, revision, custom))
            return SimpleNamespace(
                revision=revision + 1,
                properties=SimpleNamespace(mode=mode, custom=custom or {}),
            )

        coord.client = SimpleNamespace(set_active_mode=_set_active_mode)  # type: ignore[attr-defined]

        async def _passthrough(_name: str, op: Any) -> Any:
            return await op()

        coord.call_with_session_retry = _passthrough  # type: ignore[method-assign]
        await coord.async_set_active_mode(LOC, option)
        return calls, loc

    async def test_custom_label_sends_custom_body(self) -> None:
        calls, loc = await self._set("Night")
        assert calls == [(LOC, "custom", 5, {GW: UUID})]
        assert loc.mode_label == "Night"

    async def test_standard_mode_sends_no_custom(self) -> None:
        calls, loc = await self._set("armAway")
        assert calls == [(LOC, "armAway", 5, None)]
        assert loc.mode_label == "armAway"

    async def test_unknown_option_raises(self) -> None:
        with pytest.raises(ValueError, match="Nope"):
            await self._set("Nope")


class TestSelectOptions:
    def test_select_offers_custom_modes_and_shows_active_one(self) -> None:
        loc = _location()
        loc.set_mode("custom", {GW: UUID})
        sel = SecurityModeSelect.__new__(SecurityModeSelect)
        sel.coordinator = SimpleNamespace(locations={LOC: loc})  # type: ignore[attr-defined]
        sel._location_id = LOC
        sel.async_write_ha_state = lambda: None  # type: ignore[method-assign]
        sel._handle_coordinator_update()
        assert "Night" in sel.options
        assert sel.current_option == "Night"
