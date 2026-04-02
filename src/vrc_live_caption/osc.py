"""Sends VRChat chatbox OSC messages through a UDP client."""

import logging
from collections.abc import Callable
from typing import Protocol

from pythonosc.udp_client import SimpleUDPClient

from .config import OscConfig
from .errors import OscError


class _UdpClient(Protocol):
    def send_message(self, address: str, value) -> None: ...


class OscChatboxTransport:
    """Send VRChat chatbox text and typing messages to the configured OSC target."""

    def __init__(
        self,
        *,
        osc_config: OscConfig,
        logger: logging.Logger,
        client_factory: Callable[[str, int], _UdpClient] | None = None,
    ) -> None:
        """Initialize the OSC chatbox transport and its UDP client.

        Args:
            osc_config: Validated OSC host, port, and notification settings.
            logger: Logger used for transport diagnostics.
            client_factory: Optional UDP client factory for tests or alternate
                implementations.

        Raises:
            OscError: Raised when the UDP client cannot be configured.
        """
        self.host = osc_config.host
        self.port = osc_config.port
        self._notification_sfx = osc_config.notification_sfx
        self._logger = logger
        factory = client_factory or SimpleUDPClient
        try:
            self._client = factory(self.host, self.port)
        except Exception as exc:
            raise OscError(f"Failed to configure OSC output: {exc}") from exc

    def send_text(self, text: str) -> None:
        """Send VRChat chatbox text with enter and notification settings."""
        self._send(
            "/chatbox/input",
            [text, True, self._notification_sfx],
            summary=f"text_len={len(text)} notification_sfx={self._notification_sfx}",
        )

    def send_typing(self, is_typing: bool) -> None:
        """Send the VRChat chatbox typing indicator state."""
        self._send("/chatbox/typing", is_typing, summary=f"typing={is_typing}")

    def _send(self, address: str, value, *, summary: str) -> None:
        try:
            self._client.send_message(address, value)
        except Exception as exc:
            raise OscError(f"Failed to send OSC message {address}: {exc}") from exc
        self._logger.debug("Sent OSC message: address=%s %s", address, summary)
