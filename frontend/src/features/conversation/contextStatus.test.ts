import { describe, expect, it } from "vitest";
import { contextStatusLabels } from "./contextStatus";
import { mergeConversationEvent, restoreConversationTurns } from "./conversation";

describe("多轮上下文状态", () => {
  it("实时阶段按模型和阶段独立合并，保留零值和未知压缩状态", () => {
    const state = { conversation: null, turns: [{ turn: 1, prompt: "问题", answers: {}, status: "running" as const,
      assessmentsQueued: false, responses: [] }], activeTurn: 1, errorMessage: "" };
    const rewrite = mergeConversationEvent(state, { type: "context_ready", modelConfigId: 1, phase: "rewrite",
      compressed: false, estimatedTokens: 0, historyThroughTurn: 0 }, 1);
    const answer = mergeConversationEvent(rewrite, { type: "context_ready", modelConfigId: 1, phase: "answer",
      compressed: null, estimatedTokens: 50, historyThroughTurn: 0 }, 1);
    expect(answer.turns[0].contexts).toEqual([
      { modelConfigId: 1, phase: "rewrite", compressed: false, estimatedTokens: 0, historyThroughTurn: 0 },
      { modelConfigId: 1, phase: "answer", compressed: null, estimatedTokens: 50, historyThroughTurn: 0 },
    ]);
  });
  it("缺失状态保持未知，零值与未压缩有明确含义", () => {
    expect(contextStatusLabels(undefined, new Map())).toEqual(["上下文状态未记录"]);
    expect(contextStatusLabels([{ modelConfigId: 1, phase: "rewrite", compressed: false, estimatedTokens: 0,
      historyThroughTurn: 0 }], new Map([[1, "候选模型"]]))[0]).toBe("候选模型 · 检索改写上下文 · 未压缩 · 输入估计 0 Token · 无历史轮次");
  });

  it("恢复两阶段状态并只替换当前模型的普通流状态", () => {
    const contexts = [{ modelConfigId: 1, phase: "answer" as const, compressed: true, estimatedTokens: 120, historyThroughTurn: 2 }];
    const turns = restoreConversationTurns([{ id: 3, taskId: 3, turnIndex: 3, prompt: "追问", generationStatus: "generating",
      errorCode: null, createdAt: "now", contexts }]);
    const state = { conversation: null, turns, activeTurn: 3, errorMessage: "" };
    const updated = mergeConversationEvent(state, { type: "context_ready", modelConfigId: 2, compressed: true }, 3);
    const repeated = mergeConversationEvent(updated, { type: "context_ready", modelConfigId: 2, compressed: false }, 3);
    expect(repeated.turns[0].contexts).toHaveLength(2);
    expect(repeated.turns[0].contexts?.[0]).toEqual(contexts[0]);
    expect(repeated.turns[0].contexts?.[1].compressed).toBe(false);
    expect(contextStatusLabels(contexts, new Map())[0]).toContain("历史截至第 2 轮");
    expect(mergeConversationEvent(state, { type: "context_ready", modelConfigId: 2, compressed: true }, 4)).toEqual(state);
  });
});
