class FakeOscTransport:
    def __init__(self) -> None:
        self.host = "127.0.0.1"
        self.port = 9000
        self.text_messages: list[str] = []
        self.typing_messages: list[bool] = []

    def send_text(self, text: str) -> None:
        self.text_messages.append(text)

    def send_typing(self, is_typing: bool) -> None:
        self.typing_messages.append(is_typing)
