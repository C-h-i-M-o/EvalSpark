/** 会话运行模式。 */
export type ConversationMode = "chat" | "rag";

/** 会话可见性。 */
export type ConversationVisibility = "public" | "private";

/** 创建会话请求。 */
export interface ConversationCreatePayload {
  mode?: ConversationMode;
  title?: string;
  modelIds: number[];
  judgeModelId?: number | null;
  summaryModelId?: number | null;
  enableThinking?: boolean;
  visibility?: ConversationVisibility;
  knowledgeBaseId?: number | null;
  inputBudget?: number;
}

/** 会话摘要及当前运行状态。 */
export interface ConversationRead {
  id: number;
  title: string;
  mode: ConversationMode;
  ownerId: number;
  canContinue: boolean;
  visibility: ConversationVisibility;
  currentTurn: number;
  generationStatus: string;
  configuration: Record<string, unknown>;
  createdAt: string;
  updatedAt: string | null;
}

/** 会话分页结果。 */
export interface ConversationListRead {
  items: ConversationRead[];
  total: number;
  page: number;
  pageSize: number;
}

/** 轮次摘要。 */
export interface ContextStatusRead {
  modelConfigId: number;
  phase: "chat" | "rewrite" | "answer";
  compressed: boolean | null;
  estimatedTokens: number | null;
  historyThroughTurn: number | null;
}

/** 轮次摘要兼容尚未记录上下文状态的历史数据。 */
export interface ConversationTurnRead {
  id: number;
  taskId: number;
  turnIndex: number;
  prompt: string;
  generationStatus: string;
  errorCode: string | null;
  contexts?: ContextStatusRead[];
  createdAt: string;
}

/** 轮次分页结果。 */
export interface ConversationTurnListRead {
  items: ConversationTurnRead[];
  total: number;
  page: number;
  pageSize: number;
}

/** 后台评分的公开结果，未完成时 result 保持为空。 */
export interface ConversationAssessmentRead {
  id: number;
  responseId: number | null;
  modelConfigId: number;
  throughTurn: number;
  scoreVersion: string;
  status: string;
  formal: boolean;
  result: Record<string, unknown> | null;
  createdAt: string;
  completedAt: string | null;
}

/** 评分详情包含各次评审的独立结果，不包含内部输入快照。 */
export interface ConversationAssessmentDetailRead extends ConversationAssessmentRead {
  runs: {
    runIndex: number;
    status: string;
    result: Record<string, unknown> | null;
    errorCode: string | null;
  }[];
}

/** 分页评分列表支持按回答查询。 */
export interface ConversationAssessmentListRead {
  items: ConversationAssessmentRead[];
  total: number;
  page: number;
  pageSize: number;
}

/** 续聊请求。 */
export interface ConversationTurnStreamPayload {
  prompt: string;
  expectedTurn: number;
  requestKey: string;
}

/** 失败分支恢复请求。 */
export interface ConversationBranchStreamPayload {
  turnId: number;
  modelConfigId: number;
  requestKey: string;
  action: "retry" | "skip";
}

/** 多轮 RAG 阶段事件，字段与后端 multiturn/rag_generation.py 的公开事件一致。 */
export type ConversationRagEvent =
  | { type: "rag_stage"; modelConfigId: number; stage: "rewriting" | "retrieving" | "answering" | "judging" }
  | { type: "rag_retrieval"; modelConfigId: number; rewrittenQuery: string; evidence: import("../features/rag/types").RagEvidence[] };

export type ConversationTurnStreamEvent = ConversationRagEvent |
  { type: "turn_started"; turnId: number; taskId: number; turn: number; replayed: boolean }
  | { type: "context_ready"; modelConfigId: number; compressed: boolean | null;
      phase?: ContextStatusRead["phase"]; estimatedTokens?: number | null; historyThroughTurn?: number | null }
  | { type: "delta"; modelConfigId: number; delta: string }
  | { type: "answer_completed"; modelConfigId: number; responseId: number | null; status: string; errorCode: string | null }
  | { type: "assessments_queued"; turnId: number; assessmentIds: number[] }
  | { type: "assessment_submission_failed"; turnId: number; message: string }
  | { type: "turn_completed"; turnId: number; taskId: number }
  | { type: "stream_error"; code: string; message: string };
