from types import SimpleNamespace


def make_torch(*, cuda_available: bool, device_count: int = 1) -> SimpleNamespace:
    cuda = SimpleNamespace(
        is_available=lambda: cuda_available,
        device_count=lambda: device_count,
    )
    version = SimpleNamespace(cuda="12.8" if cuda_available else None)
    return SimpleNamespace(cuda=cuda, version=version)


class GeneratedModel:
    def __init__(self, responses: list[dict]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        response = self._responses.pop(0) if self._responses else {}
        return [response]


class FakeWebsocket:
    def __init__(self, *, fail_on_send: bool = False) -> None:
        self.fail_on_send = fail_on_send
        self.sent: list[str] = []

    async def send(self, message: str) -> None:
        if self.fail_on_send:
            raise RuntimeError("send failed")
        self.sent.append(message)


class FakeServeContext:
    def __init__(self, websocket: FakeWebsocket | None = None) -> None:
        self.websocket = websocket
        self.handler = None
        self.host = None
        self.port = None
        self.ping_interval = None

    def __call__(self, handler, host, port, ping_interval=None):
        self.handler = handler
        self.host = host
        self.port = port
        self.ping_interval = ping_interval
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def wait_closed(self) -> None:
        if self.websocket is not None and self.handler is not None:
            await self.handler(self.websocket)
