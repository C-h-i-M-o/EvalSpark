import { useEffect, useRef, useState } from "react";
import type { ChangeEvent } from "react";
import { getEvaluationTask, listEvaluationTasks, submitResponseFeedback } from "../../api/client";
import type { FeedbackType } from "../../api/client";
import type { EvaluationTaskListRead, EvaluationTaskRead } from "../evaluation/types";
import { errorMessage } from "../rag/rag";
import { updateTaskResponseFeedback } from "./history";

export function useHistory() {
  const [listing, setListing] = useState<EvaluationTaskListRead>({ items: [], total: 0, page: 1, pageSize: 10 });
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [taskType, setTaskType] = useState<"all" | "chat" | "rag">("all");
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [selectedTask, setSelectedTask] = useState<EvaluationTaskRead | null>(null);
  const [loading, setLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState("");
  const [feedbackIds, setFeedbackIds] = useState<number[]>([]);
  const [revision, setRevision] = useState(0);
  const detailAbort = useRef<AbortController | null>(null);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setError("");
    void listEvaluationTasks({ page, pageSize, ...(taskType === "all" ? {} : { taskType }) }, controller.signal)
      .then((result) => { if (!controller.signal.aborted) setListing(result); })
      .catch((caught: unknown) => { if (!controller.signal.aborted) setError(errorMessage(caught)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [page, pageSize, taskType, revision]);
  useEffect(() => {
    if (selectedId === null) { setDetailLoading(false); return; }
    const controller = new AbortController(); detailAbort.current = controller; setDetailLoading(true);
    void getEvaluationTask(selectedId, controller.signal)
      .then((task) => { if (!controller.signal.aborted) setSelectedTask(task); })
      .catch((caught: unknown) => { if (!controller.signal.aborted) setError(errorMessage(caught)); })
      .finally(() => { if (!controller.signal.aborted) setDetailLoading(false); });
    return () => controller.abort();
  }, [selectedId, revision]);
  function refresh() { setRevision((value) => value + 1); }
  function changeType(value: "all" | "chat" | "rag") {
    detailAbort.current?.abort(); setTaskType(value); setPage(1); setSelectedId(null); setSelectedTask(null);
  }
  function selectTask(id: number) { detailAbort.current?.abort(); setSelectedId(id); setSelectedTask(null); refresh(); }
  function changePageSize(event: ChangeEvent<HTMLSelectElement>) { setPageSize(Number(event.target.value)); setPage(1); }
  function previousPage() { setPage((value) => Math.max(1, value - 1)); }
  function nextPage() { setPage((value) => value + 1); }
  async function submitFeedback(responseId: number, feedbackType: FeedbackType) {
    if (feedbackIds.includes(responseId) || !selectedTask) return;
    const taskId = selectedTask.taskId; setFeedbackIds((ids) => [...ids, responseId]);
    try {
      const result = await submitResponseFeedback(responseId, feedbackType);
      if (mounted.current) setSelectedTask((task) => task?.taskId === taskId ? updateTaskResponseFeedback(task, result) : task);
    } catch (caught) { if (mounted.current) setError(errorMessage(caught)); }
    finally { if (mounted.current) setFeedbackIds((ids) => ids.filter((id) => id !== responseId)); }
  }
  return { listing, page, pageSize, taskType, changeType, selectedTask, loading, detailLoading, error, feedbackIds,
    refresh, changePageSize, previousPage, nextPage, submitFeedback, totalPages: Math.max(Math.ceil(listing.total / pageSize), 1),
    selectedStatus: selectedTask ?? listing.items.find((item) => item.taskId === selectedId),
    rows: listing.items.map((item) => ({ ...item, select: () => selectTask(item.taskId) })) };
}
