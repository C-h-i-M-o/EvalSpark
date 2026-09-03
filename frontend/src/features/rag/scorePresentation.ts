import type { RagDetail, RagJudgeAggregate, RagJudgeResult, RagStageUsage } from "./types";

export const ragDimensionLabels: { key: Exclude<keyof RagJudgeResult, "claims">; rangeKey: keyof RagJudgeAggregate["ranges"]; label: string }[] = [
  { key: "answerQuality", rangeKey: "answer_quality", label: "回答质量" },
  { key: "faithfulness", rangeKey: "faithfulness", label: "忠实度" },
  { key: "citationCorrectness", rangeKey: "citation_correctness", label: "引用正确性" },
  { key: "citationCompleteness", rangeKey: "citation_completeness", label: "引用完整性" }
];
export const usageStageLabels: Record<RagStageUsage["stage"], string> = { rewrite: "查询改写", embed: "Embedding", generate: "回答生成", judge: "联合评审" };
export const failureStageLabels: Record<NonNullable<RagDetail["failureStage"]>, string> = {
  rewrite: "查询改写", embed: "Embedding", retrieve: "向量检索", snapshot: "证据快照", generate: "回答生成", judge: "联合评审"
};
