import { assessmentFinding } from "./assessment";

const statusLabels: Record<string, string> = { resolved: "已修正", unresolved: "仍存在", superseded: "要求已撤销或替换", unknown: "未知" };

/** 只读取普通对象，旧报告和无效字段不推断为已解决。 */
function record(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

/** 固定轮次和组号仅接受正整数。 */
function positive(value: unknown): number | null {
  return typeof value === "number" && Number.isSafeInteger(value) && value > 0 ? value : null;
}

/** 将独立的问题现状及逐组依据映射为视图，不覆盖历史失败评分。 */
export function reportResolutions(result: Record<string, unknown> | null) {
  const metadata = record(result?.report);
  const available = metadata.resolutionVersion === 1 && metadata.resolutionComplete === true && Array.isArray(metadata.issueResolutions);
  const items = (available && Array.isArray(metadata.issueResolutions) ? metadata.issueResolutions : []).map((raw: unknown) => {
    const item = record(raw);
    const reviews = (Array.isArray(item.reviews) ? item.reviews : []).map((rawReview: unknown) => {
      const review = record(rawReview);
      const valid = review.valid === true;
      return { index: positive(review.reviewIndex), valid,
        state: valid && typeof review.status === "string" ? statusLabels[review.status] ?? "未知" : "无有效结论",
        reason: typeof review.reason === "string" ? review.reason : "未记录理由",
        references: valid ? assessmentFinding({ evidence_refs: review.evidence }).references : [] };
    });
    return { id: typeof item.issueId === "string" ? item.issueId : "未知问题",
      description: typeof item.description === "string" ? item.description : "未记录问题说明",
      throughTurn: positive(item.throughTurn),
      state: typeof item.status === "string" ? statusLabels[item.status] ?? "未知" : "未知",
      reason: typeof item.reason === "string" ? item.reason : "未记录理由", reviews };
  });
  return { available, items };
}
