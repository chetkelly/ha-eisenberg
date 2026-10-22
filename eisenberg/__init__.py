"""Eisenberg -- async Python client for the Arlo camera API."""

from .client import EVENT_STREAM_URL, EisenbergClient
from .exceptions import (
    APIError,
    AuthenticationError,
    EisenbergError,
    MfaRequired,
    MQTTConnectionError,
    RateLimitedError,
    SessionExpiredError,
    TransientAPIError,
)
from .models import (
    ActiveMode,
    ArloMode,
    Connectivity,
    DeviceInfo,
    DeviceState,
    FactorType,
    LastImageSnapshotAvailable,
    MediaUpload,
    ModeChangeEvent,
    MotionEvent,
    SecondFactor,
    SirenState,
    SnapshotAvailable,
    SpotlightState,
    StreamResponse,
)
from .mqtt import MQTTEventStream
from .sse import SSEEventStream

__all__ = [
    "EVENT_STREAM_URL",
    "APIError",
    "ActiveMode",
    "ArloMode",
    "AuthenticationError",
    "Connectivity",
    "DeviceInfo",
    "DeviceState",
    "EisenbergClient",
    "EisenbergError",
    "FactorType",
    "LastImageSnapshotAvailable",
    "MQTTConnectionError",
    "MQTTEventStream",
    "MediaUpload",
    "MfaRequired",
    "ModeChangeEvent",
    "MotionEvent",
    "RateLimitedError",
    "SSEEventStream",
    "SecondFactor",
    "SessionExpiredError",
    "SirenState",
    "SnapshotAvailable",
    "SpotlightState",
    "StreamResponse",
    "TransientAPIError",
]
