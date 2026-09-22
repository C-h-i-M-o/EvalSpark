import type { ContextStatusRead, ConversationRead, ConversationTurnRead, ConversationTurnStreamEvent } from "../../api/conversationTypes";
import type { DisplayModelResponse } from "../evaluation/types";
import { mergeStreamEvent } from "../evaluation/evaluation";

/** 会话工作台中单轮的前端状态。 */
export interface ConversationTurnState {
  turnId?: number | null;
  turn: number;
  prompt: string;
  answers: Record<number, string>;
  status: "running" | "completed" | "failed";
  assessmentsQueued: boolean;
  responses: DisplayModelResponse[];
  contexts?: ContextStatusRead[];
}

/** 会话工作台的完整状态。 */
export interface ConversationWorkspaceState {
  conversation: ConversationRead | null;
  turns: ConversationTurnState[];
  activeTurn: number | null;
  errorMessage: string;
}

/** 将服务端轮次摘要恢复为可展示的最小状态。 */
export function restoreConversationTurns(turns: ConversationTurnRead[]): ConversationTurnState[] {
  return turns.map((turn) => ({
    turnId: turn.id,
    turn: turn.turnIndex,
    prompt: turn.prompt,
    answers: {},
    status: turn.generationStatus === "completed" ? "completed" : ["pending", "generating"].includes(turn.generationStatus) ? "running" : "failed",
    assessmentsQueued: false,
    responses: [],
    contexts: turn.contexts ?? []
  }));
}

/** 将一次会话流事件归并到当前轮次，避免旧流覆盖新状态。 */
export function mergeConversationEvent(
  state: ConversationWorkspaceState,
  event: ConversationTurnStreamEvent,
  expectedTurn: number,
): ConversationWorkspaceState {
  if (event.type === "turn_started") {
    if (event.turn !== expectedTurn) return state;
    const turns = state.turns.filter((turn) => turn.turn !== event.turn);
    const existing = state.turns.find((turn) => turn.turn === event.turn);
    turns.push(existing ? { ...existing, turnId: event.turnId } : { turnId: event.turnId, turn: event.turn, prompt: "", answers: {}, status: "running", assessmentsQueued: false, responses: [] });
    return { ...state, turns, activeTurn: event.turn, errorMessage: "" };
  }
  const current = state.turns.find((turn) => turn.turn === expectedTurn);
  if (!current) return state;
  if (event.type === "context_ready") {
    const context: ContextStatusRead = { modelConfigId: event.modelConfigId, phase: event.phase ?? "chat",
      compressed: event.compressed, estimatedTokens: event.estimatedTokens ?? null, historyThroughTurn: event.historyThroughTurn ?? null };
    return { ...state, turns: state.turns.map((turn) => turn.turn !== expectedTurn ? turn : {
      ...turn, contexts: [...(turn.contexts ?? []).filter((item) => item.modelConfigId !== context.modelConfigId || item.phase !== context.phase), context]
    }) };
  }
  if (event.type === "rag_stage" || event.type === "rag_retrieval") {
    return { ...state, turns: state.turns.map((turn) => turn.turn !== expectedTurn ? turn : {
      ...turn, responses: mergeStreamEvent({ taskId: null, status: "running", prompt: turn.prompt, responses: turn.responses }, event).responses
    }) };
  }
  if (event.type === "delta") {
    return {
      ...state,
      turns: state.turns.map((turn) => turn.turn === expectedTurn
        ? { ...turn, answers: { ...turn.answers, [event.modelConfigId]: `${turn.answers[event.modelConfigId] ?? ""}${event.delta}` },
          responses: mergeStreamEvent({ taskId: null, status: "running", prompt: turn.prompt, responses: turn.responses },
            { type: "model_delta", modelConfigId: event.modelConfigId, delta: event.delta }).responses }
        : turn)
    };
  }
  if (event.type === "assessments_queued") {
    return { ...state, turns: state.turns.map((turn) => turn.turn === expectedTurn ? { ...turn, assessmentsQueued: true } : turn) };
  }
  if (event.type === "assessment_submission_failed") return { ...state, errorMessage: event.message };
  if (event.type === "answer_completed") {
    const responses = mergeStreamEvent({ taskId: null, status: "running", prompt: current.prompt, responses: current.responses },
      { type: "model_answer_completed", modelConfigId: event.modelConfigId }).responses;
    return { ...state, turns: state.turns.map((turn) => turn.turn !== expectedTurn ? turn : { ...turn,
      responses: responses.map((response) => response.modelConfigId !== event.modelConfigId ? response : {
        ...response, ragStage: undefined, interrupted: event.status !== "success"
      }) }) };
  }
  if (event.type === "turn_completed") {
    return { ...state, turns: state.turns.map((turn) => turn.turn === expectedTurn ? { ...turn, status: "completed" } : turn), activeTurn: null };
  }
  if (event.type === "stream_error") {
    return { ...state, errorMessage: event.message, turns: state.turns.map((turn) => turn.turn === expectedTurn ? { ...turn, status: "failed" } : turn), activeTurn: null };
  }
  return state;
}

/** 生成不含特殊字符的幂等请求键。 */
export function createConversationRequestKey(): string {
  return `turn-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}
