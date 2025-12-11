export type Health = {
  status: string;
  storage: string;
  embeddings: "enabled" | "disabled";
  embedding_model: string | null;
  answers: "enabled" | "disabled";
  answer_model: string | null;
};

export type DocumentRecord = {
  id: string;
  original_filename: string;
  format: "pdf" | "text" | "markdown";
  size_bytes: number;
  title: string;
  page_count: number | null;
  chunk_count: number;
  extractor_name: string;
  extractor_version: string;
  created_at: string;
  lifecycle_status: "current" | "superseded" | "archived";
  revision: number;
  supersedes_document_id: string | null;
  lifecycle_updated_at: string;
};

export type DocumentList = {
  items: DocumentRecord[];
  limit: number;
  offset: number;
};

export type AnswerCitation = {
  source_id: string;
  score: number;
  document: DocumentRecord;
  chunk_id: string;
  chunk_sequence: number;
  parent_context_id: string | null;
  excerpt: string;
  page_start: number | null;
  page_end: number | null;
  headings: string[];
  line_start: number | null;
  line_end: number | null;
};

export type GroundedAnswer = {
  question: string;
  answerable: boolean;
  answer: string;
  citations: AnswerCitation[];
  model: string;
  usage: { input_tokens: number; output_tokens: number };
};

export type ApiFailure = {
  error?: { code?: string; message?: string };
  detail?: string;
};

export async function readJson<T>(response: Response): Promise<T> {
  const payload = (await response.json()) as T | ApiFailure;
  if (!response.ok) {
    const failure = payload as ApiFailure;
    throw new Error(
      failure.error?.message ?? failure.detail ?? "Something went wrong. Please try again.",
    );
  }
  return payload as T;
}

export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function sourceLocation(citation: AnswerCitation): string {
  if (citation.page_start !== null) {
    return citation.page_end && citation.page_end !== citation.page_start
      ? `Pages ${citation.page_start}–${citation.page_end}`
      : `Page ${citation.page_start}`;
  }
  if (citation.headings.length) return citation.headings.join(" › ");
  if (citation.line_start !== null) {
    return citation.line_end && citation.line_end !== citation.line_start
      ? `Lines ${citation.line_start}–${citation.line_end}`
      : `Line ${citation.line_start}`;
  }
  return `Section ${citation.chunk_sequence + 1}`;
}
