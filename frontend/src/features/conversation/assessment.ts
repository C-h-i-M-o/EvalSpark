import type { ConversationAssessmentDetailRead, ConversationAssessmentRead } from "../../api/conversationTypes";

const labels: Record<string, string> = {
  solution: "问题解决", context: "上下文理解", instruction: "要求遵循", expression: "表达质量",
  goal: "目标完成", memory: "长期信息保持", consistency: "前后一致", correction: "纠错能力", efficiency: "推进效率",
  faithfulness: "资料忠实度", citation_correctness: "引用正确性", citation_completeness: "引用完整性",
};

/** 用于卡片和详情的评分分组，未知信息保留明确文案。 */
export interface AssessmentGroupView {
  title: string;
  final: string;
  coverage: string;
  critical: string;
  dimensions: { label: string; value: string }[];
}

/** 安全读取公开 JSON 对象，不将数组或空值误当评分。 */
function record(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

/** 服务端 Decimal 字符串只按有限、指定范围内的数字展示。 */
function numberValue(value: unknown, maximum: number): number | null {
  if (typeof value !== "number" && !(typeof value === "string" && /^\d+(\.\d+)?$/.test(value))) return null;
  const number = Number(value);
  return Number.isFinite(number) && number >= 0 && number <= maximum ? number : null;
}

/** 根据暂评、普通正式复评和 RAG 正式复评结构生成独立分组。 */
export function assessmentGroups(assessment: ConversationAssessmentRead): AssessmentGroupView[] {
  if (!assessment.result) return [];
  const result = assessment.result;
  const dialogue = record(result.dialogue ?? result.score ?? (assessment.formal ? result : null));
  const groups: [string, Record<string, unknown>][] = [[result.report ? "会话表现" : "对话质量", dialogue]];
  if (result.evidence) groups.push(["资料与引用", record(result.evidence)]);
  return groups.filter(([, group]) => Object.keys(group).length > 0).map(([title, group]) => {
    const final = numberValue(group.final, 10);
    const coverage = numberValue(group.coverage, 1);
    const states = record(group.applicability);
    const dimensions = record(group.dimensions);
    const keys = title === "资料与引用" ? ["faithfulness", "citation_correctness", "citation_completeness"]
      : Object.keys(dimensions);
    return {
      title, final: final === null ? "未形成完整评分" : `${final.toFixed(2)} / 10`,
      coverage: coverage === null ? "未知" : `${Math.round(coverage * 100)}%`,
      critical: group.critical_passed === false ? "关键要求未通过" : group.critical_passed === true ? "未发现关键要求失败" : "关键要求未判定",
      dimensions: keys.map((key) => {
        const value = numberValue(dimensions[key] ?? group[key], 10);
        return { label: labels[key] ?? key, value: states[key] === "not_applicable" ? "不适用"
          : states[key] === "unknown" || value === null ? "无法判断" : value.toFixed(2) };
      }),
    };
  });
}

/** 展示后台评分状态，无法识别的值不伪装为成功。 */
export function assessmentStatus(status: string): string {
  return ({ queued: "等待评分", running: "评分中", provisional: "暂定评分", scored: "正式评分",
    judge_failed: "评审失败", judge_unstable: "评审分歧较大", incomplete: "评价材料不足", interrupted: "评审中断" } as Record<string, string>)[status] ?? "未知评分状态";
}

/** 仅作者可从已结束的暂评提交正式复评。 */
export function canReassess(assessment: ConversationAssessmentRead, owner: boolean): boolean {
  return owner && !assessment.formal && !["queued", "running"].includes(assessment.status);
}

/** 从逐次结果读取简短判定及原文依据，不展示内部输入快照。 */
export function assessmentFindings(detail: ConversationAssessmentDetailRead) {
  return detail.runs.map((run) => {
    const result = record(run.result);
    const items = [...(Array.isArray(result.items) ? result.items : []), ...(Array.isArray(result.ragAssertions) ? result.ragAssertions : [])];
    return { run: run.runIndex, status: assessmentStatus(run.status), findings: items.map(assessmentFinding) };
  });
}

/** 展示已校验的结构化来源，旧文本保留但不猜测其轮次。 */
export function assessmentFinding(raw: unknown) {
  const item = record(raw);
  const references = (Array.isArray(item.evidence_refs) ? item.evidence_refs : []).flatMap((rawReference: unknown) => {
    const reference = record(rawReference);
    return typeof reference.source_id === "string" && typeof reference.quote === "string"
      && typeof reference.turn === "number" && Number.isSafeInteger(reference.turn) && reference.turn > 0
      ? [{ sourceId: reference.source_id, turn: reference.turn, quote: reference.quote }] : [];
  });
  return { id: typeof item.id === "string" ? item.id : "未知检查项", critical: item.critical === true,
    state: item.applicability === "unknown" ? "待判断" : "判定依据",
    label: typeof item.dimension === "string" ? labels[item.dimension] ?? "要求检查" : "资料断言",
    reason: typeof item.reason === "string" ? item.reason : "未提供理由", references,
    evidence: Array.isArray(item.evidence) ? item.evidence.filter((value): value is string => typeof value === "string") : [] };
}
