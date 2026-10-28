"""Per-camera on/off (#39): Arlo's privacy mode.

A camera reports `privacyActive` in its properties; true means the camera is
off. pyaarlo's ArloCamera.turn_on/turn_off set it with a notify to the
camera's gateway on resource `cameras/<id>`.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from aioresponses import aioresponses

from custom_components.eisenberg.switch import CameraPowerSwitch, build_switches
from eisenberg import DeviceInfo
from eisenberg.client import MYAPI_BASE, EisenbergClient
from eisenberg.models import DeviceState


def _device(device_id: str, properties: dict[str, Any] | None) -> DeviceInfo:
    raw: dict[str, Any] = {
        "deviceId": device_id,
        "deviceName": f"Camera {device_id}",
        "modelId": "VMC2052A",
        "deviceType": "camera",
        "xCloudId": "CLOUD",
        "parentId": "BASE",
    }
    if properties is not None:
        raw["properties"] = properties
    return DeviceInfo.model_validate(raw)


def _switch(device: DeviceInfo, states: dict[str, DeviceState]) -> CameraPowerSwitch:
    sw = CameraPowerSwitch.__new__(CameraPowerSwitch)
    sw.coordinator = SimpleNamespace(device_states=states)  # type: ignore[attr-defined]
    sw._device = device
    sw.async_write_ha_state = lambda: None  # type: ignore[method-assign]
    return sw


class TestBuildSwitches:
    def test_power_switch_only_for_cameras_reporting_privacy(self) -> None:
        with_privacy = _device("CAM-A", {"privacyActive": False})
        without = _device("CAM-B", {"batteryLevel": 50})
        coord = SimpleNamespace(cameras=[with_privacy, without], device_states={})
        powers = [s for s in build_switches(coord) if isinstance(s, CameraPowerSwitch)]  # type: ignore[arg-type]
        assert [s._device.device_id for s in powers] == ["CAM-A"]

    def test_power_switch_when_rest_reports_no_properties(self) -> None:
        # VMB4000 accounts return empty REST properties for base-stationed
        # cameras; their state only arrives over MQTT (#39).
        empty = _device("CAM-E", {})
        absent = _device("CAM-N", None)
        coord = SimpleNamespace(cameras=[empty, absent], device_states={})
        powers = [s for s in build_switches(coord) if isinstance(s, CameraPowerSwitch)]  # type: ignore[arg-type]
        assert [s._device.device_id for s in powers] == ["CAM-E", "CAM-N"]

    def test_state_unknown_until_mqtt_reports_privacy(self) -> None:
        sw = _switch(_device("CAM", {}), {})
        sw._handle_coordinator_update()
        assert sw._attr_is_on is None


class TestCameraPowerState:
    def test_on_when_privacy_inactive(self) -> None:
        sw = _switch(_device("CAM", {"privacyActive": False}), {})
        sw._handle_coordinator_update()
        assert sw.is_on is True

    def test_mqtt_state_wins_over_startup_properties(self) -> None:
        state = DeviceState.model_validate({"privacyActive": True})
        sw = _switch(_device("CAM", {"privacyActive": False}), {"CAM": state})
        sw._handle_coordinator_update()
        assert sw.is_on is False


class TestSetPrivacy:
    async def test_notify_to_gateway_on_camera_resource(self) -> None:
        client = EisenbergClient(email="e", password="p", device_id="d")
        client.token = "T"
        client.user_id = "USER"
        client._device_cloud_ids = {"CAM": "CLOUD"}
        client._device_parent_ids = {"CAM": "BASE"}
        url = f"{MYAPI_BASE}/hmsweb/users/devices/notify/BASE"
        with aioresponses() as m:
            m.post(url, payload={"success": True})
            async with client:
                await client.set_privacy("CAM", active=True)
            body = next(iter(m.requests.values()))[0].kwargs["json"]
        assert body["to"] == "BASE"
        assert body["resource"] == "cameras/CAM"
        assert body["properties"] == {"privacyActive": True}
