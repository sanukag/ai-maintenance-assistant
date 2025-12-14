# Grounded answers and citations

Grounded answering is a retrieval-augmented generation path over the documents
already ingested and embedded by this application. It is deliberately separate
from ingestion: stored documents remain useful when answer generation is
disabled.

## Runtime sequence

For each `POST /answers` request, the application:

1. embeds the validated question with the configured embedding provider;
2. searches the local SQLite vectors, optionally within one document;
3. collapses child hits belonging to the same parent section;
4. labels the selected sources `S1`, `S2` and so on and sends their parent
   context to the configured answer provider;
5. requests a typed result containing `answerable`, answer text and source IDs;
6. verifies the structured IDs and inline `[S#]` markers against the retrieved
   chunks; and
7. maps valid citations back to the retrieved child and the exact parent
   evidence excerpt supplied to the model, with available page, heading or line
   locations; and
8. atomically stores the successful user question and assistant response in the
   selected conversation, including immutable citation snapshots.

When visual analysis was enabled during ingestion, those sources can include a
page-cited model description of a diagram or image. The citation points back to
the original page and is labelled with a heading such as `Visual analysis: flow
diagram`, so workers can distinguish visual interpretation from extracted text.

Vector search always filters the joined document record to `current`. A
superseded or archived manual therefore cannot be selected even when its exact
document identifier is supplied.

The OpenAI implementation uses the Responses API with a Pydantic structured
output schema. The default answer model is `gpt-5.6-terra`. The provider is
fixed, while the model identifier remains an environment-managed deployment
setting.

## Grounding guarantees

The application can guarantee that every returned citation identifier refers to
one of the sources selected for that request. The response retains the child
retrieval anchor and identifies the parent context that was supplied. It rejects:

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

This is especially important for visual evidence: answer citation validation
proves which stored description was retrieved, but cannot prove that the earlier
vision model interpreted the source diagram correctly.

## Configuration

Answers require semantic retrieval. Add an OpenAI API key under **Settings → API
keys** to enable the fixed embedding and answer services. Model limits remain
environment-managed:

```env
AMA_ANSWER_MODEL=gpt-5.6-terra
AMA_ANSWER_MAX_OUTPUT_TOKENS=1000
```

The question and selected parent context leave the local machine when the OpenAI
answer provider is enabled. Original files, SQLite metadata and vectors remain
local. Completed user questions, assistant responses and their citations are
stored in the local SQLite data directory.

Stored conversation history is a record, not model memory. Earlier messages are
not automatically included in retrieval queries or sent to the answer provider.
Each follow-up is grounded independently using its own question and current
manual set. This prevents an earlier generated response from silently becoming
evidence for a later answer.

Documents must have vectors for the active embedding model and dimensions.
Re-submit an already ingested document while embeddings are enabled to backfill
missing vectors without creating a duplicate document.

## Current limitations

- The API returns complete responses rather than streaming partial text.
- The optional model reranker and its minimum score must still be calibrated
  against representative maintenance manuals.
- The retrieval dataset does not yet measure answer correctness or
  citation entailment against representative maintenance manuals.
- The local API has no authentication or rate limiting and must not be exposed
  directly to an untrusted network.
