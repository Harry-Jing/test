import pytest

from tests.support.stt_live import (
    assert_live_transcription_result,
    build_live_app_config,
    run_live_stt_fixture,
)
from vrc_live_caption.env import AppSecrets, SecretError

pytestmark = [pytest.mark.integration, pytest.mark.openai_live]


def test_openai_realtime_live_transcribes_fixture() -> None:
    try:
        secrets = AppSecrets()
        secrets.require_openai_credentials()
    except SecretError as exc:
        pytest.skip(str(exc))

    result = run_live_stt_fixture(
        app_config=build_live_app_config(
            provider="openai_realtime",
            openai_realtime_overrides={"language": "zh"},
        ),
        secrets=secrets,
        logger_name="tests.integration.openai_live",
    )

    assert_live_transcription_result(result)
