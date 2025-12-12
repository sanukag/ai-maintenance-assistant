# Document ingestion

## Purpose

The ingestion pipeline turns a local PDF, scanned `PNG`/`JPEG` image,
plain-text or Markdown file into locally stored, source-aware chunks.

Validation, parsing, chunking and storage run locally. Embedding is optional and
calls the explicitly configured provider.

## Pipeline

1. Validate that the path is a readable, non-empty regular file with a
   configured type and size.
2. Inspect the content for basic format mismatches and calculate its SHA-256
   fingerprint.
3. Return the existing stored record if that fingerprint is already present.
4. Extract digital PDF text by page. Render only pages without a text layer and
   recognise them locally with Tesseract. Image documents use the same local
   OCR engine; Markdown and UTF-8 text preserve their structural locations.
5. When enabled, render every PDF page and analyse maintenance-relevant images,
   diagrams, drawings, charts and tables. Add only useful visual descriptions
   as page-cited segments; standalone document images use the same stage.
6. Normalise Unicode, line endings, control characters and excessive blank
   lines without rewriting the document's meaning.
7. Split text into section-aligned parent context and smaller retrieval children
   using configurable model-token budgets and word-aligned child overlap.
8. Prefix each child with all selected brands, machines, sites/areas and document
   types, then create one embedding per child when embeddings are enabled. Parent
   sections are stored as context and are not embedded separately.
9. Copy the original into controlled local storage and save its metadata,
   lifecycle state, chunks and vectors to SQLite in one transaction.

Metadata values are normalised and de-duplicated case-insensitively. The value
catalogue is retained independently from manual lifecycle records so a value
used during an earlier upload remains available for future tagging.

The source copy is hashed again before storage. Ingestion stops if the document
changed after validation.

If the document already exists, ingestion checks whether its chunks have
vectors for the configured model and dimensions. Only missing vectors are
created and backfilled. If supplied metadata differs from the stored values,
the existing document is updated and every active-model vector is refreshed so
the stored classification and embedding content cannot drift apart.

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
links, equipment classification, parent sections, ordered child chunks and any
enabled embeddings. Metadata values are optional, whitespace-normalised,
limited to 100 characters and reject control characters.

Each chunk records the source information available for its format:

- PDF page range
- Scanned-image page number
- Markdown headings and line range
- Plain-text line range
- Sequence, character count and model-token count for every format

If file or database storage fails, the transaction is rolled back and partial
source copies are removed.

## Configuration

| Setting | Default | Meaning |
| --- | ---: | --- |
| `AMA_DATA_DIRECTORY` | `./data` | Local database and source-file root |
| `AMA_MAX_DOCUMENT_SIZE_MB` | `25` | Maximum accepted source size |
| `AMA_SUPPORTED_FILE_TYPES` | `.pdf,.txt,.md,.png,.jpg,.jpeg` | Accepted filename extensions |
| `AMA_OCR_PROVIDER` | `tesseract` | Local OCR engine, or `none` to disable OCR |
| `AMA_OCR_LANGUAGE` | `eng` | Installed Tesseract language code(s) |
| `AMA_OCR_DPI` | `300` | PDF page rendering resolution for OCR |
| `AMA_OCR_PAGE_TIMEOUT_SECONDS` | `30` | Maximum recognition time per image/page |
| `AMA_OCR_MAX_PAGES` | `100` | Maximum textless PDF pages recognised per request |
| `AMA_OCR_MAX_IMAGE_PIXELS` | `50000000` | Maximum rendered or uploaded image pixels |
| `AMA_VISUAL_ANALYSIS_PROVIDER` | `none` | `none` or opt-in `openai` page analysis |
| `AMA_VISUAL_ANALYSIS_MODEL` | `gpt-5.6-terra` | Image-capable Responses API model |
| `AMA_VISUAL_ANALYSIS_DETAIL` | `high` | Provider image fidelity: `low`, `high`, `original` or `auto` |
| `AMA_VISUAL_ANALYSIS_RENDER_DPI` | `150` | PDF resolution sent for visual analysis |
| `AMA_VISUAL_ANALYSIS_TIMEOUT_SECONDS` | `60` | Maximum provider time per page |
| `AMA_VISUAL_ANALYSIS_MAX_PAGES` | `100` | Maximum analysed PDF pages per request |
| `AMA_VISUAL_ANALYSIS_MAX_IMAGE_PIXELS` | `25000000` | Maximum rendered page pixels |
| `AMA_VISUAL_ANALYSIS_MAX_OUTPUT_TOKENS` | `1000` | Maximum typed description output per page |
| `AMA_CHUNK_SIZE_TOKENS` | `300` | Maximum model tokens in a retrieval chunk |
| `AMA_CHUNK_OVERLAP_TOKENS` | `40` | Maximum repeated model tokens between chunks |
| `AMA_PARENT_CHUNK_SIZE_TOKENS` | `900` | Maximum model tokens in answer context |
| `AMA_CHUNK_TOKEN_ENCODING` | `cl100k_base` | Token encoding used for chunk boundaries |
| `AMA_EMBEDDING_PROVIDER` | `none` | `none` or the opt-in `openai` provider |
| `AMA_EMBEDDING_MODEL` | `text-embedding-3-small` | Embedding model identifier |
| `AMA_EMBEDDING_DIMENSIONS` | `512` | Stored vector dimensions |
| `AMA_EMBEDDING_BATCH_SIZE` | `128` | Maximum texts per embedding request |
| `OPENAI_API_KEY` | none | Required when the OpenAI provider is enabled |

Token boundaries use OpenAI's `cl100k_base` encoding, which matches the current
embedding-model family. The verified encoding table is packaged with the
application, so tokenisation does not make a network request during ingestion
or API startup. Its SHA-256 checksum is checked whenever the table is loaded.

Existing `.env` files should replace `AMA_CHUNK_SIZE_CHARACTERS` and
`AMA_CHUNK_OVERLAP_CHARACTERS` with the token-based settings above. Existing
stored chunks remain readable; their token count is reported as unknown until
the document is re-indexed with the new chunker. Re-indexing reparses the stored
source and replaces its hierarchy and vectors in one database transaction.

The API key is read from the environment and is never stored in the database.

## Outcomes and errors

A successful request returns either `completed` or `already_exists`. Failures
use stable codes such as `unsupported_type`, `file_too_large`,
`invalid_document`, `encrypted_document`, `no_extractable_text`,
`ocr_unavailable`, `ocr_timed_out`, `ocr_failed`, `embedding_failed` and
`visual_analysis_unavailable`, `visual_analysis_timed_out`,
`visual_analysis_failed` and `storage_failed`.

The command-line interface prints safe messages. Underlying exceptions remain
available to application logging without exposing document content.

## Initial limitations

- Handwriting, complex diagrams and low-quality scans may not be recognised
  accurately; workers must verify extracted procedures against the source.
- Visual descriptions are model-generated evidence aids, not authoritative
  interpretations. Small labels, rotated pages, subtle line styles, precise
  spatial relationships and object counts can be misunderstood.
- Enabling visual analysis sends rendered pages to OpenAI. OCR, source storage,
  chunking and retrieval storage remain local, but visual analysis is not local.
- Only installed Tesseract languages are available. The container includes
  English language data by default.
- Password-protected PDFs are rejected.
- Text and Markdown documents must use UTF-8 encoding.
- Exact duplicates are reused rather than installed as a new revision; changed
  classifications update that record and refresh its active vectors.
- Ingestion runs synchronously; there is no background processing queue yet.
- Local hybrid ranking calculates vector similarity in the application process
  and exact-text ranking with SQLite FTS5; this is intended for an initial small
  corpus.
- Ingestion is intended for a local, single-user process in this version.

These limitations keep the first implementation observable and testable while
leaving clear extension points for later product work.
