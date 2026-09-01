# MultiChatEval

面向多模型问答的对话质量评估系统。

本项目用于课程设计，目标不是判断“哪个 AI 一定最好”，而是通过多模型并发回答、客观指标、规则评分、可选 LLM 评审和用户反馈，帮助用户结构化比较不同模型的回答质量。

## 当前状态

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
- Windows 开发数据库：本地 MySQL 8；Docker Compose 数据库部署继续保留

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
- `docs/react-rewrite/`：React 替代 Vue 的历史重构文档。
- `docs/react-rewrite/acceptance.md`：React 替代完成时的验收清单。
- `docs/legacy-v2/`：v2 已落地阶段设计归档；原 v2 开发计划已废除并移除。

## 快速开始

Windows 11 推荐在 PowerShell 控制台运行：

```powershell
.\scripts\start-local.ps1
```

如果系统执行策略阻止 `.ps1`，可以仅为本次启动临时绕过：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start-local.ps1
```

macOS 或 Linux 继续使用 Bash 脚本：

```bash
./scripts/start-local.sh
```

两个脚本都会启动当前 React 主前端全栈项目。Windows 脚本默认使用本机安装的 MySQL；Bash 脚本继续使用 Docker MySQL。

脚本会自动检查 `.env`、准备后端虚拟环境、安装后端和 React 前端依赖、执行 Alembic 数据库迁移，并默认启动后端 `http://127.0.0.1:8000` 与 React 前端 `http://127.0.0.1:5174`。Windows 本地模式只等待 `.env` 指定的 MySQL 端口，不会启动或修改 Windows MySQL 服务；如需临时使用 Docker 数据库，可运行 `.\scripts\start-local.ps1 -DatabaseMode Docker`。Windows 可通过 `.\scripts\start-local.ps1 -BackendPort 8001 -FrontendPort 5175` 指定起始端口；macOS 或 Linux 可通过 `BACKEND_PORT=8001 FRONTEND_PORT=5175 ./scripts/start-local.sh` 指定。按 `Ctrl+C` 可以停止本次启动的后端和前端进程。

运行前请确保已经安装并启动：

- Python 3.11 或更高版本。
- Node.js 与 pnpm。
- Windows 本地模式需要 MySQL 8 和 PowerShell 5.1 或更高版本，无需安装 Docker、`curl` 或 `lsof`。
- Docker 数据库模式需要 Docker Desktop（包含 Docker Compose）。
- macOS 或 Linux 需要 Docker、Bash、`curl` 和 `lsof`。

React 版本脚本会按 `backend/pyproject.toml` 和 `frontend/pnpm-lock.yaml` 校验依赖，并在数据库端口和前后端真实可访问后才提示启动完成。Windows 脚本不会弹出额外控制台窗口，标准输出和错误日志分别写入 `logs/`；按 `Ctrl+C` 只停止本次启动的前后端进程，不停止本地 MySQL 服务或 Docker MySQL 容器。

React 前端通过 `frontend/pnpm-workspace.yaml` 固定 `picomatch@4.0.4`。这是为了避开新版 pnpm minimum release age 策略对刚发布传递依赖的拦截，保证 VSCode、系统终端和 Codex 终端都能按锁文件稳定安装。

如果默认端口已被占用，脚本会自动向后寻找可用端口，并把实际后端地址同步给 Vite 代理。

首次运行时脚本会自动复制环境变量模板；如需手动准备，Windows PowerShell 可执行：

```powershell
Copy-Item .env.example .env
```

macOS 或 Linux 可执行：

```bash
cp .env.example .env
```

本地开发前必须填写根目录 `.env` 中的 `MYSQL_DATABASE`、`MYSQL_USER`、`MYSQL_PASSWORD` 和 `DATABASE_URL`，并提前创建 `MYSQL_DATABASE` 指定的数据库；使用 Docker 模式时还必须填写 `MYSQL_ROOT_PASSWORD`。Windows 启动脚本发现当前模式所需配置为空或仍含 `CHANGE_ME` 时会直接停止，不会执行数据库迁移。若用户名或密码含有 `@`、`:`、`/` 等 URL 特殊字符，写入 `DATABASE_URL` 前需要进行百分号编码。

部署到非本地环境前，必须将 `.env` 中的 `JWT_SECRET_KEY` 改为足够长的随机值，并在 HTTPS 环境设置 `AUTH_COOKIE_SECURE=true`。

### 1. 启动数据库

Windows 开发环境先启动本机 MySQL 服务，然后直接运行 PowerShell 一键启动脚本。Docker 数据库部署仍可使用：

```bash
docker compose up -d mysql
```

本地 MySQL 和 Docker MySQL 默认都使用宿主机 `3306`，二者不要同时运行。

### 2. 启动后端

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

后端默认地址：

```text
http://localhost:8000
```

### 3. 启动 React 主前端

```bash
cd frontend
pnpm install --frozen-lockfile
pnpm dev
```

React 前端默认地址：

```text
http://localhost:5174
```

如果需要查看历史 Vue 版本，可使用带 Vue 后缀的启动脚本，或进入 `vue-frontend/` 手动启动；该目录仅作为历史版本保留，不再作为后续主要开发目标。

```bash
./scripts/start-local-vue.sh
```

React 替代完成后的验收可运行：

```bash
./scripts/verify-react-rewrite.sh
```

该脚本会依次验证后端测试、React 测试、React 构建、Vue 测试、Vue 构建和 `git diff --check`。Vue 相关检查用于确认历史版本仍未被无意破坏，后续业务开发以 React 前端为准。

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

```bash
cd backend
source .venv/bin/activate
python -m app.scripts.create_admin --username admin
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
