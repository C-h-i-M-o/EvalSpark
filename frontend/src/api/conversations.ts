import { ApiError, fetchJson, readErrorMessage } from "./client";
import type {
  ConversationAssessmentDetailRead,
  ConversationAssessmentListRead,
  ConversationAssessmentRead,
  ConversationCreatePayload,
  ConversationBranchStreamPayload,
  ConversationListRead,
  ConversationRead,
  ConversationTurnListRead,
  ConversationTurnStreamEvent,
  ConversationTurnStreamPayload,
  ConversationVisibility,
} from "./conversationTypes";

const conversationPath = "/api/evaluation/conversations";

/** 从固定暂定评分提交正式复评，重复点击由服务端返回同一作业。 */
export function submitConversationReassessment(
  conversationId: number,
  sourceAssessmentId: number,
): Promise<ConversationAssessmentRead> {
  return postJson<ConversationAssessmentRead>(`${conversationPath}/${conversationId}/assessments`, { sourceAssessmentId });
}

/** 创建固定分支与截止轮次的会话报告，服务端按请求幂等处理。 */
export function submitConversationReport(conversationId: number, modelConfigId: number, throughTurn: number): Promise<ConversationAssessmentRead> {
  return postJson<ConversationAssessmentRead>(`${conversationPath}/${conversationId}/reports`, { modelConfigId, throughTurn });
}

/** 分页读取会话报告，不混入回答级评分。 */
export function listConversationReports(conversationId: number, page = 1, pageSize = 10, signal?: AbortSignal): Promise<ConversationAssessmentListRead> {
  const query = new URLSearchParams({ page: String(page), pageSize: String(pageSize) });
  return fetchJson<ConversationAssessmentListRead>(`${conversationPath}/${conversationId}/reports?${query.toString()}`, signal);
}

/** 分页读取评分状态和结果，可限制到当前回答。 */
export function listConversationAssessments(
  conversationId: number,
  page = 1,
  pageSize = 20,
  responseId?: number,
  signal?: AbortSignal,
): Promise<ConversationAssessmentListRead> {
  const query = new URLSearchParams({ page: String(page), pageSize: String(pageSize) });
  if (responseId !== undefined) query.set("responseId", String(responseId));
  return fetchJson<ConversationAssessmentListRead>(`${conversationPath}/${conversationId}/assessments?${query.toString()}`, signal);
}

/** 获取评分详情与独立评审证据，不触发复评。 */
export function getConversationAssessment(
  conversationId: number,
  assessmentId: number,
  signal?: AbortSignal,
): Promise<ConversationAssessmentDetailRead> {
  return fetchJson<ConversationAssessmentDetailRead>(`${conversationPath}/${conversationId}/assessments/${assessmentId}`, signal);
}

/** 创建一个仅固定配置、不自动生成首轮的会话。 */
export function createConversation(payload: ConversationCreatePayload): Promise<ConversationRead> {
  return postJson<ConversationRead>(conversationPath, payload);
}

/** 分页读取当前用户可见的普通或 RAG 会话。 */
export function listConversations(
  mode: "chat" | "rag" = "chat",
  page = 1,
  pageSize = 10,
  signal?: AbortSignal,
): Promise<ConversationListRead> {
  const query = new URLSearchParams({ taskType: mode, page: String(page), pageSize: String(pageSize) });
  return fetchJson<ConversationListRead>(`${conversationPath}?${query.toString()}`, signal);
}

/** 读取会话状态和固定配置摘要。 */
export function getConversation(conversationId: number, signal?: AbortSignal): Promise<ConversationRead> {
  return fetchJson<ConversationRead>(`${conversationPath}/${conversationId}`, signal);
}

/** 分页读取会话轮次摘要。 */
export function listConversationTurns(
  conversationId: number,
  page = 1,
  pageSize = 20,
  signal?: AbortSignal,
): Promise<ConversationTurnListRead> {
  const query = new URLSearchParams({ page: String(page), pageSize: String(pageSize) });
  return fetchJson<ConversationTurnListRead>(`${conversationPath}/${conversationId}/turns?${query.toString()}`, signal);
}

/** 修改会话可见性，并由服务端同步既有轮次任务权限。 */
export function updateConversationVisibility(
  conversationId: number,
  visibility: ConversationVisibility,
): Promise<ConversationRead> {
  return requestJson<ConversationRead>(`${conversationPath}/${conversationId}/visibility`, "PATCH", { visibility });
}

/** 以 NDJSON 事件流提交普通会话的新一轮问题。 */
export async function streamConversationTurn(
  conversationId: number,
  payload: ConversationTurnStreamPayload,
  onEvent: (event: ConversationTurnStreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  return streamConversationPath(`${conversationPath}/${conversationId}/turns/stream`, payload, onEvent, signal);
}

/** 以同一 NDJSON 协议恢复指定失败分支。 */
export function streamConversationBranch(
  conversationId: number,
  payload: ConversationBranchStreamPayload,
  onEvent: (event: ConversationTurnStreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  return streamConversationPath(`${conversationPath}/${conversationId}/branches/stream`, payload, onEvent, signal);
}

/** 复用会话流解析逻辑，保持恢复与普通续聊的错误语义一致。 */
async function streamConversationPath(
  url: string,
  payload: unknown,
  onEvent: (event: ConversationTurnStreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(url, { ...(signal ? { signal } : {}), method: "POST", credentials: "include",
    headers: { Accept: "application/x-ndjson", "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  if (!response.ok) throw new ApiError(response.status, await readErrorMessage(response));
  if (!response.body) throw new Error("当前浏览器不支持渐进式读取会话结果");
  const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = "";
  try {
    while (true) {
      const result = await reader.read(); if (result.done) break;
      buffer += decoder.decode(result.value, { stream: true }); const lines = buffer.split("\n"); buffer = lines.pop() ?? "";
      for (const line of lines) emitConversationStreamLine(line, onEvent);
    }
    buffer += decoder.decode(); emitConversationStreamLine(buffer, onEvent);
  } finally { await reader.cancel().catch(() => undefined); reader.releaseLock(); }
}

/** 使用统一鉴权语义发送 JSON 请求。 */
async function requestJson<T>(url: string, method: string, payload: unknown): Promise<T> {
  const response = await fetch(url, {
    method,
    credentials: "include",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw new ApiError(response.status, await readErrorMessage(response));
  return response.json() as Promise<T>;
}

/** 发送 JSON POST 请求。 */
function postJson<T>(url: string, payload: unknown): Promise<T> {
  return requestJson<T>(url, "POST", payload);
}

/** 解析单行会话 NDJSON，忽略空行并保留协议错误给调用方。 */
function emitConversationStreamLine(line: string, onEvent: (event: ConversationTurnStreamEvent) => void): void {
  const trimmedLine = line.trim();
  if (trimmedLine) onEvent(JSON.parse(trimmedLine) as ConversationTurnStreamEvent);
}
