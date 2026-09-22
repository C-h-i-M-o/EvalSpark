import { describe, expect, it } from "vitest";
import { assessmentFinding, assessmentGroups, assessmentStatus, canReassess } from "./assessment";
import type { ConversationAssessmentRead } from "../../api/conversationTypes";

/** 构造服务端返回的评分，不以旧单轮分数冒充多轮成绩。 */
function assessment(result: Record<string, unknown>, formal = false): ConversationAssessmentRead {
  return { id: 1, responseId: 2, modelConfigId: 3, throughTurn: 4, scoreVersion: "chat-multiturn-v1",
    formal, status: formal ? "scored" : "provisional", result, createdAt: "now", completedAt: "now" };
}

describe("多轮评分展示", () => {
  it("只使用结构化引用定位轮次，旧文本和非法轮次不参与推断", () => {
    const finding = assessmentFinding({ id: "memory:1", dimension: "memory", applicability: "unknown", critical: true,
      evidence: ["turn:99:user: 旧格式"], evidence_refs: [
        { source_id: "response:7", turn: 3, quote: "预算无限" }, { source_id: "x", turn: "99", quote: "非法轮次" },
      ] });
    expect(finding).toMatchObject({ id: "memory:1", state: "待判断", critical: true,
      references: [{ sourceId: "response:7", turn: 3, quote: "预算无限" }] });
    expect(assessmentFinding({ evidence: ["turn:99:user: 旧格式"] }).references).toEqual([]);
  });
  it("保留零分、N/A 和未知，未知不能显示为零分", () => {
    const groups = assessmentGroups(assessment({ score: { final: null, coverage: "0.73", critical_passed: false,
      dimensions: { solution: "0", context: null, instruction: null, expression: "5" },
      applicability: { solution: "applicable", context: "not_applicable", instruction: "unknown", expression: "applicable" } } }));
    expect(groups[0].final).toBe("未形成完整评分");
    expect(groups[0].coverage).toBe("73%");
    expect(groups[0].critical).toBe("关键要求未通过");
    expect(groups[0].dimensions.map((item) => item.value)).toEqual(["0.00", "不适用", "无法判断", "5.00"]);
  });

  it("支持普通正式分和 RAG 两组分，不混入旧规则评分", () => {
    expect(assessmentGroups(assessment({ final: "8", coverage: "1", dimensions: { solution: "8" } }, true))[0].final).toBe("8.00 / 10");
    const groups = assessmentGroups(assessment({ dialogue: { final: "8", coverage: "1", dimensions: {} },
      evidence: { final: "5", coverage: "1", dimensions: { faithfulness: "5" } } }, true));
    expect(groups.map((item) => item.title)).toEqual(["对话质量", "资料与引用"]);
  });

  it("未完成或正式来源不能递归复评，失败状态文案明确", () => {
    const source = assessment({});
    expect(canReassess(source, true)).toBe(true);
    expect(canReassess({ ...source, status: "running" }, true)).toBe(false);
    expect(canReassess(source, false)).toBe(false);
    expect(canReassess({ ...source, formal: true }, true)).toBe(false);
    expect(assessmentStatus("judge_unstable")).toBe("评审分歧较大");
  });
});
