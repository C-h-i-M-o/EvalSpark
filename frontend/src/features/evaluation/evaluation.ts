import type {
  AvailableModelConfig,
  DisplayModelResponse,
  EvaluationStreamEvent,
  EvaluationTaskState,
  FeedbackToggleResult,
  ModelResponse,
  PendingModelResponse,
  StreamingModelResponse
} from "./types";
import { mergeRagStage } from "../rag/rag";

export interface StreamEventBatcher {
  enqueue: (event: EvaluationStreamEvent) => void;
  flush: () => void;
  clear: () => void;
  pendingCount: () => number;
}

interface StreamEventBatcherOptions {
  scheduleFlush?: (callback: () => void) => number;
  cancelFlush?: (handle: number) => void;
}

export function createPendingResponses(
  modelIds: number[],
  availableModels: AvailableModelConfig[]
): PendingModelResponse[] {
  const modelNameMap = new Map(availableModels.map((modelConfig) => [modelConfig.id, modelConfig.displayName]));
  return modelIds.map((modelId) => ({
    id: `pending-${modelId}`,
    modelConfigId: modelId,
    modelName: modelNameMap.get(modelId) || `模型 ${modelId}`,
    pending: true
  }));
}

export function getIdleJudgeModels(
  availableModels: AvailableModelConfig[],
  selectedModelIds: number[]
): AvailableModelConfig[] {
  const selectedModelSet = new Set(selectedModelIds);
  return availableModels.filter((modelConfig) => !selectedModelSet.has(modelConfig.id));
}

export function normalizeJudgeModelId(
  currentJudgeModelId: number | null,
  availableModels: AvailableModelConfig[],
  selectedModelIds: number[]
): number | null {
  const judgeModels = getIdleJudgeModels(availableModels, selectedModelIds);
  if (judgeModels.some((modelConfig) => modelConfig.id === currentJudgeModelId)) {
    return currentJudgeModelId;
  }
  return judgeModels[0]?.id ?? null;
}

export function getInitialJudgeSelection(
  availableModels: AvailableModelConfig[],
  selectedModelIds: number[]
): { enabled: boolean; judgeModelId: number | null } {
  const judgeModelId = normalizeJudgeModelId(null, availableModels, selectedModelIds);
  return {
    enabled: judgeModelId !== null,
    judgeModelId
  };
}

export function mergeStreamEvent(
  state: EvaluationTaskState | null,
  event: EvaluationStreamEvent
): EvaluationTaskState {
  if (event.type === "rag_stage" || event.type === "rag_retrieval") {
    return mergeRagStage(state, event);
  }
  if (event.type === "task_started") {
    return {
      taskType: event.taskType ?? state?.taskType ?? "chat",
      taskId: event.taskId,
      status: event.status ?? "running",
      visibility: state?.visibility,
      prompt: event.prompt,
      responses: state?.responses || []
    };
  }

  if (event.type === "model_response") {
    return {
      ...(state || { taskId: null, status: "running", prompt: "", responses: [] }),
      responses: mergeModelResponse(state?.responses || [], event.response)
    };
  }

  if (event.type === "model_delta") {
    return {
      ...(state || { taskId: null, status: "running", prompt: "", responses: [] }),
      responses: appendModelDelta(state?.responses || [], event.modelConfigId, event.delta)
    };
  }

  if (event.type === "model_answer_completed") {
    return {
      ...(state || { taskId: null, status: "running", prompt: "", responses: [] }),
      responses: markModelScoring(state?.responses || [], event.modelConfigId)
    };
  }

  return {
    taskType: event.task.taskType ?? "chat",
    taskId: event.task.taskId,
    status: event.task.status,
    prompt: event.task.prompt,
    visibility: event.task.visibility,
    responses: event.task.responses
  };
}

export function mergeStreamEvents(
  state: EvaluationTaskState | null,
  events: EvaluationStreamEvent[]
): EvaluationTaskState | null {
  return events.reduce<EvaluationTaskState | null>(
    (currentState, event) => mergeStreamEvent(currentState, event),
    state
  );
}

export function createStreamEventBatcher(
  applyEvents: (events: EvaluationStreamEvent[]) => void,
  options: StreamEventBatcherOptions = {}
): StreamEventBatcher {
  const scheduleFlush = options.scheduleFlush ?? defaultScheduleFlush;
  const cancelFlush = options.cancelFlush ?? defaultCancelFlush;
  let pendingEvents: EvaluationStreamEvent[] = [];
  let scheduledHandle: number | null = null;

  function flush(): void {
    if (scheduledHandle !== null) {
      cancelFlush(scheduledHandle);
      scheduledHandle = null;
    }
    if (pendingEvents.length === 0) {
      return;
    }
    const eventsToApply = pendingEvents;
    pendingEvents = [];
    applyEvents(eventsToApply);
  }

  function enqueue(event: EvaluationStreamEvent): void {
    pendingEvents.push(event);
    if (scheduledHandle !== null) {
      return;
    }
    scheduledHandle = scheduleFlush(flush);
  }

  function clear(): void {
    if (scheduledHandle !== null) {
      cancelFlush(scheduledHandle);
      scheduledHandle = null;
    }
    pendingEvents = [];
  }

  return {
    enqueue,
    flush,
    clear,
    pendingCount: () => pendingEvents.length
  };
}

export function applyFeedbackResult(state: EvaluationTaskState, result: FeedbackToggleResult): EvaluationTaskState {
  return {
    ...state,
    responses: state.responses.map((response) => {
      if (isPendingResponse(response) || response.id !== result.responseId) {
        return response;
      }
      return {
        ...response,
        feedback: result.feedback,
        score: result.score
      };
    })
  };
}

export function isPendingResponse(response: DisplayModelResponse): response is PendingModelResponse {
  return "pending" in response && response.pending;
}

export function isStreamingResponse(response: DisplayModelResponse): response is StreamingModelResponse {
  return "streaming" in response && response.streaming;
}

export function isTransientResponse(response: DisplayModelResponse): response is PendingModelResponse | StreamingModelResponse {
  return isPendingResponse(response) || "streaming" in response;
}

function mergeModelResponse(responses: DisplayModelResponse[], nextResponse: ModelResponse): DisplayModelResponse[] {
  const targetModelId = nextResponse.modelConfigId;
  let replaced = false;
  const nextResponses = responses.map((response) => {
    if (targetModelId !== null && response.modelConfigId === targetModelId) {
      replaced = true;
      return nextResponse;
    }
    if (!isPendingResponse(response) && response.id === nextResponse.id) {
      replaced = true;
      return nextResponse;
    }
    return response;
  });

  return replaced ? nextResponses : [...nextResponses, nextResponse];
}

function appendModelDelta(
  responses: DisplayModelResponse[],
  modelConfigId: number,
  delta: string
): DisplayModelResponse[] {
  let updated = false;
  const nextResponses = responses.map((response) => {
    if (response.modelConfigId !== modelConfigId) {
      return response;
    }
    updated = true;
    if ("streaming" in response) {
      return { ...response, answer: `${response.answer}${delta}`, scoring: false };
    }
    return {
      id: `streaming-${modelConfigId}`,
      modelConfigId,
      modelName: response.modelName,
      answer: delta,
      ragStage: response.ragStage,
      ragRetrieval: response.ragRetrieval,
      streaming: true,
      scoring: false
    };
  });

  if (updated) {
    return nextResponses;
  }

  return [
    ...responses,
    {
      id: `streaming-${modelConfigId}`,
      modelConfigId,
      modelName: `模型 ${modelConfigId}`,
      answer: delta,
      streaming: true,
      scoring: false
    }
  ];
}

function markModelScoring(responses: DisplayModelResponse[], modelConfigId: number): DisplayModelResponse[] {
  let updated = false;
  const nextResponses = responses.map((response) => {
    if (response.modelConfigId !== modelConfigId) {
      return response;
    }
    updated = true;
    if ("streaming" in response) {
      return { ...response, streaming: false, scoring: true };
    }
    if (isPendingResponse(response)) {
      return {
        id: `streaming-${modelConfigId}`,
        modelConfigId,
        modelName: response.modelName,
        answer: "",
        ragStage: response.ragStage,
        ragRetrieval: response.ragRetrieval,
        streaming: false,
        scoring: true
      };
    }
    return response;
  });

  return updated ? nextResponses : responses;
}

function defaultScheduleFlush(callback: () => void): number {
  if (typeof window !== "undefined" && window.requestAnimationFrame) {
    return window.requestAnimationFrame(callback);
  }
  return globalThis.setTimeout(callback, 16);
}

function defaultCancelFlush(handle: number): void {
  if (typeof window !== "undefined" && window.cancelAnimationFrame) {
    window.cancelAnimationFrame(handle);
    return;
  }
  globalThis.clearTimeout(handle);
}
