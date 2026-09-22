import { Button, Space, Tag } from "antd";
import type { ConversationAssessmentRead } from "../api/conversationTypes";
import { assessmentGroups, assessmentStatus, canReassess } from "../features/conversation/assessment";

interface Props {
  assessments: ConversationAssessmentRead[];
  owner: boolean;
  disabled: boolean;
  busy: number[];
  judgeEnabled: boolean;
  onDetail: (source: ConversationAssessmentRead) => Promise<void>;
  onReassess: (source: ConversationAssessmentRead) => Promise<void>;
}

/** 独立展示新多轮成绩和原文依据入口，不混入旧单轮规则评分。 */
export function ConversationAssessment(props: Props) {
  return <section className="conversation-assessment" aria-label="多轮评分">
    {props.assessments.length === 0 && <p>{props.judgeEnabled ? "尚无评分结果，可刷新会话查询" : "本会话未启用评审模型"}</p>}
    {props.assessments.map((assessment) => <article key={assessment.id}>
      <Space wrap><Tag>{assessmentStatus(assessment.status)}</Tag><span>{assessment.formal ? "三次正式复评" : "单次暂评"}</span></Space>
      <p className="form-hint">截至第 {assessment.throughTurn} 轮 · {assessment.scoreVersion}</p>
      {assessmentGroups(assessment).map((group) => <section key={group.title}>
        <strong>{group.title}：{group.final}</strong>
        <p>评价覆盖率 {group.coverage}{group.title === "对话质量" ? ` · ${group.critical}` : ""}</p>
        <dl className="conversation-score-dimensions">{group.dimensions.map((dimension) => <div key={dimension.label}><dt>{dimension.label}</dt><dd>{dimension.value}</dd></div>)}</dl>
      </section>)}
      <Space wrap>
        <Button size="small" onClick={() => void props.onDetail(assessment)}>查看评审依据</Button>
        {canReassess(assessment, props.owner) && !props.assessments.some((item) => item.formal) &&
          <Button size="small" disabled={props.disabled} loading={props.busy.includes(assessment.id)} onClick={() => void props.onReassess(assessment)}>正式复评</Button>}
      </Space>
    </article>)}
  </section>;
}
