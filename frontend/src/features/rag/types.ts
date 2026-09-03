import type { EvaluationTaskPayload } from "../../api/client";
import type { SourceLocation } from "../knowledge-bases/types";

// 后端 Decimal 在 RAG JSON 中以字符串传输，转换只用于显示，不在前端重算基础分。
export type RagDecimal = string;
export type RagStage = "rewriting" | "retrieving" | "answering" | "judging";
export interface RagEvidence {
  label: string; documentId: number; documentName: string; chunkId: string; indexRevision: number;
  text: string; similarity: number; source: SourceLocation;
}
export interface RagRetrieval { rewrittenQuery: string; evidence: RagEvidence[] }
export interface RagProgress { ragStage?: RagStage; ragRetrieval?: RagRetrieval; interrupted?: boolean }
export type RagStreamEvent =
  | { type: "rag_stage"; modelConfigId: number; stage: RagStage }
  | ({ type: "rag_retrieval"; modelConfigId: number } & RagRetrieval);
export interface RagModelSnapshot {
  modelConfigId: number; providerName: string; displayName: string; modelName: string;
  maxTokens: number; temperature: number; timeoutSeconds: number; currency: "CNY" | "USD";
  priceInput: RagDecimal; priceOutput: RagDecimal; priceCacheHit: RagDecimal; priceCacheCreation: RagDecimal;
}
export interface RagStageUsage {
  stage: "rewrite" | "embed" | "generate" | "judge"; runIndex: number; status: "pending" | "known" | "unknown";
  model: RagModelSnapshot | null; inputTokens: number | null; outputTokens: number | null;
  cacheHitTokens: number | null; cacheCreationTokens: number | null; totalTokens: number | null;
  latencyMs: number | null; estimatedCost: RagDecimal | null;
}
export interface RagClaim {
  claim: string; evidenceLabels: string[]; invalidCitationLabels: string[]; supported: boolean;
  needsCitation: boolean; citationSupported: boolean; reason: string;
}
export interface RagJudgeResult {
  answerQuality: RagDecimal; faithfulness: RagDecimal; citationCorrectness: RagDecimal;
  citationCompleteness: RagDecimal; claims: RagClaim[];
}
export interface RagJudgeRun {
  runIndex: number; promptCode: string; result: RagJudgeResult | null;
  rawResult: Record<string, unknown> | null; errorCode: string | null;
}
export interface RagJudgeAggregate {
  scoreStatus: "scored" | "judge_failed" | "judge_unstable"; validRunCount: number;
  answerQuality: RagDecimal | null; faithfulness: RagDecimal | null;
  citationCorrectness: RagDecimal | null; citationCompleteness: RagDecimal | null;
  ranges: Partial<Record<"answer_quality" | "faithfulness" | "citation_correctness" | "citation_completeness", RagDecimal>>;
}
export interface RagDetail {
  knowledgeBaseId: number; knowledgeBaseName: string; contentRevision: number; embeddingRevision: string;
  chunkSize: number; chunkOverlap: number; documentVersions: { documentId: number; indexRevision: number }[];
  rewrittenQuery: string | null; evidence: RagEvidence[]; stageUsage: RagStageUsage[];
  externalTotalTokens: number; costByCurrency: Record<string, RagDecimal>; hasUnknownUsage: boolean;
  judgeRuns: RagJudgeRun[]; judgeAggregate: RagJudgeAggregate | null;
  faithfulness: RagDecimal | null; citationCorrectness: RagDecimal | null; citationCompleteness: RagDecimal | null;
  ragFinal: RagDecimal | null; baseFinal: RagDecimal | null; scoreVersion: "rag-v1";
  failureStage: "rewrite" | "embed" | "retrieve" | "snapshot" | "generate" | "judge" | null;
  errorCode: string | null;
}
export interface RagEvaluationPayload extends EvaluationTaskPayload {
  taskType: "rag"; knowledgeBaseId: number; judgeModelId: number; enableJudge: true; visibility: "private";
}
