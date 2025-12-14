export type Health = {
  status: string;
  storage: string;
  ocr: "available" | "unavailable" | "disabled";
  ocr_engine: string | null;
  ocr_version: string | null;
  visual_analysis: "available" | "unavailable" | "disabled";
  visual_analysis_model: string | null;
  embeddings: "enabled" | "disabled";
  embedding_model: string | null;
  answers: "enabled" | "disabled";
  answer_model: string | null;
  vector_store: "sqlite" | "qdrant";
  vector_index: "available" | "unavailable" | "disabled";
  reranking: "enabled" | "disabled";
  rerank_model: string | null;
};

export type RuntimeMetrics = {
  started_at: string;
  uptime_seconds: number;
  requests_total: number;
  requests_in_flight: number;
  errors_total: number;
  routes: Array<{
    method: string;
    route: string;
    count: number;
    errors: number;
    average_duration_ms: number;
    maximum_duration_ms: number;
  }>;
  embedding_cache: { entries: number; hits: number; maximum_entries: number };
  sqlite: { journal_mode: string; synchronous: number; busy_timeout_ms: number };
};

export type DocumentMetadata = {
  brand: string[];
  machine: string[];
  site: string[];
  document_type: string[];
};

export type MetadataOptions = DocumentMetadata;

export type DocumentRecord = {
  id: string;
  original_filename: string;
  format: "pdf" | "text" | "markdown" | "image";
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
  metadata: DocumentMetadata;
};

export type DocumentList = {
  items: DocumentRecord[];
  limit: number;
  offset: number;
};

export type IngestionJob = {
  id: string;
  original_filename: string;
  metadata: DocumentMetadata;
  status: "queued" | "processing" | "cancel_requested" | "completed" | "failed" | "cancelled";
  stage: string;
  progress: number;
  attempts: number;
  document_id: string | null;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  completed_at: string | null;
};

export type IngestionJobList = { items: IngestionJob[] };

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
  conversation_id: string;
  question: string;
  answerable: boolean;
  answer: string;
  citations: AnswerCitation[];
  model: string;
  usage: { input_tokens: number; output_tokens: number };
};

export type ConversationSummary = {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
};

export type ConversationList = {
  items: ConversationSummary[];
  limit: number;
  offset: number;
};

export type ConversationCitation = {
  source_id: string;
  score: number;
  document_id: string;
  document_title: string;
  original_filename: string;
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

export type ConversationMessage = {
  id: string;
  sequence: number;
  role: "user" | "assistant";
  content: string;
  created_at: string;
  scope_document_id: string | null;
  answerable: boolean | null;
  model: string | null;
  usage: { input_tokens: number; output_tokens: number } | null;
  citations: ConversationCitation[];
  feedback: "up" | "down" | null;
  scope_metadata: DocumentMetadata;
};

export type ConversationDetail = {
  conversation: ConversationSummary;
  messages: ConversationMessage[];
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

export function sourceLocation(citation: Pick<AnswerCitation, "page_start" | "page_end" | "headings" | "line_start" | "line_end" | "chunk_sequence">): string {
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
