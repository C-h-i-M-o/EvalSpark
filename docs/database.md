# 数据库设计

2026-09-22 迁移 `20260922_01`（前置 `20260920_01`）新增 `conversation_turns.generation_epoch`，整数非空、默认1。每次显式重试锁内递增；心跳、阶段启动、结果写入和收尾核对当前代次。迁移仅编写未执行，业务部署需依次包含 `20260918_01`、`20260920_01`、`20260922_01`，备份及执行另行授权。

失败分支操作沿用 `conversation_usage`：stage=`branch_action`，operation_key=`branch:{requestKey}`，已完成且 accounted=true，Token/费用为0。detail_json 保存请求摘要、turnId、modelConfigId、action、responseId、attempt；它是幂等操作记录，不是模型费用。每次操作新建同任务/同模型的 `model_responses`，config_snapshot 追加 conversationAttempt/branchAction；旧失败回答及费用保持原值。新响应ID隔离生成/检索费用，递增attempt隔离摘要上下文。跳过追加失败占位及排除统计的评分状态，不重开原轮任务。续聊只读取成功回答，展示读取最新尝试。

原轮要求提取完成后，requirements 流水 detail_json 标记 requirementsSaved=true；显式重试只复用这些已保存要求，标记缺失时拒绝重试，避免用不完整要求评价新回答。无历史记录回填或业务数据库写入。

问题状态沿用 `conversation_usage`，`stage=resolution`，`operation_key=assessment:{jobId}:resolution:{issueHash}:{reviewIndex}`。detail_json 保存报告/问题/组号、固定 packet、实际 usage 及独立 result（valid/status/reason/errorCode/evidence）；不写回 ConversationJudgeRun 或历史分数。报告完成前将三组状态汇总写入 input_json.report.issueResolutions/resolutionComplete，finish 同步到 result_json.report。无新增迁移，当前仅隔离库验证。

新报告 `input_json.report.semanticPreparation=3`：窗口、相邻边界、非相邻完整窗口对依次复用 opportunity 流水。`detail_json.result` 保留未过滤提取结果，正式机会按精确触发/目标锚点合并；最多新增32次远距离提取，预算及数量缺口保留。冻结时按版本重建来源及规范合并结果，版本1/2保持原算法；无新表或迁移。

单轮评分 `input_json.packet` 保留完整历史、回答和 RAG 资料，`batches` 保存逐项规范材料或预算不足的 unknown 包，正式复评原样复用。歧义检查项新增可空 `ambiguous_source_id` 绑定本分支用户消息，结论与引用存于独立 JudgeRun，不改共享 requirement 记录。资料未能完整送评时结果显式标记 `rag_evidence_budget_exceeded`，不持久化虚构的通过断言。本轮仅代码检查，未执行数据库操作。

上下文展示不新增表或迁移：读取 `conversation_contexts.snapshot_json`（普通根快照、RAG 的 rag_requests.rewrite/answer），按轮次与模型选择 attempt 最大记录；`covered_through_turn` 仅表示历史读取边界。公开接口投影最小元数据，不返回快照正文。

迁移 `20260920_01`（前序 `20260918_01`）为 model_configs 增加可空 INTEGER `context_window`，旧值为 NULL，不猜测供应商容量。仅在固定隔离测试库执行升级；业务部署需先备份并单独授权执行两项多轮相关迁移，当前不得自动升级业务库。降级会删除容量字段，尚未验证。

报告准备固定沿用 `conversation_assessments.input_json`：`originalReportInput` 保存提交时的 packet 及可选 batches；`preparationFrozen=true`、最终 packet/batches 与 report 内 preparationUsageIds、semanticOpportunityCount 在同一事务保存。原始要求和未知约束仍保留，不能由提取结果覆盖。无新增表或迁移；已验证错误计划不会改变原快照。

报告准备阶段复用 `conversation_usage`，无新增迁移：stage 为 `opportunity`，operation_key 为 `assessment:{id}:opportunity:{index}`。detail_json 保存 assessmentId、preparationIndex、sourceIds、snapshotHash，调用结束后追加 usage 和经过校验的 result。费用先结算，解析失败仍保留已知费用；结果保存前不允许正式 JudgeRun 开始。作业中断将 pending 准备流水置 unknown，已完成流水保留。准备服务已在固定隔离 MySQL 验证，业务库未执行多轮迁移。

## 2026-09-20 报告存储增量

生成心跳复用 `conversations.updated_at`，每次更新同时限定 user_id、generating 状态及 conversation_turns 的当前轮次标识。结束轮次和旧轮次心跳不更新后续会话。该时间也会随会话正常变更更新，后续恢复只能把它作为保守存活信号；不能仅凭任务创建时间判定多轮失活。无新迁移。

多轮恢复使用同一事务锁会话、轮次、任务、回答和本轮用量；在持有阶段流水锁后才结算日额度，避免与摘要回调形成相反锁序。未完成 ConversationUsage 置 unknown 并保留空费用，已完成且未入账的流水沿用 accounted 幂等补账；RAG pending 阶段改 unknown，已知部分沿用唯一 TokenUsageLog 入账。成功回答保留，其他回答失败，任务/轮次 interrupted，最后会话 idle。不会批量重写旧任务或启动新模型调用。

要求检查项在固定 packet/batches 中增加可选 `allowed_source_ids`。原文允许范围由要求有效区间装配，非 null 时逐条引文必须属于该集合；超预算降为未知时保存空集合，不伪装为已检查部分原文。字段属于已有 JSON 快照，无新增迁移。旧快照未提供时按原有来源校验兼容读取，不批量回填。

检查项 JSON 新增 `evidence_refs` 保存校验后的来源 ID、原文轮次和逐字引文；`conversation_judge_runs.result_json.items` 与报告 `result_json.report.opportunityReviews[].findings` 保留这些字段。旧检查项加载时默认空引用列表，不能从旧拼接文本猜测轮次。无新增数据表/列，不批量改写历史记录。

会话报告复用 `conversation_assessments` 和 `conversation_judge_runs`，无额外迁移。`input_json` 固定总 packet、batches 和 report 元数据；每个检查项只属于一个分段，来源可跨段重复，原文不截断。`run_index=(reviewIndex-1)*batchCount+batchIndex`，正式报告执行三组相同分段，每次调用独立登记用量；按完整评审组聚合检查项，不能平均分段总分。`result_json.report` 记录固定范围的生成成功/失败、限制、RAG 趋势和逐组机会统计，未来对话不修改既有报告。业务库仍未执行 `20260918_01`。

历史引用映射随实际回答消息保存在 conversation_contexts.snapshot_json.rag_requests.answer 中；评分时核对当前资料的文档版本、片段、原文与位置，并复制至 conversation_assessments.input_json.packet.rag.evidence[].historical_references。数组保留多个旧轮次来源，无新增列或迁移；原回答资料快照不被重新编号覆盖。

RAG 实际输入在 conversation_contexts.snapshot_json.rag_requests 中按 rewrite/answer 保存，每阶段包含 messages、preparation_hash、估计量、压缩结果及原文 source_ids。两阶段共用同一生成 attempt，不能相互覆盖；摘要调用在 conversation_usage 使用 rewrite_summary/answer_summary 阶段键分别幂等入账，无新增数据库列。

多轮 RAG 存储补充（开发中）：预留轮次与 RAG 回答共用同一 evaluation_tasks.id，不再另外建任务。RAG 检索/生成费用仍从 rag_response_details.stage_usage_json 汇总至既有唯一回答用量日志，新摘要、要求提取与 Judge 费用使用 conversation_usage，避免重复入账。多轮生成成功的兼容 evaluation_results 保持 final_score=null、excluded_from_stats=true，judge_prompt_version=rag-multiturn-v1；正式质量结果以 conversation_assessments 为准。RAG 协调器及 HTTP 已接通隔离验证。

## 2026-09-18 多轮结构（开发中）

RAG 新评分使用既有新增表的 JSON 字段，不另增迁移：conversation_assessments.input_json.packet.rag 保存固定回答片段/位置和该回答资料快照；conversation_judge_runs.result_json 保存 ragAssertions（包括资料逐字引用）和 evidence 分组。正式复评重用输入并检查逐片段适用性，输出 dialogue/evidence 两组聚合；生成后自动提交已在隔离 MySQL 验证。旧 RAG 兼容评分不参与新评分聚合。

`conversation_requirements` 已有版本存取服务：requirement_key 为服务端要求 ID，version 单调递增，source_turn 为本次版本变更轮次；detail_json 中 source_turn/source_id/quote 保留原始要求来源，retired_at 表示被替代的生效轮次。修改预算只新增退休版本和新要求，不更新旧行。读取报告截止轮次时先选当时每个 key 的最新版本，再按 scope/retired_at 判断适用性。服务仅作者可用，已由普通生成入口在候选调用前自动保存。

普通生成内部协调器已创建 pending model_responses，完整 reply 返回后更新 success；失败/取消保存 failed，不保存失败残片为可续聊历史。generate:{responseId} 对应生成阶段用量并同事务入日累计。兼容旧 evaluation_results 时 final_score 为空且 excluded_from_stats=true，避免未接新评分的多轮结果被旧算法赋分。现有 TEXT 对单条回答仍有限制，超出 65535 UTF-8 字节显式失败并保留已知用量；长会话不等于无限单消息。

摘要已使用 conversation_usage：operation_key 为 summary:{turnId}:{branchId}:{attempt}:{batch}，每次请求先登记 pending，回调后保存模型计费快照、latency、usage。已知用量与日累计同事务完成；未知用量不入账。重复开始被拒绝，重复结束不再次累计，生成终止后仍可记录已发生调用。现有输入快照命中时不创建新摘要流水。

`conversation_contexts` 已由历史装配服务实际写入：snapshot_json 包含发送消息、来源列表、压缩信息及 preparation_hash；source_hash 校验对应原始历史内容。同一轮/模型/attempt 不可改写快照；准备参数一致时重用已保存输入。不会覆盖原始任务问题或模型回答。

评分执行器已使用 assessment/judge_run/usage 三表：queued 作业原子认领为 running；外部调用前保存 pending run 和用量占位；每次返回在同事务更新判定与用量，最终从已保存 run 聚合成绩。取消时 pending 调用改 interrupted、用量标 unknown 且 tokens/cost 保持空值。已知阶段用量与日累计同事务幂等入账，accounted=true；未知用量保持 accounted=false。归属日期按阶段登记的 UTC 时间转换为北京时间，不重复写旧回答用量日志。Celery 作业入口已注册；每 60 秒恢复扫描已接入 Worker：重投 queued，超过四小时的 running 只标中断；普通/RAG HTTP 生成后即时投递已接通，真实队列联合验收尚待完成。

新增迁移 `20260918_01`，前置 `20260909_01`。仅在独立测试库执行，业务库尚未迁移；不得因本文描述而直接对业务库执行升级或降级。升级保留原任务、回答和评分，新增会话默认私有，不修改旧任务可见性。

- `conversations` 增加 visibility、config_json、knowledge_snapshot_json、current_turn、generation_status、updated_at。
- `conversation_turns` 保存每轮问题和旧 task_id 关联，(conversation_id,turn_index) 与 (conversation_id,request_key) 唯一；生成锁不等待评分。
- `conversation_contexts` 保存模型分支的压缩/输入快照与原文来源，(turn_id,model_config_id,attempt) 唯一。
- `conversation_requirements` 保存要求版本及来源，(conversation_id,requirement_key,version) 唯一。
- `conversation_assessments` 保存评分作业/结果快照，(conversation_id,operation_key) 唯一；`conversation_judge_runs` 保存各次 Judge 结果与失败码。
- `conversation_usage` 保存阶段用量与入账标记；未知 Token/费用为空，operation_key 防止重复记账。后台评分和计费接入仍在开发，不能将表结构存在视为流程完成。

新外键使用 BIGINT 匹配既有表。MySQL DDL 非事务化，升级检测已存在的会话新增列并核对类型/可空性后继续，避免部分失败后重复加列。回滚会删除新会话明细，需先备份且另行授权；本次未执行降级。完整字段和验收见 [多轮合并计划](multiturn-spec-plan.md)。

## 2026-09-09 任务可见性更新

复用 `evaluation_tasks.visibility` 存储普通与 RAG 的 public/private；RAG 创建时省略则默认 private。作者修改时按任务 ID 与 user_id 加行锁后更新现有字段，不新增表或迁移，不批量转换旧记录，不改回答、评分、用量与知识库所有权。公开读取包括已存储的证据快照，源文件仍私有。测试仅使用隔离数据，本轮不修改业务任务的可见性。

> 当前状态（2026-09-09）：全局 Embedding API 与本地兼容接入已实现，业务库已备份并迁移，前后端已启动。本文阶段 1—7 的早期冻结/未执行描述为历史记录，最新验证及未覆盖范围以 docs/v3-rag-spec-plan.md 顶部为准。

## V3 阶段 2 增量迁移

新增迁移 `20260902_01`，唯一父版本为 `20260705_03`。本阶段只在独立 `multichateval_rag_test` 验证，尚未应用到业务库。新外键使用 BIGINT，与既有 MySQL 主键对齐；不修改旧初始化 SQL 或历史迁移。

| 表/字段 | 作用及约束 |
| --- | --- |
| `evaluation_tasks.task_type` | 非空 VARCHAR(16)，默认 `chat`；保留旧问题、回答、归属、可见性和原始得分。阶段 5 接通 `rag` 创建请求 |
| `knowledge_bases` | 用户归属、名称/说明、切分配置、状态、内容版本、安全错误码和时间；用户/状态索引 |
| `knowledge_documents` | 库/用户外键、展示原名、唯一随机存储键、类型、字节数、SHA-256、状态、索引版本、块数及时间；库/状态索引 |
| `knowledge_chunks` | UUID 块 ID、文档/库/用户外键、索引版本、块序号、原文、Token 数和 `source_json`；文档/版本/块序号唯一；归属版本索引 |
| `rag_jobs` | 确定性 UUID、库/可空文档外键、操作与目标版本、状态/进度、尝试次数、租约 `lease_until`、通知时间 `dispatched_at`、安全错误码和时间 |
| `rag_response_details` | 以回答外键为主键；知识库、文档版本、改写、证据、阶段用量、Judge 轮次独立快照；RAG 分项、基础分用 DECIMAL(18,10)，错误/公式版本独立保存 |

块来源支持文本行区间、PDF 页区间、DOCX 段落/表格逻辑块区间，均从 1 开始。Qdrant 和 MySQL 将共用块 UUID；此阶段尚未写块或向量。`rag_response_details` 不以外键关联知识库/文档，后续删除源资料不能级联抹去历史证据。

## V3 阶段 3 增量与生命周期

新增 `20260902_02`，父版本 `20260902_01`，仅将 `knowledge_chunks.text` 从 TEXT 扩为 MEDIUMTEXT。真实 Qwen 测试证明 Token 上限并不能保证正文低于 64 KiB；79,203 字节合规块在旧字段报 1406，扩容后完整存取通过。已有历史迁移不改，不自动执行缩容 downgrade；该迁移目前仅在隔离测试库应用。

块 UUID 由文档 ID、索引版本、块序号确定。MySQL 保存正文、Token 数和来源，Qdrant 只保存向量及归属/版本元数据；重复消息和自动重试均幂等写入。完整目标块数、向量数与文档版本三者核对后才发布 ready，其他库/旧版数据不参与核对或配额。

`rag_jobs.attempt` 每次领取递增，不给旧执行器复用；running 的 `lease_until` 是租约期限，queued/failed 上的未来时间是重试/在途写入冷却期限。同库领取检查当前运行作业和冷却窗口；失败清理保持 deleting，不声称物理删除。过期恢复扫描、版本检查、发布和最终清理由 `rag/jobs.py` 与 `rag/indexing.py` 执行。只有已确认删除向量、原文件和块后才标记 deleted；知识库和文档墓碑、作业记录保留，历史证据独立存储约束不变。

真实服务测试采用独立 Compose 项目 `evalspark-rag-lifecycle-test` 和 `lifecycle` profile：MySQL/原文/向量/Redis 卷全部独立，仅复用固定模型缓存；不要对默认业务项目运行测试。按用户资源受限决定，完整模型链路延期到条件充足设备，见合并规格计划的阶段 3 记录。

文档先保存受控文件，再在知识库行锁内检查 100 份上限、插入元数据/作业并提交。提交失败只清理本次未引用文件；事务提交后通知 broker，通知失败保持 `queued`，由后续 Worker 恢复补投。重试/重建/删除提升对应版本，旧作业不能发布为新版本。删除先写墓碑，物理文件/块/向量清理由阶段 3 实现，不把请求成功等同物理删除成功。

业务升级前必须另行确认：实际父版本、备份位置与校验、DDL 元数据锁影响、恢复窗口和权限。新增 `task_type` 后 ORM 依赖该列，不能跳过迁移直接启动新后端。迁移不提供自动破坏性 downgrade；恢复使用经批准的备份流程。默认完整 Compose 会执行 `migrate`，未批准业务升级时只使用独立测试 Compose。

隔离测试统一使用 `scripts/verify-rag.ps1 -Mode integration` 或 `bash scripts/verify-rag.sh integration`，完整执行顺序见 `v3-rag-spec-plan.md` 第 11 节。入口固定项目 `evalspark-rag-test` 和 `docker/rag-test.env`，先停测试 Worker、运行迁移/数据库回归，再启动索引服务与假模型验收。测试服务无宿主机端口、无业务网络/数据卷、无业务 `.env` 挂载；运行前强制校验开关、主机 `mysql-test` 和数据库名。每轮使用唯一测试用户/供应商并保留测试库，不清空任何表或数据卷；只有模型文件缓存与现有开发栈共用。

阶段 7 只新增测试入口，不新增业务迁移。测试配置解析已通过；新增两候选 API、三轮评分、反馈/取消、并发记账与删除后快照联合验证未执行，仍不得据此升级业务库或声称全部 MySQL 验收通过。

## V3 阶段 4 快照与用量

本阶段不新增 DDL，使用已有 `rag_response_details`。开始时固定知识库 ID/名称、内容修订号、已发布文档版本、切分配置和 Embedding revision，提前创建候选回答。证据屏障后二次版本检查通过，分别保存 `rewritten_query` 和最多 5 条证据 JSON；失败阶段及错误码同事务保存。后续删除源资料不修改历史快照。

`stage_usage_json` 按 `(stage,runIndex)` 唯一，stage 为 rewrite/embed/generate/judge；模型快照仅包含展示名、模型名、参数、单价和币种，不存凭据/地址/备注。status 为 pending/known/unknown；未知 Token 和费用为 null，汇总返回已知外部 Token 小计与 `hasUnknownUsage`，费用分币种保存。回答顶层用量/费用仍只代表生成阶段。

阶段 5 起，阶段占位与更新先锁 `evaluation_tasks`，再锁 `model_responses` 与明细，均使用 current read，避免 REPEATABLE READ 旧快照覆盖最新状态。终态评分与 `record_rag_usage` 同事务，锁下检查唯一 `token_usage_logs.response_id`，复用原每日累加路径；重复提交不二次累计。已确认中断的任务仅将 pending 置 unknown、保留片段和已知用量并结束，不自动重试模型。

阶段 5 不新增 DDL。每轮 Judge 的结构化原始结果、有效结果/安全错误码和该轮用量在同一短事务保存；三轮完整后聚合并写 `rag_response_details` 的高精度分项、基础分及 `evaluation_results` 的终态。失败/不稳定没有最终分，反馈从 rag-v1 的保存基础分重算。RAG 不覆盖任何旧 chat 得分。

任务执行最多 55 分钟，现有 Worker 每分钟恢复入口只关闭创建超过 60 分钟的 pending/private/rag 任务；锁内重新检查状态和截止时间，已结束或过期任务拒绝新的阶段写入。恢复不依赖当前知识库是否仍存在、不重放外部请求，只有持久化已知用量可以补记。新增 SQL/恢复/反馈测试尚未在当前关闭的 Docker 环境执行。

SQLite 轻量测试覆盖 SQL 往返与回滚，但不能证明 MySQL 并发锁行为；`test_rag_usage_mysql.py` 保留显式隔离库开关用例，本机延期执行。业务库迁移门禁不变。

## 初始化与迁移基线

Docker 首次创建 `mysql_data` 时，`docker/mysql/init/001_schema.sql` 会创建截至 Alembic `20260612_01` 的基础结构，并在 `alembic_version` 中写入该版本。随后 `migrate` 服务执行 `alembic upgrade head`，只应用基线之后的迁移；已有数据卷不会重复执行初始化 SQL，但每次启动仍会检查并升级到最新版本。

`alembic_version` 是迁移工具维护的元数据表，不承载业务数据。普通 `docker compose down` 会保留该表和全部业务表；只有显式执行带 `-v` 的清卷命令才会删除开发数据库卷。

## users

用户表，保存登录用户信息。

字段重点：

- `username`：登录用户名，唯一。
- `password_hash`：Argon2 密码哈希。
- `role`：用户角色，支持 `user` 和 `admin`。
- `status`：用户状态，支持 `active` 和 `disabled`。
- `last_login_at`：最近一次登录成功时间。

开放注册用户默认为 `user` 和 `active`。注册密码不会明文入库，接口要求二次确认密码，并要求密码至少 8 位且包含数字、小写字母和大写字母。匿名用户固定使用 `id = 0`，状态为 `disabled`，只用于承载 demo-v1 历史数据，不允许登录。

管理员用户管理页可以将普通账号或其他管理员账号的 `status` 切换为 `disabled` 或 `active`；被禁用用户不能登录。系统匿名用户和当前登录的管理员账号不能被封禁。

## conversations

会话表，保存用户的一组评测上下文。

## evaluation_tasks

评测任务表。用户每提交一次问题，就创建一个任务。

字段重点：

- `user_id`：任务创建者。旧匿名任务统一迁移为 `0`。
- `prompt`：用户问题
- `status`：任务状态
- `visibility`：`public` 或 `private`，默认 `public`
- `completed_at`：完成时间

创建评测时会先写入任务记录。同步接口和逐 token 流式接口都会使用真实任务 ID 返回给前端。

公开任务对所有登录用户可见；私有任务只对创建者可见。管理员通过普通任务接口也不能查看其他用户的私有任务。

## model_providers

模型供应商表，保存管理员实际创建的 OpenAI-compatible 供应商。

字段重点：

- `name`：供应商唯一名称。
- `base_url`：OpenAI-compatible 接口基础地址，例如 `https://api.deepseek.com`。
- `api_key_encrypted`：MVP 阶段复用为版本化密钥字段。当前使用 `plain:<api_key>` 明文格式保存，未来可升级为 `enc:v1:<ciphertext>` 加密格式。
- `enabled`：供应商是否启用。

业务代码不得直接读写原始 API Key 字段，需要通过统一密钥 helper 保存、读取和掩码展示。列表接口只返回 `hasApiKey` 和 `maskedApiKey`，不返回原文。

## model_configs

具体模型配置表，例如 `deepseek-v4-flash`、`MiniMax-M2.5`、`glm-4.7`。

字段重点：

- `provider_id`：关联模型供应商。
- `model_name`：发送给模型接口的真实模型名。
- `display_name`：前端展示名。
- `temperature`：默认温度。
- `timeout_seconds`：单次请求超时秒数。
- `notes`：管理员备注。
- `currency`：价格币种，支持 `CNY` 和 `USD`。
- `price_input` / `price_output` / `price_cache_hit` / `price_cache_creation`：每 100 万 Token 的四类单价。
- `max_tokens`：单次回答最大输出 token。
- `enabled`：该模型配置是否可在评测页选择。

系统不自动创建供应商数据。前端提供常见官方供应商预设和 OpenAI-compatible 空白模板，所有实际保存的配置均可编辑、禁用或删除。

## model_responses

模型回答表。每个模型的一次回答单独保存。

字段重点：

- `answer_text`：原始回答
- `latency_ms`：响应耗时
- `input_tokens` / `output_tokens` / `cache_hit_tokens` / `cache_creation_tokens` / `total_tokens`：四类与总 Token 统计
- `input_cost` / `output_cost` / `cache_hit_cost` / `cache_creation_cost`：四类费用
- `estimated_cost`：四项费用之和
- `currency`：费用币种
- `config_snapshot`：调用时的模型参数与价格快照，不含 API Key
- `status` / `error_message`：调用状态

每个模型调用结束后写入一条回答记录。接口响应中的 `responses[].id` 对应本表主键，`responses[].modelConfigId` 对应 `model_configs.id`。

## user_token_quotas

保存普通用户每日总 Token 上限。每个用户最多一条记录；没有记录时使用默认值 100,000。管理员角色不受额度限制。

## token_usage_logs

记录每个模型回答产生的总 Token，用于审计。字段包含 `user_id`、`task_id`、`response_id`、`model_config_id`、`usage_date` 和 `total_tokens`。

## daily_user_token_usage

按用户和北京时间自然日汇总总 Token。`user_id + usage_date` 唯一，模型回答持久化时原子累加。

## evaluation_results

评分结果表。每条模型回答对应一组评分。

当前规则评分会写入：

- `relevance_score`：相关性
- `completeness_score`：完整性
- `clarity_score`：清晰度
- `format_score`：格式符合度
- `safety_score`：安全性
- `rule_score`：规则综合分
- `judge_score` / `judge_comment`：LLM Judge 至少 2 次成功且稳定后的成功评分平均分、理由和结构化明细；无有效 Judge 时为空
- `final_score`：当前最终分；排除统计的状态下为空
- `score_status`：`scored`、`model_failed`、`judge_failed`、`judge_unstable`、`manual_required` 或 `judge_disabled`
- `excluded_from_stats`：是否从反馈统计中排除
- `judge_runs_json` / `judge_score_range`：三轮 Judge 原始结果和分差
- `rule_dictionary_version`：本次使用的规则词库版本
- `judge_prompt_group_code` / `judge_prompt_group_version`：本次使用的 Judge Prompt 分组

基础分为规则分，或在三轮 Judge 中至少 2 次成功且成功分数分差不超过 2.0 时使用 `rule_score * 0.30 + judge_score * 0.70`。存在点赞/点踩时，反馈分按点赞比例映射到 0–10，并以 10% 权重计入 `final_score`；评论不参与评分。

写入规则评分前，评分器会排除完整的 `<think>...</think>` 思考区块；若标签未闭合，则忽略 `<think>` 及其后续内容。`model_responses.answer_text` 仍保存完整原始回答，便于前端继续默认展开展示思考过程。

## rule_dictionaries / rule_terms

管理员可维护的规则词库。`rule_dictionaries` 保存词库编码、名称、版本和启用状态；`rule_terms` 保存词条文本、分类、权重、启用状态和备注。规则评分服务通过词库缓存读取启用词条，用于用户意图识别、格式要求、安全高风险、拒答质量、专业提醒和关键词硬检查。

评分配置服务读取管理员规则词库时会从 `backend/app/services/scoring/default_rule_seed.json` 幂等维护一组 `builtin-v1` 默认词典和词条，保证用户第一次部署空库后会自动导入当前完整词表。默认种子包含 7 个词典、390 条词条，覆盖 `format_requirement`、`intent_marker`、`refusal`、`safe_alternative`、`high_risk_domain`、`professional_caution` 和 `dangerous_pattern`。代码/格式词表覆盖常见编程语言、脚本、查询语言、前后端框架和测试框架；专业提醒覆盖医疗、法律、金融、安全和心理健康等高风险场景。若历史初始化过程中产生了相同词典类型或相同词条，服务会保留最早记录，合并重复词典，并删除重复词条；已有管理员修改不会被默认种子覆盖。

## judge_prompt_groups / judge_prompt_templates

管理员可维护的 Judge Prompt。`judge_prompt_groups` 保存分组编码、名称、版本和启用状态；`judge_prompt_templates` 保存同一分组下三轮 Prompt 的模板内容。评测时服务选择启用分组并执行三轮 Judge，至少 2 次成功且成功分差稳定时才写入有效 `judge_score`。

## user_feedback

用户反馈表。用于保存登录用户的点赞和点踩状态。

- demo-v1 旧匿名反馈继续归属 `users.id = 0`。
- 新反馈写入当前登录用户 ID。
- `user_id + response_id` 具备唯一约束，保证同一用户对同一回答只有一个当前反馈。
- 当前支持的 `feedback_type` 为：

- `like`：点赞
- `dislike`：点踩

`user_feedback` 不再保存评论。

## user_comments

公开评论表。每条记录表示用户对某个模型回答发布的一条纯文本评论。

- `user_id`：评论用户；当前匿名用户固定为 `0`。
- `response_id`：关联 `model_responses.id`。
- `content`：去除首尾空白后的评论正文，接口限制为 1–1000 个字符。
- `created_at`：评论发布时间。
- 不设置 `user_id + response_id` 唯一约束，同一用户可以对同一回答发布多条评论。
- 第一版支持发布、分页查询和硬删除，不支持编辑、评论点赞、审核或富文本。

旧版 `user_feedback.comment` 中已有的非空内容会在 Alembic 迁移时复制到 `user_comments`，随后删除旧字段。

demo-v1 旧匿名评论继续归属 `user_id = 0`。新评论写入当前登录用户 ID，只有评论作者可以删除。

## 2026-09-09 全局 Embedding 数据变化

迁移 `20260909_01`（前置 `20260902_02`）新增 `embedding_config`：id 固定 1、version 乐观锁版本、settings_json 服务端配置。迁移插入空对象，保留旧本地配置语义，不自动创建云端凭据。管理员保存锁定单例和知识库，在没有活动作业/RAG 任务时更新；索引语义变化递增各知识库 content_revision 并标记 reindex_required。

API Key 沿用现有 `store_api_key` 的 `plain:` 服务端存储机制，当前没有新增静态加密能力；不得把数据库字段名理解为已加密。接口响应、普通用户状态与 RAG 快照均不包含凭据。

兼容 API 的集合名为 `rag_chunks_api_` 加配置语义摘要，旧集合仍为 rag_chunks_v1。删除/重建清理该文档在历史受管集合中的向量，不删除集合或其他用户向量。RAG 历史 embedding_revision 保存集合标识。knowledge_chunks.token_count 保留既有列名：旧 TEI 模式为 Token 数，新 API 模式为字符数，只用于分块，不能用于推算账单；历史切分单位由回答配置标识区分。

业务升级前备份与恢复要求见合并计划顶部，隔离测试成功不自动授权业务数据操作。
