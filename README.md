# MultiChatEval

面向多模型问答的对话质量评估系统。

本项目用于课程设计，目标不是判断“哪个 AI 一定最好”，而是通过多模型并发回答、客观指标、规则评分、可选 LLM 评审和用户反馈，帮助用户结构化比较不同模型的回答质量。

## 当前状态

V3 RAG 正在按 [需求规格与分阶段实施计划](docs/v3-rag-spec-plan.md) 开发。已有私有知识库/文档 API、四格式索引、逐模型检索回答、证据快照和三轮忠实度/引用联合评分；阶段 6 已接入 React `/knowledge-bases`、`/rag` 及历史类型筛选、引用与评分详情代码。当前设备内存不足且 Docker 已关闭，优先交付代码：阶段 5—6 新增测试、前端构建、浏览器验收、真实模型全链路与本地部署均待资源充足设备执行，不能视为通过。迁移 `20260902_01/02` 仅在隔离测试库验证，业务升级需先确认备份与恢复方案。Agent 工具评测与 AI 安全测试集将在 RAG 之后单独设计。

当前 **v2 版本开发已经结束并冻结**，后续不再继续推进语义分析、模型推荐、运行监控等新功能。React 前端已经完成对原 Vue 前端的功能替代，后续开发统一使用 `frontend/` React 技术栈；`vue-frontend/` 仅作为历史版本保留，用于必要时回看旧实现。

v2 已完成并保留的账号体系与权限控制能力：

- 开放注册、登录、退出和 HttpOnly Cookie JWT 登录态；注册要求二次确认密码，并校验密码复杂度。
- 普通用户与管理员角色隔离，模型配置仅管理员可维护。
- 评测任务支持公开和私有模式。
- 任务、点赞/点踩和评论归属真实登录用户。
- 公开任务对所有登录用户可见，私有任务仅创建者可见。

demo-v1 的核心评测能力继续保留：

- 管理员模型配置与 API Key 管理。
- 多模型并发调用和逐 token 流式展示。
- 本地词库硬规则评分与默认开启的三轮 LLM Judge 稳定性评审；无法稳定评分的回答会标记状态并从统计中排除。
- 点赞/点踩反馈计分、公开评论和历史任务查询。
- MySQL 持久化、Alembic 迁移和一键启动脚本。
- 管理员可在 `/scoring-rules` 维护规则词库和 Judge Prompt。

v2 已提供按角色分流的反馈统计：普通用户查看个人评测表现和本人互动，管理员查看全局模型质量、趋势与互动明细。未实现的 v2 后续规划只作为历史记录保留，不再作为当前开发目标。

## 技术栈

- 后端：Python、FastAPI、SQLAlchemy 2.0、Alembic、Pydantic Settings、pytest
- 当前主前端：React 19、TypeScript、Vite、React Router、Tailwind CSS、Ant Design、Recharts、GSAP，位于 `frontend/`
- 历史前端：Vue 3、JavaScript、Vite、Pinia、Vue Router、Axios、Element Plus、Markdown-it、DOMPurify、GSAP，位于 `vue-frontend/`，后续不再作为主要开发目标
- 数据库：MySQL 8
- RAG 基础设施：Qwen3-Embedding-0.6B、TEI CPU、Qdrant、Redis、Celery（实施中）
- 开发环境：Docker Compose 统一运行 MySQL、Alembic 迁移、FastAPI 后端和 React/Vite 前端

## 仓库结构

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

## 文档入口

- `docs/system-features-status.md`：系统功能和实现状态。
- `docs/README.md`：文档目录说明，区分当前权威文档、React 历史重构文档和 v2 历史归档。
- `docs/architecture.md`：系统架构和核心流程。
- `docs/api.md`：后端接口说明。
- `docs/database.md`：数据库表结构说明。
- `docs/docker-development-spec-plan.md`：全栈 Docker 开发环境的需求、方案、实施步骤和验收标准。
- `docs/v3-rag-spec-plan.md`：V3 RAG 合并规格、阶段计划、固定依赖版本和验收证据。
- `docs/react-rewrite/`：React 替代 Vue 的历史重构文档。
- `docs/react-rewrite/acceptance.md`：React 替代完成时的验收清单。
- `docs/legacy-v2/`：v2 已落地阶段设计归档；原 v2 开发计划已废除并移除。

## 快速开始

当前资源不足设备不启动 Docker。V3 代码在资源充足设备的验收入口为 `scripts/verify-rag.ps1 -Mode unit` / `-Mode integration`（Linux/macOS 使用 `bash scripts/verify-rag.sh unit` / `integration`）。它们使用独立测试项目，不读取业务 `.env` 或升级业务库；范围与未执行清单见 [RAG 验收与交接](docs/v3-rag-spec-plan.md#11-验证命令与执行边界)。阶段 7 已编写测试及脚本，仅配置/语法/差异静态检查通过，完整测试仍未执行。

下述默认开发栈命令会运行数据库迁移；已有业务数据升级前，必须先确认备份、恢复和对应迁移授权。

默认开发环境只要求安装并启动 Docker Desktop（包含 Docker Compose）。Python、Node.js、pnpm、MySQL、后端和 React 前端均在容器中运行。

首次运行先准备根目录 `.env`：

```powershell
Copy-Item .env.example .env
```

macOS 或 Linux 使用：

```bash
cp .env.example .env
```

填写 `.env` 中的 `MYSQL_PASSWORD`、`MYSQL_ROOT_PASSWORD`、`JWT_SECRET_KEY` 和 `DATABASE_URL` 密码部分。应用数据库账号固定为 `multichateval`，数据库主机固定为 Compose 服务名 `mysql`。用户名或密码含有 `@`、`:`、`/` 等 URL 特殊字符时，写入 `DATABASE_URL` 前需要百分号编码。脚本发现有效配置行中仍含 `CHANGE_ME` 时会在创建容器前停止。

Windows 11 在 PowerShell 中运行：

```powershell
.\scripts\start-local.ps1
```

如果执行策略阻止 `.ps1`，可以仅为本次启动临时绕过：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start-local.ps1
```

macOS 或 Linux 运行：

```bash
./scripts/start-local.sh
```

两个脚本都会先校验 Docker 与 Compose 配置，再构建并前台启动以下服务：

- `mysql`：MySQL 8.4，数据保存在 `mysql_data` 命名卷中，不映射宿主机 `3306`。
- `migrate`：等待 MySQL 健康后执行 `alembic upgrade head`，成功后退出。
- `backend`：FastAPI + Uvicorn 热更新，默认映射到 `http://127.0.0.1:8000`。
- `frontend`：React + Vite 热更新，默认映射到 `http://127.0.0.1:5174`，并通过 Compose 网络代理 `/api` 到后端。
- `embedding`：CPU 私有部署 Qwen3-Embedding-0.6B，首次启动下载固定版本模型到 `rag_model_cache`；下载期间健康检查可能尚未通过。
- `qdrant`：向量存储，使用 `qdrant_data`。
- `redis`：Celery 消息队列，使用 `rag_redis_data` 持久化。
- `rag-worker`：复用后端镜像，只读共享分词器缓存，处理文档索引/清理、作业恢复与超期 RAG 任务收尾。

新增 RAG 服务不映射宿主机端口，普通后端不依赖它们就绪。首次下载耗时取决于网络；查看 `docker compose logs -f embedding`。后端与 Worker 共用 `rag_documents` 文件卷，不在宿主机安装模型或 Python 依赖。只需验证新增服务时使用 `docker compose up -d --no-deps embedding qdrant redis`，不会触发 `migrate`；完整启动脚本仍会自动执行数据库迁移。

前后端源码以 bind mount 挂载；宿主机 `.venv` 和 `node_modules` 不参与容器运行。依赖清单变化时重新构建镜像，普通源码修改会自动热更新。

端口固定且冲突时直接报错，不会自动漂移。需要覆盖宿主机端口时修改 `.env`：

```dotenv
BACKEND_PORT=8001
FRONTEND_PORT=5175
```

查看状态和日志：

```powershell
docker compose ps
docker compose logs -f backend frontend
```

按 `Ctrl+C` 可停止前台开发环境，也可以在另一个终端执行普通停止：

```powershell
docker compose down
```

普通停止、镜像重建和再次启动都会保留 `mysql_data`。以下命令会永久删除开发数据库卷，仅在明确需要清空全部开发数据时人工执行，项目脚本绝不会自动调用：

```powershell
docker compose down -v
```

部署到非本地环境前，必须将 `.env` 中的 `JWT_SECRET_KEY` 改为足够长的随机值，并在 HTTPS 环境设置 `AUTH_COOKIE_SECURE=true`。

历史 `vue-frontend/` 不进入默认 Compose 主流程。确需回看旧版本时可使用 `scripts/start-local-vue.sh`，该旧脚本仍要求宿主机安装相应开发依赖。

React 替代完成后的历史验收脚本 `scripts/verify-react-rewrite.sh` 仍保留；当前容器化开发环境的设计与验收标准见 `docs/docker-development-spec-plan.md`。

## 当前能力

当前版本已经跑通：

1. 用户注册或登录，并选择公开或私有评测。
2. 普通用户读取管理员已启用且已配置 API Key 的模型。
3. OpenAI-compatible 适配器并发调用真实模型。
4. 多个模型并发逐 token 返回回答，单个模型回答结束后进入“评分中……”状态。
5. 记录回答四类 Token、总 Token、分项费用、总费用和错误状态。
6. 执行规则评分，可选启用 LLM Judge，并展示评分条和评分详情。
7. 支持全局“思考模式”开关。
8. 将评测任务、模型回答、评分、点赞/点踩和公开评论按登录用户保存到 MySQL，并支持分页查看历史任务。
9. 点赞/点踩以 10% 权重计入最终分；没有反馈时保持基础分不变。
10. 历史任务详情会正确展示超时未完成状态，并在暂无回答时显示明确说明。
11. 反馈统计页按角色展示个人或全局的评分、点赞、点踩、评论、模型表现和每日趋势。

`/api/evaluation/tasks/stream` 使用 NDJSON 返回逐 token 增量事件；模型回答完成后先展示“评分中……”，规则评分和可选 LLM Judge 完成后再更新为最终评分结果。

v2 阶段 2/3 已提供管理员模型参数、四类计费配置、用户每日 Token 额度、用户搜索筛选分页、封号/解封和用量展示；反馈统计页也已形成角色隔离的可运行闭环。v2 到此结束，未实现的语义分析、模型推荐和运行监控不再继续开发。React 前端已经完成认证、角色导航、评测工作台、历史任务、公开评论交互、管理员模型配置、用户额度、反馈统计、品牌视觉和动效收尾，并成为后续唯一主前端技术栈。`vue-frontend/` 仅作为历史版本保留。

## 前端展示能力

当前 React 主前端已经支持：

- 使用多路由结构组织页面：`/login` 和 `/register` 为认证页面，`/` 为对比评测，`/models` 为管理员模型配置，`/users` 为管理员用户管理与额度，`/history` 为历史任务，`/feedback` 为按角色分流的反馈统计。
- 普通用户不显示模型配置入口，管理员可以维护模型配置。
- 评测表单支持公开和私有模式，历史任务展示创建者与可见性。
- 按实际模型名称展示模型选择项和结果卡片，例如 `deepseek-v4-flash`、`MiniMax-M2.5`、`glm-4.7`。
- 对比评测使用平衡摘要网格：四个模型固定两行两列，五至九个模型自动平衡分行并铺满每一行；容器变窄时自动降为两列或单列。
- 模型请求期间展示等待卡片、等待秒数和占位动画；收到模型增量事件后在卡片内实时 Markdown 渲染回答文本，回答结束后展示“评分中……”，评分完成后替换成最终回答卡片。
- 使用 GSAP 实现结果进入、等待卡替换、评分条和详情弹窗动画，并适配减少动态效果偏好。
- 全局“思考模式”开关会随评测请求发送给后端，不区分具体模型，也不提供思考程度选项。
- 回答内容使用 Markdown 渲染，支持标题、列表、代码块、表格、引用、链接和 `$...$` / `$$...$$` 数学公式。
- 使用 DOMPurify 清洗渲染后的 HTML，降低 Markdown 内容带来的 XSS 风险。
- 自动识别 `<think>...</think>` 内容，默认展开展示为“思考过程”，并在流式输出时同步更新。
- 回答卡片内容区支持用户滚动；仅当用户视口接近底部时才跟随新增 token 自动滚动到底部。
- 侧边栏“历史任务”入口支持分页查看历史评测，回答以单列紧凑列表展示，并可加载完整回答和评分详情。
- Ant Design 使用中文语言配置，历史任务和反馈统计分页使用中文交互。
- 回答摘要卡支持点赞、点踩和全文详情弹窗，反馈会真实写入或取消写入 `user_feedback`，并立即更新最终分。
- 回答卡先展示总费用，悬停或键盘聚焦总费用时展示四类 Token 与分项费用；移动端点击切换。
- 评测页展示北京时间当日 Token 已用、剩余和每日额度，额度耗尽时禁止创建新任务。
- 评分详情支持分页查看、发布和删除公开评论，评论独立保存在 `user_comments`，不参与评分。
- 历史任务时间按北京时间展示；`pending` 任务默认显示“进行中”，创建超过 120 秒后仍未完成才显示为“超时未完成”。
- 侧边栏“反馈统计”面向所有登录用户；普通用户只看个人范围，管理员可看全局统计和互动明细。
- 桌面端侧边栏固定在可视区域内；移动端使用深色抽屉导航，保持与桌面端一致的品牌底色和高对比文字。

## 真实模型配置

后端当前已支持 OpenAI-compatible 的 `/chat/completions` 调用。系统不再自动创建内置模型记录；管理员可从 DeepSeek、MiniMax、GLM、Qwen、Xiaomi MiMo、OpenAI 预设或 OpenAI-compatible 空白模板创建配置，并自行填写 API Key、模型名和官方价格。

如果系统内没有任何已配置 API Key 的模型，管理员会看到配置入口，普通用户会看到联系管理员的提示。

首次创建管理员：

```powershell
docker compose exec backend python -m app.scripts.create_admin --username admin
```

如果评测问题较长或模型响应较慢，可在管理员模型配置的“高级选项”中调整对应模型的请求超时。

规则评分使用轻量本地多信号评分，不依赖外部语义模型或下载。评分前会排除 `<think>...</think>` 思考内容，仅分析最终回答；原始回答仍会完整保存并在前端默认展开展示思考过程。相关性会结合字符 n-gram 相似度、关键词覆盖、意图覆盖、回答聚焦度、显式要求对齐和离题惩罚计算；安全性会结合危险输出控制、拒答质量、高风险领域谨慎性和隐私/凭据保护计算。

普通 Axios API 请求超时时间为 120 秒。逐 token 流式评测使用原生 `fetch` 持续读取 NDJSON，不设置 120 秒前端强制中断；历史任务以创建超过 120 秒作为“超时未完成”的展示阈值。

评测请求发送给模型前，后端会在用户原始问题前拼接系统内置提示词，用于统一直接作答、结构化表达、安全边界和格式遵循要求。数据库、历史记录和接口响应中的 `prompt` 仍保留用户原始问题。

评测请求会使用全局“思考模式”开关控制所有模型的 thinking 参数。关闭时统一传入：

```json
{
  "thinking": {
    "type": "disabled"
  }
}
```

开启时统一传入：

```json
{
  "thinking": {
    "type": "enabled"
  }
}
```

项目不传递思考程度参数。如果某个供应商不支持 `thinking` 字段，对应模型会在回答摘要卡中显示失败，不影响其他模型继续返回。

关闭思考模式时，后端确认发送的是嵌套字段 `thinking.type=disabled`，不是 `thinkingmode:disabled`。MiniMax 等部分 OpenAI-compatible 供应商即使收到该参数，也可能仍在返回内容中包含 `<think>` 或 reasoning 字段；前端会按当前规则默认展开展示这些内容。
