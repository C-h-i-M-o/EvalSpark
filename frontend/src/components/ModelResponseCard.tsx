import { useLayoutEffect, useRef, useState } from "react";
import { Button, Modal, Space } from "antd";

import { animateModalIn } from "../animations/pageMotion";
import { isPendingResponse } from "../features/evaluation/evaluation";
import type { DisplayModelResponse, EvaluationScore, FeedbackToggleResult, ScoreStatus } from "../features/evaluation/types";
import { CommentPanel } from "./CommentPanel";
import { MarkdownRenderer } from "./MarkdownRenderer";
import { formatScore, ScoreBar } from "./ScoreBar";
import { RagEvidencePanel } from "./RagEvidencePanel";
import { RagScoreDetails, RagScoreSummary } from "./RagScoreDetails";
import { displayRagResponse, ragStageLabels } from "../features/rag/rag";

interface ModelResponseCardProps {
  response: DisplayModelResponse;
  elapsedSeconds: number;
  feedbackSubmitting: boolean;
  showComments?: boolean;
  privateDiscussion?: boolean;
  onFeedback: (responseId: number, feedbackType: FeedbackToggleResult["feedbackType"]) => void;
}

const dimensionLabels: Array<{ key: keyof EvaluationScore; label: string; weight: string }> = [
  { key: "relevance", label: "相关性", weight: "30%" },
  { key: "completeness", label: "完整性", weight: "25%" },
  { key: "clarity", label: "清晰度", weight: "20%" },
  { key: "format", label: "格式", weight: "15%" },
  { key: "safety", label: "安全性", weight: "10%" }
];

const scoreStatusMessages: Record<ScoreStatus, string> = {
  scored: "已计入统计",
  judge_failed: "LLM 评分失败，本次不计入统计",
  judge_unstable: "LLM 有效评分分歧较大，本次不计入统计",
  judge_disabled: "本次关闭 LLM 评分，仅展示规则检查，不计入统计",
  model_failed: "模型调用失败，不计入统计"
};

export const JUDGE_SCORE_WEIGHT_LABEL = "70%";
export const JUDGE_STABILITY_THRESHOLD_LABEL = "2.0";

export function scoreStatusText(status: ScoreStatus): string {
  return scoreStatusMessages[status];
}

type NormalizedScore = Omit<Required<EvaluationScore>, "judgeScoreRange" | "ruleDictionaryVersion"> & {
  final: number | null;
  ruleFinal: number | null;
  baseFinal: number | null;
  judgeScoreRange: number | null;
  ruleDictionaryVersion: string | null;
};

export function ModelResponseCard({
  response,
  elapsedSeconds,
  feedbackSubmitting,
  showComments = false,
  privateDiscussion = false,
  onFeedback
}: ModelResponseCardProps) {
  const [detailVisible, setDetailVisible] = useState(false);
  const detailRef = useRef<HTMLElement | null>(null);
  response = displayRagResponse(response);

  if (isPendingResponse(response)) {
    return (
      <article className="response-card pending">
        <ResponseHeader modelName={response.modelName} statusLabel={response.interrupted ? "已中断" : response.ragStage ? ragStageLabels[response.ragStage] : "等待模型响应"} scoreText="..." failed={!!response.interrupted} />
        {!response.interrupted && <div className="pending-answer" aria-label="模型响应等待中">
          <span />
          <span />
          <span />
        </div>}
        {response.ragRetrieval && <RagEvidencePanel {...response.ragRetrieval} />}
        <dl className="metric-row">
          <Metric label="耗时" value={`${elapsedSeconds}s`} />
          <Metric label="输出" value="等待中" />
          <Metric label="成本" value="待估算" />
        </dl>
      </article>
    );
  }

  if ("streaming" in response) {
    return (
      <article className={`response-card${response.scoring ? " scoring" : ""}`}>
        <ResponseHeader
          modelName={response.modelName}
          statusLabel={response.interrupted ? "已中断" : response.ragStage ? ragStageLabels[response.ragStage] : response.scoring ? "评分中……" : "生成中"}
          scoreText={response.scoring ? "评分中" : "..."}
          failed={!!response.interrupted}
        />
        <ScrollableMarkdownAnswer content={response.answer} placeholder={response.ragStage ? "正在执行当前阶段，尚无可展示的回答。" : "模型回答已生成，正在准备评分。"} />
        {response.ragRetrieval && <RagEvidencePanel {...response.ragRetrieval} answer={response.answer} />}
        <dl className="metric-row">
          <Metric label="耗时" value={`${elapsedSeconds}s`} />
          <Metric label="输出" value={response.scoring ? "等待评分" : "生成中"} />
          <Metric label="成本" value="待估算" />
        </dl>
      </article>
    );
  }

  const score = normalizedScore(response.score);
  const feedback = response.feedback;
  const failed = response.status !== "success";

  return (
    <article className={`response-card${failed ? " failed" : ""}`}>
      <ResponseHeader
        modelName={response.modelName}
        statusLabel={failed ? "调用失败" : "调用成功"}
        scoreText={failed ? "失败" : formatScore(score.final)}
        failed={failed}
      />
      <ScrollableMarkdownAnswer content={response.answer} placeholder="暂无回答内容" />
      {response.rag && <RagEvidencePanel evidence={response.rag.evidence} rewrittenQuery={response.rag.rewrittenQuery} answer={response.answer} />}
      {response.rag?.failureStage && <p className="rag-error">RAG 链路未获得有效评分：{response.rag.errorCode}</p>}
      <p className={`score-status-note ${score.scoreStatus}`}>{scoreStatusText(score.scoreStatus)}</p>
      <dl className="metric-row">
        <Metric label="耗时" value={`${response.latencyMs}ms`} />
        <Metric label="输出" value={String(response.outputTokens)} />
        <Metric label="成本" value={`${formatCost(response.estimatedCost)} ${response.currency}`} />
      </dl>
      {response.rag ? <RagScoreSummary rag={response.rag} /> : <div className="score-bars">
        <ScoreBar label="相关性" value={score.relevance} />
        <ScoreBar label="完整性" value={score.completeness} />
        <ScoreBar label="清晰度" value={score.clarity} />
      </div>}
      <footer className="card-actions">
        <Button
          className={feedback.liked ? "active" : ""}
          disabled={feedbackSubmitting}
          onClick={() => onFeedback(response.id, "like")}
        >
          点赞 {feedback.likeCount}
        </Button>
        <Button
          danger
          className={feedback.disliked ? "danger active" : "danger"}
          disabled={feedbackSubmitting}
          onClick={() => onFeedback(response.id, "dislike")}
        >
          点踩 {feedback.dislikeCount}
        </Button>
        <Button type="primary" ghost onClick={() => setDetailVisible(true)}>
          {response.rag ? "评分与全文" : "查看全文"}
        </Button>
      </footer>
      <Modal
        title={`${response.modelName} · 回答详情`}
        open={detailVisible}
        width="min(860px, 94vw)"
        footer={null}
        onCancel={() => setDetailVisible(false)}
        afterOpenChange={(open) => {
          if (open) {
            animateModalIn(detailRef.current);
          }
        }}
      >
        <section ref={detailRef} className="response-detail-modal">
          <MarkdownRenderer content={response.answer} />
          {response.rag && <><RagEvidencePanel evidence={response.rag.evidence} rewrittenQuery={response.rag.rewrittenQuery} answer={response.answer} /><RagScoreDetails rag={response.rag} score={response.score} /></>}
          {response.rag && <h4>规则检查（占基础分 20%，以下为规则内部分项权重）</h4>}
          <div className="score-detail-list">
            {dimensionLabels.map((dimension) => {
              const value = score[dimension.key];
              const details = score.details[dimension.key] || ["暂无命中项明细"];
              return (
                <article key={dimension.key} className="score-detail-item">
                  <header>
                    <span>{dimension.label}</span>
                    <Space size={8}>
                      <strong>{typeof value === "number" ? `${formatScore(value)} / 10` : "-"}</strong>
                      <em>权重 {dimension.weight}</em>
                    </Space>
                  </header>
                  <ul>
                    {details.map((detail) => (
                      <li key={detail}>{detail}</li>
                    ))}
                  </ul>
                </article>
              );
            })}
          </div>
          <article className="score-detail-item">
            <header>
              <span>用户反馈</span>
              <Space size={8}>
                <strong>{feedback.liked ? "已点赞" : feedback.disliked ? "已点踩" : "未反馈"}</strong>
                <em>权重 10%</em>
              </Space>
            </header>
            <p>
              点赞 {feedback.likeCount} 次，点踩 {feedback.dislikeCount} 次。
              {score.feedbackScore === null || score.feedbackScore === undefined
                ? "暂无点赞或点踩，最终分保持基础分不变。"
                : `最终分 = 基础分 ${formatScore(score.baseFinal)} x 90% + 反馈分 ${formatScore(score.feedbackScore)} x 10%。`}
            </p>
          </article>
          <article className="score-detail-item">
            <header>
              <span>评分状态</span>
              <Space size={8}>
                <strong>{scoreStatusText(score.scoreStatus)}</strong>
                <em>{score.excludedFromStats ? "不计入统计" : "计入统计"}</em>
              </Space>
            </header>
            <p>
              最终分 {formatScore(score.final)}，规则分 {formatScore(score.ruleFinal)}，Judge 平均分{" "}
              {formatScore(score.judgeFinal)}
              {score.judgeScoreRange === null ? "。" : `，三次分差 ${formatScore(score.judgeScoreRange)}。`}
            </p>
          </article>
          {!response.rag && score.judgeRuns.length > 0 ? (
            <article className="score-detail-item">
              <header>
                <span>三次 LLM 评分</span>
                <Space size={8}>
                  <strong>{score.judgeRuns.length} 次</strong>
                  <em>稳定阈值 {JUDGE_STABILITY_THRESHOLD_LABEL}</em>
                </Space>
              </header>
              <div className="judge-run-list">
                {score.judgeRuns.map((run) => (
                  <section key={`${run.runIndex}-${run.promptCode}`} className={run.error ? "failed" : ""}>
                    <strong>
                      #{run.runIndex} {run.promptCode}
                    </strong>
                    <span>{run.error ? "失败" : `${formatScore(run.score)} / 10`}</span>
                    <p>{run.error || run.comment || "评审模型未返回说明"}</p>
                  </section>
                ))}
              </div>
            </article>
          ) : null}
          {!response.rag && score.judgeComment ? (
            <article className="score-detail-item">
              <header>
                <span>LLM 评审理由</span>
                <Space size={8}>
                  <strong>{formatScore(score.judgeFinal)} / 10</strong>
                  <em>权重 {JUDGE_SCORE_WEIGHT_LABEL}</em>
                </Space>
              </header>
              <p>{score.judgeComment}</p>
            </article>
          ) : null}
          {showComments ? <CommentPanel responseId={response.id} privateDiscussion={privateDiscussion} /> : null}
        </section>
      </Modal>
    </article>
  );
}

interface ScrollMetrics {
  scrollTop: number;
  clientHeight: number;
  scrollHeight: number;
}

export function isNearScrollBottom(metrics: ScrollMetrics, threshold = 28): boolean {
  return metrics.scrollHeight - metrics.scrollTop - metrics.clientHeight <= threshold;
}

function ScrollableMarkdownAnswer({
  content,
  placeholder
}: {
  content: string;
  placeholder: string;
}) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const shouldStickToBottomRef = useRef(true);
  const displayContent = content || placeholder;

  useLayoutEffect(() => {
    const element = scrollRef.current;
    if (!element || !shouldStickToBottomRef.current) {
      return;
    }
    element.scrollTop = element.scrollHeight;
  }, [displayContent]);

  function handleScroll(): void {
    const element = scrollRef.current;
    if (!element) {
      return;
    }
    shouldStickToBottomRef.current = isNearScrollBottom(element);
  }

  return (
    <div ref={scrollRef} className="answer-scroll" onScroll={handleScroll}>
      <MarkdownRenderer content={displayContent} />
      {/*
        Profiler 采样时临时替换为：
        <Profiler id="MarkdownRenderer" onRender={recordProfilerRender}>
          <MarkdownRenderer content={displayContent} />
        </Profiler>
      */}
    </div>
  );
}

function ResponseHeader({
  modelName,
  statusLabel,
  scoreText,
  failed
}: {
  modelName: string;
  statusLabel: string;
  scoreText: string;
  failed: boolean;
}) {
  return (
    <header className="response-head">
      <div>
        <p className="panel-label">模型名称</p>
        <h3>{modelName}</h3>
      </div>
      <div className="response-status">
        <span className={failed ? "status-badge failed" : "status-badge"}>{statusLabel}</span>
        <strong className={failed ? "failed" : ""}>{scoreText}</strong>
      </div>
    </header>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

function normalizedScore(score: EvaluationScore): NormalizedScore {
  return {
    scoreVersion: score.scoreVersion ?? "chat-v1",
    relevance: score.relevance || 0,
    completeness: score.completeness || 0,
    clarity: score.clarity || 0,
    format: score.format || 0,
    safety: score.safety || 0,
    final: score.final ?? null,
    details: score.details || {},
    ruleFinal: score.ruleFinal ?? score.final ?? null,
    judgeFinal: score.judgeFinal ?? null,
    baseFinal: score.baseFinal ?? score.final ?? null,
    feedbackScore: score.feedbackScore ?? null,
    judgeComment: score.judgeComment ?? null,
    judgeDetails: score.judgeDetails || {},
    scoreStatus: score.scoreStatus ?? "scored",
    excludedFromStats: score.excludedFromStats ?? false,
    judgeRuns: score.judgeRuns || [],
    judgeScoreRange: score.judgeScoreRange ?? null,
    ruleDictionaryVersion: score.ruleDictionaryVersion ?? null
  };
}

function formatCost(value: number): string {
  return value.toFixed(4).replace(/0+$/, "").replace(/\.$/, ".0");
}
