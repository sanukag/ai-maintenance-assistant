# Visual document understanding

## Purpose

OCR transcribes visible characters but does not explain what a photograph,
schematic, wiring diagram, flow path, technical drawing, chart or table means.
The visual-analysis stage adds that missing maintenance context as traceable
text before chunking and embedding.

Visual analysis is opt-in. Original files remain in local storage, but a
rendered image of each PDF page or an uploaded PNG/JPEG document is sent to the
configured provider. The initial provider uses OpenAI's Responses API with
schema-constrained output and `store: false`.

## Processing flow

For every PDF, whether digital, scanned or mixed:

1. Extract embedded text with pypdf.
2. Use local Tesseract OCR only on pages without a usable text layer.
3. When visual analysis is enabled, render every page independently with
   PDFium. Analysing every page also covers vector diagrams embedded in digital
   PDFs; these are not necessarily exposed as separate image files.
4. Ask the provider whether the page contains maintenance-relevant visual
   content. Text-only pages, logos and decorative graphics return no visual
   segment.
5. Convert a valid result into deterministic text covering its visual type,
   summary, components, relationships or flow, visible labels, safety-relevant
   details and explicit uncertainties.
6. Attach that text to the original page number and a heading such as
   `Visual analysis: wiring diagram`.
7. Pass extracted text, OCR text and visual descriptions through the same
   normalisation, parent/child chunking, embedding, SQLite FTS5 indexing,
   semantic retrieval and grounded-answer citation workflow.

Standalone PNG/JPEG documents follow the same OCR-plus-visual path. A useful
visual description can make an image ingestible even when it contains no text.

## Enable the complete retrieval path

Set these values in the untracked `.env` file:

```env
AMA_VISUAL_ANALYSIS_PROVIDER=openai
AMA_VISUAL_ANALYSIS_MODEL=gpt-5.6-terra
AMA_VISUAL_ANALYSIS_DETAIL=high
AMA_EMBEDDING_PROVIDER=openai
OPENAI_API_KEY=your-project-api-key
```

Then rebuild or restart the application:

```bash
docker compose up --build --detach --wait
```

The Settings page and `GET /health` report whether visual analysis is active.
Newly uploaded manuals are enriched immediately. Re-index an existing manual
after enabling the provider so its stored chunks and vectors gain visual
descriptions.

The default model and `high` detail level follow the current OpenAI guidance
that image-capable models accept Base64 data URLs through the Responses API and
that `high` is the standard high-fidelity mode. See the official
[images and vision guide](https://developers.openai.com/api/docs/guides/images-vision)
and [model catalogue](https://developers.openai.com/api/docs/models).

## Bounds and failure behaviour

The stage is intentionally bounded:

- at most 100 PDF pages per ingestion by default;
- 150 DPI PDF rendering by default;
- at most 25 million pixels in one rendered page;
- a 60-second provider timeout and 1,000 output tokens per page; and
- one schema-constrained result per page, processed synchronously.

Limits are configurable through the `AMA_VISUAL_ANALYSIS_*` settings in
`.env.example`. Exceeding a page or pixel limit fails before partial content is
stored. Provider errors and invalid structured results fail ingestion with
`visual_analysis_failed`; timeouts use `visual_analysis_timed_out`. Storage is
transactional, so a failed visual stage does not leave a partial document.

The prompt treats all visible page content as untrusted data. Instructions
printed inside a manual or diagram are never allowed to override the analysis
rules. The provider is told not to invent missing steps and to record ambiguity
explicitly.

## Accuracy boundary

Visual descriptions improve discovery and grounding; they do not turn a model
interpretation into an approved procedure. Vision models can misread small or
rotated labels, colours, dashed versus solid lines, precise spatial relations,
counts and poor-quality images. Multi-page drawings are analysed page by page,
so relationships that exist only across separate sheets may be incomplete.

Workers must inspect the cited source page before acting on safety-critical
information. The original document remains the authority.
