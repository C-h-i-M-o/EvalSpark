import { describe, expect, test } from "vitest";
import { buildRagPayload, getRagJudgeModels, mergeRagStage, invalidCitationLabels } from "./rag";
import { mergeStreamEvent } from "../evaluation/evaluation";
import type { KnowledgeBase } from "../knowledge-bases/types";

const library: KnowledgeBase = { id: 1, name: "制度", description: null, chunkSize: 800, chunkOverlap: 120,
  status: "ready", contentRevision: 2, documentCount: 1, chunkCount: 3, available: true, errorCode: null, createdAt: "", updatedAt: "" };
describe("RAG 请求与逐候选状态", () => {
  test("相同供应商和模型的重复配置不能参与自我评审", () => {
    const models = [
      { id: 1, providerName: "provider", modelName: "model-a", displayName: "候选" },
      { id: 2, providerName: "provider", modelName: "model-a", displayName: "重复配置" },
      { id: 3, providerName: "provider", modelName: "model-b", displayName: "评审" }
    ];
    expect(getRagJudgeModels(models, [1]).map((model) => model.id)).toEqual([3]);
    expect(getRagJudgeModels(models, [1, 3])).toEqual([]);
  });
  test("固定私有、强制使用不同的 Judge", () => {
    const options = { prompt: " 问题 ", library, modelIds: [1], judgeModelId: 2, enableThinking: false };
    expect(buildRagPayload(options)).toMatchObject({ taskType: "rag", visibility: "private", enableJudge: true, prompt: " 问题 " });
    expect(buildRagPayload({ ...options, visibility: "public" }).visibility).toBe("public");
    expect(mergeRagStage({ taskId: 1, status: "running", prompt: "问题", visibility: "public", responses: [] },
      { type: "rag_stage", modelConfigId: 1, stage: "rewriting" }).visibility).toBe("public");
    expect(() => buildRagPayload({ ...options, judgeModelId: 1 })).toThrow();
    expect(() => buildRagPayload({ ...options, judgeModelId: null })).toThrow();
    expect(() => buildRagPayload({ ...options, library: { ...library, status: "indexing", available: false } })).toThrow();
  });
  test("新事件不会落入 task_completed 分支", () => {
    const state = mergeStreamEvent(null, { type: "rag_stage", modelConfigId: 1, stage: "rewriting" });
    expect(state.status).toBe("running");
    expect(mergeRagStage({ ...state, responses: [{ id: "p", modelConfigId: 1, modelName: "模型", pending: true }] },
      { type: "rag_stage", modelConfigId: 1, stage: "retrieving" }).responses[0].ragStage).toBe("retrieving");
  });
  test("不存在的引用独立提示，思考内容不参与引用判断", () => {
    expect(invalidCitationLabels("<think>[S8]</think>回答 [S1] [S9]", ["S1"])).toEqual(["S9"]);
  });
});
