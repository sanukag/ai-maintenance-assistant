from contextlib import closing
from pathlib import Path
import sqlite3

import pytest

from maintenance_assistant.config import Settings
from maintenance_assistant.credentials import (
    CredentialError,
    CredentialName,
    CredentialStore,
    settings_with_credentials,
)
from maintenance_assistant.ingestion import LocalDocumentStore


def _credentials(tmp_path: Path) -> CredentialStore:
    return CredentialStore(LocalDocumentStore(tmp_path / "data"))


def test_saved_key_is_encrypted_masked_and_available_after_restart(tmp_path: Path) -> None:
    credentials = _credentials(tmp_path)
    secret = "sk-project-example-1234"

    saved = credentials.save(CredentialName.OPENAI_API_KEY, secret)
    restarted = _credentials(tmp_path)

    assert saved.configured is True
    assert saved.source == "saved"
    assert saved.masked_value == "••••1234"
    assert saved.can_delete is True
    assert restarted.resolve(CredentialName.OPENAI_API_KEY) == secret
    assert credentials.key_path.stat().st_mode & 0o777 == 0o600
    with closing(sqlite3.connect(credentials.store.database_path)) as connection:
        encrypted = connection.execute(
            "SELECT encrypted_value FROM external_credentials"
        ).fetchone()[0]
    assert secret.encode() not in bytes(encrypted)


def test_saved_key_overrides_environment_and_can_be_edited_or_deleted(
    tmp_path: Path,
) -> None:
    credentials = _credentials(tmp_path)
    environment = "sk-environment-0000"
    credentials.save(CredentialName.OPENAI_API_KEY, "sk-saved-first-1111")
    credentials.save(CredentialName.OPENAI_API_KEY, "sk-saved-edited-2222")

    assert credentials.resolve(CredentialName.OPENAI_API_KEY, environment) == (
        "sk-saved-edited-2222"
    )
    assert credentials.delete(CredentialName.OPENAI_API_KEY) is True
    assert credentials.delete(CredentialName.OPENAI_API_KEY) is False
    status = credentials.status(CredentialName.OPENAI_API_KEY, environment)
    assert status.source == "environment"
    assert status.masked_value == "••••0000"
    assert status.can_delete is False


@pytest.mark.parametrize("value", ["short", "has whitespace", "x" * 2_049])
def test_saved_key_validation_rejects_unsafe_values(tmp_path: Path, value: str) -> None:
    with pytest.raises(ValueError):
        _credentials(tmp_path).save(CredentialName.OPENAI_API_KEY, value)


def test_corrupted_ciphertext_fails_closed_without_exposing_secret(tmp_path: Path) -> None:
    credentials = _credentials(tmp_path)
    credentials.save(CredentialName.OPENAI_API_KEY, "sk-project-valid-1234")
    with credentials.store._connection() as connection:
        connection.execute(
            "UPDATE external_credentials SET encrypted_value = ?",
            (b"invalid-token",),
        )

    with pytest.raises(CredentialError, match="could not be decrypted"):
        credentials.resolve(CredentialName.OPENAI_API_KEY)


def test_missing_encryption_key_fails_closed_without_creating_a_replacement(
    tmp_path: Path,
) -> None:
    credentials = _credentials(tmp_path)
    credentials.save(CredentialName.OPENAI_API_KEY, "sk-project-valid-1234")
    credentials.key_path.unlink()

    with pytest.raises(CredentialError, match="encryption key is missing"):
        credentials.resolve(CredentialName.OPENAI_API_KEY)

    assert not credentials.key_path.exists()


def test_runtime_settings_use_fixed_openai_services_when_a_key_exists(
    tmp_path: Path,
) -> None:
    credentials = _credentials(tmp_path)
    credentials.save(CredentialName.OPENAI_API_KEY, "sk-project-valid-1234")

    settings = settings_with_credentials(Settings(), credentials)

    assert settings.openai_api_key == "sk-project-valid-1234"
    assert settings.embedding_provider == "openai"
    assert settings.visual_analysis_provider == "openai"
    assert settings.rerank_provider == "openai"
    assert settings.answer_provider == "openai"


def test_runtime_settings_disable_external_ai_without_a_key(tmp_path: Path) -> None:
    settings = settings_with_credentials(Settings(), _credentials(tmp_path))

    assert settings.openai_api_key is None
    assert settings.embedding_provider == "none"
    assert settings.visual_analysis_provider == "none"
    assert settings.rerank_provider == "none"
    assert settings.answer_provider == "none"
