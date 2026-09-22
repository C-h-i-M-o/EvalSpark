import { assessmentFinding, assessmentStatus } from "./assessment";

const dimensionLabels: Record<string, string> = { goal: "目标完成", memory: "长期信息保持", consistency: "前后一致", correction: "纠错能力", efficiency: "推进效率" };

/** 只读取普通对象，兼容尚未完成或历史缺字段的报告。 */
function record(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

/** 解析后端 Decimal 字符串，拒绝负数、非有限值和超出范围的值。 */
function numeric(value: unknown, maximum: number): number | null {
  if (typeof value !== "number" && !(typeof value === "string" && /^\d+(\.\d+)?$/.test(value))) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0 && parsed <= maximum ? parsed : null;
}

/** 将有效比例转换为百分比，缺失值保留未知。 */
function percentage(value: unknown): string {
  const parsed = numeric(value, 1);
  return parsed === null ? "未知" : `${Math.round(parsed * 100)}%`;
}

/** 机会和轮次只接受非负整数，不能用默认零掩盖字段缺失。 */
function count(value: unknown): number | null {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : null;
}

/** 生成五维逐组统计，未知、不适用与成功分别保留。 */
function opportunityDimensions(value: unknown) {
  const dimensions = record(value);
  return Object.entries(dimensionLabels).map(([key, label]) => {
    const item = record(dimensions[key]);
    return { key, label, opportunities: String(count(item.opportunities) ?? "未知"),
      successful: String(count(item.successful) ?? "未知"), unknown: String(count(item.unknown) ?? "未知"),
      notApplicable: String(count(item.notApplicable) ?? "未知"),
      coverageChecked: String(count(item.coverageChecked) ?? "未知"),
      coverageUnknown: String(count(item.coverageUnknown) ?? "未知"),
      failed: Array.isArray(item.failedCheckIds) ? item.failedCheckIds.filter((id): id is string => typeof id === "string").join("、") || "无" : "未知" };
  });
}

/** 展示报告创建时冻结的统计，不将三次评审或不同轮次总分混合。 */
export function reportStatistics(result: Record<string, unknown> | null) {
  const metadata = record(result?.report);
  const turnCount = count(metadata.turnCount);
  const successful = count(metadata.successfulResponses);
  const generationRate = turnCount !== null && turnCount > 0 && successful !== null && successful <= turnCount
    ? percentage(successful / turnCount) : "未知";
  const reviews = (Array.isArray(metadata.opportunityReviews) ? metadata.opportunityReviews : []).map((raw: unknown) => {
    const review = record(raw);
    return { index: count(review.reviewIndex), valid: review.valid === true,
      dimensions: review.valid === true ? opportunityDimensions(review.dimensions) : [],
      findings: review.valid === true && Array.isArray(review.findings) ? review.findings.map(assessmentFinding) : [] };
  });
  const trend = (Array.isArray(metadata.ragEvidenceTrend) ? metadata.ragEvidenceTrend : []).map((raw: unknown) => {
    const item = record(raw);
    const evidence = record(item.evidence);
    const final = numeric(evidence.final, 10);
    const status = typeof evidence.status === "string" ? evidence.status : item.status;
    return { turn: count(item.turn), status: status === "not_assessed" ? "未评分" : assessmentStatus(typeof status === "string" ? status : ""),
      final: final === null ? "未知" : `${final.toFixed(2)} / 10`, coverage: percentage(evidence.coverage) };
  });
  return { generationRate, sourceCoverage: percentage(metadata.sourceCoverage), scoreCoverage: percentage(result?.coverage), reviews, trend };
}
