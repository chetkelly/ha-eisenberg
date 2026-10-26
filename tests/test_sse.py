"""Arlo SSE event stream (`/hmsweb/client/subscribe`), the supplement to MQTT
for accounts whose broker refuses a topic a device publishes on (issue #38).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

import aiohttp

from eisenberg.sse import SSEEventStream, SSEParser

if TYPE_CHECKING:
    import pytest


class TestSSEParser:
    def test_single_event(self) -> None:
        assert SSEParser().feed(b'data: {"a": 1}\n\n') == ['{"a": 1}']

    def test_event_split_across_chunks(self) -> None:
        parser = SSEParser()
        assert parser.feed(b'data: {"a"') == []
        assert parser.feed(b": 1}\n") == []
        assert parser.feed(b"\n") == ['{"a": 1}']

    def test_crlf_line_endings(self) -> None:
        assert SSEParser().feed(b"data: x\r\n\r\ndata: y\r\n\r\n") == ["x", "y"]

    def test_multiline_data_joined_with_newline(self) -> None:
        assert SSEParser().feed(b"data: a\ndata: b\n\n") == ["a\nb"]

    def test_comments_and_other_fields_ignored(self) -> None:
        assert SSEParser().feed(b": keepalive\nevent: message\nid: 7\ndata: x\n\n") == ["x"]

    def test_event_without_data_dropped(self) -> None:
        assert SSEParser().feed(b": ping\n\n") == []

    def test_utf8_split_mid_character(self) -> None:
        raw = "data: caffè\n\n".encode()
        cut = raw.index(b"\xc3") + 1
        parser = SSEParser()
        assert parser.feed(raw[:cut]) == []
        assert parser.feed(raw[cut:]) == ["caffè"]


class _FakeContent:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def iter_any(self) -> Any:
        for chunk in self._chunks:
            yield chunk


class _FakeResponse:
    def __init__(self, status: int, chunks: list[bytes]) -> None:
        self.status = status
        self.content = _FakeContent(chunks)
        self.released = False

    def release(self) -> None:
        self.released = True


class _FakeSession:
    """Serves one scripted response per GET; blocks forever when exhausted."""

    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = responses
        self.requests: list[dict[str, str]] = []

    async def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.requests.append(kwargs["headers"])
        if not self._responses:
            await asyncio.Event().wait()
        return self._responses.pop(0)


def _frame(payload: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(payload)}\n\n".encode()


async def _run(session: _FakeSession, tokens: list[str], until: int) -> list[dict[str, Any]]:
    received: list[dict[str, Any]] = []
    done = asyncio.Event()
    token_iter = iter(tokens)

    async def on_event(payload: dict[str, Any]) -> None:
        received.append(payload)
        if len(received) >= until:
            done.set()

    stream = SSEEventStream(
        url="https://example/hmsweb/client/subscribe",
        headers_factory=lambda: {"Authorization": next(token_iter)},
        http_session=session,  # type: ignore[arg-type]
        on_event=on_event,
        backoff=(0.0,),
        read_timeout=5.0,
    )
    stream.start()
    try:
        await asyncio.wait_for(done.wait(), timeout=2)
    finally:
        await stream.stop()
    return received


class TestSSEEventStream:
    async def test_delivers_payloads_and_skips_connected(self) -> None:
        motion = {"resource": "cameras/D", "action": "is", "properties": {"motionDetected": True}}
        session = _FakeSession(
            [_FakeResponse(200, [_frame({"status": "connected"}), _frame(motion)])]
        )
        assert await _run(session, ["T1"], until=1) == [motion]

    async def test_reconnects_with_fresh_headers_after_logout(self) -> None:
        first = {"resource": "cameras/D", "action": "is", "properties": {}}
        second = {"resource": "cameras/D", "action": "is", "properties": {"motionDetected": True}}
        session = _FakeSession(
            [
                _FakeResponse(200, [_frame(first), _frame({"action": "logout"}), _frame(second)]),
                _FakeResponse(200, [_frame(second)]),
            ]
        )
        received = await _run(session, ["T1", "T2"], until=2)
        # The frame after logout belongs to a dead session and is not delivered.
        assert received == [first, second]
        assert [h["Authorization"] for h in session.requests] == ["T1", "T2"]

    async def test_http_error_retries(self) -> None:
        payload = {"resource": "cameras/D", "action": "is", "properties": {}}
        session = _FakeSession([_FakeResponse(401, []), _FakeResponse(200, [_frame(payload)])])
        assert await _run(session, ["T1", "T2"], until=1) == [payload]
        assert len(session.requests) == 2

    async def test_non_json_frame_skipped(self) -> None:
        payload = {"resource": "cameras/D", "action": "is", "properties": {}}
        session = _FakeSession([_FakeResponse(200, [b"data: not json\n\n", _frame(payload)])])
        assert await _run(session, ["T1"], until=1) == [payload]

    async def test_handler_error_does_not_kill_stream(self) -> None:
        good = {"resource": "cameras/D", "action": "is", "properties": {}}
        session = _FakeSession([_FakeResponse(200, [_frame({"boom": True}), _frame(good)])])
        received: list[dict[str, Any]] = []
        done = asyncio.Event()

        async def on_event(payload: dict[str, Any]) -> None:
            if "boom" in payload:
                raise ValueError("handler bug")
            received.append(payload)
            done.set()

        stream = SSEEventStream(
            url="u",
            headers_factory=lambda: {},
            http_session=session,  # type: ignore[arg-type]
            on_event=on_event,
            backoff=(0.0,),
            read_timeout=5.0,
        )
        stream.start()
        await asyncio.wait_for(done.wait(), timeout=2)
        await stream.stop()
        assert received == [good]

    async def test_stop_is_idempotent(self) -> None:
        stream = SSEEventStream(
            url="u",
            headers_factory=lambda: {},
            http_session=_FakeSession([]),  # type: ignore[arg-type]
            on_event=lambda _p: asyncio.sleep(0),
            backoff=(0.0,),
            read_timeout=5.0,
        )
        stream.start()
        await asyncio.sleep(0)
        await stream.stop()
        await stream.stop()


class TestEventStreamHeaders:
    """Arlo answers a browser-UA subscribe with `connected` and then nothing at
    all; only the app UA pyaarlo uses gets events. Verified live 2026-10-22."""

    def test_app_user_agent_and_raw_token(self) -> None:
        from eisenberg.client import EisenbergClient

        client = EisenbergClient(email="e", password="p", device_id="d")
        client.token = "TOKEN"
        headers = client.event_stream_headers()
        assert "iOS Arlo" in headers["User-Agent"]
        assert headers["Authorization"] == "TOKEN"
        assert "xCloudId" not in headers

    def test_requires_login(self) -> None:
        import pytest

        from eisenberg.client import EisenbergClient

        client = EisenbergClient(email="e", password="p", device_id="d")
        with pytest.raises(RuntimeError):
            client.event_stream_headers()


class _TimeoutContent:
    """An idle Arlo stream: nothing arrives until the read timeout fires."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def iter_any(self) -> Any:
        for chunk in self._chunks:
            yield chunk
        raise aiohttp.SocketTimeoutError("Timeout on reading data from socket")


class TestIdleTimeout:
    """Arlo sends nothing on an idle SSE stream, so the read timeout fires
    every few minutes. That is the normal end of a session, not a failure: a
    backoff there left the stream down for minutes at a time and missed the
    doorbell motion it exists to catch (#38)."""

    async def test_idle_timeout_reconnects_at_once_without_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        first = _FakeResponse(200, [])
        first.content = _TimeoutContent([_frame({"status": "connected"})])  # type: ignore[assignment]
        payload = {"resource": "cameras/D", "action": "is", "properties": {}}
        session = _FakeSession([first, _FakeResponse(200, [_frame(payload)])])
        received: list[dict[str, Any]] = []
        done = asyncio.Event()

        async def on_event(p: dict[str, Any]) -> None:
            received.append(p)
            done.set()

        stream = SSEEventStream(
            url="u",
            headers_factory=lambda: {},
            http_session=session,  # type: ignore[arg-type]
            on_event=on_event,
            backoff=(60.0,),  # would blow the test timeout if applied
            read_timeout=5.0,
        )
        with caplog.at_level(logging.WARNING):
            stream.start()
            await asyncio.wait_for(done.wait(), timeout=2)
            await stream.stop()
        assert received == [payload]
        assert "SSE event stream failed" not in caplog.text

    async def test_real_failure_still_backs_off(self) -> None:
        session = _FakeSession([_FakeResponse(503, []), _FakeResponse(200, [])])
        stream = SSEEventStream(
            url="u",
            headers_factory=lambda: {},
            http_session=session,  # type: ignore[arg-type]
            on_event=lambda _p: asyncio.sleep(0),
            backoff=(60.0,),
            read_timeout=5.0,
        )
        stream.start()
        await asyncio.sleep(0.2)
        await stream.stop()
        assert len(session.requests) == 1  # still waiting out the backoff
