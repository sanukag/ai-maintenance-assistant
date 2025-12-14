# App-managed API keys

## Purpose

The Settings page lets a local operator add, replace or delete the API keys
needed by the application's fixed external integrations. The current release
uses one OpenAI API key for embeddings, reranking, visual document analysis and
grounded answers. It deliberately provides no model-provider selector.

Add the key under **Settings → API keys**. A successful save takes effect in
the running API immediately and is used after later application or container
restarts. The ingestion worker refreshes its credential-backed services before
claiming each job, so a newly saved or replaced key is used for subsequent
work without restarting the worker.

The application never returns the complete key. Settings shows only a mask and
the final four characters, and an edit starts with an empty password field.

## Storage and precedence

The saved value is encrypted with Fernet authenticated symmetric encryption
before being written to the `external_credentials` SQLite table. The generated
encryption key is stored at `credential-encryption.key` beneath
`AMA_DATA_DIRECTORY` and is created with owner-only (`0600`) permissions.

A saved application credential takes precedence over `OPENAI_API_KEY` from the
process environment. Deleting the saved value falls back to the environment
value when one exists. An environment value can be replaced locally, but it
cannot be deleted through the application because the parent process owns it.
Remove it from the environment or untracked `.env` file and restart instead.

The database ciphertext and encryption-key file must be backed up and restored
together. Losing the key file makes the saved credential unreadable. Anyone
who can read the complete data directory can obtain both the key and ciphertext,
so this design protects database-only disclosure and accidental inspection; it
does not replace operating-system account security, volume permissions or disk
encryption.

Saving the OpenAI key enables the fixed OpenAI services. This means document
chunks, retrieval questions and selected answer context may be sent to OpenAI;
rendered PDF pages and uploaded document images may also be sent for visual
analysis. OCR, originals, SQLite records and returned vectors remain local.

## API

The web interface uses these local endpoints:

| Method | Path | Behaviour |
| --- | --- | --- |
| `GET` | `/credentials` | Return non-sensitive status for supported credentials |
| `PUT` | `/credentials/OPENAI_API_KEY` | Validate, encrypt and save or replace a key |
| `DELETE` | `/credentials/OPENAI_API_KEY` | Delete a saved key and use any environment fallback |

The update body is `{"value":"..."}`. Responses contain availability,
source, mask and update time only. They never include the submitted secret.
Unknown credential names and malformed values are rejected. A request to delete
an environment-managed key returns HTTP `409`.

## Docker persistence

The API and ingestion worker share `/app/data` through the `maintenance-data`
volume, so both can decrypt the same saved value. Rebuilding or recreating the
containers preserves the database and encryption key. Running
`docker compose down --volumes` deletes both permanently.
