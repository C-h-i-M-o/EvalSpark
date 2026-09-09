import { useEffect, useRef, useState } from "react";
import type { ChangeEvent, KeyboardEvent } from "react";
import { listAvailableModels, streamEvaluationTask, submitResponseFeedback } from "../../api/client";
import type { FeedbackType } from "../../api/client";
import { useTodayTokenUsage } from "../evaluation/useTodayTokenUsage";
import { listAllKnowledgeBases } from "../../api/knowledgeBases";
import { applyFeedbackResult, createPendingResponses, createStreamEventBatcher,
  mergeStreamEvents } from "../evaluation/evaluation";
import type { StreamEventBatcher } from "../evaluation/evaluation";
import type { AvailableModelConfig, EvaluationTaskState } from "../evaluation/types";
import type { EvaluationVisibility } from "../../api/client";
import type { KnowledgeBase } from "../knowledge-bases/types";
import { buildRagPayload, errorMessage, getRagJudgeModels, stopRagState } from "./rag";

export function useRagEvaluation() {
  const [models, setModels] = useState<AvailableModelConfig[]>([]);
  const [libraries, setLibraries] = useState<KnowledgeBase[]>([]);
  const [libraryId, setLibraryId] = useState<number | null>(null);
  const [modelIds, setModelIds] = useState<number[]>([]);
  const [judgeId, setJudgeId] = useState<number | null>(null);
  const [prompt, setPrompt] = useState("");
  const [enableThinking, setEnableThinking] = useState(true);
  const [visibility, setVisibility] = useState<EvaluationVisibility>("private");
  const [initialLoading, setInitialLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const { tokenUsage, tokenUsageErrorMessage, loadTokenUsage } = useTodayTokenUsage(running);
  const [task, setTask] = useState<EvaluationTaskState | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [elapsed, setElapsed] = useState(0);
  const [feedbackIds, setFeedbackIds] = useState<number[]>([]);
  const [revision, setRevision] = useState(0);
  const mounted = useRef(true);
  const runningRef = useRef(false);
  const runVersion = useRef(0);
  const abort = useRef<AbortController | null>(null);
  const batcher = useRef<StreamEventBatcher | null>(null);
  useEffect(() => { mounted.current = true; return () => {
    mounted.current = false; runVersion.current += 1; abort.current?.abort(); batcher.current?.clear();
  }; }, []);
  useEffect(() => {
    const controller = new AbortController();
    setInitialLoading(true);
    void Promise.all([listAvailableModels(controller.signal), listAllKnowledgeBases(controller.signal)])
      .then(([available, bases]) => {
        if (controller.signal.aborted) return;
        setModels(available); setLibraries(bases);
        setLibraryId((current) => bases.some((base) => base.id === current) ? current : bases.find((base) => base.available)?.id ?? null);
        setModelIds((current) => {
          const retained = current.filter((id) => available.some((model) => model.id === id));
          return retained.length ? retained : available.slice(0, Math.min(2, Math.max(0, available.length - 1))).map((model) => model.id);
        });
      }).catch((caught: unknown) => { if (!controller.signal.aborted) setError(errorMessage(caught)); })
      .finally(() => { if (!controller.signal.aborted) setInitialLoading(false); });
    return () => controller.abort();
  }, [revision]);
  useEffect(() => {
    const choices = getRagJudgeModels(models, modelIds);
    setJudgeId((current) => choices.some((model) => model.id === current) ? current : choices[0]?.id ?? null);
  }, [models, modelIds]);
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => setElapsed((seconds) => seconds + 1), 1000);
    return () => clearInterval(timer);
  }, [running]);
  const library = libraries.find((value) => value.id === libraryId) ?? null;
  const idleModels = getRagJudgeModels(models, modelIds);
  const quotaExhausted = tokenUsage !== null && !tokenUsage.unlimited && tokenUsage.remainingTokens === 0;
  const canSubmit = !initialLoading && !running && !!prompt.trim() && !!library?.available && library.status === "ready"
    && modelIds.length > 0 && judgeId !== null && idleModels.some((model) => model.id === judgeId) && !quotaExhausted;
  function refresh() { if (!runningRef.current) { setError(""); setRevision((value) => value + 1); void loadTokenUsage(); } }
  function selectLibrary(value: number) {
    // 切换任务上下文时立即丢弃旧批次；即使网络稍后返回，也不能覆盖新选择。
    runVersion.current += 1; abort.current?.abort(); batcher.current?.clear();
    runningRef.current = false; setRunning(false); setTask(null); setLibraryId(value); setNotice(""); setError("");
  }
  function changePrompt(event: ChangeEvent<HTMLTextAreaElement>) { setPrompt(event.target.value); }
  function cancel() { abort.current?.abort(); setNotice("已请求停止。已发生的调用仍可能计费，最终收尾状态请在历史任务中查看。"); }
  async function submit() {
    if (!canSubmit || runningRef.current) return;
    const payload = buildRagPayload({ prompt, library, modelIds, judgeModelId: judgeId, enableThinking, visibility });
    const version = ++runVersion.current;
    const controller = new AbortController(); abort.current = controller;
    runningRef.current = true; setRunning(true); setElapsed(0); setError(""); setNotice("");
    setTask({ taskId: null, taskType: "rag", status: "running", prompt, visibility, responses: createPendingResponses(modelIds, models) });
    const events = createStreamEventBatcher((values) => {
      if (mounted.current && version === runVersion.current) setTask((current) => mergeStreamEvents(current, values));
    });
    batcher.current?.clear(); batcher.current = events;
    let completed = false;
    try {
      await streamEvaluationTask(payload, (event) => {
        if (version !== runVersion.current || controller.signal.aborted) return;
        if (event.type === "task_completed") completed = true;
        events.enqueue(event);
      }, controller.signal);
      if (!completed) throw new Error("连接在任务完成前结束，已保存的结果可在历史任务中查看。");
    } catch (caught) {
      if (mounted.current && version === runVersion.current) {
        events.flush(); setTask(stopRagState);
        if (!controller.signal.aborted) setError(errorMessage(caught));
      }
    } finally {
      events.flush();
      if (mounted.current && version === runVersion.current) {
        runningRef.current = false; setRunning(false); setRevision((value) => value + 1);
        void loadTokenUsage();
      }
    }
  }
  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault(); void submit();
    }
  }
  async function feedback(responseId: number, feedbackType: FeedbackType) {
    if (feedbackIds.includes(responseId)) return;
    const version = runVersion.current;
    setFeedbackIds((ids) => [...ids, responseId]);
    try {
      const result = await submitResponseFeedback(responseId, feedbackType);
      if (mounted.current && version === runVersion.current) setTask((current) => current ? applyFeedbackResult(current, result) : current);
    } catch (caught) { if (mounted.current && version === runVersion.current) setError(errorMessage(caught)); }
    finally { if (mounted.current) setFeedbackIds((ids) => ids.filter((id) => id !== responseId)); }
  }
  return { models, libraries, library, libraryId, selectLibrary, judgeId, setJudgeId, prompt, changePrompt, handleKeyDown,
    modelOptions: models.map((model) => ({ ...model, selected: modelIds.includes(model.id), toggle: () => setModelIds((current) =>
      current.includes(model.id) ? current.filter((id) => id !== model.id) : [...current, model.id]) })),
    idleOptions: idleModels.map((model) => ({ value: model.id, label: model.displayName })),
    libraryOptions: libraries.map((base) => ({ value: base.id, label: `${base.name}${base.available ? "" : "（未就绪）"}`, disabled: !base.available })),
    enableThinking, setEnableThinking, visibility, setVisibility, tokenUsage, quotaExhausted, initialLoading, running, task, error: error || tokenUsageErrorMessage, notice, elapsed,
    feedbackIds, feedback, canSubmit, submit, cancel, refresh };
}
