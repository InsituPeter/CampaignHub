import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_settings_use_expected_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.app_name == "CampaignHub"
    assert settings.environment == "development"
    assert settings.debug is False
    assert settings.log_level == "INFO"
    assert settings.api_v1_prefix == "/api/v1"
    assert settings.is_production is False


def test_settings_normalize_environment_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CAMPAIGNHUB_APP_NAME", "  CampaignHub Local  ")
    monkeypatch.setenv("CAMPAIGNHUB_LOG_LEVEL", "debug")
    monkeypatch.setenv("CAMPAIGNHUB_API_V1_PREFIX", "  /api/local  ")

    settings = Settings(_env_file=None)

    assert settings.app_name == "CampaignHub Local"
    assert settings.log_level == "DEBUG"
    assert settings.api_v1_prefix == "/api/local"


def test_settings_reject_prefix_without_leading_slash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CAMPAIGNHUB_API_V1_PREFIX", "api/v1")

    with pytest.raises(
        ValidationError,
        match="API prefix must begin with '/'",
    ):
        Settings(_env_file=None)


def test_settings_reject_debug_mode_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CAMPAIGNHUB_ENVIRONMENT", "production")
    monkeypatch.setenv("CAMPAIGNHUB_DEBUG", "true")

    with pytest.raises(
        ValidationError,
        match="Debug mode must be disabled in production",
    ):
        Settings(_env_file=None)
