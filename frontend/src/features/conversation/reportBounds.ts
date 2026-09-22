import type { ConversationRead } from "../../api/conversationTypes";

/** 从会话全局快照读取报告边界，不受历史分页影响。 */
export function reportBounds(conversation: ConversationRead | null) {
  const configured = conversation?.configuration.modelIds;
  const modelIds = Array.isArray(configured)
    ? configured.filter((id): id is number => typeof id === "number" && Number.isSafeInteger(id) && id > 0) : [];
  const current = conversation?.currentTurn ?? 0;
  const maxTurn = Math.max(0, current - (conversation?.generationStatus === "idle" ? 0 : 1));
  const judge = conversation?.configuration.judgeModelId;
  return { modelIds, maxTurn, judgeEnabled: typeof judge === "number" && Number.isSafeInteger(judge) && judge > 0 };
}
