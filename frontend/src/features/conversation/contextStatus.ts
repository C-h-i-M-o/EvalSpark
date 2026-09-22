import type { ContextStatusRead } from "../../api/conversationTypes";

/** 将最小上下文元数据转为提示，读取范围不表示摘要的覆盖范围。 */
export function contextStatusLabels(contexts: ContextStatusRead[] | undefined, names: ReadonlyMap<number, string>): string[] {
  if (!contexts?.length) return ["上下文状态未记录"];
  const phases = { chat: "回答上下文", rewrite: "检索改写上下文", answer: "RAG 回答上下文" };
  return contexts.map((item) => {
    const compressed = item.compressed === null ? "压缩状态未知" : item.compressed ? "已压缩" : "未压缩";
    const tokens = item.estimatedTokens === null ? "输入估计量未知" : `输入估计 ${item.estimatedTokens} Token`;
    const history = item.historyThroughTurn === null ? "历史范围未知" : item.historyThroughTurn === 0 ? "无历史轮次" : `历史截至第 ${item.historyThroughTurn} 轮`;
    return `${names.get(item.modelConfigId) ?? `模型 ${item.modelConfigId}`} · ${phases[item.phase]} · ${compressed} · ${tokens} · ${history}`;
  });
}
