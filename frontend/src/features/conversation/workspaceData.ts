import { listConversationAssessments, listConversationTurns } from "../../api/conversations";
import { getEvaluationTask } from "../../api/client";
import type { ConversationAssessmentRead } from "../../api/conversationTypes";
import { restoreConversationTurns } from "./conversation";
import type { ConversationTurnState } from "./conversation";

export const CONVERSATION_PAGE_SIZE = 10;
export type ResponseAssessments = Record<number, ConversationAssessmentRead[]>;

/** 仅加载当前页回答的评分，响应中保留暂评及正式复评。 */
export async function loadResponseAssessments(conversationId: number, responseIds: number[], signal: AbortSignal): Promise<ResponseAssessments> {
  const result: ResponseAssessments = {};
  for (const responseId of responseIds) {
    signal.throwIfAborted();
    result[responseId] = (await listConversationAssessments(conversationId, 1, 20, responseId, signal)).items;
  }
  return result;
}

/** 分页读取轮次及完整回答，限制同时加载数量而不截断长会话。 */
export async function loadWorkspacePage(conversationId: number, page: number, signal: AbortSignal): Promise<{
  turns: ConversationTurnState[]; total: number; assessments: ResponseAssessments;
}> {
  const summary = await listConversationTurns(conversationId, page, CONVERSATION_PAGE_SIZE, signal);
  const turns = restoreConversationTurns(summary.items);
  const responseIds: number[] = [];
  for (const [index, item] of summary.items.entries()) {
    signal.throwIfAborted();
    const task = await getEvaluationTask(item.taskId, signal);
    turns[index].responses = task.responses;
    responseIds.push(...task.responses.map((response) => response.id));
  }
  return { turns, total: summary.total, assessments: await loadResponseAssessments(conversationId, responseIds, signal) };
}

/** 判断当前页是否仍存在未结束的评分作业。 */
export function hasPendingAssessments(assessments: ResponseAssessments): boolean {
  return Object.values(assessments).some((items) => items.some((item) => ["queued", "running"].includes(item.status)));
}

/** 可取消的轮询间隔；取消同时结束等待，避免留下悬空 Promise。 */
export function waitForAssessmentPoll(signal: AbortSignal, intervalMs = 1500): Promise<boolean> {
  if (signal.aborted) return Promise.resolve(false);
  return new Promise((resolve) => {
    /** 无论超时或取消均移除监听器并结束等待。 */
    function finish(ready: boolean): void {
      clearTimeout(timer);
      signal.removeEventListener("abort", abort);
      resolve(ready);
    }
    /** 接收当前页面生命周期取消事件。 */
    function abort(): void { finish(false); }
    const timer = setTimeout(() => finish(true), intervalMs);
    signal.addEventListener("abort", abort, { once: true });
  });
}
