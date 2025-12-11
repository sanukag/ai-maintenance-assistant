# Document ingestion

## Purpose

The initial ingestion pipeline turns a local PDF, plain-text or Markdown file
into locally stored, source-aware chunks. These chunks are intended to support
retrieval and answer citation in later product sections.

Validation, parsing, chunking and storage run locally. Embedding is optional and
calls the explicitly configured provider.

## Pipeline

1. Validate that the path is a readable, non-empty regular file with a
   configured type and size.
2. Inspect the content for basic format mismatches and calculate its SHA-256
   fingerprint.
3. Return the existing stored record if that fingerprint is already present.
4. Extract PDF text by page, Markdown by heading section, or UTF-8 plain text
   with line information.
5. Normalise Unicode, line endings, control characters and excessive blank
   lines without rewriting the document's meaning.
6. Split text at structural boundaries using a configurable character budget
   and word-aligned overlap.
7. When enabled, create one embedding for every prepared chunk.
8. Copy the original into controlled local storage and save its metadata,
   lifecycle state, chunks and vectors to SQLite in one transaction.

The source copy is hashed again before storage. Ingestion stops if the document
changed after validation.

If the document already exists, ingestion checks whether its chunks have
vectors for the configured model and dimensions. Only missing vectors are
created and backfilled.

## Local storage

The default data root is `./data` and can be changed with
`AMA_DATA_DIRECTORY`.

```text
data/
├── maintenance-assistant.db
└── documents/
    └── <generated-document-id>/
        └── original.<extension>
```

Generated document identifiers determine storage paths; user-provided
filenames are retained only as metadata. The SQLite database contains document
fingerprints, extraction details, creation times, lifecycle state, revision
links, ordered chunks and any enabled embeddings.

Each chunk records the source information available for its format:

- PDF page range
- Markdown headings and line range
- Plain-text line range
- Sequence and character count for every format

If file or database storage fails, the transaction is rolled back and partial
source copies are removed.

## Configuration

| Setting | Default | Meaning |
| --- | ---: | --- |
| `AMA_DATA_DIRECTORY` | `./data` | Local database and source-file root |
| `AMA_MAX_DOCUMENT_SIZE_MB` | `25` | Maximum accepted source size |
| `AMA_SUPPORTED_FILE_TYPES` | `.pdf,.txt,.md` | Accepted filename extensions |
| `AMA_CHUNK_SIZE_CHARACTERS` | `2400` | Maximum characters in a chunk |
| `AMA_CHUNK_OVERLAP_CHARACTERS` | `400` | Maximum context repeated between chunks |
| `AMA_EMBEDDING_PROVIDER` | `none` | `none` or the opt-in `openai` provider |
| `AMA_EMBEDDING_MODEL` | `text-embedding-3-small` | Embedding model identifier |
| `AMA_EMBEDDING_DIMENSIONS` | `512` | Stored vector dimensions |
| `AMA_EMBEDDING_BATCH_SIZE` | `128` | Maximum texts per embedding request |
| `OPENAI_API_KEY` | none | Required when the OpenAI provider is enabled |

Character limits are intentionally model-independent for the first local
version. Retrieval can introduce model-specific token counting when its model
and embedding strategy are selected.

The API key is read from the environment and is never stored in the database.

## Outcomes and errors

A successful request returns either `completed` or `already_exists`. Failures
use stable codes such as `unsupported_type`, `file_too_large`,
`invalid_document`, `encrypted_document`, `no_extractable_text` and
`embedding_failed` and `storage_failed`.

The command-line interface prints safe messages. Underlying exceptions remain
available to application logging without exposing document content.

## Initial limitations

- Scanned PDFs are recognised as having no extractable text; optical character
  recognition is not attempted.
- Password-protected PDFs are rejected.
- Text and Markdown documents must use UTF-8 encoding.
- Exact duplicates are reused rather than installed as a new revision.
- Ingestion runs synchronously; there is no background processing queue yet.
- Local vector ranking loads matching vectors and calculates cosine similarity
  in the application process; this is intended for an initial small corpus.
- Ingestion is intended for a local, single-user process in this version.

These limitations keep the first implementation observable and testable while
leaving clear extension points for later product work.
