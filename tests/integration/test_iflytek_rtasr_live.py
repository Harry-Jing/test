import pytest

from tests.support.stt_live import (
    assert_live_transcription_result,
    build_live_app_config,
    run_live_stt_fixture,
)
from vrc_live_caption.env import AppSecrets, SecretError

pytestmark = [pytest.mark.integration, pytest.mark.iflytek_live]


def test_iflytek_rtasr_live_transcribes_fixture() -> None:
    try:
        secrets = AppSecrets()
        secrets.require_iflytek_credentials()
    except SecretError as exc:
        pytest.skip(str(exc))

    result = run_live_stt_fixture(
        app_config=build_live_app_config(
            provider="iflytek_rtasr",
            iflytek_rtasr_overrides={"language": "autodialect"},
        ),
        secrets=secrets,
        logger_name="tests.integration.iflytek_live",
    )

    assert_live_transcription_result(result)
