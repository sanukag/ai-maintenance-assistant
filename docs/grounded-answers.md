# Grounded answers and citations

Grounded answering is a retrieval-augmented generation path over the documents
already ingested and embedded by this application. It is deliberately separate
from ingestion: stored documents remain useful when answer generation is
disabled.

## Runtime sequence

For each `POST /answers` request, the application:

1. embeds the validated question with the configured embedding provider;
2. searches the local SQLite vectors, optionally within one document;
3. labels the selected chunks `S1`, `S2` and so on;
4. sends the question and those chunks to the configured answer provider;
5. requests a typed result containing `answerable`, answer text and source IDs;
6. verifies the structured IDs and inline `[S#]` markers against the retrieved
   chunks; and
7. maps valid citations back to document metadata, the exact evidence excerpt
   and available page, heading or line locations.

Vector search always filters the joined document record to `current`. A
superseded or archived manual therefore cannot be selected even when its exact
document identifier is supplied.

The OpenAI implementation uses the Responses API with a Pydantic structured
output schema. The default answer model is `gpt-5.6-terra`, selected as the
current balance between intelligence and cost. The model is configurable so a
deployment can make its own cost, latency and quality trade-off.

## Grounding guarantees

The application can guarantee that every returned citation identifier refers to
one of the chunks retrieved for that request. It rejects:

- an answer with no citations or no inline source markers;
- citation IDs that do not match the markers in the answer;
- duplicate IDs;
- IDs that were not supplied to the model; and
- citations attached to a response marked unanswerable.

Retrieved text is explicitly identified as untrusted evidence. Instructions
inside a manual are not meant to override the answer policy. If the model marks
the evidence insufficient, the application discards its prose and returns a
fixed local explanation with no citations. If no chunks are found, the answer
provider is not called.

These controls validate provenance, not the real-world correctness of the
source manual. They also cannot prove that every sentence is semantically
entailed by its excerpt. Maintenance answers should still be checked against the
original approved documentation and site safety procedures.

## Configuration

Answers require semantic retrieval, so both providers must be enabled:

```env
AMA_EMBEDDING_PROVIDER=openai
AMA_ANSWER_PROVIDER=openai
AMA_ANSWER_MODEL=gpt-5.6-terra
AMA_ANSWER_MAX_OUTPUT_TOKENS=1000
OPENAI_API_KEY=your-project-api-key
```

The question and retrieved chunk text leave the local machine when the OpenAI
answer provider is enabled. Original files, SQLite metadata and vectors remain
local. Answer responses are not stored by the application in this initial
version.

Documents must have vectors for the active embedding model and dimensions.
Re-submit an already ingested document while embeddings are enabled to backfill
missing vectors without creating a duplicate document.

## Current limitations

- The API returns complete responses rather than streaming partial text.
- Retrieval uses cosine similarity without reranking or a minimum score.
- Answer history and user feedback are not persisted.
- There is no evaluation dataset yet for measuring answer correctness or
  citation entailment against representative maintenance manuals.
- The local API has no authentication or rate limiting and must not be exposed
  directly to an untrusted network.
