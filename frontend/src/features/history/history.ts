import type { EvaluationTaskRead, FeedbackToggleResult } from "../evaluation/types";

const HISTORY_PENDING_TIMEOUT_MS = 120 * 1000;
type HistoryStatusSource = {
  taskType?: "chat" | "rag";
  status: string;
  createdAt?: string | null;
  completedAt?: string | null;
};

export function historyStatusText(taskItem: HistoryStatusSource, now = new Date()): string {
  if (isStalePendingTask(taskItem, now)) {
    return "超时未完成";
  }
  if (taskItem.status === "completed") {
    return "已完成";
  }
  if (taskItem.status === "failed") {
    return "失败";
  }
  return "进行中";
}

export function historyStatusClass(
  taskItem: HistoryStatusSource,
  now = new Date()
): string {
  if (isStalePendingTask(taskItem, now) || taskItem.status === "failed") {
    return "failed";
  }
  if (taskItem.status === "completed") {
    return "completed";
  }
  return "running";
}

export function isStalePendingTask(
  taskItem: HistoryStatusSource,
  now = new Date()
): boolean {
  if (taskItem.status !== "pending" || taskItem.completedAt || !taskItem.createdAt) {
    return false;
  }
  const createdAt = parseBackendTime(taskItem.createdAt);
  if (Number.isNaN(createdAt.getTime())) {
    return false;
  }
  const timeout = taskItem.taskType === "rag" ? 60 * 60 * 1000 : HISTORY_PENDING_TIMEOUT_MS;
  return now.getTime() - createdAt.getTime() >= timeout;
}

export function historyEmptyCopy(task: HistoryStatusSource): { title: string; description: string } {
  const stale = isStalePendingTask(task);
  const running = task.status === "pending" || task.status === "running";
  return { title: stale ? "任务超时未完成" : running ? "模型回答仍在生成" : "暂无模型回答",
    description: stale ? "该任务超过等待时间后仍未产生模型回答，可以刷新历史任务查看收尾状态。"
      : running ? "模型请求尚未完成，可以稍后刷新详情查看最新结果。" : "该任务没有可展示的模型回答。" };
}

/** 返回多轮会话列表中的可继续状态文案，公开读者始终显示只读。 */
export function conversationStatusText(canContinue: boolean, generationStatus: string): string {
  if (!canContinue) return "只读";
  return generationStatus === "idle" ? "可继续" : "生成中";
}

export function formatHistoryTime(value: string | null | undefined): string {
  if (!value) {
    return "未知时间";
  }
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false
  }).format(parseBackendTime(value));
}

export function parseBackendTime(value: string): Date {
  const hasTimezone = /(?:Z|[+-]\d{2}:?\d{2})$/.test(value);
  return new Date(hasTimezone ? value : `${value}Z`);
}

export function updateTaskResponseFeedback(
  task: EvaluationTaskRead,
  result: FeedbackToggleResult
): EvaluationTaskRead {
  return {
    ...task,
    responses: task.responses.map((response) => {
      if (response.id !== result.responseId) {
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
