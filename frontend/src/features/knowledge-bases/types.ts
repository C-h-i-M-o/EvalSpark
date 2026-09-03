export type KnowledgeBaseStatus = "empty" | "indexing" | "ready" | "reindex_required" | "failed" | "deleting" | "deleted";
export type DocumentStatus = "queued" | "parsing" | "embedding" | "indexing" | "ready" | "failed" | "deleting" | "deleted";
export interface PageResult<T> { items: T[]; total: number; page: number; pageSize: number }
export interface KnowledgeBase {
  id: number; name: string; description: string | null; chunkSize: number; chunkOverlap: number;
  status: KnowledgeBaseStatus; contentRevision: number; documentCount: number; chunkCount: number;
  available: boolean; errorCode: string | null; createdAt: string; updatedAt: string;
}
export interface KnowledgeBaseForm { name: string; description: string; chunkSize: number; chunkOverlap: number }
export interface RagJob {
  id: string; knowledgeBaseId: number; documentId: number | null;
  operation: "index" | "reindex" | "delete_document" | "delete_knowledge_base";
  targetRevision: number; status: "queued" | "running" | "succeeded" | "failed";
  stage: string; processedCount: number; totalCount: number; attempt: number;
  errorCode: string | null; dispatchPending: boolean; createdAt: string; updatedAt: string;
}
export interface KnowledgeDocument {
  id: number; knowledgeBaseId: number; originalName: string; mediaType: string; sizeBytes: number;
  status: DocumentStatus; indexRevision: number; chunkCount: number; errorCode: string | null;
  currentJob: RagJob | null; createdAt: string; updatedAt: string;
}
export interface JobAccepted { documentId: number | null; jobId: string; status: RagJob["status"]; dispatchPending: boolean }
export type SourceLocation =
  | { kind: "text"; lineStart: number; lineEnd: number }
  | { kind: "pdf"; pageStart: number; pageEnd: number }
  | { kind: "docx"; blockStart: number; blockEnd: number; blockType: "paragraph" | "table" | "mixed" };
