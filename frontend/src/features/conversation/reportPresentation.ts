import type { ConversationAssessmentDetailRead, ConversationAssessmentRead } from "../../api/conversationTypes";
import { assessmentFindings } from "./assessment";
import { reportStatistics } from "./reportStatistics";
import { reportResolutions } from "./reportResolutions";

/** 将会话冻结的有效模型转换为报告选项。 */
export function conversationModelOptions(modelIds: number[], names: Map<number, string>) {
  return modelIds.map((value) => ({ value, label: names.get(value) ?? `模型 ${value}` }));
}

/** 从服务端 result.report 读取元数据，不将缺失成功次数当作零。 */
export function reportMetadata(result: Record<string, unknown> | null) {
  const raw = result?.report;
  const metadata = raw && typeof raw === "object" && !Array.isArray(raw) ? raw as Record<string, unknown> : {};
  return {
    limitations: Array.isArray(metadata.limitations) ? metadata.limitations.filter((item): item is string => typeof item === "string") : [],
    failedGenerationTurns: Array.isArray(metadata.failedGenerationTurns) ? metadata.failedGenerationTurns.filter((item): item is number => typeof item === "number" && Number.isSafeInteger(item) && item > 0) : [],
    successfulResponses: typeof metadata.successfulResponses === "number" ? metadata.successfulResponses : null,
  };
}

/** 为每份报告保留模型分支和固定截止轮次标签。 */
export function reportRows(reports: ConversationAssessmentRead[], names: Map<number, string>) {
  return reports.map((report) => ({ report, title: `${names.get(report.modelConfigId) ?? `模型 ${report.modelConfigId}`} · 截至第 ${report.throughTurn} 轮`,
    ...reportMetadata(report.result), statistics: reportStatistics(report.result), resolutions: reportResolutions(report.result) }));
}

/** 读取合法的评审序号及分段序号，兼容旧单段结果。 */
export function reviewPresentation(detail: ConversationAssessmentDetailRead, runIndex: number) {
  const result = detail.runs.find((item) => item.runIndex === runIndex)?.result;
  const review = result?.reviewIndex;
  const batch = result?.batchIndex;
  return { reviewIndex: typeof review === "number" && Number.isSafeInteger(review) && review > 0 ? review : runIndex,
    batchIndex: typeof batch === "number" && Number.isSafeInteger(batch) && batch > 0 ? batch : null };
}

/** 合并原文判定与分段标题，使组件只负责渲染。 */
export function reportDetailRows(detail: ConversationAssessmentDetailRead | null) {
  return detail ? assessmentFindings(detail).map((run) => ({ ...run, ...reviewPresentation(detail, run.run) })) : [];
}

/** 只读报告卡片不发起单轮复评。 */
export function unavailableReassess(): Promise<void> { return Promise.resolve(); }
