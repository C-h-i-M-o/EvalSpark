# AGENT.md

## 项目概览

MultiChatEval 是一个“面向多模型问答的对话质量评估系统”。项目目标不是绝对判断哪个 AI 回答最好，而是通过多模型并发回答、客观指标、规则评分、可选 LLM 评审和用户反馈，帮助用户结构化比较不同模型回答。

一句话定位：

> 多模型对话质量评估与对比平台。

## 当前版本状态

当前 **v2 版本开发已经结束并冻结**。开放注册、注册密码确认与复杂度校验、HttpOnly Cookie JWT、普通用户/管理员 RBAC、真实用户数据归属、公开/私有评测、管理员模型配置、用户搜索筛选分页、封号/解封、Token 额度、反馈统计等已实现能力继续保留；语义分析、模型推荐、运行监控等未完成的 v2 后续功能不再继续开发。

`main` 分支的 `demo-v1` 标签对应可发布基线。模型配置、多模型并发评测、逐 token 流式展示、规则评分、可选 LLM Judge、历史任务、点赞/点踩反馈计分和公开评论均已形成可运行闭环。v2 的 `/feedback` 已实现角色分流：普通用户查看个人统计，管理员查看全局统计和互动明细。React 前端已经完成对原 Vue 前端的功能替代，后续开发统一使用 `frontend/` React 技术栈；`vue-frontend/` 仅作为历史版本保留。

## 当前技术栈

- V3 RAG 正在实施：Compose 增加 TEI CPU（Qwen3-Embedding-0.6B）、Qdrant、Redis、Celery Worker；规格、阶段提交及验收统一见 `docs/v3-rag-spec-plan.md`。阶段 2 已有私有知识库/文档管理 API 和增量迁移 `20260902_01`，作业暂留 queued/dispatchPending；异步索引、RAG 评测、React 页面尚未开放。迁移只在独立测试库验证，业务升级仍须确认备份与恢复；普通新后端 ORM 依赖新增 task_type 列。Agent 与 AI 安全测试集另行设计。
- 后端：Python、FastAPI、SQLAlchemy 2.0、Alembic、Pydantic Settings、pytest
- 当前主前端：React 19、TypeScript、Vite、React Router、Tailwind CSS、Ant Design、Recharts、GSAP，独立目录 `frontend/` 并复用现有后端 API
- 历史前端：Vue 3、JavaScript、Vite、Pinia、Vue Router、Axios、Element Plus、Markdown-it、DOMPurify、GSAP，位于 `vue-frontend/`，后续不再作为主要开发目标
- 数据库：MySQL 8
- 开发环境：Docker Compose 统一运行 MySQL、Alembic 迁移、FastAPI 后端和 React/Vite 前端
- RAG 内部服务不映射宿主机端口；普通后端启动不等待 RAG。`rag_model_cache` 仅 TEI 可写，后端/Worker 只读；文档使用 `rag_documents`，向量与队列分别使用 `qdrant_data`、`rag_redis_data`。
- 包管理：前端优先使用 pnpm

注意：当前主前端使用 React + TypeScript。历史 Vue 前端使用 JavaScript，不作为后续新功能开发目标。

## 目录结构

```text
MultiChatEval/
  backend/        FastAPI 后端服务
  frontend/       React 19 + TypeScript + Vite 主前端应用，已完成原 Vue 功能替代
  vue-frontend/   原 Vue 3 + JavaScript 前端应用，仅作为历史版本保留
  docker/         MySQL 初始化脚本
  docs/           架构、接口、数据库和开源复用说明
  scripts/        本地启动与维护脚本
  docker-compose.yml
  README.md
  .env.example
```

## 已完成内容

### 后端

- FastAPI 应用入口：`backend/app/main.py`
- 配置管理：`backend/app/core/config.py`
- 数据库连接：`backend/app/db/session.py`
- SQLAlchemy 基础模型：
    - 用户
    - 会话
    - 模型供应商
    - 模型配置
    - 评测任务
    - 模型回答
    - 评分结果
    - 用户反馈
    - 用户评论
- API 路由：
    - `GET /api/health`
    - `POST /api/auth/register`
    - `POST /api/auth/login`
    - `POST /api/auth/logout`
    - `GET /api/auth/me`
    - `GET /api/models/available`
    - `GET /api/admin/model-configs`
    - `POST /api/admin/model-configs`
    - `PUT /api/admin/model-configs/{modelConfigId}`
    - `DELETE /api/admin/model-configs/{modelConfigId}`
    - `POST /api/admin/model-configs/test`
    - `POST /api/evaluation/tasks`
    - `POST /api/evaluation/tasks/stream`
    - `GET /api/evaluation/tasks`
    - `GET /api/evaluation/tasks/{taskId}`
    - `POST /api/evaluation/responses/{responseId}/feedback`
    - `GET /api/evaluation/responses/{responseId}/comments`
    - `POST /api/evaluation/responses/{responseId}/comments`
    - `DELETE /api/evaluation/comments/{commentId}`
    - `GET /api/feedback-stats/me`
    - `GET /api/admin/feedback-stats`
- 模型适配器接口：`backend/app/adapters/base.py`
- OpenAI-compatible 真实调用适配器：`backend/app/adapters/openai_compatible.py`
- 规则评分器：`backend/app/services/rule_evaluator.py`
- 规则评分相关性使用轻量本地多信号评分，不依赖外部语义模型或下载。
- 规则评分会排除完整或未闭合的 `<think>` 思考内容，仅评价最终回答；原始回答仍完整保存和返回。
- 当前评测服务已接入真实模型 API，并从数据库中的模型配置动态读取可调用模型。
- 评测请求发送给模型前，会在用户原始问题前拼接系统内置提示词；数据库、历史任务和接口响应中的 `prompt` 仍保留用户原始问题。
- 系统不自动创建内置模型配置；管理员从供应商预设或 OpenAI-compatible 空白模板创建配置并维护 API Key。
- 评测请求支持全局“思考模式”开关。关闭时所有模型统一发送 `thinking.type=disabled`；开启时统一发送 `thinking.type=enabled`；不传递思考程度参数。
- 思考模式字段以嵌套 JSON `thinking.type` 传输，不使用 `thinkingmode`。MiniMax 等 OpenAI-compatible 供应商即使收到 `thinking.type=disabled`，也可能仍返回 `<think>` 或 reasoning 内容。
- `POST /api/evaluation/tasks` 保留一次性返回完整结果。
- `POST /api/evaluation/tasks/stream` 支持多模型并发逐 token 流式返回；单个模型回答结束后先进入“评分中……”状态，评分和持久化完成后再返回最终模型结果。
- 评测任务、模型回答、规则评分、LLM Judge 结果、点赞/点踩和评论会真实写入 MySQL。
- `GET /api/evaluation/tasks` 支持分页查询历史任务。
- `GET /api/evaluation/tasks/{taskId}` 支持从数据库查询任务详情。
- 评测任务支持 `public` 和 `private`；公开任务对所有登录用户可见，私有任务只对创建者可见。
- 反馈和评论归属当前登录用户，用户只能删除自己的评论。

### React 主前端

- 独立目录：`frontend/`
- 技术栈：React 19、TypeScript、Vite、React Router、Vitest、Tailwind CSS、Ant Design、Recharts、GSAP
- 已完成对原 Vue 前端的功能替代：
    - 登录、注册、退出和 HttpOnly Cookie 登录态恢复。
    - 受保护业务路由、公开认证路由、基础业务布局和按角色导航。
    - `/` 评测工作台：模型列表、今日 Token、公开/私有评测、思考模式、空闲模型 LLM 评审、多模型逐 token NDJSON 展示、评分中状态、卡片内 Markdown 与数学公式渲染、`<think>` 默认展开、评分详情和点赞/点踩反馈。
    - `/history` 历史任务：分页、详情加载、状态标记、超时提示、公开/私有标记、反馈操作和公开评论分页、发布、删除。
    - `/models` 管理员模型配置：列表、新增、编辑、启用/禁用、删除和连接测试。
    - `/users` 管理员用户管理：按用户名、角色和状态搜索筛选用户，分页查看今日 Token 用量，封号/解封，并调整普通用户每日额度。
    - `/feedback` 反馈统计：普通用户个人统计，管理员全局模型表现、每日趋势和互动明细。
    - 品牌 logo、深色侧栏、主题色层、移动端深色导航抽屉和 GSAP 页面/卡片/弹窗动画。

### Vue 历史前端

- 目录：`vue-frontend/`
- 技术栈：Vue 3、JavaScript、Vite、Pinia、Vue Router、Axios、Element Plus、Markdown-it、DOMPurify、GSAP
- 该目录仅作为历史版本保留，用于必要时回看旧实现；后续新功能和样式优化默认不再修改 Vue 前端。

### 数据库与文档

- MySQL Docker Compose：`docker-compose.yml`
- 初始化 SQL：`docker/mysql/init/001_schema.sql`
- Alembic 基础配置：`backend/alembic.ini`
- 文档：
    - `docs/README.md`
    - `docs/architecture.md`
    - `docs/database.md`
    - `docs/api.md`
    - `docs/open-source-reuse.md`
    - `docs/docker-development-spec-plan.md`
    - `docs/react-rewrite/`
    - `docs/legacy-v2/`

## 本地运行方式

默认开发环境只依赖 Docker Desktop 与 Docker Compose。MySQL、Alembic 迁移、FastAPI 后端和 React/Vite 前端全部在容器中运行，Windows 与 macOS/Linux 使用同一套 Compose 定义。

Windows PowerShell：

```powershell
.\scripts\start-local.ps1
```

macOS 或 Linux：

```bash
./scripts/start-local.sh
```

两个脚本都会检查根目录 `.env`、Docker daemon 和 Compose 配置，再执行 `docker compose up --build`。默认 React 前端地址为 `http://127.0.0.1:5174`，后端健康检查为 `http://127.0.0.1:8000/api/health`；端口可通过 `.env` 的 `FRONTEND_PORT`、`BACKEND_PORT` 显式覆盖，冲突时不自动漂移。

后端和前端源码通过 bind mount 保留热更新。Python 依赖安装在镜像中，前端 `node_modules` 使用 Docker Volume；宿主机无需安装 Python、Node.js、pnpm 或 MySQL。MySQL 不映射宿主机 `3306`，数据保存在 `mysql_data`，任何项目脚本都不得执行 `docker compose down -v`。

历史 Vue 版本不进入默认 Compose 主流程；确需回看时可使用 `scripts/start-local-vue.sh`，该旧脚本仍依赖宿主机开发工具。

## 环境变量

复制 `.env.example` 为 `.env` 后填写密码。容器内数据库主机固定为 Compose 服务名 `mysql`，应用数据库账号固定为普通账号 `multichateval`：

```text
MYSQL_HOST=mysql
MYSQL_USER=multichateval
BACKEND_PORT=8000
FRONTEND_PORT=5174
DATABASE_URL=mysql+aiomysql://multichateval:<密码>@mysql:3306/multichateval
BACKEND_CORS_ORIGINS=http://localhost:5173,http://localhost:5174,http://127.0.0.1:5173,http://127.0.0.1:5174
```

`.env` 和真实模型 API Key 不应提交到 Git。用户名或密码包含 URL 特殊字符时，`DATABASE_URL` 中对应部分需要百分号编码。

## 当前实现状态与 React 前端维护方向

### 已跑通主流程

当前已实现：

- 用户提问 → 选择多个模型 → 后端并发调用真实 OpenAI-compatible 模型 → 逐 token 流式展示 → 回答完成后显示“评分中……” → 规则评分与可选 LLM Judge → 前端对比展示。
- 模型配置从数据库动态读取。
- 模型耗时、输出 token、成本估算、错误状态、规则评分和可选 LLM Judge 结果会返回给前端。
- `<think>...</think>` 和 `reasoning_content` 会默认展开展示为“思考过程”，并在流式响应中同步更新。

### 已实现：持久化与历史任务

- 将评测任务写入 `evaluation_tasks`。
- 将模型回答写入 `model_responses`。
- 将规则评分写入 `evaluation_results`。
- 将 `GET /api/evaluation/tasks/{taskId}` 改为真实查询。
- 前端“历史任务”入口支持分页查看和详情加载。

### 评分增强

已实现：

- 相关性评分使用字符 n-gram 相似度、意图覆盖、回答聚焦度、显式要求对齐和离题惩罚，不使用旧的关键词交集主算法。
- 完整性、清晰度、格式和安全性已增加更细的规则与命中项明细；安全性使用危险输出控制、拒答质量、高风险领域谨慎性和隐私/凭据保护四类本地信号合成。
- LLM Judge 输出结构化 JSON，评分、理由和明细会持久化；有效 Judge 分以 40% 权重计入基础分。
- 点赞比例映射为反馈分，并以 10% 权重计入最终分；没有反馈时最终分保持基础分。

冻结说明：

- v2 不再继续增加语言一致性评分。

### LLM Judge

已实现：

- 使用独立评审 Prompt，并将候选回答视为不可信输入。
- 评审模型必须是未参与本次测评的空闲模型；如果所有可用模型都已被测，前端会禁用 LLM 评审开关并提示用户保留一个空闲模型。
- 要求评审模型输出结构化 JSON。
- 解析评分、优点、缺点、改进建议和推荐理由。
- 将有效 LLM Judge 分以 40% 权重纳入基础分。

### 用户反馈

已实现：

- 点赞、点踩按钮已接通反馈接口。
- 点赞和点踩会真实写入或取消写入 `user_feedback`。
- demo-v1 旧匿名数据继续归属 `user_id = 0`；新反馈和评论归属当前登录用户。
- 评测页和历史任务详情页都可以提交点赞或点踩。
- 点赞/点踩变化会重算并持久化最终分。
- 全文详情弹窗会展示完整回答、维度分数、权重、命中项明细、当前反馈状态和评分公式。
- 支持分页查看、发布和硬删除公开评论；同一用户对同一回答的评论数量不受限制。
- 反馈统计页支持最近 7 天、30 天和全部历史范围，普通用户查看本人评测表现与本人互动，管理员查看全局模型统计、每日趋势和分页互动明细。

冻结说明：

- v2 不再继续增加模型推荐。

### v2 阶段 2/3 设计

- 管理员从 DeepSeek、MiniMax、GLM、Qwen、Xiaomi MiMo、OpenAI 预设或 OpenAI-compatible 空白模板创建模型配置，不再自动补齐三家内置记录。
- 模型配置支持温度、最大输出、超时、备注、币种及输入、输出、缓存命中、缓存创建四类每百万 Token 单价。
- 普通用户默认每日额度为 100,000 总 Token，管理员不限额；额度按北京时间自然日统计。
- 回答卡片先显示总费用，悬停或聚焦查看四项费用明细。

### v2 冻结与 React 前端方向

- v2 后续新功能开发到此结束。
- 原 v2 开发计划已废除并移除；未完成的模型推荐、语言一致性评分、评论审核、结果导出、批量评测和运行监控不再作为当前开发目标。
- React 前端已经完成对原 Vue 前端的功能替代，后续新功能、样式优化和维护工作默认使用 `frontend/`。
- `vue-frontend/` 仅作为历史版本保留；如需移除或继续维护，需要单独任务决策。

## 当前评分公式

当前基础分和最终分：

```text
BaseFinal = RuleFinal
BaseFinal = 0.30 × RuleFinal + 0.70 × JudgeFinal  # 三轮 Judge 稳定且有效时
FeedbackScore = 10 × LikeCount / (LikeCount + DislikeCount)
Final = BaseFinal                                 # 暂无反馈
Final = 0.90 × BaseFinal + 0.10 × FeedbackScore  # 已有反馈
```

`model_failed`、`judge_failed`、`judge_unstable` 和 `manual_required` 状态的回答 `final_score` 为空，并从反馈统计中排除。评论不参与评分。新评论归属当前登录用户，只有作者可以删除；旧匿名评论继续归属 `user_id = 0`。

## 开源项目参考

### OpenCompass

适合作为评测体系和答辩相关工作的参考，但不建议直接作为主业务底座。原因是 OpenCompass 更偏离线 benchmark，而本项目是面向用户的在线多模型对话评测平台。

### promptfoo

适合参考 LLM 输出测试、规则评分和 LLM-as-a-Judge 的配置思路。

### FastChat

适合参考多模型聊天、模型对比和用户偏好反馈思路。

## 开发约定

- 全程使用中文交流、中文注释和中文文档。
- 当前主前端使用 React + TypeScript。
- 历史 Vue 前端使用 JavaScript，不使用 TypeScript，默认不再承接新功能开发。
- 前端包管理优先使用 pnpm。
- React 前端使用 `frontend/pnpm-workspace.yaml` 固定 `picomatch@4.0.4`，避免新版 pnpm minimum release age 策略拦截刚发布的传递依赖。
- 后端尽量补充类型标注。
- 不要把 API Key、数据库密码等真实敏感信息提交到仓库。
- 文件修改前需要确认用户授权。
- 不要随意删除用户已有文件。
- 不要将 `.env`、`node_modules`、虚拟环境、构建产物提交到 Git。
- 当前 `POST /api/evaluation/tasks/stream` 做逐 token 流式输出；一次性 `POST /api/evaluation/tasks` 仍保留完整结果返回。

## 文档先行约定

- V3 按合并规格与计划分阶段开发、验证和提交；当前获准在 `dev` 原地工作、下载依赖/镜像/模型和运行测试。不得自动推送或把测试数据写入许可扩大为清空业务数据；实际业务库迁移仍需列明迁移、备份、恢复并获得对应授权。
- 每次做功能更新前，先确认会影响哪些文档；如果需求、接口、数据结构或交互行为不清楚，先补充或更新设计/说明文档，再改代码。
- 每次完成后端接口、前端功能、数据库结构、配置项、评分规则或模型调用逻辑变更时，必须同步更新对应文档。
- 功能实现状态统一维护在 `docs/system-features-status.md`；当功能从“未实现”变为“部分实现”或“已实现”时，需要同步更新该文档。
- API 请求/响应变化需要同步更新 `docs/api.md`。
- 数据表、字段、关系或持久化流程变化需要同步更新 `docs/database.md`。
- 架构、模块边界、主流程或模型调用链路变化需要同步更新 `docs/architecture.md`。
- README 只保留项目概览、运行方式和重要入口；详细实现状态优先写入 `docs/` 下的专题文档。
- 交付前需要检查本次代码变更和文档是否一致（包含`README.md`和`AGENTS.md`两个核心文档）；如果暂时无法更新文档，必须在回复中说明原因和后续补文档位置。

## 后续 AI 接手建议

新的 AI 或开发者接手时，建议按以下顺序阅读：

1. `README.md`
2. `docs/README.md`
3. `docs/architecture.md`
4. `docs/database.md`
5. `docs/api.md`
6. `docs/system-features-status.md`
7. `docs/react-rewrite/README.md`
8. `docs/react-rewrite/plan.md`
9. `docs/react-rewrite/architecture.md`
10. `backend/app/services/evaluation_service.py`
11. `frontend/src/pages/EvaluationPage.tsx`

优先推进的任务是：

1. 启动 Docker Desktop 后，Windows 使用 `.\scripts\start-local.ps1`，macOS 或 Linux 使用 `./scripts/start-local.sh` 构建并运行完整 Compose 开发栈。
2. 确认 React 主前端能调用后端真实模型接口。
3. 确认逐 token 流式展示、“评分中……”状态和全局思考模式行为正常。
4. 验证评分结果、点赞/点踩和公开评论均正确持久化到 MySQL。
5. 验证公开任务跨用户可见、私有任务仅创建者可见。
6. React 主前端收尾验收可运行 `./scripts/verify-react-rewrite.sh`。
7. 如需查看旧实现，可使用 `./scripts/start-local-vue.sh` 启动历史 Vue 前端。
