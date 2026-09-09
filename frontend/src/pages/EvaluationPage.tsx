import { useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { Button, Input, Segmented, Select, Space, Switch } from "antd";
import { useNavigate } from "react-router-dom";

import {
  ApiError,
  listAvailableModels,
  streamEvaluationTask,
  submitResponseFeedback
} from "../api/client";
import type { EvaluationTaskPayload, EvaluationVisibility, FeedbackType } from "../api/client";
import { useTodayTokenUsage } from "../features/evaluation/useTodayTokenUsage";
import { useResponseGridMotion } from "../animations/pageMotion";
import { ModelResponseCard } from "../components/ModelResponseCard";
import { useAuth } from "../features/auth/AuthContext";
import {
  applyFeedbackResult,
  createStreamEventBatcher,
  createPendingResponses,
  getInitialJudgeSelection,
  getIdleJudgeModels,
  isTransientResponse,
  mergeStreamEvents,
  normalizeJudgeModelId
} from "../features/evaluation/evaluation";
import type { StreamEventBatcher } from "../features/evaluation/evaluation";
import type { AvailableModelConfig, EvaluationTaskState } from "../features/evaluation/types";

const { TextArea } = Input;

export function EvaluationPage() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [prompt, setPrompt] = useState("");
  const [selectedModelIds, setSelectedModelIds] = useState<number[]>([]);
  const [judgeModelId, setJudgeModelId] = useState<number | null>(null);
  const [enableJudge, setEnableJudge] = useState(true);
  const [enableThinking, setEnableThinking] = useState(false);
  const [visibility, setVisibility] = useState<EvaluationVisibility>("public");
  const [availableModels, setAvailableModels] = useState<AvailableModelConfig[]>([]);
  const [modelConfigLoading, setModelConfigLoading] = useState(false);
  const [modelConfigErrorMessage, setModelConfigErrorMessage] = useState("");
  const [taskState, setTaskState] = useState<EvaluationTaskState | null>(null);
  const [loading, setLoading] = useState(false);
  const { tokenUsage, tokenUsageLoading, tokenUsageErrorMessage, loadTokenUsage } = useTodayTokenUsage(loading);
  const [errorMessage, setErrorMessage] = useState("");
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [feedbackSubmittingIds, setFeedbackSubmittingIds] = useState<number[]>([]);
  const timerRef = useRef<number | null>(null);
  const responseGridRef = useRef<HTMLElement | null>(null);
  const streamBatcherRef = useRef<StreamEventBatcher | null>(null);

  const quotaExhausted = Boolean(tokenUsage && !tokenUsage.unlimited && (tokenUsage.remainingTokens ?? 0) <= 0);
  const showModelNotice = !modelConfigLoading && availableModels.length === 0;
  const selectedModelSet = useMemo(() => new Set(selectedModelIds), [selectedModelIds]);
  const judgeModelOptions = useMemo(
    () => getIdleJudgeModels(availableModels, selectedModelIds),
    [availableModels, selectedModelIds]
  );
  const judgeUnavailable = judgeModelOptions.length === 0;
  const canSubmit =
    !loading &&
    prompt.trim().length > 0 &&
    selectedModelIds.length > 0 &&
    !quotaExhausted &&
    (!enableJudge || (judgeModelId !== null && !selectedModelSet.has(judgeModelId)));
  const completedResponseCount =
    taskState?.responses.filter((response) => !isTransientResponse(response)).length ?? 0;
  useResponseGridMotion(responseGridRef, completedResponseCount);

  useEffect(() => {
    void loadAvailableModels();
  }, []);

  useEffect(() => {
    if (availableModels.length === 0) {
      return;
    }
    const nextJudgeModelId = normalizeJudgeModelId(judgeModelId, availableModels, selectedModelIds);
    if (judgeModelId !== nextJudgeModelId) {
      setJudgeModelId(nextJudgeModelId);
    }
    if (enableJudge && nextJudgeModelId === null) {
      setEnableJudge(false);
    }
  }, [availableModels, enableJudge, judgeModelId, selectedModelIds]);

  useEffect(() => {
    return () => {
      streamBatcherRef.current?.clear();
      stopWaitingTimer();
    };
  }, []);

  async function loadAvailableModels(): Promise<void> {
    setModelConfigLoading(true);
    setModelConfigErrorMessage("");
    try {
      const models = await listAvailableModels();
      setAvailableModels(models);
      setSelectedModelIds((currentIds) => {
        const nextSelectedModelIds = selectDefaultModelIds(models, currentIds);
        const nextJudgeSelection = getInitialJudgeSelection(models, nextSelectedModelIds);
        setJudgeModelId(nextJudgeSelection.judgeModelId);
        setEnableJudge(nextJudgeSelection.enabled);
        return nextSelectedModelIds;
      });
    } catch (error) {
      setModelConfigErrorMessage(getErrorMessage(error, "模型配置加载失败"));
    } finally {
      setModelConfigLoading(false);
    }
  }

  function toggleJudge(checked: boolean): void {
    if (!checked) {
      setEnableJudge(false);
      return;
    }

    const nextJudgeModelId = normalizeJudgeModelId(judgeModelId, availableModels, selectedModelIds);
    if (nextJudgeModelId === null) {
      setEnableJudge(false);
      return;
    }

    setJudgeModelId(nextJudgeModelId);
    setEnableJudge(true);
  }

  async function submitTask(): Promise<void> {
    if (!canSubmit) {
      return;
    }

    const payload: EvaluationTaskPayload = {
      prompt,
      modelIds: selectedModelIds,
      enableJudge,
      judgeModelId: enableJudge ? judgeModelId : null,
      enableThinking,
      visibility
    };

    setErrorMessage("");
    setLoading(true);
    setTaskState({
      taskId: null,
      status: "running",
      prompt,
      visibility,
      responses: createPendingResponses(selectedModelIds, availableModels)
    });
    startWaitingTimer();
    streamBatcherRef.current?.clear();
    const streamBatcher = createStreamEventBatcher((events) => {
      setTaskState((currentState) => mergeStreamEvents(currentState, events));
    });
    streamBatcherRef.current = streamBatcher;

    try {
      await streamEvaluationTask(payload, (event) => {
        streamBatcher.enqueue(event);
      });
    } catch (error) {
      setErrorMessage(getErrorMessage(error, "评测任务创建失败"));
    } finally {
      streamBatcher.flush();
      if (streamBatcherRef.current === streamBatcher) {
        streamBatcherRef.current = null;
      }
      stopWaitingTimer();
      setLoading(false);
      await loadTokenUsage();
    }
  }

  async function submitFeedback(responseId: number, feedbackType: FeedbackType): Promise<void> {
    if (feedbackSubmittingIds.includes(responseId) || !taskState) {
      return;
    }
    setFeedbackSubmittingIds((ids) => [...ids, responseId]);
    try {
      const result = await submitResponseFeedback(responseId, feedbackType);
      setTaskState((currentState) => (currentState ? applyFeedbackResult(currentState, result) : currentState));
    } catch (error) {
      setErrorMessage(getErrorMessage(error, "用户反馈提交失败"));
    } finally {
      setFeedbackSubmittingIds((ids) => ids.filter((id) => id !== responseId));
    }
  }

  function startWaitingTimer(): void {
    stopWaitingTimer();
    setElapsedSeconds(0);
    timerRef.current = window.setInterval(() => {
      setElapsedSeconds((seconds) => seconds + 1);
    }, 1000);
  }

  function stopWaitingTimer(): void {
    if (timerRef.current !== null) {
      window.clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }

  function toggleModel(modelId: number): void {
    setSelectedModelIds((ids) => (ids.includes(modelId) ? ids.filter((id) => id !== modelId) : [...ids, modelId]));
  }

  function handlePromptKeyDown(event: KeyboardEvent<HTMLTextAreaElement>): void {
    if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) {
      return;
    }
    event.preventDefault();
    if (canSubmit) {
      void submitTask();
    }
  }

  return (
    <section className="evaluation-page">
      <header className="page-head">
        <div>
          <p className="eyebrow">Evaluation Task</p>
          <h2>回答质量对比</h2>
        </div>
      </header>

      {showModelNotice ? (
        <section className="notice-panel">
          <div>
            <p className="panel-label">可用模型</p>
            <h3>当前没有可用于评测的模型</h3>
            <p>{user?.role === "admin" ? "请进入模型配置启用模型并填写 API Key。" : "请联系管理员配置可用模型。"}</p>
          </div>
          {user?.role === "admin" ? (
            <Button type="primary" onClick={() => navigate("/models")}>
              去配置
            </Button>
          ) : null}
        </section>
      ) : null}

      <section className="token-usage-panel" aria-busy={tokenUsageLoading}>
        <div>
          <p className="panel-label">今日 Token</p>
          <strong>{tokenUsage ? (tokenUsage.unlimited ? "管理员账号不限额" : "按北京时间自然日重置") : "正在读取今日额度"}</strong>
        </div>
        <dl>
          <Metric label="已使用" value={formatTokens(tokenUsage?.usedTokens)} />
          <Metric label="剩余" value={tokenUsage?.unlimited ? "不限额" : formatTokens(tokenUsage?.remainingTokens)} />
          <Metric label="每日额度" value={tokenUsage?.unlimited ? "不限额" : formatTokens(tokenUsage?.dailyLimit)} />
        </dl>
      </section>

      <section className="query-panel">
        <div className="query-header">
          <div>
            <p className="panel-label">用户问题</p>
            <h3>创建一次多模型评测</h3>
          </div>
          <Space className="query-switches" wrap>
            <Segmented<EvaluationVisibility>
              value={visibility}
              disabled={loading}
              options={[
                { value: "public", label: "公开评测" },
                { value: "private", label: "私有评测" }
              ]}
              onChange={(nextValue) => setVisibility(nextValue)}
            />
            <Space size={6}>
              <Switch
                checked={enableThinking}
                disabled={loading}
                onChange={(checked) => setEnableThinking(checked)}
              />
              思考模式
            </Space>
            <Space size={6}>
              <Switch
                checked={enableJudge}
                disabled={loading || judgeUnavailable}
                onChange={toggleJudge}
              />
              LLM 评审
            </Space>
          </Space>
        </div>
        <TextArea
          id="evaluation-prompt"
          name="evaluationPrompt"
          value={prompt}
          disabled={loading}
          rows={5}
          placeholder="输入问题后按 Enter 发起评测，Shift + Enter 换行"
          onChange={(event) => setPrompt(event.target.value)}
          onKeyDown={handlePromptKeyDown}
        />
        <div className="model-row">
          <div className="model-options">
            {availableModels.map((modelConfig) => (
              <Button
                key={modelConfig.id}
                className={selectedModelSet.has(modelConfig.id) ? "selected" : ""}
                disabled={loading}
                onClick={() => toggleModel(modelConfig.id)}
              >
                {modelConfig.displayName}
              </Button>
            ))}
          </div>
          <Button type="primary" className="primary-action" disabled={!canSubmit} onClick={() => void submitTask()}>
            {loading ? "等待模型响应" : "开始评测"}
          </Button>
        </div>
        {enableJudge ? (
          <div className="judge-row">
            <span className="panel-label">评审模型</span>
            <Select
              value={judgeModelId ?? ""}
              disabled={loading}
              style={{ minWidth: 240 }}
              options={judgeModelOptions.map((modelConfig) => ({
                value: modelConfig.id,
                label: modelConfig.displayName
              }))}
              onChange={(value) => setJudgeModelId(Number(value))}
            />
          </div>
        ) : null}
        {judgeUnavailable ? (
          <p className="form-hint warning">LLM 评审需要至少保留一个未参与本次测评的空闲模型。</p>
        ) : null}
      </section>

      <AlertMessage message={modelConfigErrorMessage} tone="warning" />
      <AlertMessage message={tokenUsageErrorMessage} tone="warning" />
      <AlertMessage
        message={quotaExhausted ? "今日 Token 额度已用完，请明日再试或联系管理员调整额度" : ""}
        tone="error"
      />
      {loading ? (
        <section className="waiting-banner" aria-live="polite">
          <div>
            <p className="panel-label">模型调用中</p>
            <strong>
              已完成 {completedResponseCount} / {selectedModelIds.length}，已等待 {elapsedSeconds}s
            </strong>
          </div>
          <span className="waiting-pulse" aria-hidden="true" />
        </section>
      ) : null}
      <AlertMessage message={errorMessage} tone="error" />

      {taskState?.responses.length ? (
        <section ref={responseGridRef} className="response-grid">
          {taskState.responses.map((response) => (
            <ModelResponseCard
              key={response.modelConfigId ?? response.id}
              response={response}
              elapsedSeconds={elapsedSeconds}
              feedbackSubmitting={!isTransientResponse(response) && feedbackSubmittingIds.includes(response.id)}
              onFeedback={(responseId, feedbackType) => void submitFeedback(responseId, feedbackType)}
            />
          ))}
        </section>
      ) : null}
      {/*
        Profiler 采样时临时替换上面的 response-grid：
        1. import { Profiler, ... } from "react";
        2. import { recordProfilerRender } from "../utils/profilerMetrics";
        3. 用 <Profiler id="EvaluationPage.response-grid" onRender={recordProfilerRender}> 包裹 response-grid，
           并用 <Profiler id="ModelResponseCard" onRender={recordProfilerRender}> 包裹每个 ModelResponseCard。
        4. 浏览器中读取 window.__MCE_PROFILER_METRICS__。
      */}
    </section>
  );
}

function selectDefaultModelIds(models: AvailableModelConfig[], currentIds: number[]): number[] {
  const availableIds = models.map((model) => model.id);
  const retainedIds = availableIds.filter((modelId) => currentIds.includes(modelId));
  if (retainedIds.length > 0) {
    return retainedIds;
  }
  const preferredIds = models
    .filter((modelConfig) => ["deepseek", "minimax"].includes(modelConfig.providerName))
    .map((modelConfig) => modelConfig.id);
  return (preferredIds.length > 0 ? preferredIds : availableIds).slice(0, 2);
}

function getErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof ApiError || error instanceof Error) {
    return error.message || fallback;
  }
  return fallback;
}

function formatTokens(value: number | null | undefined): string {
  return Number(value || 0).toLocaleString("zh-CN");
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

function AlertMessage({ message, tone }: { message: string; tone: "warning" | "error" }) {
  if (!message) {
    return null;
  }
  return <p className={`alert-message ${tone}`}>{message}</p>;
}
