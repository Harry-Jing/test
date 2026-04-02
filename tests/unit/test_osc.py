import logging

import pytest

from vrc_live_caption.config import OscConfig
from vrc_live_caption.errors import OscError
from vrc_live_caption.osc import OscChatboxTransport


class _FakeClient:
    def __init__(self) -> None:
        self.messages: list[tuple[str, object]] = []

    def send_message(self, address: str, value) -> None:
        self.messages.append((address, value))


def test_osc_chatbox_transport_sends_chatbox_input_and_typing() -> None:
    client = _FakeClient()
    transport = OscChatboxTransport(
        osc_config=OscConfig(host="127.0.0.1", port=9000, notification_sfx=False),
        logger=logging.getLogger("test.osc"),
        client_factory=lambda _host, _port: client,
    )

    transport.send_text("hello")
    transport.send_typing(True)

    assert client.messages == [
        ("/chatbox/input", ["hello", True, False]),
        ("/chatbox/typing", True),
    ]


def test_osc_chatbox_transport_wraps_send_failures() -> None:
    class _BrokenClient:
        def send_message(self, address: str, value) -> None:
            raise OSError("udp down")

    transport = OscChatboxTransport(
        osc_config=OscConfig(),
        logger=logging.getLogger("test.osc.error"),
        client_factory=lambda _host, _port: _BrokenClient(),
    )

    with pytest.raises(OscError, match="/chatbox/input"):
        transport.send_text("hello")
