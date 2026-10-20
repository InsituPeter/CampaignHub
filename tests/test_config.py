import pytest
from pydantic import SecretStr, ValidationError

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


def test_jwt_settings_load_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    key = "test-only-signing-secret-" * 3
    monkeypatch.setenv("CAMPAIGNHUB_JWT_SECRET_KEY", key)
    monkeypatch.setenv("CAMPAIGNHUB_JWT_ISSUER", "  test-issuer  ")
    monkeypatch.setenv("CAMPAIGNHUB_JWT_AUDIENCE", "  test-api  ")
    monkeypatch.setenv("CAMPAIGNHUB_ACCESS_TOKEN_LIFETIME_SECONDS", "300")
    settings = Settings(_env_file=None)
    assert settings.jwt_secret_key is not None
    assert settings.jwt_secret_key.get_secret_value() == key
    assert key not in repr(settings)
    assert settings.jwt_issuer == "test-issuer"
    assert settings.jwt_audience == "test-api"
    assert settings.access_token_lifetime_seconds == 300


@pytest.mark.parametrize("key", ["", "short", " " * 32])
def test_invalid_jwt_secret_is_rejected(key: str) -> None:
    with pytest.raises(ValidationError, match="at least 32 bytes"):
        Settings(_env_file=None, jwt_secret_key=SecretStr(key))


@pytest.mark.parametrize("lifetime", [0, -1, 3601])
def test_invalid_access_token_lifetime_is_rejected(lifetime: int) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, access_token_lifetime_seconds=lifetime)


def test_blank_jwt_identifiers_are_rejected() -> None:
    with pytest.raises(ValidationError, match="cannot be blank"):
        Settings(_env_file=None, jwt_issuer=" ")
    with pytest.raises(ValidationError, match="cannot be blank"):
        Settings(_env_file=None, jwt_audience=" ")


def test_production_requires_jwt_secret() -> None:
    with pytest.raises(ValidationError, match="required in production"):
        Settings(_env_file=None, environment="production", debug=False, jwt_secret_key=None)


def test_production_accepts_explicit_jwt_secret() -> None:
    settings = Settings(
        _env_file=None,
        environment="production",
        debug=False,
        jwt_secret_key=SecretStr("test-only-signing-secret-" * 3),
    )
    assert settings.is_production


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
