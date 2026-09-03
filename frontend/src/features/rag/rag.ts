import { parseThinkContent } from "../evaluation/content";
import type { DisplayModelResponse, EvaluationTaskState } from "../evaluation/types";
import type { KnowledgeBase, SourceLocation } from "../knowledge-bases/types";
import type { RagDecimal, RagEvaluationPayload, RagStage, RagStreamEvent } from "./types";

export const ragStageLabels: Record<RagStage, string> = { rewriting: "改写查询", retrieving: "检索资料", answering: "生成回答", judging: "三轮联合评审" };
export const RAG_EXTERNAL_NOTICE = "检索片段将发送给所选回答模型与评审模型；若使用外部 API，片段将离开本地部署环境。";
export const RAG_DELETE_NOTICE = "删除后不可再检索该文档；已完成评测中的引用片段仍会保留。";
export function buildRagPayload(options: { prompt: string; library: KnowledgeBase | null; modelIds: number[];
  judgeModelId: number | null; enableThinking: boolean }): RagEvaluationPayload {
  if (!options.library?.available || options.library.status !== "ready") throw new Error("请选择已就绪且有可检索文档的知识库");
  if (!options.prompt.trim() || !options.modelIds.length || new Set(options.modelIds).size !== options.modelIds.length) throw new Error("请填写问题并选择不重复的候选模型");
  if (options.judgeModelId === null || options.modelIds.includes(options.judgeModelId)) throw new Error("请为三轮评审保留一个空闲模型");
  return { taskType: "rag", knowledgeBaseId: options.library.id, prompt: options.prompt, modelIds: options.modelIds,
    judgeModelId: options.judgeModelId, enableJudge: true, enableThinking: options.enableThinking, visibility: "private" };
}
export function mergeRagStage(state: EvaluationTaskState | null, event: RagStreamEvent): EvaluationTaskState {
  const current = state ?? { taskId: null, status: "running", prompt: "", responses: [] };
  return { ...current, taskType: "rag", visibility: "private", responses: current.responses.map((response) =>
    response.modelConfigId !== event.modelConfigId ? response : { ...response,
      ...(event.type === "rag_stage" ? { ragStage: event.stage } : { ragRetrieval: { rewrittenQuery: event.rewrittenQuery, evidence: event.evidence } }) }) };
}
export function stopRagState(state: EvaluationTaskState | null): EvaluationTaskState | null {
  return state ? { ...state, status: "interrupted", responses: state.responses.map((response) =>
    typeof response.id === "string" ? { ...response, interrupted: true } : response) } : null;
}
export function displayRagResponse(response: DisplayModelResponse): DisplayModelResponse {
  if (!("rag" in response) || !response.rag || ["success", "failed"].includes(response.status)) return response;
  const stages = response.rag.stageUsage;
  const stage: RagStage = response.status === "answer_completed" || stages.some((usage) => usage.stage === "judge") ? "judging"
    : stages.some((usage) => usage.stage === "generate") ? "answering" : stages.some((usage) => usage.stage === "embed") ? "retrieving" : "rewriting";
  // 历史中已预创建但未完成的回答，不应显示成“调用失败”。
  return { id: `history-running-${response.id}`, modelConfigId: response.modelConfigId ?? -response.id, modelName: response.modelName,
    answer: response.answer, streaming: stage !== "judging", scoring: stage === "judging", ragStage: stage,
    ragRetrieval: response.rag.evidence.length ? { rewrittenQuery: response.rag.rewrittenQuery ?? "", evidence: response.rag.evidence } : undefined };
}
export function invalidCitationLabels(answer: string, labels: string[]): string[] {
  return [...new Set([...parseThinkContent(answer).answer.matchAll(/\[(S\d+)\]/g)].map((match) => match[1]))]
    .filter((label) => !labels.includes(label));
}
export function sourceLabel(source: SourceLocation): string {
  if (source.kind === "text") return `第 ${source.lineStart}–${source.lineEnd} 行`;
  if (source.kind === "pdf") return `第 ${source.pageStart}–${source.pageEnd} 页`;
  return `第 ${source.blockStart}–${source.blockEnd} 逻辑块（段落/表格）`;
}
export function formatRagNumber(value: RagDecimal | number | null | undefined, digits = 2): string {
  if (value === null || value === undefined) return "—";
  const number = Number(value);
  return Number.isFinite(number) ? number.toLocaleString("zh-CN", { maximumFractionDigits: digits }) : "—";
}
export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "请求未完成，请重试";
}
