import type {
  CommentListRead,
  CommentRead,
  EvaluationStreamEvent,
  EvaluationTaskListRead,
  EvaluationTaskRead,
  FeedbackToggleResult
} from "../features/evaluation/types";

export interface HealthStatus {
  status: "ok";
}

export type UserRole = "user" | "admin";
export type UserStatus = "active" | "disabled";

export interface UserProfile {
  id: number;
  username: string;
  role: UserRole;
  status: UserStatus;
}

export interface AuthCredentials {
  username: string;
  password: string;
}

export interface RegisterCredentials extends AuthCredentials {
  confirmPassword: string;
}

export interface AvailableModel {
  contextWindow?: number | null;
  maxTokens?: number | null;
  id: number;
  providerName: string;
  displayName: string;
  modelName: string;
}

export interface TokenUsage {
  usageDate: string;
  usedTokens: number;
  dailyLimit: number | null;
  remainingTokens: number | null;
  unlimited: boolean;
}

export type EvaluationVisibility = "public" | "private";

export interface EvaluationTaskPayload {
  taskType?: "chat" | "rag";
  knowledgeBaseId?: number;
  prompt: string;
  modelIds: number[];
  enableJudge: boolean;
  judgeModelId: number | null;
  enableThinking: boolean;
  visibility: EvaluationVisibility;
}

export type FeedbackType = "like" | "dislike";

export interface FeedbackPayload {
  feedbackType: FeedbackType;
}

export type FeedbackStatsRange = "7d" | "30d" | "all";
export type FeedbackActivityType = "all" | "like" | "dislike" | "comment";

export interface ModelConfigPayload {
  contextWindow?: number | null;
  providerName?: string;
  displayName?: string;
  modelName?: string;
  baseUrl?: string;
  apiKey?: string | null;
  enabled?: boolean;
  maxTokens?: number;
  temperature?: number;
  timeoutSeconds?: number;
  notes?: string;
  currency?: "CNY" | "USD";
  priceInput?: number;
  priceOutput?: number;
  priceCacheHit?: number;
  priceCacheCreation?: number;
}

export interface ModelConfig extends Required<Omit<ModelConfigPayload, "apiKey" | "contextWindow">> {
  contextWindow?: number | null;
  id: number;
  hasApiKey: boolean;
  maskedApiKey: string;
}

export interface ModelConfigTestPayload {
  modelConfigId?: number;
  providerName?: string;
  modelName?: string;
  baseUrl?: string;
  apiKey?: string | null;
  maxTokens?: number;
  temperature?: number;
  timeoutSeconds?: number;
}

export interface ModelConfigTestResult {
  success: boolean;
  message: string;
  latencyMs: number;
}

export interface AdminUserUsage {
  id: number;
  username: string;
  role: UserRole;
  status: UserStatus;
  usageDate: string;
  usedTokens: number;
  dailyLimit: number | null;
}

export interface AdminUserList {
  items: AdminUserUsage[];
  total: number;
  page: number;
  pageSize: number;
}

export interface AdminUserListParams {
  page: number;
  pageSize: number;
  keyword?: string;
  role?: UserRole | "all";
  status?: UserStatus | "all";
}

export interface FeedbackStatsSummary {
  taskCount: number;
  callCount: number;
  scoredCount: number;
  averageFinalScore: number | null;
  likeCount: number;
  dislikeCount: number;
  likeRate: number | null;
  commentCount: number;
}

export interface FeedbackInteractionSummary {
  likeCount: number;
  dislikeCount: number;
  commentCount: number;
}

export interface FeedbackModelStats {
  modelConfigId: number | null;
  modelName: string;
  callCount: number;
  scoredCount: number;
  averageFinalScore: number | null;
  averageRuleScore: number | null;
  averageJudgeScore: number | null;
  likeCount: number;
  dislikeCount: number;
  likeRate: number | null;
  commentCount: number;
}

export interface FeedbackTrendPoint {
  date: string;
  callCount: number;
  averageFinalScore: number | null;
  likeCount: number;
  dislikeCount: number;
  commentCount: number;
}

export interface FeedbackActivity {
  activityId: number;
  activityType: Exclude<FeedbackActivityType, "all">;
  userId: number;
  username: string;
  taskId: number;
  responseId: number;
  modelConfigId: number | null;
  modelName: string;
  prompt: string;
  content: string | null;
  createdAt: string;
}

export interface FeedbackActivityList {
  items: FeedbackActivity[];
  total: number;
  page: number;
  pageSize: number;
}

export interface PersonalFeedbackStats {
  scope: "personal";
  range: FeedbackStatsRange;
  startAt: string | null;
  endAt: string;
  summary: FeedbackStatsSummary;
  myInteractions: FeedbackInteractionSummary;
  models: FeedbackModelStats[];
  trend: FeedbackTrendPoint[];
}

export interface AdminFeedbackStats {
  scope: "global";
  range: FeedbackStatsRange;
  startAt: string | null;
  endAt: string;
  summary: FeedbackStatsSummary;
  models: FeedbackModelStats[];
  trend: FeedbackTrendPoint[];
  activities: FeedbackActivityList;
}

export interface RuleDictionary {
  id: number;
  dictionaryType: string;
  name: string;
  version: string;
  enabled: boolean;
}

export interface RuleTerm {
  id: number;
  dictionaryId: number;
  dictionaryType: string;
  category: string;
  content: string;
  matchType: "keyword" | "regex";
  severity: number;
  enabled: boolean;
  updatedAt: string;
}

export interface RuleTermList {
  items: RuleTerm[];
  total: number;
  page: number;
  pageSize: number;
}

export interface RuleTermPayload {
  dictionaryId: number;
  category: string;
  content: string;
  matchType: "keyword" | "regex";
  severity: number;
  enabled: boolean;
}

export interface JudgePromptGroup {
  id: number;
  code: string;
  name: string;
  rubric: string;
  version: string;
  enabled: boolean;
}

export interface JudgePromptTemplate {
  id: number;
  groupId: number;
  groupCode: string;
  code: string;
  content: string;
  outputSchema: Record<string, object>;
  enabled: boolean;
  updatedAt: string;
}

export interface JudgePromptTemplatePayload {
  content: string;
  outputSchema: Record<string, object>;
  enabled: boolean;
}

export interface JudgePromptValidation {
  valid: boolean;
  issues: string[];
}

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export async function fetchJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(url, {
    ...(signal ? { signal } : {}),
    credentials: "include",
    headers: {
      Accept: "application/json"
    }
  });

  if (!response.ok) {
    const message = await readErrorMessage(response);
    throw new ApiError(response.status, message);
  }

  return response.json() as Promise<T>;
}

async function postJson<T>(url: string, payload?: unknown): Promise<T> {
  const headers: HeadersInit = {
    Accept: "application/json"
  };
  const init: RequestInit = {
    method: "POST",
    credentials: "include",
    headers
  };

  if (payload !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(payload);
  }

  const response = await fetch(url, init);

  if (!response.ok) {
    const message = await readErrorMessage(response);
    throw new ApiError(response.status, message);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return response.json() as Promise<T>;
}

async function deleteJson(url: string): Promise<void> {
  const response = await fetch(url, {
    method: "DELETE",
    credentials: "include",
    headers: {
      Accept: "application/json"
    }
  });

  if (!response.ok) {
    const message = await readErrorMessage(response);
    throw new ApiError(response.status, message);
  }
}

export async function readErrorMessage(response: Response): Promise<string> {
  const payload = (await response.json().catch(() => null)) as { detail?: unknown } | null;
  return formatErrorDetail(payload?.detail) || `请求失败：${response.status}`;
}

function formatErrorDetail(detail: unknown): string | null {
  if (typeof detail === "string") {
    return detail;
  }
  if (detail && typeof detail === "object" && "message" in detail && typeof detail.message === "string") {
    return detail.message;
  }

  if (Array.isArray(detail)) {
    const messages = detail.map(formatValidationIssue).filter((message) => message.length > 0);
    return messages.length > 0 ? messages.join("；") : null;
  }

  if (detail && typeof detail === "object" && "msg" in detail) {
    return formatValidationIssue(detail);
  }

  return null;
}

function formatValidationIssue(issue: unknown): string {
  if (!issue || typeof issue !== "object") {
    return "";
  }

  const record = issue as { loc?: unknown; msg?: unknown };
  const field = Array.isArray(record.loc) ? record.loc.at(-1) : null;
  const message = typeof record.msg === "string" ? record.msg : "";

  if (field === "username" && message.includes("at least 3")) {
    return "用户名长度不能少于 3 位";
  }
  if (field === "password" && message.includes("at least 8")) {
    return "密码长度不能少于 8 位";
  }

  return message;
}

export async function getHealthStatus(): Promise<HealthStatus> {
  return fetchJson<HealthStatus>("/api/health");
}

export async function getCurrentUser(): Promise<UserProfile> {
  return fetchJson<UserProfile>("/api/auth/me");
}

export async function loginUser(credentials: AuthCredentials): Promise<UserProfile> {
  return postJson<UserProfile>("/api/auth/login", credentials);
}

export async function registerUser(credentials: RegisterCredentials): Promise<UserProfile> {
  return postJson<UserProfile>("/api/auth/register", credentials);
}

export async function logoutUser(): Promise<void> {
  await postJson<void>("/api/auth/logout");
}

export async function listAvailableModels(signal?: AbortSignal): Promise<AvailableModel[]> {
  return fetchJson<AvailableModel[]>("/api/models/available", signal);
}

export async function getTodayTokenUsage(signal?: AbortSignal): Promise<TokenUsage> {
  return fetchJson<TokenUsage>("/api/token-usage/me/today", signal);
}

export async function listEvaluationTasks(params: { page: number; pageSize: number; taskType?: "chat" | "rag" }, signal?: AbortSignal): Promise<EvaluationTaskListRead> {
  return fetchJson<EvaluationTaskListRead>(
    `/api/evaluation/tasks?${new URLSearchParams({
      page: String(params.page),
      pageSize: String(params.pageSize),
      ...(params.taskType ? { taskType: params.taskType } : {})
    }).toString()}`, signal
  );
}

export async function getEvaluationTask(taskId: number, signal?: AbortSignal): Promise<EvaluationTaskRead> {
  return fetchJson<EvaluationTaskRead>(`/api/evaluation/tasks/${taskId}`, signal);
}

async function patchJson<T>(url: string, payload: unknown): Promise<T> {
  const response = await fetch(url, {
    method: "PATCH", credentials: "include",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  if (!response.ok) throw new ApiError(response.status, await readErrorMessage(response));
  return response.json() as Promise<T>;
}

export async function updateEvaluationTaskVisibility(taskId: number, visibility: EvaluationVisibility): Promise<EvaluationTaskRead> {
  return patchJson<EvaluationTaskRead>(`/api/evaluation/tasks/${taskId}/visibility`, { visibility });
}

export async function submitResponseFeedback(
  responseId: number,
  feedbackType: FeedbackType
): Promise<FeedbackToggleResult> {
  return postJson<FeedbackToggleResult>(`/api/evaluation/responses/${responseId}/feedback`, {
    feedbackType
  } satisfies FeedbackPayload);
}

export async function listResponseComments(
  responseId: number,
  params: { page: number; pageSize: number }
): Promise<CommentListRead> {
  return fetchJson<CommentListRead>(
    `/api/evaluation/responses/${responseId}/comments?${new URLSearchParams({
      page: String(params.page),
      pageSize: String(params.pageSize)
    }).toString()}`
  );
}

export async function createResponseComment(responseId: number, content: string): Promise<CommentRead> {
  return postJson<CommentRead>(`/api/evaluation/responses/${responseId}/comments`, { content });
}

export async function deleteResponseComment(commentId: number): Promise<void> {
  await deleteJson(`/api/evaluation/comments/${commentId}`);
}

export async function listModelConfigs(): Promise<ModelConfig[]> {
  return fetchJson<ModelConfig[]>("/api/admin/model-configs");
}

export async function createModelConfig(payload: ModelConfigPayload): Promise<ModelConfig> {
  return postJson<ModelConfig>("/api/admin/model-configs", payload);
}

export async function updateModelConfig(id: number, payload: ModelConfigPayload): Promise<ModelConfig> {
  const response = await fetch(`/api/admin/model-configs/${id}`, {
    method: "PUT",
    credentials: "include",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json"
    },
    body: JSON.stringify(payload)
  });

  if (!response.ok) {
    const message = await readErrorMessage(response);
    throw new ApiError(response.status, message);
  }

  return response.json() as Promise<ModelConfig>;
}

export async function deleteModelConfig(id: number): Promise<void> {
  await deleteJson(`/api/admin/model-configs/${id}`);
}

export async function testModelConfig(payload: ModelConfigTestPayload): Promise<ModelConfigTestResult> {
  return postJson<ModelConfigTestResult>("/api/admin/model-configs/test", payload);
}

export async function listAdminUsers(params: AdminUserListParams): Promise<AdminUserList> {
  const searchParams = new URLSearchParams({
    page: String(params.page),
    pageSize: String(params.pageSize)
  });
  const keyword = params.keyword?.trim();
  if (keyword) {
    searchParams.set("keyword", keyword);
  }
  if (params.role && params.role !== "all") {
    searchParams.set("role", params.role);
  }
  if (params.status && params.status !== "all") {
    searchParams.set("status", params.status);
  }
  return fetchJson<AdminUserList>(`/api/admin/users?${searchParams.toString()}`);
}

export async function updateAdminUserStatus(userId: number, userStatus: UserStatus): Promise<AdminUserUsage> {
  const response = await fetch(`/api/admin/users/${userId}/status`, {
    method: "PATCH",
    credentials: "include",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json"
    },
    body: JSON.stringify({ status: userStatus })
  });

  if (!response.ok) {
    const message = await readErrorMessage(response);
    throw new ApiError(response.status, message);
  }

  return response.json() as Promise<AdminUserUsage>;
}

export async function updateUserQuota(userId: number, dailyLimit: number): Promise<AdminUserUsage> {
  const response = await fetch(`/api/admin/users/${userId}/quota`, {
    method: "PUT",
    credentials: "include",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json"
    },
    body: JSON.stringify({ dailyLimit })
  });

  if (!response.ok) {
    const message = await readErrorMessage(response);
    throw new ApiError(response.status, message);
  }

  return response.json() as Promise<AdminUserUsage>;
}

export async function getPersonalFeedbackStats(range: FeedbackStatsRange): Promise<PersonalFeedbackStats> {
  return fetchJson<PersonalFeedbackStats>(`/api/feedback-stats/me?range=${range}`);
}

export async function getAdminFeedbackStats(params: {
  range: FeedbackStatsRange;
  activityType: FeedbackActivityType;
  modelConfigId?: number | null;
  page: number;
  pageSize: number;
}): Promise<AdminFeedbackStats> {
  const searchParams = new URLSearchParams({
    range: params.range,
    activityType: params.activityType
  });
  if (params.modelConfigId) {
    searchParams.set("modelConfigId", String(params.modelConfigId));
  }
  searchParams.set("page", String(params.page));
  searchParams.set("pageSize", String(params.pageSize));
  return fetchJson<AdminFeedbackStats>(`/api/admin/feedback-stats?${searchParams.toString()}`);
}

export async function listRuleDictionaries(): Promise<RuleDictionary[]> {
  return fetchJson<RuleDictionary[]>("/api/admin/scoring/rule-dictionaries");
}

export async function listRuleTerms(params: {
  page: number;
  pageSize: number;
  keyword?: string;
  dictionaryType?: string;
  category?: string;
  enabled?: boolean | null;
}): Promise<RuleTermList> {
  const searchParams = new URLSearchParams({
    page: String(params.page),
    pageSize: String(params.pageSize)
  });
  if (params.keyword?.trim()) {
    searchParams.set("keyword", params.keyword.trim());
  }
  if (params.dictionaryType && params.dictionaryType !== "all") {
    searchParams.set("dictionaryType", params.dictionaryType);
  }
  if (params.category?.trim()) {
    searchParams.set("category", params.category.trim());
  }
  if (params.enabled !== null && params.enabled !== undefined) {
    searchParams.set("enabled", String(params.enabled));
  }
  return fetchJson<RuleTermList>(`/api/admin/scoring/rule-terms?${searchParams.toString()}`);
}

export async function createRuleTerm(payload: RuleTermPayload): Promise<RuleTerm> {
  return postJson<RuleTerm>("/api/admin/scoring/rule-terms", payload);
}

export async function updateRuleTerm(id: number, payload: RuleTermPayload): Promise<RuleTerm> {
  const response = await fetch(`/api/admin/scoring/rule-terms/${id}`, {
    method: "PUT",
    credentials: "include",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  if (!response.ok) {
    throw new ApiError(response.status, await readErrorMessage(response));
  }
  return response.json() as Promise<RuleTerm>;
}

export async function updateRuleTermStatus(id: number, enabled: boolean): Promise<RuleTerm> {
  const response = await fetch(`/api/admin/scoring/rule-terms/${id}/status`, {
    method: "PATCH",
    credentials: "include",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify({ enabled })
  });
  if (!response.ok) {
    throw new ApiError(response.status, await readErrorMessage(response));
  }
  return response.json() as Promise<RuleTerm>;
}

export async function deleteRuleTerm(id: number): Promise<void> {
  await deleteJson(`/api/admin/scoring/rule-terms/${id}`);
}

export async function listJudgePromptGroups(): Promise<JudgePromptGroup[]> {
  return fetchJson<JudgePromptGroup[]>("/api/admin/scoring/judge-prompt-groups");
}

export async function listJudgePromptTemplates(): Promise<JudgePromptTemplate[]> {
  return fetchJson<JudgePromptTemplate[]>("/api/admin/scoring/judge-prompt-templates");
}

export async function updateJudgePromptTemplate(
  id: number,
  payload: JudgePromptTemplatePayload
): Promise<JudgePromptTemplate> {
  const response = await fetch(`/api/admin/scoring/judge-prompt-templates/${id}`, {
    method: "PUT",
    credentials: "include",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  if (!response.ok) {
    throw new ApiError(response.status, await readErrorMessage(response));
  }
  return response.json() as Promise<JudgePromptTemplate>;
}

export async function validateJudgePromptGroup(id: number): Promise<JudgePromptValidation> {
  return postJson<JudgePromptValidation>(`/api/admin/scoring/judge-prompt-groups/${id}/validate`);
}

export async function streamEvaluationTask(
  payload: EvaluationTaskPayload,
  onEvent: (event: EvaluationStreamEvent) => void,
  signal?: AbortSignal
): Promise<void> {
  const response = await fetch("/api/evaluation/tasks/stream", {
    ...(signal ? { signal } : {}),
    method: "POST",
    credentials: "include",
    headers: {
      Accept: "application/x-ndjson",
      "Content-Type": "application/json"
    },
    body: JSON.stringify(payload)
  });

  if (!response.ok) {
    const message = await readErrorMessage(response);
    throw new ApiError(response.status, message || "评测任务创建失败");
  }

  if (!response.body) {
    throw new Error("当前浏览器不支持渐进式读取模型结果");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const result = await reader.read();
      if (result.done) {
        break;
      }
      buffer += decoder.decode(result.value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";
      for (const line of lines) {
        emitStreamLine(line, onEvent);
      }
    }
    buffer += decoder.decode();
    emitStreamLine(buffer, onEvent);
  } finally {
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}

function emitStreamLine(
  line: string,
  onEvent: (event: EvaluationStreamEvent) => void
): void {
  const trimmedLine = line.trim();
  if (!trimmedLine) {
    return;
  }
  onEvent(JSON.parse(trimmedLine) as EvaluationStreamEvent);
}
