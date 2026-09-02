# 系统架构说明

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

### V3 RAG 基础设施（阶段 1）

阶段 1 基础设施已完成，阶段 2 新增私有知识库管理 API；尚未开放 RAG 评测执行入口。`app/services/rag/clients.py` 通过 HTTP 调用内网 TEI，使用官方 Qdrant SDK 查询；`app/worker.py` 提供 Celery 配置，阶段 3 才注册文档作业。

知识库路由复用 Cookie/RBAC 登录态，库和文档授权始终按当前用户过滤，管理员没有私有内容豁免。`knowledge_base_service.py` 管理库/文档版本、状态、行锁配额和作业事务；`rag/documents.py` 负责鉴权后的 multipart 限流、轻量格式检查和随机键文件存储。控制器不返回物理存储路径，下载仅为私有附件。

文件先保存，再在库行锁内提交元数据与 `rag_jobs`，提交后通知队列；失败时只清理确认无引用的本次文件，提交结果不明时优先保留文件。事务开始前结束鉴权快照，防止 REPEATABLE READ 读到旧作业状态。阶段 2 任务尚未注册，诚实返回排队待投递；重试/重建/删除提高目标版本，清理与恢复将在阶段 3 落地。新五表及 `task_type=chat` 默认值通过增量 Alembic 提供，不回算旧评分。独立测试 Compose 已用于验证 SQL，业务库尚未升级。

- TEI CPU 加载固定 revision 的 Qwen3-Embedding-0.6B，输出 1024 维向量；查询带检索指令，文档不带指令。
- TEI 独占模型缓存写权限。后端/Worker 使用同卷的只读 `tokenizer.json`，只按固定 revision 本地加载，不联网回退、不加载模型权重。
- 客户端预检完整输入，按条数和 Token 总量分批，默认 16 条/2048 Token/60 秒；与 TEI 共用批量 Token 配置，所有 `/embed` 请求显式 `truncate=false`，不静默截断，拒绝错误维度、非有限数值或零向量。
- Qdrant 查询固定集合 `rag_chunks_v1`，必须同时限定用户、知识库、文档与其版本配对；返回元数据再次校验归属，防止读取错误版本原文。集合创建和写入后续实现。
- Redis 使用 AOF 和 `noeviction`；Worker 单并发、预取 1、JSON 消息、延迟确认、硬时限 3 小时与 4 小时可见性超时。持久作业、幂等、租约与恢复将在索引阶段实现，不能仅靠队列配置保证只执行一次。
- 新增服务均只走 Compose 网络，普通后端健康检查不探测 TEI/Qdrant/Redis。Worker 等待 TEI/Redis 健康及 Qdrant 启动；Qdrant 连接错误由客户端处理。

文件、向量与消息分别使用 `rag_documents`、`qdrant_data`、`rag_redis_data` 命名卷；原 MySQL 和前端卷保持不变。完整设计和阶段证据见 `v3-rag-spec-plan.md`。

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
