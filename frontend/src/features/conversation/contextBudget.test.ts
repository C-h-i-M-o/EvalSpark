import { describe, expect, it } from "vitest";
import { contextBudget } from "./contextBudget";

describe("会话输入预算", () => {
  const models = [
    { id: 1, displayName: "候选", providerName: "test", modelName: "one", contextWindow: 12000, maxTokens: 2000 },
    { id: 2, displayName: "评审", providerName: "test", modelName: "two", contextWindow: 5000, maxTokens: 1000 },
  ];
  it("候选和评审共同限制预算，并预留输出", () => {
    expect(contextBudget(models, [1], 2)).toMatchObject({ maximum: 4000, recommended: 4000, canCreate: true, unknownNames: [] });
    expect(contextBudget(models, [1], null).recommended).toBe(8192);
  });
  it("未知容量明确提示，不能越过已知模型的较小限制", () => {
    const unknown = { ...models[0]!, contextWindow: null };
    expect(contextBudget([unknown, models[1]!], [1], 2)).toMatchObject({ maximum: 4000, unknownNames: ["候选"] });
  });
  it("不足最小输入预算或模型已不可用时禁止创建", () => {
    expect(contextBudget([{ ...models[0]!, contextWindow: 2500 }], [1], null).canCreate).toBe(false);
    expect(contextBudget(models, [3], null).canCreate).toBe(false);
    expect(contextBudget(models, [], null).canCreate).toBe(false);
  });
});
