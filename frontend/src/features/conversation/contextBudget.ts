import type { AvailableModel } from "../../api/client";

/** 统一计算候选与默认摘要/评审模型的输入空间，未知容量不伪装为已验证。 */
export function contextBudget(models: AvailableModel[], selectedIds: number[], judgeId: number | null) {
  const ids = new Set([...selectedIds, ...(judgeId === null ? [] : [judgeId])]);
  const selected = [...ids].map((id) => models.find((model) => model.id === id));
  let maximum = 262144;
  const unknownNames: string[] = [];
  for (const model of selected) {
    if (!model) continue;
    if (typeof model.contextWindow !== "number" || !Number.isSafeInteger(model.contextWindow) || model.contextWindow < 2
      || typeof model.maxTokens !== "number" || !Number.isSafeInteger(model.maxTokens) || model.maxTokens < 1) {
      unknownNames.push(model.displayName);
    } else maximum = Math.min(maximum, Math.max(0, model.contextWindow - model.maxTokens));
  }
  const missing = selectedIds.length === 0 || selected.some((model) => !model);
  const canCreate = !missing && maximum >= 1024;
  const recommended = Math.min(8192, maximum);
  const message = missing ? "请选择当前可用模型。" : maximum < 1024
    ? "所选模型预留输出后的输入空间不足 1024 Token，请调整模型配置或选择其他模型。"
    : unknownNames.length > 0 ? `容量未知：${unknownNames.join("、")}。默认采用保守预算，供应商实际容量仍需管理员确认。`
    : `已预留各模型最大输出，可用输入上限为 ${maximum} Token。`;
  return { maximum, recommended, canCreate, unknownNames, message };
}
