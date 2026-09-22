import { useEffect, useMemo, useRef, useState } from "react";
import type { ChangeEvent, KeyboardEvent } from "react";
import { useSearchParams } from "react-router-dom";
import { createConversation, getConversation, getConversationAssessment, streamConversationBranch, streamConversationTurn, submitConversationReassessment } from "../../api/conversations";
import { submitResponseFeedback } from "../../api/client";
import type { ConversationAssessmentDetailRead, ConversationAssessmentRead, ConversationVisibility } from "../../api/conversationTypes";
import type { AvailableModelConfig } from "../evaluation/types";
import { createPendingResponses } from "../evaluation/evaluation";
import { createConversationRequestKey, mergeConversationEvent } from "./conversation";
import type { ConversationTurnState, ConversationWorkspaceState } from "./conversation";
import { canReassess } from "./assessment";
import { CONVERSATION_PAGE_SIZE, hasPendingAssessments, loadResponseAssessments, loadWorkspacePage, waitForAssessmentPoll } from "./workspaceData";
import type { ResponseAssessments } from "./workspaceData";
import { contextBudget } from "./contextBudget";

interface Options {
  mode?: "chat" | "rag";
  availableModels: AvailableModelConfig[];
  selectedModelIds: number[];
  judgeModelId: number | null;
  enableThinking: boolean;
  visibility: ConversationVisibility;
  disabled: boolean;
  createDisabled?: boolean;
  onUsageChange?: () => Promise<void>;
  knowledgeBaseId?: number | null;
}

/** 返回无旧会话数据的初始视图。 */
function emptyWorkspace(): ConversationWorkspaceState {
  return { conversation: null, turns: [], activeTurn: null, errorMessage: "" };
}

/** 管理普通多轮页面生命周期，各轮评分与新一轮生成可以同时运行。 */
export function useConversationWorkspace(options: Options) {
  const mode = options.mode ?? "chat";
  const [searchParams, setSearchParams] = useSearchParams();
  const [state, setState] = useState<ConversationWorkspaceState>(emptyWorkspace);
  const [prompt, setPrompt] = useState("");
  const [customBudget, setCustomBudget] = useState<number | null>(null);
  const budget = contextBudget(options.availableModels, options.selectedModelIds, options.judgeModelId);
  const inputBudget = customBudget ?? budget.recommended;
  const validBudget = budget.canCreate && Number.isSafeInteger(inputBudget) && inputBudget >= 1024 && inputBudget <= budget.maximum;
  const selectionKey = options.selectedModelIds.join(",");
  useEffect(() => { setCustomBudget(null); }, [selectionKey, options.judgeModelId, budget.maximum, budget.recommended]);
  const [loading, setLoading] = useState(false);
  const [creating, setCreating] = useState(false);
  const [running, setRunning] = useState(false);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [assessments, setAssessments] = useState<ResponseAssessments>({});
  const [detail, setDetail] = useState<ConversationAssessmentDetailRead | null>(null);
  const [feedbackBusy, setFeedbackBusy] = useState<number[]>([]);
  const [reassessmentBusy, setReassessmentBusy] = useState<number[]>([]);
  const epoch = useRef(0);
  const pageAbort = useRef<AbortController | null>(null);
  const streamAbort = useRef<AbortController | null>(null);
  const detailAbort = useRef<AbortController | null>(null);
  const createLock = useRef(false);
  const submitLock = useRef(false);
  const feedbackLocks = useRef(new Set<number>());
  const reassessmentLocks = useRef(new Set<number>());
  const rawId = searchParams.get("conversationId");
  const conversationId = rawId && /^[1-9]\d*$/.test(rawId) && Number.isSafeInteger(Number(rawId)) ? Number(rawId) : null;
  const modelNames = useMemo(() => new Map(options.availableModels.map((model) => [model.id, model.displayName])), [options.availableModels]);

  useEffect(() => {
    const current = ++epoch.current;
    setState(emptyWorkspace()); setPrompt(""); setAssessments({}); setDetail(null);
    setRunning(false); setCreating(false); setLoading(false); setPage(1); setTotal(0);
    setFeedbackBusy([]); setReassessmentBusy([]);
    createLock.current = false; submitLock.current = false;
    feedbackLocks.current.clear(); reassessmentLocks.current.clear();
    if (conversationId !== null) void reload(conversationId, undefined, current);
    else if (rawId) setState({ ...emptyWorkspace(), errorMessage: "会话地址无效" });
    return () => {
      epoch.current += 1;
      pageAbort.current?.abort(); streamAbort.current?.abort(); detailAbort.current?.abort();
    };
  }, [conversationId, rawId, mode]);

  /** 只把当前会话请求的错误呈现给用户。 */
  function report(error: unknown, current: number): void {
    if (current === epoch.current) setState((value) => ({ ...value, errorMessage: error instanceof Error ? error.message : "会话操作失败" }));
  }

  /** 重新加载指定页，旧页请求和轮询都必须失效。 */
  async function reload(id: number, requestedPage: number | undefined, current: number): Promise<void> {
    pageAbort.current?.abort();
    const controller = new AbortController();
    pageAbort.current = controller;
    setLoading(true);
    try {
      const conversation = await getConversation(id, controller.signal);
      if (conversation.mode !== (options.mode ?? "chat")) throw new Error("请在对应工作台打开该会话");
      const target = requestedPage ?? Math.max(1, Math.ceil(conversation.currentTurn / CONVERSATION_PAGE_SIZE));
      const loaded = await loadWorkspacePage(id, target, controller.signal);
      if (current !== epoch.current || controller.signal.aborted) return;
      setState({ conversation, turns: loaded.turns, activeTurn: null, errorMessage: "" });
      setPage(target); setTotal(loaded.total); setAssessments(loaded.assessments);
      if (hasPendingAssessments(loaded.assessments)) void poll(id, loaded.assessments, controller.signal, current);
    } catch (error) { if (!controller.signal.aborted) report(error, current); }
    finally { if (current === epoch.current && !controller.signal.aborted) setLoading(false); }
  }

  /** 有界刷新评分结果；页面切换会取消等待和正在发送的请求。 */
  async function poll(id: number, initial: ResponseAssessments, signal: AbortSignal, current: number): Promise<void> {
    let latest = initial;
    for (let attempt = 0; attempt < 40 && hasPendingAssessments(latest); attempt += 1) {
      if (!await waitForAssessmentPoll(signal) || current !== epoch.current) return;
      try {
        latest = await loadResponseAssessments(id, Object.keys(latest).map(Number), signal);
        if (current !== epoch.current || signal.aborted) return;
        setAssessments(latest);
        void options.onUsageChange?.();
      } catch (error) { if (!signal.aborted) report(error, current); return; }
    }
  }

  /** 固定当前配置创建新会话，同步锁防止连续点击重复提交。 */
  async function startConversation(): Promise<void> {
    if (createLock.current || !validBudget || options.disabled || options.createDisabled || options.selectedModelIds.length === 0
      || (mode === "rag" && !options.knowledgeBaseId)) return;
    createLock.current = true; setCreating(true);
    const current = epoch.current;
    try {
      const conversation = await createConversation({ mode: options.mode ?? "chat", modelIds: options.selectedModelIds,
        inputBudget,
        judgeModelId: options.judgeModelId, enableThinking: options.enableThinking, visibility: options.visibility,
        knowledgeBaseId: options.mode === "rag" ? options.knowledgeBaseId : undefined });
      if (current !== epoch.current) return;
      const next = new URLSearchParams(searchParams);
      next.set("conversationId", String(conversation.id)); setSearchParams(next);
    } catch (error) { report(error, current); }
    finally { if (current === epoch.current) { createLock.current = false; setCreating(false); } }
  }

  /** 离开当前会话返回创建入口，原会话和任务仍然保留。 */
  function leaveConversation(): void {
    const next = new URLSearchParams(searchParams);
    next.delete("conversationId"); setSearchParams(next);
  }

  /** 提交下一轮；评分查询不受生成锁影响，失败后重新核对服务端状态。 */
  async function submitTurn(): Promise<void> {
    const conversation = state.conversation;
    if (!conversation?.canContinue || conversation.generationStatus !== "idle" || submitLock.current
      || loading || creating || options.disabled || !prompt.trim()) return;
    submitLock.current = true; setRunning(true);
    const current = epoch.current;
    const question = prompt;
    const turn = conversation.currentTurn + 1;
    const controller = new AbortController();
    streamAbort.current = controller;
    const configuredModelIds = conversation.configuration.modelIds;
    const frozenIds = Array.isArray(configuredModelIds)
      ? configuredModelIds.filter((id): id is number => typeof id === "number")
      : options.selectedModelIds;
    const pending: ConversationTurnState = { turnId: null, turn, prompt: question, answers: {}, status: "running", assessmentsQueued: false,
      responses: createPendingResponses(frozenIds, options.availableModels) };
    setPrompt("");
    setState((value) => ({ ...value, errorMessage: "", activeTurn: turn,
      turns: [...value.turns.filter((item) => item.turn !== turn), pending] }));
    let completed = false;
    let streamError = "";
    let scoringError = "";
    try {
      await streamConversationTurn(conversation.id, { prompt: question, expectedTurn: conversation.currentTurn,
        requestKey: createConversationRequestKey() }, (event) => {
        if (current !== epoch.current || controller.signal.aborted) return;
        if (event.type === "turn_completed") completed = true;
        if (event.type === "stream_error") streamError = event.message;
        if (event.type === "assessment_submission_failed") scoringError = event.message;
        setState((value) => mergeConversationEvent(value, event, turn));
      }, controller.signal);
      if (streamError) throw new Error(streamError);
      if (!completed) throw new Error("连接已结束，但本轮完成状态尚未确认，请刷新会话");
      if (current === epoch.current) {
        await reload(conversation.id, undefined, current);
        if (scoringError) report(new Error(scoringError), current);
      }
    } catch (error) {
      if (current === epoch.current && !controller.signal.aborted) {
        setPrompt(question);
        await reload(conversation.id, undefined, current); report(error, current);
      }
    } finally {
      if (current === epoch.current) { submitLock.current = false; setRunning(false); streamAbort.current = null; void options.onUsageChange?.(); }
    }
  }

  /** 重试或跳过最新失败分支，期间复用续聊锁并只更新目标模型。 */
  async function branchAction(turn: ConversationTurnState, modelConfigId: number, action: "retry" | "skip"): Promise<void> {
    const conversation = state.conversation;
    if (!conversation?.canContinue || conversation.generationStatus !== "idle" || submitLock.current || loading || creating
      || options.disabled || turn.turnId === null || typeof turn.turnId !== "number" || turn.turn !== conversation.currentTurn || turn.status === "running") return;
    const response = turn.responses.find((item) => item.modelConfigId === modelConfigId);
    if (!response || response.status !== "failed" || response.answer === "用户已明确跳过本轮回答") return;
    const replacement = createPendingResponses([modelConfigId], options.availableModels)[0];
    if (!replacement) return;
    submitLock.current = true; setRunning(true);
    const current = epoch.current; const controller = new AbortController(); streamAbort.current = controller;
    let replayed = false; let completed = false; let streamError = ""; let scoringError = "";
    setState((value) => ({ ...value, activeTurn: turn.turn, turns: value.turns.map((item) => item.turn !== turn.turn ? item : {
      ...item, status: "running", answers: Object.fromEntries(Object.entries(item.answers).filter(([id]) => Number(id) !== modelConfigId)),
      contexts: (item.contexts ?? []).filter((context) => context.modelConfigId !== modelConfigId),
      responses: item.responses.map((itemResponse) => itemResponse.modelConfigId === modelConfigId ? replacement : itemResponse)
    }) }));
    try {
      await streamConversationBranch(conversation.id, { turnId: turn.turnId, modelConfigId, requestKey: createConversationRequestKey(), action }, (event) => {
        if (current !== epoch.current || controller.signal.aborted) return;
        if (event.type === "turn_started") replayed = event.replayed;
        if (event.type === "turn_completed") completed = true;
        if (event.type === "stream_error") streamError = event.message;
        if (event.type === "assessment_submission_failed") scoringError = event.message;
        setState((value) => mergeConversationEvent(value, event, turn.turn));
      }, controller.signal);
      if (streamError) throw new Error(streamError);
      if (!completed && !replayed) throw new Error("连接已结束，但分支完成状态尚未确认，请刷新会话");
      if (current === epoch.current) {
        await reload(conversation.id, page, current);
        if (scoringError) report(new Error(scoringError), current);
      }
    } catch (error) { if (current === epoch.current && !controller.signal.aborted) { await reload(conversation.id, page, current); report(error, current); } }
    finally { if (current === epoch.current) { submitLock.current = false; setRunning(false); streamAbort.current = null; void options.onUsageChange?.(); } }
  }

  /** 分页只改变读取范围，生成过程中禁止切换以保留实时输出。 */
  function changePage(next: number): void {
    if (conversationId !== null && !running && !loading) void reload(conversationId, next, epoch.current);
  }

  /** 手动刷新也用于恢复超过有界轮询时间的后台评分。 */
  function refresh(): void {
    if (conversationId !== null && !running && !loading) void reload(conversationId, page, epoch.current);
  }

  /** 反馈只更新原回答的互动数据，不拿旧评分覆盖多轮判定。 */
  async function submitFeedback(responseId: number, feedbackType: "like" | "dislike"): Promise<void> {
    if (feedbackLocks.current.has(responseId)) return;
    const current = epoch.current;
    feedbackLocks.current.add(responseId); setFeedbackBusy([...feedbackLocks.current]);
    try {
      const result = await submitResponseFeedback(responseId, feedbackType);
      if (current !== epoch.current) return;
      setState((value) => ({ ...value, turns: value.turns.map((turn) => ({ ...turn,
        responses: turn.responses.map((response) => response.id === responseId ? { ...response, feedback: result.feedback } : response) })) }));
    } catch (error) { report(error, current); }
    finally { if (current === epoch.current) { feedbackLocks.current.delete(responseId); setFeedbackBusy([...feedbackLocks.current]); } }
  }

  /** 发起正式复评后读取最新作业并开始当前页的有界轮询。 */
  async function reassess(source: ConversationAssessmentRead): Promise<void> {
    if (!state.conversation || submitLock.current || loading || !canReassess(source, state.conversation.canContinue) || reassessmentLocks.current.has(source.id)) return;
    const current = epoch.current;
    const id = state.conversation.id;
    const originalPage = pageAbort.current;
    reassessmentLocks.current.add(source.id); setReassessmentBusy([...reassessmentLocks.current]);
    try {
      const formal = await submitConversationReassessment(id, source.id);
      if (current === epoch.current && pageAbort.current === originalPage) {
        if (!submitLock.current) await reload(id, page, current);
        else {
          const responseId = formal.responseId;
          if (responseId !== null) setAssessments((value) => ({ ...value,
            [responseId]: [formal, ...(value[responseId] ?? []).filter((item) => item.id !== formal.id)] }));
        }
      }
    } catch (error) { report(error, current); }
    finally { if (current === epoch.current) { reassessmentLocks.current.delete(source.id); setReassessmentBusy([...reassessmentLocks.current]); } }
  }

  /** 加载逐次评审依据，连续点击时仅保留最后一次详情。 */
  async function showAssessment(source: ConversationAssessmentRead): Promise<void> {
    if (conversationId === null) return;
    detailAbort.current?.abort();
    const controller = new AbortController();
    detailAbort.current = controller;
    const current = epoch.current;
    try {
      const value = await getConversationAssessment(conversationId, source.id, controller.signal);
      if (current === epoch.current && !controller.signal.aborted) setDetail(value);
    } catch (error) { if (!controller.signal.aborted) report(error, current); }
  }

  /** 关闭详情时取消尚未完成的加载。 */
  function closeAssessment(): void { detailAbort.current?.abort(); setDetail(null); }

  /** 处理输入，保留换行和中文输入法组合按键。 */
  function changePrompt(event: ChangeEvent<HTMLTextAreaElement>): void { setPrompt(event.target.value); }

  /** 清空预算时保持不可提交，不能悄悄回退到默认值。 */
  function changeInputBudget(value: number | null): void { setCustomBudget(value ?? 0); }

  /** Enter 发送，Shift+Enter 换行，组合输入不触发发送。 */
  function handlePromptKeyDown(event: KeyboardEvent<HTMLTextAreaElement>): void {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void submitTurn(); }
  }

  /** 流式占位没有持久化回答 ID，不参与反馈。 */
  function isFeedbackBusy(responseId: string | number): boolean { return typeof responseId === "number" && feedbackBusy.includes(responseId); }

  const configuredIds = state.conversation?.configuration.modelIds;
  const frozenModelIds = Array.isArray(configuredIds) ? configuredIds.filter((id): id is number => typeof id === "number") : options.selectedModelIds;
  const canSend = !!state.conversation?.canContinue && state.conversation.generationStatus === "idle"
    && !loading && !creating && !running && !options.disabled;
  return { state, prompt, loading, creating, running, page, total, assessments, detail, canSend,
    budget, inputBudget, validBudget, changeInputBudget,
    frozenInputBudget: typeof state.conversation?.configuration.inputBudget === "number" ? String(state.conversation.configuration.inputBudget) : "未知",
    modelNames, frozenModelIds, disabled: options.disabled, reassessmentBusy,
    startConversation, leaveConversation, submitTurn, changePage, refresh, submitFeedback,
    reassess, branchAction, showAssessment, closeAssessment, changePrompt, handlePromptKeyDown, isFeedbackBusy };
}
