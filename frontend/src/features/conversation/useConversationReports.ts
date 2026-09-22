import { useEffect, useRef, useState } from "react";
import { getConversationAssessment, listConversationReports, submitConversationReport } from "../../api/conversations";
import type { ConversationAssessmentDetailRead, ConversationAssessmentRead, ConversationRead } from "../../api/conversationTypes";
import { waitForAssessmentPoll } from "./workspaceData";
import { reportBounds } from "./reportBounds";

/** 管理会话报告，过期请求不能更新已切换的会话。 */
export function useConversationReports(conversation: ConversationRead | null, disabled: boolean, onUsageChange?: () => Promise<void>) {
  const { modelIds, maxTurn, judgeEnabled } = reportBounds(conversation);
  const [reports, setReports] = useState<ConversationAssessmentRead[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [revision, setRevision] = useState(0);
  const [modelConfigId, setModelConfigId] = useState<number | null>(modelIds[0] ?? null);
  const [throughTurn, setThroughTurn] = useState(maxTurn);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detail, setDetail] = useState<ConversationAssessmentDetailRead | null>(null);
  const [error, setError] = useState("");
  const epoch = useRef(0);
  const detailAbort = useRef<AbortController | null>(null);
  const submitLock = useRef(false);
  const usageCallback = useRef(onUsageChange);
  usageCallback.current = onUsageChange;
  const owner = conversation?.canContinue === true;

  useEffect(() => {
    ++epoch.current;
    setReports([]); setTotal(0); setPage(1); setDetail(null); setError("");
    setModelConfigId(modelIds[0] ?? null); setThroughTurn(maxTurn);
    submitLock.current = false; setSubmitting(false); setDetailLoading(false);
    return () => { ++epoch.current; detailAbort.current?.abort(); };
  }, [conversation?.id]);

  useEffect(() => {
    setThroughTurn((previous) => previous === 0 ? maxTurn : Math.min(previous, maxTurn));
  }, [maxTurn]);

  useEffect(() => {
    if (!conversation) return;
    const controller = new AbortController();
    const id = conversation.id;
    setLoading(true); setError("");
    /** 串行刷新待处理报告，最多三十次，不创建收费作业。 */
    async function load(): Promise<void> {
      try {
        for (let attempt = 0; attempt < 30; attempt += 1) {
          const value = await listConversationReports(id, page, 10, controller.signal);
          if (controller.signal.aborted) return;
          setReports(value.items); setTotal(value.total); setLoading(false);
          if (owner && usageCallback.current) {
            try { await usageCallback.current(); }
            catch {
              if (!controller.signal.aborted) setError("报告已更新，但今日用量刷新失败，请稍后刷新报告");
            }
            if (controller.signal.aborted) return;
          }
          if (!value.items.some((item) => item.status === "queued" || item.status === "running")) return;
          if (attempt === 29 || !await waitForAssessmentPoll(controller.signal, 3000)) return;
        }
      } catch (caught: unknown) {
        if (!controller.signal.aborted) setError(caught instanceof Error ? caught.message : "报告读取失败");
      } finally { if (!controller.signal.aborted) setLoading(false); }
    }
    void load();
    return () => controller.abort();
  }, [conversation?.id, page, revision, owner]);

  /** 重新读取当前分页并重启有界刷新。 */
  function refresh(): void { setRevision((value) => value + 1); }
  /** 限制分页范围，数量以服务端响应为准。 */
  function changePage(next: number): void {
    if (Number.isSafeInteger(next) && next >= 1 && next <= Math.max(1, Math.ceil(total / 10))) setPage(next);
  }
  /** 校验冻结分支和结束轮次，提交后重新读取服务端分页。 */
  async function submit(): Promise<void> {
    if (!conversation || !owner || disabled || !judgeEnabled || submitLock.current || modelConfigId === null
      || !modelIds.includes(modelConfigId) || !Number.isSafeInteger(throughTurn) || throughTurn < 1 || throughTurn > maxTurn) return;
    const current = epoch.current;
    submitLock.current = true; setSubmitting(true); setError("");
    try {
      await submitConversationReport(conversation.id, modelConfigId, throughTurn);
      if (current !== epoch.current) return;
      setPage(1); refresh();
    } catch (caught: unknown) {
      if (current === epoch.current) setError(caught instanceof Error ? caught.message : "报告提交失败");
    } finally {
      if (current === epoch.current) { submitLock.current = false; setSubmitting(false); }
    }
  }
  /** 连续选择报告时取消旧详情请求。 */
  async function showDetail(source: ConversationAssessmentRead): Promise<void> {
    if (!conversation) return;
    detailAbort.current?.abort();
    const controller = new AbortController(); detailAbort.current = controller;
    setDetail(null); setDetailLoading(true); setError("");
    try {
      const value = await getConversationAssessment(conversation.id, source.id, controller.signal);
      if (!controller.signal.aborted) setDetail(value);
    } catch (caught: unknown) {
      if (!controller.signal.aborted) setError(caught instanceof Error ? caught.message : "报告详情读取失败");
    } finally { if (!controller.signal.aborted) setDetailLoading(false); }
  }
  /** 关闭详情并取消读取。 */
  function closeDetail(): void { detailAbort.current?.abort(); setDetailLoading(false); setDetail(null); }
  /** 清空轮次输入后保持不可提交。 */
  function selectThroughTurn(value: number | null): void { setThroughTurn(value ?? 0); }
  return { reports, total, page, loading, submitting, detailLoading, detail, error, owner, modelConfigId, throughTurn,
    modelIds, maxTurn, judgeEnabled, refresh, setModelConfigId, setThroughTurn: selectThroughTurn,
    changePage, submit, showDetail, closeDetail, totalPages: Math.max(Math.ceil(total / 10), 1) };
}
