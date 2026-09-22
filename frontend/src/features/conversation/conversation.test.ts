import { describe, expect, it } from "vitest";

import { createConversationRequestKey, mergeConversationEvent, restoreConversationTurns } from "./conversation";

describe("普通多轮工作台状态", () => {
  it("按模型归并回答增量并完成轮次", () => {
    const initial = { conversation: null, turns: [{ turn: 1, prompt: "问题", answers: {}, status: "running" as const, assessmentsQueued: false, responses: [] }], activeTurn: 1, errorMessage: "" };
    const withDelta = mergeConversationEvent(initial, { type: "delta", modelConfigId: 2, delta: "答" }, 1);
    const completed = mergeConversationEvent(withDelta, { type: "turn_completed", turnId: 1, taskId: 2 }, 1);
    expect(completed.turns[0]?.answers[2]).toBe("答");
    expect(completed.turns[0]?.status).toBe("completed");
  });

  it("忽略过期轮次事件并恢复轮次摘要", () => {
    const restored = restoreConversationTurns([{ id: 1, taskId: 2, turnIndex: 1, prompt: "旧问题", generationStatus: "completed", errorCode: null, createdAt: "now" }]);
    const state = { conversation: null, turns: restored, activeTurn: null, errorMessage: "" };
    expect(mergeConversationEvent(state, { type: "delta", modelConfigId: 2, delta: "过期" }, 2)).toEqual(state);
  });

  it("请求键满足后端幂等键约束", () => {
    expect(createConversationRequestKey()).toMatch(/^[A-Za-z0-9_-]+$/);
  });

  it("服务端确认轮次时保留问题和已有增量", () => {
    const initial = { conversation: null, turns: [{ turn: 1, prompt: "预算是多少", answers: { 2: "回答" }, status: "running" as const, assessmentsQueued: false, responses: [] }], activeTurn: 1, errorMessage: "" };
    const confirmed = mergeConversationEvent(initial, { type: "turn_started", turnId: 11, taskId: 21, turn: 1, replayed: false }, 1);
    expect(confirmed.turns[0].prompt).toBe("预算是多少");
    expect(confirmed.turns[0].answers[2]).toBe("回答");
  });

  it("评分提交失败不抹除已生成回答", () => {
    const initial = { conversation: null, turns: [{ turn: 1, prompt: "问题", answers: { 2: "回答" }, status: "completed" as const, assessmentsQueued: false, responses: [] }], activeTurn: null, errorMessage: "" };
    const failed = mergeConversationEvent(initial, { type: "assessment_submission_failed", turnId: 1, message: "评分提交失败" }, 1);
    expect(failed.errorMessage).toBe("评分提交失败");
    expect(failed.turns[0].answers[2]).toBe("回答");
  });

  it("流式增量同步写入回答卡片，并保留 RAG 阶段与证据", () => {
    const initial = { conversation: null, turns: [{ turn: 1, prompt: "问题", answers: {}, status: "running" as const, assessmentsQueued: false,
      responses: [{ id: "pending-2", modelConfigId: 2, modelName: "模型", pending: true as const }] }], activeTurn: 1, errorMessage: "" };
    const staged = mergeConversationEvent(initial, { type: "rag_stage", modelConfigId: 2, stage: "retrieving" }, 1);
    const retrieved = mergeConversationEvent(staged, { type: "rag_retrieval", modelConfigId: 2, rewrittenQuery: "改写问题", evidence: [] }, 1);
    const answered = mergeConversationEvent(retrieved, { type: "delta", modelConfigId: 2, delta: "回答" }, 1);
    const response = answered.turns[0]?.responses[0];
    expect(response?.ragStage).toBe("retrieving");
    expect(response?.ragRetrieval?.rewrittenQuery).toBe("改写问题");
    expect(response && "answer" in response ? response.answer : "").toBe("回答");
    expect(response && "pending" in response).toBe(false);
    const completed = mergeConversationEvent(answered, { type: "answer_completed", modelConfigId: 2,
      responseId: 9, status: "success", errorCode: null }, 1).turns[0].responses[0];
    expect(completed && "scoring" in completed && completed.scoring).toBe(true);
    expect(completed.ragStage).toBeUndefined();
  });
});
