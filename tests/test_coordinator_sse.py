"""SSE supplement for resources the MQTT broker will not deliver (issue #38).

A Gen2 doorbell publishes its instant motion as resource `cameras/<id>`, but
some accounts' brokers refuse the `d/{xCloudId}/out/#` wildcard and declare
only `doorbells/<id>/#` for it. When the SUBACK leaves a device resource
uncovered, the coordinator opens Arlo's SSE stream and feeds it — for those
resources only — through the same handlers MQTT uses. Everything MQTT already
delivers is dropped, so nothing is handled twice.
"""

from __future__ import annotations

from custom_components.eisenberg.coordinator import EisenbergCoordinator
from eisenberg import DeviceInfo
from eisenberg.models import SubscribeOutcome, TopicResult

CLOUD = "XC"
DOOR = "DOOR1"
CAM = "CAM1"


def _dev(device_id: str, device_type: str, topics: list[str]) -> DeviceInfo:
    return DeviceInfo.model_validate(
        {
            "deviceId": device_id,
            "deviceName": f"dev {device_id}",
            "modelId": "AVD3001A",
            "deviceType": device_type,
            "xCloudId": CLOUD,
            "allowedMqttTopics": topics,
        }
    )


DOORBELL = _dev(DOOR, "doorbell", [f"d/{CLOUD}/out/doorbells/{DOOR}/#"])
CAMERA = _dev(CAM, "camera", [f"d/{CLOUD}/out/cameras/{CAM}/#"])
BASE = _dev("BASE", "basestation", [])


def _outcome(granted: list[str], refused: list[str]) -> SubscribeOutcome:
    return SubscribeOutcome(
        results=[TopicResult(topic=t, code=0, granted=True) for t in granted]
        + [TopicResult(topic=t, code=0x80, granted=False) for t in refused]
    )


def _coord(devices: list[DeviceInfo]) -> EisenbergCoordinator:
    coord = EisenbergCoordinator.__new__(EisenbergCoordinator)
    coord._devices = devices
    coord.device_states = {}
    coord.spotlight_states = {}
    coord.data = {}
    coord._push_to_entities = lambda: None  # type: ignore[method-assign]
    return coord


class TestGapResources:
    def test_wildcard_granted_leaves_no_gap(self) -> None:
        coord = _coord([DOORBELL, CAMERA])
        outcome = _outcome([f"d/{CLOUD}/out/#"], [])
        assert coord._sse_gap_resources(outcome) == {}

    def test_doorbell_cameras_resource_uncovered(self) -> None:
        """Reporter's account: wildcard refused, cameras/<doorbell> refused."""
        coord = _coord([DOORBELL, CAMERA])
        outcome = _outcome(
            [*DOORBELL.allowed_mqtt_topics, *CAMERA.allowed_mqtt_topics],
            [f"d/{CLOUD}/out/#", DOORBELL.camera_resource_filter],
        )
        assert coord._sse_gap_resources(outcome) == {f"cameras/{DOOR}": CLOUD}

    def test_explicit_cameras_filter_granted_closes_gap(self) -> None:
        coord = _coord([DOORBELL])
        outcome = _outcome(
            [*DOORBELL.allowed_mqtt_topics, DOORBELL.camera_resource_filter],
            [f"d/{CLOUD}/out/#"],
        )
        assert coord._sse_gap_resources(outcome) == {}

    def test_base_station_never_a_gap(self) -> None:
        coord = _coord([BASE])
        assert coord._sse_gap_resources(_outcome([], [f"d/{CLOUD}/out/#"])) == {}


class TestHandleSSEEvent:
    def _gap_coord(self) -> EisenbergCoordinator:
        coord = _coord([DOORBELL])
        coord._set_sse_resources({f"cameras/{DOOR}": CLOUD})
        return coord

    async def test_gap_motion_reaches_device_state(self) -> None:
        coord = self._gap_coord()
        await coord._handle_sse_event(
            {
                "from": DOOR,
                "resource": f"cameras/{DOOR}",
                "action": "is",
                "properties": {"motionDetected": True},
            }
        )
        assert coord.device_states[DOOR].motion_detected is True

    async def test_covered_resource_dropped(self) -> None:
        coord = self._gap_coord()
        await coord._handle_sse_event(
            {
                "from": DOOR,
                "resource": f"doorbells/{DOOR}",
                "action": "is",
                "properties": {"motionDetected": True},
            }
        )
        assert DOOR not in coord.device_states

    async def test_user_events_dropped(self) -> None:
        coord = self._gap_coord()
        await coord._handle_sse_event({"resource": "mediaUploadNotification", "action": "is"})
        assert coord.device_states == {}

    async def test_payload_without_action_dropped(self) -> None:
        coord = self._gap_coord()
        await coord._handle_sse_event({"resource": f"cameras/{DOOR}", "properties": {}})
        assert coord.device_states == {}
