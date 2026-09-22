import { describe, expect, it } from "vitest";
import type { ConversationRead } from "../../api/conversationTypes";
import { reportBounds } from "./reportBounds";

/** 构造全局轮次快照，列表中是否加载历史页不影响报告范围。 */
function conversation(generationStatus = "idle"): ConversationRead {
  return { id: 1, title: "连续问答", mode: "rag", ownerId: 1, canContinue: true, visibility: "private",
    currentTurn: 42, generationStatus, configuration: { modelIds: [2, 5, "9", null, -1], judgeModelId: 6 }, createdAt: "now", updatedAt: null };
}

describe("报告固定范围", () => {
  it("保留全局已结束轮次且仅接受冻结的有效模型标识", () => {
    expect(reportBounds(conversation())).toEqual({ modelIds: [2, 5], maxTurn: 42, judgeEnabled: true });
  });
  it("正在生成的最新一轮不进入可提交范围", () => {
    expect(reportBounds(conversation("running")).maxTurn).toBe(41);
  });
  it("未创建或未配置评审模型时不能提交", () => {
    expect(reportBounds(null)).toEqual({ modelIds: [], maxTurn: 0, judgeEnabled: false });
    expect(reportBounds({ ...conversation(), configuration: { modelIds: [2] } }).judgeEnabled).toBe(false);
  });
});
