# 数据库设计

## V3 阶段 2 增量迁移

新增迁移 `20260902_01`，唯一父版本为 `20260705_03`。本阶段只在独立 `multichateval_rag_test` 验证，尚未应用到业务库。新外键使用 BIGINT，与既有 MySQL 主键对齐；不修改旧初始化 SQL 或历史迁移。

| 表/字段 | 作用及约束 |
| --- | --- |
| `evaluation_tasks.task_type` | 非空 VARCHAR(16)，默认 `chat`；保留旧问题、回答、归属、可见性和原始得分。阶段 2 不接受 `rag` 创建请求 |
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

真实服务测试采用独立 Compose 项目 `evalspark-rag-lifecycle-test` 和 `lifecycle` profile：MySQL/原文/向量/Redis 卷全部独立，仅复用固定模型缓存；不要对默认业务项目运行测试。完整模型链路仍在验收，见合并规格计划的阶段 3 记录。

文档先保存受控文件，再在知识库行锁内检查 100 份上限、插入元数据/作业并提交。提交失败只清理本次未引用文件；事务提交后通知 broker，通知失败保持 `queued`，由后续 Worker 恢复补投。重试/重建/删除提升对应版本，旧作业不能发布为新版本。删除先写墓碑，物理文件/块/向量清理由阶段 3 实现，不把请求成功等同物理删除成功。

业务升级前必须另行确认：实际父版本、备份位置与校验、DDL 元数据锁影响、恢复窗口和权限。新增 `task_type` 后 ORM 依赖该列，不能跳过迁移直接启动新后端。迁移不提供自动破坏性 downgrade；恢复使用经批准的备份流程。默认完整 Compose 会执行 `migrate`，未批准业务升级时只使用独立测试 Compose。

隔离测试：`docker compose --env-file .env.example -f docker-compose.rag-test.yml up -d mysql-test`，随后 `docker compose --env-file .env.example -f docker-compose.rag-test.yml run --rm runner`。测试服务无宿主机端口、无业务网络/卷、无业务 `.env` 挂载；运行前强制校验开关、主机 `mysql-test` 和数据库名。每轮使用唯一测试用户并保留测试库，不清空任何表或数据卷。

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
