"""Encrypted local credentials and fixed external-service activation."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
import os
from pathlib import Path
import sqlite3
from time import sleep

from cryptography.fernet import Fernet, InvalidToken

from maintenance_assistant.config import Settings
from maintenance_assistant.ingestion import LocalDocumentStore


class CredentialName(StrEnum):
    OPENAI_API_KEY = "OPENAI_API_KEY"


@dataclass(frozen=True, slots=True)
class CredentialDefinition:
    name: CredentialName
    label: str
    description: str
    used_by: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CredentialStatus:
    name: CredentialName
    label: str
    description: str
    used_by: tuple[str, ...]
    configured: bool
    source: str
    masked_value: str | None
    updated_at: datetime | None
    can_delete: bool


class CredentialError(RuntimeError):
    """A safe credential persistence or decryption failure."""


DEFINITIONS = (
    CredentialDefinition(
        name=CredentialName.OPENAI_API_KEY,
        label="OpenAI API key",
        description=(
            "Used for document embeddings, diagram understanding, evidence "
            "reranking and grounded answers."
        ),
        used_by=(
            "Document embeddings",
            "Visual analysis",
            "Evidence reranking",
            "Grounded answers",
        ),
    ),
)
_DEFINITIONS_BY_NAME = {item.name: item for item in DEFINITIONS}


class CredentialStore:
    """Encrypt credentials with a local volume key and persist only ciphertext."""

    def __init__(self, store: LocalDocumentStore) -> None:
        self.store = store
        self.key_path = store.data_directory / "credential-encryption.key"

    def resolve(
        self,
        name: CredentialName,
        environment_value: str | None = None,
    ) -> str | None:
        row = self._saved_row(name)
        if row is not None:
            return self._decrypt(bytes(row["encrypted_value"]))
        return (
            environment_value.strip()
            if environment_value and environment_value.strip()
            else None
        )

    def list_statuses(
        self,
        environment_values: dict[CredentialName, str | None],
    ) -> tuple[CredentialStatus, ...]:
        return tuple(
            self.status(definition.name, environment_values.get(definition.name))
            for definition in DEFINITIONS
        )

    def status(
        self,
        name: CredentialName,
        environment_value: str | None = None,
    ) -> CredentialStatus:
        definition = _DEFINITIONS_BY_NAME[name]
        row = self._saved_row(name)
        if row is not None:
            value = self._decrypt(bytes(row["encrypted_value"]))
            return CredentialStatus(
                name=name,
                label=definition.label,
                description=definition.description,
                used_by=definition.used_by,
                configured=True,
                source="saved",
                masked_value=_mask(value),
                updated_at=datetime.fromisoformat(row["updated_at"]),
                can_delete=True,
            )
        value = environment_value.strip() if environment_value else ""
        return CredentialStatus(
            name=name,
            label=definition.label,
            description=definition.description,
            used_by=definition.used_by,
            configured=bool(value),
            source="environment" if value else "missing",
            masked_value=_mask(value) if value else None,
            updated_at=None,
            can_delete=False,
        )

    def save(self, name: CredentialName, value: str) -> CredentialStatus:
        normalised = _normalise_secret(value)
        encrypted = self._fernet(create=True).encrypt(normalised.encode("utf-8"))
        now = datetime.now(UTC).isoformat()
        self.store.initialise()
        try:
            with self.store._connection() as connection:
                connection.execute(
                    """INSERT INTO external_credentials (
                           name, encrypted_value, created_at, updated_at
                       ) VALUES (?, ?, ?, ?)
                       ON CONFLICT(name) DO UPDATE SET
                           encrypted_value = excluded.encrypted_value,
                           updated_at = excluded.updated_at""",
                    (name.value, encrypted, now, now),
                )
        except sqlite3.Error as error:
            raise CredentialError("The API key could not be saved") from error
        return self.status(name)

    def delete(self, name: CredentialName) -> bool:
        self.store.initialise()
        try:
            with self.store._connection() as connection:
                deleted = connection.execute(
                    "DELETE FROM external_credentials WHERE name = ?",
                    (name.value,),
                ).rowcount
        except sqlite3.Error as error:
            raise CredentialError("The saved API key could not be deleted") from error
        return deleted == 1

    def _saved_row(self, name: CredentialName) -> sqlite3.Row | None:
        if not self.store.database_path.is_file():
            return None
        self.store.initialise()
        try:
            with self.store._connection() as connection:
                return connection.execute(
                    "SELECT * FROM external_credentials WHERE name = ?",
                    (name.value,),
                ).fetchone()
        except sqlite3.Error as error:
            raise CredentialError("Saved API keys could not be read") from error

    def _fernet(self, *, create: bool = False) -> Fernet:
        return Fernet(self._read_or_create_key() if create else self._read_key())

    def _read_key(self) -> bytes:
        if self.key_path.is_symlink():
            raise CredentialError("The credential encryption key path is unsafe")
        try:
            key = self.key_path.read_bytes()
            os.chmod(self.key_path, 0o600)
            Fernet(key)
            return key
        except FileNotFoundError as error:
            raise CredentialError("The credential encryption key is missing") from error
        except (OSError, ValueError) as error:
            raise CredentialError("The credential encryption key is invalid") from error

    def _read_or_create_key(self) -> bytes:
        self.store.data_directory.mkdir(parents=True, exist_ok=True)
        if self.key_path.is_symlink():
            raise CredentialError("The credential encryption key path is unsafe")
        try:
            descriptor = os.open(
                self.key_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError:
            descriptor = None
        except OSError as error:
            raise CredentialError("The credential encryption key could not be created") from error
        if descriptor is not None:
            key = Fernet.generate_key()
            try:
                with os.fdopen(descriptor, "wb") as key_file:
                    key_file.write(key)
                    key_file.flush()
                    os.fsync(key_file.fileno())
            except OSError as error:
                self.key_path.unlink(missing_ok=True)
                raise CredentialError("The credential encryption key could not be saved") from error
            return key

        for _ in range(5):
            try:
                key = self.key_path.read_bytes()
                os.chmod(self.key_path, 0o600)
                Fernet(key)
                return key
            except (OSError, ValueError):
                sleep(0.02)
        raise CredentialError("The credential encryption key is invalid")

    def _decrypt(self, encrypted: bytes) -> str:
        try:
            return self._fernet().decrypt(encrypted).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError) as error:
            raise CredentialError("A saved API key could not be decrypted") from error


def settings_with_credentials(
    settings: Settings,
    credentials: CredentialStore,
) -> Settings:
    """Apply saved-over-environment precedence to the fixed OpenAI integration."""

    api_key = credentials.resolve(
        CredentialName.OPENAI_API_KEY,
        settings.openai_api_key,
    )
    provider = "openai" if api_key else "none"
    return replace(
        settings,
        openai_api_key=api_key,
        embedding_provider=provider,
        visual_analysis_provider=provider,
        rerank_provider=provider,
        answer_provider=provider,
    )


def _normalise_secret(value: str) -> str:
    normalised = value.strip()
    if len(normalised) < 8:
        raise ValueError("API key must contain at least 8 characters")
    if len(normalised) > 2_048:
        raise ValueError("API key must not exceed 2048 characters")
    if any(character.isspace() for character in normalised):
        raise ValueError("API key must not contain whitespace")
    return normalised


def _mask(value: str) -> str:
    return f"••••{value[-4:]}"
