import { useEffect, useRef, useState } from "react";
import type { ChangeEvent } from "react";
import { getEvaluationTask, listEvaluationTasks, submitResponseFeedback, updateEvaluationTaskVisibility } from "../../api/client";
import { listConversations } from "../../api/conversations";
import type { ConversationListRead } from "../../api/conversationTypes";
import { useNavigate } from "react-router-dom";
import type { EvaluationVisibility, FeedbackType } from "../../api/client";
import { useAuth } from "../auth/AuthContext";
import type { EvaluationTaskListRead, EvaluationTaskRead } from "../evaluation/types";
import { errorMessage } from "../rag/rag";
import { updateTaskResponseFeedback } from "./history";

export function useHistory(taskType: "chat" | "rag") {
  const navigate = useNavigate();
  const { user } = useAuth();
  const [listing, setListing] = useState<EvaluationTaskListRead>({ items: [], total: 0, page: 1, pageSize: 10 });
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [selectedTask, setSelectedTask] = useState<EvaluationTaskRead | null>(null);
  const [loading, setLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState("");
  const [feedbackIds, setFeedbackIds] = useState<number[]>([]);
  const [revision, setRevision] = useState(0);
  const [visibilitySaving, setVisibilitySaving] = useState(false);
  const [conversations, setConversations] = useState<ConversationListRead>({ items: [], total: 0, page: 1, pageSize: 10 });
  const [conversationPage, setConversationPage] = useState(1);
  const [conversationLoading, setConversationLoading] = useState(false);
  const [conversationResultMode, setConversationResultMode] = useState(taskType);
  const visibilityPending = useRef(false);
  const detailAbort = useRef<AbortController | null>(null);
  const mounted = useRef(true);
  const conversationModeRef = useRef(taskType);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setError("");
    void listEvaluationTasks({ page, pageSize, taskType }, controller.signal)
      .then((result) => { if (!controller.signal.aborted) setListing(result); })
      .catch((caught: unknown) => { if (!controller.signal.aborted) setError(errorMessage(caught)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [page, pageSize, taskType, revision]);
  useEffect(() => {
    const controller = new AbortController();
    setConversationLoading(true);
    const targetPage = conversationModeRef.current === taskType ? conversationPage : 1;
    if (conversationModeRef.current !== taskType) {
      conversationModeRef.current = taskType;
      setConversationPage(1);
      setConversations({ items: [], total: 0, page: 1, pageSize: 10 });
    }
    void listConversations(taskType, targetPage, 10, controller.signal)
      .then((result) => { if (!controller.signal.aborted) { setConversations(result); setConversationResultMode(taskType); } })
      .catch((caught: unknown) => { if (!controller.signal.aborted) setError(errorMessage(caught)); })
      .finally(() => { if (!controller.signal.aborted) setConversationLoading(false); });
    return () => controller.abort();
  }, [conversationPage, taskType, revision]);
  useEffect(() => {
    if (selectedId === null) { setDetailLoading(false); return; }
    const controller = new AbortController(); detailAbort.current = controller; setDetailLoading(true);
    void getEvaluationTask(selectedId, controller.signal)
      .then((task) => { if (!controller.signal.aborted) setSelectedTask(task); })
      .catch((caught: unknown) => { if (!controller.signal.aborted) { setSelectedTask(null); setError(errorMessage(caught)); } })
      .finally(() => { if (!controller.signal.aborted) setDetailLoading(false); });
    return () => controller.abort();
  }, [selectedId, revision]);
  function refresh() { setRevision((value) => value + 1); }
  async function changeVisibility(visibility: EvaluationVisibility) {
    if (!selectedTask || selectedTask.ownerId !== user?.id || visibilityPending.current || detailLoading) return;
    const taskId = selectedTask.taskId;
    visibilityPending.current = true;
    setVisibilitySaving(true); setError("");
    detailAbort.current?.abort();
    try {
      const updated = await updateEvaluationTaskVisibility(taskId, visibility);
      if (mounted.current) {
        setSelectedTask((current) => current?.taskId === taskId ? updated : current);
        setRevision((value) => value + 1);
      }
    } catch (caught) { if (mounted.current) setError(errorMessage(caught)); }
    finally { visibilityPending.current = false; if (mounted.current) setVisibilitySaving(false); }
  }
  function selectTask(id: number) { detailAbort.current?.abort(); setSelectedId(id); setSelectedTask(null); refresh(); }
  function changePageSize(event: ChangeEvent<HTMLSelectElement>) { setPageSize(Number(event.target.value)); setPage(1); }
  function previousPage() { setPage((value) => Math.max(1, value - 1)); }
  function nextPage() { setPage((value) => value + 1); }
  /** 切换多轮会话列表页，并保持每页只请求固定数量摘要。 */
  function previousConversationPage(): void { setConversationPage((value) => Math.max(1, value - 1)); }
  /** 切换多轮会话列表页，并保持每页只请求固定数量摘要。 */
  function nextConversationPage(): void {
    setConversationPage((value) => Math.min(Math.max(Math.ceil(conversations.total / 10), 1), value + 1));
  }
  /** 打开指定会话工作台，由 URL 保留会话上下文以支持刷新和返回。 */
  function openConversation(conversationId: number): void {
    navigate(`${taskType === "rag" ? "/rag" : "/"}?conversationId=${conversationId}`);
  }
  async function submitFeedback(responseId: number, feedbackType: FeedbackType) {
    if (feedbackIds.includes(responseId) || !selectedTask) return;
    const taskId = selectedTask.taskId; setFeedbackIds((ids) => [...ids, responseId]);
    try {
      const result = await submitResponseFeedback(responseId, feedbackType);
      if (mounted.current) setSelectedTask((task) => task?.taskId === taskId ? updateTaskResponseFeedback(task, result) : task);
    } catch (caught) { if (mounted.current) setError(errorMessage(caught)); }
    finally { if (mounted.current) setFeedbackIds((ids) => ids.filter((id) => id !== responseId)); }
  }
  return { listing, page, pageSize, taskType, selectedTask, loading, detailLoading, error, feedbackIds, visibilitySaving,
    conversations: conversationResultMode === taskType ? conversations : { items: [], total: 0, page: 1, pageSize: 10 },
    conversationPage, conversationLoading, previousConversationPage, nextConversationPage,
    conversationTotalPages: Math.max(Math.ceil(conversations.total / 10), 1), openConversation,
    refresh, changePageSize, previousPage, nextPage, submitFeedback, changeVisibility, canChangeVisibility: selectedTask?.ownerId === user?.id,
    totalPages: Math.max(Math.ceil(listing.total / pageSize), 1),
    selectedStatus: selectedTask ?? listing.items.find((item) => item.taskId === selectedId),
    rows: listing.items.map((item) => ({ ...item, select: () => selectTask(item.taskId) })) };
}
