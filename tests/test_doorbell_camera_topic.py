"""Doorbell motion published under the `cameras` resource (issue #38).

A Gen2 video doorbell (AVD3001A) on direct WiFi publishes its instant
`motionDetected` as resource `cameras/<id>`, but its `allowedMqttTopics` only
declare `doorbells/<id>/#`. On accounts where the broker refuses the broad
`d/{xCloudId}/out/#` wildcard, nothing else covers that topic, so Motion only
caught up ~20 s later with the AI "Person" event. We subscribe to the doorbell's
`cameras/<id>/#` filter explicitly and report the broker's verdict at INFO, so
one startup log says whether this account lets it through.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING

from custom_components.eisenberg.coordinator import EisenbergCoordinator
from eisenberg import DeviceInfo
from eisenberg.models import SubscribeOutcome, TopicResult

if TYPE_CHECKING:
    import pytest


def _dev(device_id: str, device_type: str | None, x_cloud_id: str = "XC") -> DeviceInfo:
    raw: dict[str, object] = {
        "deviceId": device_id,
        "deviceName": f"dev {device_id}",
        "modelId": "AVD3001A",
        "xCloudId": x_cloud_id,
        "allowedMqttTopics": [f"d/{x_cloud_id}/out/doorbells/{device_id}/#"],
    }
    if device_type is not None:
        raw["deviceType"] = device_type
    return DeviceInfo.model_validate(raw)


def _coord(devices: list[DeviceInfo]) -> EisenbergCoordinator:
    coord = EisenbergCoordinator.__new__(EisenbergCoordinator)
    coord._devices = devices
    return coord


class TestIsDoorbell:
    def test_doorbell(self) -> None:
        assert _dev("D", "doorbell").is_doorbell is True

    def test_case_insensitive(self) -> None:
        assert _dev("D", "Doorbell").is_doorbell is True

    def test_camera_is_not(self) -> None:
        assert _dev("C", "camera").is_doorbell is False

    def test_absent_type_is_not(self) -> None:
        assert _dev("C", None).is_doorbell is False


class TestCameraResourceFilter:
    def test_built_from_own_cloud_and_id(self) -> None:
        dev = _dev("DOOR1", "doorbell", x_cloud_id="XC-DOOR")
        assert dev.camera_resource_filter == "d/XC-DOOR/out/cameras/DOOR1/#"


class TestExtraTopics:
    def test_doorbell_gets_cameras_filter(self) -> None:
        coord = _coord([_dev("DOOR1", "doorbell")])
        topics = coord._mqtt_extra_topics()
        assert "d/XC/out/doorbells/DOOR1/#" in topics
        assert "d/XC/out/cameras/DOOR1/#" in topics

    def test_camera_gets_nothing_extra(self) -> None:
        cam = _dev("CAM1", "camera")
        coord = _coord([cam])
        assert coord._mqtt_extra_topics() == cam.allowed_mqtt_topics

    def test_not_duplicated_when_already_declared(self) -> None:
        dev = _dev("DOOR1", "doorbell")
        dev.allowed_mqtt_topics.append(dev.camera_resource_filter)
        coord = _coord([dev])
        assert coord._mqtt_extra_topics().count(dev.camera_resource_filter) == 1


class TestSubackVerdict:
    def _log(
        self,
        coord: EisenbergCoordinator,
        results: list[TopicResult],
        caplog: pytest.LogCaptureFixture,
    ) -> str:
        coord.client = SimpleNamespace(user_id="USER")  # type: ignore[attr-defined]
        coord._mqtt = SimpleNamespace(subscribe_outcome=SubscribeOutcome(results=results))  # type: ignore[attr-defined]
        with caplog.at_level(logging.INFO):
            coord._log_subscribe_outcome()
        return caplog.text

    def test_refused_reported_at_info(self, caplog: pytest.LogCaptureFixture) -> None:
        dev = _dev("DOOR1", "doorbell")
        text = self._log(
            _coord([dev]),
            [TopicResult(topic=dev.camera_resource_filter, code=0x80, granted=False)],
            caplog,
        )
        assert f"{dev.camera_resource_filter} = REFUSED" in text

    def test_granted_reported_at_info(self, caplog: pytest.LogCaptureFixture) -> None:
        dev = _dev("DOOR1", "doorbell")
        text = self._log(
            _coord([dev]),
            [TopicResult(topic=dev.camera_resource_filter, code=0, granted=True)],
            caplog,
        )
        assert f"{dev.camera_resource_filter} = GRANTED" in text

    def test_cameras_get_no_verdict_line(self, caplog: pytest.LogCaptureFixture) -> None:
        cam = _dev("CAM1", "camera")
        text = self._log(_coord([cam]), [], caplog)
        assert cam.camera_resource_filter not in text
