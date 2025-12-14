# Retrieval evaluation

## Purpose

Retrieval evaluation measures whether the search stage finds the passages that
maintenance workers need before those passages are sent to the answer model.
It provides a baseline for comparing chunk sizes, embedding configurations,
score thresholds and hybrid-retrieval settings.

The evaluator does not call the answer provider. It uses the configured
embedding provider to search the local corpus, so an OpenAI-backed run sends the
evaluation questions to the embeddings endpoint.

## Starter dataset

The repository includes three fictional manuals under `evals/corpus/` and 12
labelled questions in `evals/retrieval-cases.json`. Nine questions have a known
supporting passage and three deliberately ask for information that the manuals
do not contain. The documents are synthetic and are not operating instructions
for real equipment.

This starter set verifies the harness and exposes obvious regressions. It is too
small to establish production quality. Extend it with reviewed, representative
questions before making retrieval decisions; keep confidential manuals and
their passages outside Git.

## Run the baseline

Use a separate local data directory so the fictional manuals do not enter the
normal manual library:

```bash
export AMA_DATA_DIRECTORY=data/evaluation

for manual in evals/corpus/*.md; do
  ama-ingest "$manual"
done

ama-evaluate-retrieval evals/retrieval-cases.json \
  --limit 5 \
  --output data/evaluation/retrieval-report.json
```

Add the OpenAI key in the normal Settings page before running this workflow, or
set the `OPENAI_API_KEY` environment fallback in this shell. The evaluator and
CLI resolve the same encrypted application credential as the API.

The report contains every ranked result, aggregate metrics, embedding model and
dimensions, the configured token budget, overlap and encoding, and the hybrid
candidate, RRF and weighting settings. It does not include a timestamp, which
makes configuration-to-configuration comparisons easier. Use a fresh evaluation
data directory for each chunking experiment so the recorded settings match the
stored chunks.

An experimental score threshold can remove weak results:

```bash
ama-evaluate-retrieval evals/retrieval-cases.json \
  --limit 5 \
  --minimum-score 0.45
```

Do not adopt a threshold from the starter dataset alone. Calibrate it against a
larger set containing realistic answerable and unanswerable questions.

## Metrics

- **Hit rate at k** is the proportion of answerable questions with at least one
  expected source in the first `k` results.
- **Mean reciprocal rank** rewards putting the first relevant result near the
  top of the list.
- **Mean recall at k** measures how many labelled supporting passages were
  recovered.
- **Unanswerable no-result rate** is the proportion of unanswerable questions
  for which retrieval returned no chunks. Without a calibrated threshold, a
  nearest-neighbour search will usually return something and this value may be
  zero.
- **Latency** measures query embedding, vector ranking, full-text ranking and
  fusion in the current process; it does not include answer generation.

Source labels use a filename and a case-insensitive `text_contains` passage.
This keeps labels stable when chunk sequences change. A passage stops matching
if a chunking experiment splits its labelled text, which correctly exposes a
loss of retrievable evidence.

## Dataset format

Each case has a unique identifier, question, answerability label and zero or
more relevant passages:

```json
{
  "version": 1,
  "name": "Workshop retrieval review",
  "cases": [
    {
      "id": "pump-isolation",
      "query": "How do I isolate the pump?",
      "answerable": true,
      "relevant_sources": [
        {
          "document": "pump-manual.md",
          "text_contains": "close the suction valve"
        }
      ]
    }
  ]
}
```

An answerable case must have at least one relevant source. An unanswerable case
must have none.

## Regression gates

The command can return status `2` when measured quality falls below an explicit
gate:

```bash
ama-evaluate-retrieval evals/retrieval-cases.json \
  --fail-hit-rate-below 0.85 \
  --fail-mrr-below 0.70
```

Only add gates to CI after the evaluation corpus and embedding environment are
stable. The normal automated suite remains deterministic and does not require
an external provider.

## Comparing retrieval changes

For every experiment:

1. Keep the corpus, questions and labels unchanged.
2. Record the chunking and embedding configuration with the report.
3. Re-ingest into a fresh evaluation data directory.
4. Compare retrieval quality, unanswerable behaviour and latency.
5. Review individual misses before accepting an aggregate improvement.

Token-aware parent-child chunking and hybrid semantic/full-text retrieval now
provide the structural baseline. Use the recorded semantic and text weights to
compare dense-only, text-only and fused runs against the same reviewed dataset.
A later learned reranker should only replace RRF when the evaluation demonstrates
a material improvement.
