# Retrieval reranking

Hybrid retrieval remains the fast first stage: Qdrant or SQLite semantic search
and SQLite full-text search contribute candidates which are fused with weighted
reciprocal rank fusion. An optional second stage asks a schema-constrained
OpenAI Responses model to score that bounded candidate set against the worker's
question.

The reranker receives only the question, candidate text, safe document title,
source location and fused score. Candidate content is explicitly treated as
untrusted evidence. The response must contain one score from `0` to `1` for
every supplied chunk ID, exactly once. Missing, duplicate or unknown IDs cause
the request to fall back to the deterministic fused order.

Reranking becomes available with the OpenAI key managed in Settings. Tune its
bounded model settings with:

```text
AMA_RERANK_MODEL=gpt-5.6-terra
AMA_RERANK_CANDIDATE_LIMIT=15
AMA_RERANK_MIN_SCORE=0.25
AMA_RERANK_MAX_OUTPUT_TOKENS=1000
```

`AMA_RERANK_CANDIDATE_LIMIT` bounds model input and latency. The minimum score
removes weakly related evidence before grounded answering; if no candidate
passes, the answer service returns its existing insufficient-evidence response
without calling the answer provider.

Search responses preserve `semantic_score`, `lexical_score` and `fusion_score`
for diagnosis. When reranking succeeds, `score` and `rerank_score` contain the
second-stage relevance score. Reranking is disabled by default, so local-only
installations retain their earlier retrieval behaviour and send no candidate
text to OpenAI.

The threshold is an operational control, not a universal confidence
calibration. Measure it with representative manuals and the retrieval
evaluation harness before raising it in a working installation.
