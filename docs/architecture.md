# 系统架构说明

2026-09-22 失败分支恢复接入共用会话路由与双工作台。作者只能恢复最新已结束轮次的失败模型；事务锁内创建新响应和幂等操作流水，其他分支回答不变。重试复用原问题、共享要求与该模型独立历史，只重新生成并评价目标分支；skip 直接保存失败占位，不解析供应商凭据或检索。任务详情投影最新尝试，旧尝试和费用留存。生成代次随重试递增，普通心跳/上下文/收尾与 RAG 固定响应集合共同阻断旧执行写入新尝试。已有截止报告禁止修改其原轮结果。新增代码仅静态审查，未进行运行验收。

2026-09-20 最新代码增量（未运行测试）：普通/RAG 单轮长评分按检查项生成固定子包；父快照保存全部原文，完整必要材料放不下的项保持 unknown。表达包独立承载一次完整 RAG 资料评审，资料放不下时对话质量仍可部分评价，资料与引用保持未知。三组正式复评复用相同子包，组内按检查项聚合，RAG 只汇总各组真正携带资料的段。歧义要求在当前分支内依据用户指代和更早回答双重引文判断，不修改共享要求。

报告版本3通过 `report_relations.py` 添加完整非相邻窗口对，最多32次额外准备；新报告窗口为成对读取预留预算，单条长消息不裁切。精确锚点相同的机会统一合并描述和辅助依据，费用原流水仍完整保留。准备重建校验使用同一规范结果，正式机会仍读取完整连续区间；任意多窗口联合证明和不同引文的语义去重不能由两窗口提取替代，未知保护仍保留。

问题状态协调器 `report_resolutions.py` 已接入 execute_assessment：新报告 resolutionVersion=1，三组历史评分保存后收集失败项，逐问题重建材料并执行三组状态判断；分歧、无锚点、超预算列 unknown，不发起该问题调用。额度不足保留已完成组，流程收尾后固定独立汇总，再由 finish 保存原历史评分与状态。React reportResolutions.ts 格式化状态和有效组引用，ConversationReportPanel 展示独立区域；旧报告缺失字段明确提示未记录。页面交互尚待浏览器验收。

问题后续状态模块 `issue_resolution.py` 提供材料构造、完整提示词预算、严格引用解析与三组一致性聚合。`resolution_store.py` 将单次调用接入实际流水：锁报告后确认全部三组保存、有效组一致失败，重建固定材料并检查预算/额度，事务外调用模型，先保存实际费用再保存结论。重复调用被唯一键拒绝，结果重新按固定快照校验；中断恢复把 pending 状态流水收尾为 unknown，禁止迟到结论覆盖。协调器和 UI 已接入，仍待浏览器联合验收；引用/角色校验仅验证依据结构，语义是否真正修正仍由评审判断。

跨窗口边界构造器 `report_bridges.py` 提供固定顺序的有界完整原文包与未覆盖边界编号，提取器通过 `opportunity_messages` 共用真实提示词预算。版本2已接入准备流水：先原窗口，再可容纳边界；`scoped_discoveries` 按左右来源过滤单窗重复，原始提取结果仍完整入库。固定阶段按版本重建来源和正式材料，保留全局未知约束，无法装入的边界加入限制说明。

上下文状态展示由 `context_status.py` 从持久化快照投影最小元数据，Catalog 在授权后按轮次分页查询并为每个模型选择最新尝试。React 恢复分页状态，普通 `context_ready` 合并到当前轮模型；RAG rewrite/answer 分别发送实时事件，并可从持久化快照恢复。RAG 协调器在阶段请求构建后读取当前尝试的快照，关闭读事务再投递队列，缓存请求同样经过此路径。`contextStatus.ts` 统一格式化未知、零值及历史读取边界，TSX 只渲染提示。

## 2026-09-20 会话报告链路（部分实现）

工作台预算交互：contextBudget.ts 根据已选候选和评审计算最小剩余容量，评审或首候选同时承担默认摘要角色，默认推荐不超过8192。Hook 管理预算输入、模型切换后的推荐重置和提交前校验，TSX 仅渲染输入控件、未知提示及冻结预算。后端可用模型查询只暴露容量和最大输出，创建时仍复核当前配置。

模型容量从管理员配置进入 RuntimeModelConfig 和 RagModelSnapshot。ConversationCatalog.create 在任何会话写入前校验所有参与角色的输入预算加冻结最大输出，不超过已知总容量；未知容量维持原预算路径，不根据模型名称猜测。消息输入仍使用包含协议开销的保守估计控制压缩。容量配置变更不追溯改写已有快照。

报告展示与用量：聚合器根据固定检查项识别覆盖审核，单列 coverageChecked/coverageUnknown，避免审核数量污染具体机会统计。React 两种工作台共用报告面板，作者列表读取成功后调用既有今日用量回调；回调引用变化不重启报告轮询，取消后停止后续刷新。用量刷新异常独立提示，报告结果保留，真实浏览器效果仍待验收。

语义准备链路已补充真实队列验收：直接将固定作业投递到本次独立 Redis 队列，生产 Worker 完成一次提取、三组双段复评、逐次计费与重复消息拦截。HTTP 假模型仅验证结构和调用协议，不证明模型评审能力；默认 HTTP 发布路由和硬崩溃重启仍未覆盖。

新提交报告在 report 元数据标记 semanticPreparation=3；execute_assessment 认领后调用 semantic_report.prepare_report，按原始窗口、边界及可容纳非相邻窗口对提取、落库、装配覆盖审核与具体机会，原子固定后再执行三组正式评审。版本1仅提取原窗口、版本2增加相邻边界，无标记旧报告保持原执行路径。coverage_only 检查只能 not_applicable 或 unknown，避免把覆盖审核当成额外成功机会。原有要求、生成缺失与跨窗口未知约束保留。提取失败结束本次作业并保留实际费用，不自动重试。

准备到正式评分的边界：PreparationStore.freeze 从已保存提取结果重建规范机会段，与提交计划逐项核对后在作业锁内一次替换 packet/batches。来源、范围及非窗口检查项保持一致；originalReportInput 保存原计划。preparationFrozen 写入后准备服务拒绝继续登记和修改，begin_run 在存在准备流水时要求该标记。此边界已隔离验证，新报告执行器已接入自动准备与计划装配。

`preparation.py` 提供准备阶段持久化与 execute_preparation：以报告作业锁校验运行状态、作者和未开始正式评审；源材料与原始 packet 逐项一致后唯一登记。供应商调用在事务外执行，回调先写用量、后保存解析结果。正式 begin_run 拒绝未保存结果的准备流水；interrupt 收尾 pending 准备记录。上述服务已接入新报告执行器并在正式评分前原子固定分段。

`opportunity_packets.py` 将已校验机会转换为独立 JudgePacket，装入最早触发/辅助依据至目标轮次的全部原文。超预算只形成 unknown 检查，原始引文不能替代完整区间。JudgeCheck.required_source_ids 约束 applicable 判定必须逐字引用触发和目标两侧；必要来源必须唯一且在允许引用范围内，旧检查项不增加额外提示词字段。该装配能力已接入持久化准备流程。

`multiturn/opportunities.py` 提供独立机会提取边界：以固定 JudgeSource 快照为输入，一次模型调用提取触发、目标和辅助引文，服务端校验原文、角色、时间关系并生成稳定机会 ID。提取不产生评分，complete 不作为覆盖证明；输入超预算不登记调用，输出无效仍回报用量，取消不重试。目前已接入新报告准备作业，结果和费用分别落库，旧报告快照保持原执行路径。

真实执行验证：已用临时 Celery Worker、隔离 Redis 专用队列和 model-test HTTP 服务验证正式评审三次请求、72 Token/0.000084 CNY 的固定测试入账、重复消息不额外请求，以及 Worker bootstep 自动收尾失活轮次。未 mock Worker 执行或直接调用恢复函数。专用队列避免消费历史 rag 消息；默认 HTTP 发布路由和 Worker 硬崩溃重启仍需独立演练。

生成存活与恢复：普通/RAG 新轮次启动 60 秒心跳，以作者、当前轮次和 generating 状态作更新条件，退出取消并等待。Worker 恢复扫描最多选取 20 个超过四小时未更新的多轮会话，锁内复核后原子结算本轮回答与费用，标记 interrupted 并释放会话；不调用供应商。普通调用占位与迟到保存校验当前轮次，RAG 写入校验任务终态；活跃长 RAG 使用心跳解除旧总时长限制。旧 RAG 扫描排除多轮，新表不存在时多轮扫描直接跳过。隔离验证已接通，真实 Worker 崩溃/恢复演练仍待执行。

报告按要求的 source_turn/scope/retired_at 计算有效原文集合；范围能独立装入预算时单独形成要求评审段，并按完整评审组三次执行。短报告即使共用一段，也用检查项 allowed_source_ids 拒绝越出要求范围的引文。超过预算和歧义要求保留未知；这些局部要求判断不解除全局跨窗口目标、记忆与一致性的未知检查项。

Judge 只提交 source_id/quote；来源轮次由 `judge.py` 查固定 packet 生成结构化 evidence_refs。检查项入库后供单轮详情与会话报告共用，报告逐组保留失败和未知项，不把未知当失败。React 展示检查项编号、原因及“依据来自第几轮”的原文，旧字段仍可显示但不解析为可定位轮次。

`reports.py` 按作者、固定模型和截止轮次装配原始问答及历史有效要求；`report_plan.py` 按实际 Judge 提示词预算形成完整消息窗口，`assessment_batches.py` 验证来源与检查项守恒。异步评分按三组分段执行，先合并组内检查项再判断组间稳定性。单条原文超预算时收费前拒绝，多窗口全局关系缺失时显式未知；完整跨段联合评价尚未完成。

React 报告面板复用普通/RAG 会话，按会话 ID 隔离生命周期，服务端分页为唯一总数依据，待处理列表每三秒刷新、最多三十次，之后允许手动刷新。读请求卸载取消，提交旧结果不得影响新会话。报告详情保留评审组号和分段号；面板另展示生成成功率、评分/原文覆盖率、逐组五维机会和 RAG 证据趋势快照，零分与未知保持区分。当前测试覆盖协议、范围与展示转换，浏览器生命周期验收仍需执行。

## 2026-09-18 多轮架构增量（开发中）

历史引用内部链路由 rag_references 解析和合并、rag_reference_store 校验作者/分支/实际引用及知识版本，RagContextBuilder 将映射纳入两阶段实际输入与硬预算。rag_assessment 从本轮固定输入重建全部旧来源映射并核对资料身份，避免 Judge 将旧标签误当本轮同名资料。已有生成/评分与 RAG HTTP 隔离验证。共用 HTTP 先按作者读取持久化 mode，关闭分发读事务后调用对应生成器。

普通/RAG 前端通过 ConversationPanel/useConversationWorkspace 接入会话 API，workspaceData 按页顺序读取任务详情与新评分，并提供可取消的轮询间隔。页面使用会话生命周期标识和 AbortController 隔离切换、分页与详情请求；评分与生成分别管理。ModelResponseCard 的可选 assessmentContent 替换旧评分栏及详情，原单轮使用默认渲染。assessment.ts 将公开结果映射为独立分项/覆盖率/关键状态，并提取逐次原文依据。双面板已有代码与 Docker 检查；RAG 创建携带知识库 ID，续聊临时卡片使用会话固定模型，阶段/引用/增量复用现有卡片归并。浏览器、历史入口与报告仍待联合验收。

2026-09-20 更新：内部 RAG 新评分已接通，rag_assessment 从对应回答的固定资料重建快照，rag_judge 固定回答片段、实际引用并校验 Judge 引文；judge.evaluate_packet 在同次调用返回普通检查项和证据判定，AssessmentStore 保存两组结果与逐次费用，正式复评检查片段适用性及维度分差。RAG 生成结束通过共用 turn_scoring 提交，评分不阻塞下一轮生成；HTTP 已接通，真实队列与前端联合验收未完成。

`multiturn/rag_generation.py` 已提供内部 RAG 多轮协调，复用预留 task、共享要求和独立分支检索/回答；生成结算不等待评分，断连先停止后台生产者再关闭阶段流水与会话锁。`rag_context.py` 为改写/回答分别构造有界消息，回答预算包括固定证据，在同一上下文行的 rag_requests 下保存不可变阶段快照；rewrite_summary/answer_summary 分开计费并支持缓存。共用 HTTP 已按会话模式分发至该协调器，完成隔离集成验证。

RagEvaluationStore.create 增加内部 reserved_turn_id 分支，复用 ConversationStore.reserve_turn 的唯一任务；在会话锁内验证作者、轮次、模型快照及索引版本，已存在回答时拒绝重复初始化。fix_snapshots 重新检查 Embedding 语义版本。finalize_multiturn_response 只结算检索/生成费用并标记可续聊回答，新的 Judge 由 conversation_assessments 独立运行；旧单轮流程保持原接口。内部 RAG 生成协调器已接通，新证据评分已接通，HTTP 已接通隔离验证。

RagEvaluationRunner 接受可选异步 rewrite_request_builder/answer_request_builder，参数为固定模型、任务上下文和该分支 PreparedRagResponse，返回 ModelRequest，供多轮协调器装配独立历史。请求构建成功后才登记付费阶段；构建失败不调用供应商、不写未知付费用量，阶段登记失败直接退出。未提供回调时使用原单轮请求。回调仅为内部入口，不接受客户端提供任意 messages。

`multiturn/reassessment.py` 为正式复评提交入口：仅作者引用已结束暂定作业，复用同一 JudgePacket 和预算，以 formal:{sourceId} 保证一个来源只有一个正式作业。先提交数据库再投递队列；投递失败保留 queued，恢复扫描负责重投，数据库原子认领防止重复收费。前端已接入提交封装与正式复评按钮，浏览器验收尚待完成。

`multiturn/assessment_reader.py` 提供评分分页与详情，只读会话当前可见性授权后的作业与逐次结果；显式投影排除 input_json/operation_key，不触发评分或用量。前端 conversationTypes/conversations 提供会话、流式生成和评分查询封装，工作台已连接查询与结果展示，浏览器验收尚待完成。

`multiturn/requirements.py` 在候选回答生成前以当前用户原文和已有要求为输入，校验逐字来源及替代关系，歧义要求不能撤销已有约束；`requirement_store.py` 追加变更版本，按轮次读取当时状态。已通过 turn_scoring 接入普通生成前要求提取及生成后检查项装配，RAG 内部要求提取与新评分装配均已接通。

普通生成内部协调器 `multiturn/generation.py` 已并发运行模型分支并持久化完成回答；使用 context 快照和 summary_usage，生成用量独立保存为 generate:{responseId} 并接入日额度。每分支队列传递 delta/answer_completed，完整回答落库后才对后续历史可见。取消流会取消分支并收尾轮次。普通 HTTP NDJSON 续聊入口已注册并通过两轮隔离集成；普通及 RAG 内部新评分作业自动提交已接通。

`multiturn/summary.py` 提供 ModelSummarizer，可作为 context 的摘要回调：按预算分批读取原文，滚动合并结构化摘要，验证关键事实原文引用，保持每次模型请求和最终记忆有界。调用前/后回调已通过 SummaryUsageRecorder 连接权限、额度和阶段流水；prepare_context_with_summary 将其与输入快照装配连接。普通内部生成协调与 HTTP 已接通，RAG 内部上下文已接通，HTTP 与双工作台代码已接入，浏览器验收尚待完成。

`multiturn/history.py` 按作者、会话、固定模型及截止轮次装配完整成功问答，使用 turn/response 原始 ID 追溯。原文加载与模型上下文清洗分开；准备过程在无数据库事务期间执行可选摘要回调，之后重新校验生成状态再持久化 context。准备参数哈希用于复用已经保存的同次输入，不能覆盖历史快照。此入口由单一生成协调者调用，普通内部流式协调已实现，普通 HTTP 已接通，RAG 内部协调器已复用历史读取，HTTP 已接通隔离验证。

`multiturn/assessments.py` 已实现内部持久化执行器：会话锁保证创建幂等，作业锁保证唯一执行者，外部模型调用使用独立无锁阶段。调用前登记 pending 评审/费用，返回后保存结果；聚合只读取已持久化评审。取消将待定调用标记 interrupted/未知用量，重复投递不重跑。已接入已知用量的日累计与逐次额度检查；Celery 作业入口已连接；已接入有界漏投恢复及过期运行清理；普通 HTTP 与 RAG 内部生成会提交评分作业，旧评分链路已通过隔离 Redis 与真实 Worker 专用队列验收；新语义准备链路也已通过独立队列验收，默认 HTTP 发布路由仍待验证。

`services/multiturn` 当前提供 context（完整轮次与异步压缩预算）、scoring（普通/会话/RAG 的确定性公式）、store（短事务与权限）和 catalog（冻结非秘密配置、分页、知识库版本快照）。ModelRequest 增加显式角色 messages，优先于旧 prompt，供应商 extra_body 不能覆盖结构化系统边界；旧调用继续兼容。

会话元数据、普通生成 HTTP、内部普通/RAG 压缩、RAG 每轮检索和持久化评分已接通。历史引用与 RAG HTTP 已接通；会话报告、真实队列与前端联合验收尚待完成，执行蓝图及状态以 [multiturn-spec-plan.md](multiturn-spec-plan.md) 为准。原单轮普通/RAG 管线继续存在；不得把后台 asyncio 评分误称为已实现持久化多轮评分作业。

新增 judge 调用边界接收固定检查项和截至某轮的原文来源，经 ModelClient 单次调用后校验返回 ID、严格 JSON 类型、原文片段，再交给 scoring 计算。模型无法改写权重或关键属性。输入预算超限不发起调用，输出解析失败仍携带 ModelReply 用量，调用异常不伪造零用量；取消继续向调度层传播。权限、快照加载、作业持久化与阶段计费由会话执行服务负责，此模块未对外注册接口。

轮次和请求键双唯一，事务按会话优先锁定；统一可见性更新全部关联任务。公开读者仅可读，续聊必须是作者；旧单轮入口不能向受管理会话追加任务。

## 2026-09-09 公开 RAG 与独立历史

普通与 RAG 任务复用“公开或本人”的访问条件；完整回答、评分与证据快照按任务可见性读取。知识库、原始文档、检索操作继续校验资源所有权。作者通过 PATCH 接口修改可见性，事务锁定本人任务后提交；评分收尾、取消恢复和用量入账不依赖任务为私有。

React 的 `/history` 与 `/rag/history` 分别固定 chat/rag 查询，复用历史组件但使用独立组件 key，切换路由重置分页和详情。保存可见性期间禁止重复提交，响应按任务 ID 合并，避免覆盖新选中的任务。

> 当前状态（2026-09-09）：全局 Embedding API 与本地兼容接入已实现，业务库已备份并迁移，前后端已启动。本文阶段 1—7 的早期冻结/未执行描述为历史记录，最新验证及未覆盖范围以 docs/v3-rag-spec-plan.md 顶部为准。

V3 验收环境与业务环境分离：`docker-compose.rag-test.yml` 的 `unit-runner`/`frontend-test` 断网运行回归与构建，`acceptance-runner` 使用测试 MySQL、TEI、Qdrant、Redis、Worker 与 `model-test`。只有候选/Judge 由确定性 HTTP 服务替代，Cookie 鉴权、业务路由、持久化和检索组件保持真实；API 传输为进程内 ASGI，浏览器/代理/真实模型效果另行验收。跨平台 `verify-rag` 入口、隔离范围和延期清单见 `v3-rag-spec-plan.md` 第 11 节；当前未部署或运行完整验收。

MultiChatEval 采用前后端分离架构。当前主前端为 `frontend/` React 19 + TypeScript + Vite 应用，已完成对原 Vue 前端的功能替代；`vue-frontend/` 仅作为历史版本保留。v2 已冻结，后续新功能和样式维护默认在 React 前端推进，并复用同一套 FastAPI API、MySQL 数据结构和评分逻辑。

```text
React 前端（当前主前端）
  ↓
FastAPI API
  ├── Auth / RBAC
  ├── Token Quota Service
  ├── Feedback Stats Service
  ↓
Evaluation Service
  ├── Model Adapter Layer
  ├── Objective Metrics Evaluator
  ├── Rule-based Evaluator
  ├── LLM Judge Evaluator
  └── User Feedback Collector
  ↓
MySQL
```

## Docker 开发部署

默认开发环境由根目录 `docker-compose.yml` 统一编排，宿主机只需要 Docker Desktop 与 Docker Compose：

```text
浏览器 http://127.0.0.1:5174
  ↓ 同源 /api
frontend:5174（React + Vite 热更新）
  ↓ http://backend:8000
backend:8000（FastAPI + Uvicorn 热更新）
  ↓ mysql+aiomysql://...@mysql:3306/multichateval
mysql:3306（仅 Compose 内部网络）
```

启动依赖为 `mysql healthy → migrate completed → backend healthy → frontend`。首次创建数据库卷时，初始化 SQL 创建截至 `20260612_01` 的结构并写入对应 Alembic 基线；`migrate` 每次启动再升级到最新版本。只有迁移成功后后端才会运行；前端通过 `VITE_BACKEND_TARGET=http://backend:8000` 把 `/api` 请求代理到后端。

`backend/` 和 `frontend/` 以 bind mount 挂入容器，分别由 Uvicorn 和 Vite 监听源码变化。Python 依赖安装在后端镜像中，前端 `node_modules` 使用独立命名卷，因此宿主机 `.venv` 与 `node_modules` 不进入容器运行环境。MySQL 数据保存在 `mysql_data` 命名卷中，普通停止和镜像重建不会删除数据；Compose 不把 MySQL `3306` 映射到宿主机。

## 核心流程

### V3 RAG 基础设施与异步索引（阶段 1—3）

阶段 1—3 基础设施、私有知识库管理和文档作业代码已交付；阶段 4—5 已接通逐模型链路及 RAG 评测 API。受开发设备内存限制，阶段 5 新增测试、真实模型完整联调和部署延期。`app/services/rag/clients.py` 通过 HTTP 调用内网 TEI，使用官方 Qdrant SDK 存取与查询；`app/worker.py` 注册 Celery 作业和启动/每 60 秒的数据库恢复入口。

知识库路由复用 Cookie/RBAC 登录态，库和文档授权始终按当前用户过滤，管理员没有私有内容豁免。`knowledge_base_service.py` 管理库/文档版本、状态、行锁配额和作业事务；`rag/documents.py` 负责鉴权后的 multipart 限流、轻量格式检查和随机键文件存储。控制器不返回物理存储路径，下载仅为私有附件。

文件先保存，再在库行锁内提交元数据与 `rag_jobs`，提交后通知队列；失败时只清理确认无引用的本次文件，提交结果不明时优先保留文件。事务开始前结束鉴权快照，防止 REPEATABLE READ 读到旧作业状态。Worker 的 `rag/jobs.py` 以库锁和短事务领取租约，`attempt` 隔离旧执行代次；不持事务锁等待解析、模型或 Qdrant。过期租约和不确定写入均留出在途请求冷却窗口，自动重试最多 3 次，人工重试新建目标版本。

`rag/parsing.py` 为受时间/地址空间限制的子进程入口；pypdf/python-docx 提取正文，`documents.py` 映射行/页/逻辑块并使用开源切分器和 Qwen Tokenizer。`rag/indexing.py` 先保存目标版本正文、再分批 Embedding/upsert，核对数量并复查版本后发布；同库全部活动作业结束后才恢复可用。删除先屏蔽新访问，串行清理全部版本向量、原文件、MySQL 块，再标记 deleted。索引旧版收尾不会碰当前版或其他库。

Worker 不等待 Embedding 健康，模型离线也能恢复和清理。Celery 的恢复线程只传递作业 UUID，MySQL 为权威状态；每次异步调用创建并释放自己的连接池，避免跨事件循环或 fork 共用连接。新五表、`task_type=chat` 默认值及块正文 MEDIUMTEXT 通过增量 Alembic 提供，不回算旧评分。独立测试 Compose 已用于验证 SQL，业务库尚未升级。

- TEI CPU 加载固定 revision 的 Qwen3-Embedding-0.6B，输出 1024 维向量；查询带检索指令，文档不带指令。
- TEI 独占模型缓存写权限。后端/Worker 使用同卷的只读 `tokenizer.json`，只按固定 revision 本地加载，不联网回退、不加载模型权重。
- 客户端预检完整输入，按条数和 Token 总量分批，默认 16 条/2048 Token/60 秒；与 TEI 共用批量 Token 配置，所有 `/embed` 请求显式 `truncate=false`，不静默截断，拒绝错误维度、非有限数值或零向量。
- Qdrant 查询固定集合 `rag_chunks_v1`，必须同时限定用户、知识库、文档与其版本配对；返回元数据再次校验归属，防止读取错误版本原文。集合创建、payload 索引、幂等写入和范围清理由阶段 3 实现。
- Redis 使用 AOF 和 `noeviction`；Worker 单并发、预取 1、JSON 消息、延迟确认、硬时限 3 小时与 4 小时可见性超时。持久作业、幂等、租约与恢复已实现，不能仅靠队列配置保证只执行一次。
- 新增服务均只走 Compose 网络，普通后端健康检查不探测 TEI/Qdrant/Redis。Worker 等待 Redis 健康及 Qdrant 启动，不等待 TEI；Qdrant 连接错误由客户端处理。

文件、向量与消息分别使用 `rag_documents`、`qdrant_data`、`rag_redis_data` 命名卷；原 MySQL 和前端卷保持不变。完整设计和阶段证据见 `v3-rag-spec-plan.md`。

### V3 内部逐模型链路（阶段 4）

`rag/evaluation_store.py` 在私有库行锁内创建任务、候选回答和资料版本快照。`rag/evaluation.py` 并发执行每个候选的一次查询改写、私有 Embedding、带归属过滤的 Top-5 检索，并从 MySQL 校验和读取原文。各操作使用独立短会话，不跨模型/网络调用持 SQL 锁。

全部候选检索结束后再次锁库核对内容版本及已发布文档清单，再同时固定各自证据。版本变化使本次尚未固定证据失败，不能自动使用新库。流式生成只用已固定的当前回答证据；删除当前文件或块不影响该快照。固定系统消息与 JSON 数据消息分离，旧 chat 仍保留原请求形状。

调用前持久化阶段占位，返回后保存类型化用量，失败/取消保留已知部分，未返回明确为未知。`rag/usage.py` 复用费用算法并分币种汇总；终态事务一次写 Token 日志。中断收尾不重放外部调用。

### V3 联合评审与任务编排（阶段 5）

`rag/service.py` 在发送流式头前完成预检/创建，用有界队列汇合各候选进度；检索屏障之后候选独立流式回答，完成即独立评分。`rag/judge.py` 对每候选串行三轮联合评审，所有候选的评分互不共享数据库会话。结果经严格四维/断言引用校验后按有效轮均值与极差判定是否可评分；固定 system prompt 与 JSON 数据分离，模型来源文本不作为指令执行。`rag/scoring.py` 按 rag-v1 序列化，反馈复用高精度基础分。

`EvaluationService` 仅按 taskType 分派，chat 保持原编排；任务详情加载独立证据，不重新检索。私有查询条件在 SQL 层执行，管理员统计读取无正文汇总，互动详情排除他人的 RAG。正常关闭逐层 aclose、取消并等待工作协程后收尾；全链路时限 55 分钟，Worker 关闭超过 60 分钟的 pending RAG。新代码已编写，当前 Docker 关闭，运行验收待补。

### V3 React 接点（阶段 6）

`api/knowledgeBases.ts` 复用 Cookie、错误解析和可取消 JSON 请求，封装私有分页/原文件/作业 API。`useKnowledgeBases.ts` 管理表单、异步动作与 5 秒轮询，退出/切换清理 AbortController 与定时器。`useRagEvaluation.ts` 复用普通评测 NDJSON 批处理，按运行版本号阻止旧任务覆盖新选择；`.tsx` 页面只组装 UI，不新增依赖或并行维护 Vue。

`rag.ts` 的纯转换显式接收 rag_stage/rag_retrieval；`ModelResponseCard` 复用 Markdown、反馈和详情弹窗，挂载独立的证据/评分面板。每个面板只消费当前回答快照，正文以转义文本展示；不构造跨回答 S1 锚点，也不重新查询当前知识库。`useHistory.ts` 承接原分页/详情/反馈行为并增加类型筛选和请求取消。RAG 运行中记录转换为阶段卡片而非误报失败。前端测试、构建和视觉验收尚未执行。

### 当前普通评测流程

1. 用户注册或登录，后端通过 HttpOnly Cookie JWT 恢复当前用户。
2. 用户输入问题，选择多个模型和公开或私有模式。
3. 后端检查普通用户今日剩余额度，再创建评测任务。
4. 后端从数据库读取已启用的模型配置，并发请求模型。
5. 系统持久化模型回答、四类 Token 与费用、参数快照、总 Token 流水、规则评分和可选 LLM Judge 结果。
6. 前端展示评测结果，登录用户提交归属于自己的反馈和评论。

## 认证与权限层

- 开放注册用户默认为普通用户，注册时要求二次确认密码，并校验密码至少 8 位且包含数字、小写字母和大写字母。
- 密码使用 Argon2 哈希，登录态使用写入 HttpOnly Cookie 的短期 JWT。
- `get_current_user` 负责认证，`require_admin` 负责管理员授权。
- 普通用户通过精简接口读取可评测模型，完整模型配置接口仅管理员可访问。
- 管理员用户管理接口支持用户名搜索、角色/状态筛选、分页查询和封号/解封；被禁用用户不能登录。
- 反馈统计使用个人与管理员双端点：普通用户只能读取本人任务表现与本人互动汇总，管理员端点通过 `require_admin` 返回全局聚合和互动明细。
- 公开任务对所有登录用户可见；私有任务仅创建者可见。
- 对无权访问的私有任务或回答返回 404，避免资源枚举。

## 第一版边界

当前主流程已支持逐 token 流式请求和规则评分。前端通过 NDJSON 增量事件实时展示各模型回答；单个模型回答完成后显示“评分中……”，评分和持久化完成后再展示最终分数、Token、成本和反馈入口。

## 前端展示层

当前前端展示层采用 React Router 多路由结构，统一业务布局位于 `frontend/src/layout/AppLayout.tsx`。登录和注册页面使用独立布局；业务导航根据当前用户角色显示。React 替代 Vue 的阶段文档保留在 `docs/react-rewrite/`，后续功能状态以本文档和 `docs/system-features-status.md` 为准。

当前 React 展示层包含：

- `/` 对应 `frontend/src/pages/EvaluationPage.tsx`，用于完成问题输入、模型选择、任务提交和结果对比。
- `/login` 和 `/register` 对应 `frontend/src/pages/AuthPage.tsx`，用于登录和开放注册。
- `/models` 对应 `frontend/src/pages/ModelConfigsPage.tsx`，仅管理员用于通过供应商预设或空白模板维护 OpenAI-compatible 模型。
- `/users` 对应 `frontend/src/pages/AdminUsersPage.tsx`，用于搜索筛选用户、分页查看今日 Token 用量、封号/解封并调整普通用户每日额度。
- `/scoring-rules` 对应 `frontend/src/pages/ScoringRulesPage.tsx`，仅管理员用于维护规则词库和 Judge Prompt。
- `/history` 对应 `frontend/src/pages/HistoryPage.tsx`，用于分页查看最近评测任务，并可点击任务加载完整回答和评分详情。
- `/feedback` 对应 `frontend/src/pages/FeedbackStatsPage.tsx`，所有登录用户均可进入；页面根据角色展示个人统计或管理员全局统计。
- Ant Design 在应用入口启用中文语言配置，历史任务和反馈统计分页使用中文交互。
- 模型选择项从后端模型配置接口动态加载，直接显示具体模型名。
- 评测结果和历史详情复用 `frontend/src/components/ModelResponseCard.tsx` 展示模型回答、评分和反馈操作。
- 评测页四个模型固定为两行两列，五至九个模型采用平衡分行并保证每行铺满；容器查询会根据结果区实际宽度降为两列或单列。
- 摘要卡或列表行展示模型名称、调用状态、最终分、耗时、输出 Token、总费用和回答预览；总费用悬停或聚焦时展示四项明细。
- GSAP 动画集中封装在 `frontend/src/animations/pageMotion.ts`，负责页面入场、等待卡替换、回答卡片和详情弹窗动效；组件卸载时清理动画上下文，并根据 `prefers-reduced-motion` 降低动画强度。
- 桌面端侧边栏固定在可视区域内；移动端使用深色抽屉导航，保持与桌面端一致的品牌底色和高对比文字。
- `ModelResponseCard` 负责卡片内 Markdown 流式渲染、底部感知自动滚动、完整回答弹窗中的 Markdown、指标、评分条、点赞/点踩、评分明细和公开评论展示。
- 模型调用期间展示等待卡片、耗时计数和占位动画；单个模型完成后立即替换为真实回答卡片。
- 评测表单提供全局“思考模式”开关，所有已选模型使用相同开关状态，不提供思考程度选择。
- `frontend/src/components/MarkdownRenderer.tsx` 负责将模型输出渲染为安全的 HTML，并支持 `$...$` / `$$...$$` 数学公式。
- `MarkdownRenderer` 会把 `<think>...</think>` 中的内容默认展开展示为“思考过程”，让思考过程和最终回答都能在流式响应中同步更新。

## 模型调用层

后端通过 `OpenAICompatibleClient` 统一调用兼容 `/chat/completions` 协议的模型服务。模型配置来自 `model_providers` 和 `model_configs`，评测任务使用 `model_configs.id` 选择模型。

系统不再自动创建内置供应商。管理员从 DeepSeek、MiniMax、GLM、Qwen、Xiaomi MiMo、OpenAI 预设或 OpenAI-compatible 空白模板创建配置。普通用户只读取管理员启用且已配置密钥的模型精简列表。API Key 当前以 `plain:` 版本化明文格式保存，所有业务逻辑通过密钥 helper 读取，未来可以升级为 `enc:v1:` 加密格式而不改变接口。

适配器归一化输入、输出、缓存命中和缓存创建四类 Token，并按模型保存的每百万 Token 单价计算费用。模型回答同时保存不含 API Key 的参数与价格快照，避免配置更新后历史费用失去依据。

`TokenQuotaService` 按北京时间自然日检查和累计普通用户总 Token。任务创建前只检查是否仍有余额，不预占最大输出；每个回答持久化时写入流水并原子更新每日汇总。管理员不受额度限制。

后端在每次评测请求中会先把系统内置提示词拼接到用户原始问题前，再发送给模型；任务记录、历史详情和接口响应中的 `prompt` 仍保留用户原始问题。随后后端统一追加思考模式参数。关闭思考模式时，所有模型请求都会发送 `thinking.type=disabled`；开启思考模式时，所有模型请求都会发送 `thinking.type=enabled`。这里发送的是嵌套 JSON 字段 `thinking.type`，不是 `thinkingmode`。第一版不传递思考程度参数。如果某个 OpenAI-compatible 供应商不支持 `thinking` 字段并返回错误，该失败只展示在对应模型卡片中，不影响其他模型继续返回。MiniMax 等供应商即使收到 `thinking.type=disabled`，也可能仍在返回内容中包含 `<think>` 或 reasoning 字段，前端会按当前渲染规则默认展开展示。

为了让前端尽早展示模型生成过程，后端提供 `POST /api/evaluation/tasks/stream`。该接口使用 NDJSON 返回多模型逐 token 事件：模型生成中持续返回 `model_delta`，单个模型回答结束后返回 `model_answer_completed` 让前端展示“评分中……”，评分和持久化完成后再返回最终 `model_response`。旧的 `POST /api/evaluation/tasks` 仍保留一次性返回完整任务结果。

两个创建接口都会写入真实任务、模型回答和评分结果。规则评分器会先从评分输入中移除完整的 `<think>...</think>` 区块；遇到未闭合的 `<think>` 时忽略该标签及其后续内容，因此只有最终回答参与规则评分。原始回答仍完整写入数据库并通过接口返回，前端可继续默认展开展示思考过程。规则评分器改为基于管理员词库的本地硬规则检查，不依赖外部语义模型或下载，并记录本次使用的词库版本。Judge 默认开启，服务会选择未参与本次测评的空闲模型并并发执行三轮 Prompt；至少 2 次成功且成功分差不超过 2.0 时，按成功评分平均值作为 Judge 分，并按规则分 30% 和 Judge 分 70% 合成基础分。模型失败、Judge 失败、Judge 不稳定和需要人工复核的回答会保留状态，最终分为空，并从反馈统计中排除。`GET /api/evaluation/tasks` 提供分页历史列表，`GET /api/evaluation/tasks/{taskId}` 从数据库返回完整任务详情、任务创建/完成时间，并携带每条回答的反馈状态。反馈接口会对 `user_feedback` 执行互斥状态式新增、切换或取消。

新反馈写入当前登录用户 ID，demo-v1 旧匿名反馈继续保留在 `user_id = 0`。同一用户对同一回答只能保留一个当前反馈，重复点击相同类型会取消，点击另一类型会在点赞和点踩之间切换。没有反馈时最终分等于基础分；存在反馈时，点赞比例映射为 0–10 的反馈分，并以 10% 权重计入 `final_score`。反馈接口返回更新后的反馈状态和评分，前端同步刷新卡片。

公开评论使用独立的 `user_comments` 表和分页接口，不嵌入任务详情响应，避免评论增长导致历史任务载荷持续膨胀。评论支持发布和按归属硬删除，不支持编辑；评论不参与评分。新评论归属当前登录用户，只有作者可以删除。

`FeedbackStatsService` 直接聚合已持久化的任务、回答、评分、点赞、点踩和评论，不建立预聚合表。个人统计按 `evaluation_tasks.user_id` 限定本人任务，同时单独按互动记录的 `user_id` 统计本人操作；管理员统计覆盖公开、私有和历史匿名数据。评分与调用使用回答创建时间，点赞、点踩和评论使用各自创建时间，7 天和 30 天边界按北京时间自然日计算。统计只纳入 `excluded_from_stats = false` 且 `final_score` 非空的评分结果，避免失败或需人工复核的回答拉低聚合质量。各数据源分别聚合后再按模型和日期合并，避免多表联接导致重复计数。

## 2026-09-09 Embedding 运行链路调整

全系统使用管理员维护的一份 Embedding 配置，管理入口 `/embedding-config`，与对话/Judge 模型配置分开。RAG 创建任务时锁定全局配置并将运行配置仅保存在服务端上下文；不修改共享单例客户端，也不序列化密钥。Worker 领取作业后解析同一全局配置；活动任务期间禁止管理员保存以避免中途切换。

兼容模式：文档解析 → 开源 semantic-text-splitter 按字符切分 → HTTP `/embeddings` → 按配置摘要隔离的 Qdrant 集合。查询仅按配置添加查询前缀；结果按 index 恢复顺序并检查向量数量、维度和有限非零数值。无需下载 Qwen 权重/分词器。旧 TEI 模式仍按固定 Qwen 分词，作为历史兼容路径。

默认 Compose 不启动内置 TEI；`local-embedding` profile 显式启用。后端和 Worker 都以 Base URL/API Key 连接服务，云端与独立本地部署共用协议。文档块会发送到管理员选定的服务，页面展示相应数据范围。

## 2026-09-09 评测与设置一致性

导航按普通评测、RAG 评测、Agent 评测（待开发）、系统设置分组；系统设置保持管理员权限。大模型和 Embedding 复用原有接口，合并在模型设置页签内，兼容旧路由。RAG、知识库与设置沿用现有标题、卡片及 Token 面板样式。

普通与 RAG 共用 useTodayTokenUsage：进入页面、任务终态、窗口聚焦及重新可见时读取统一用量接口，任务运行中每 5 秒刷新；取消旧请求以阻止乱序响应覆盖。RAG 的用量在逐回答评分终态入账，刷新仅读取账本，不增加重复写入。管理员也读取实际汇总值，额度仍不限。

RAG 默认思考开启，评审模型按配置 ID 和供应商/模型名隔离；后端在任务创建前重新验证，前端候选改变时重新选择可用评审模型。
