# EvalSpark 全栈 Docker 开发环境 Spec 与实施计划

**目标：** 将 EvalSpark 的默认开发启动方式统一为 Docker Compose：MySQL、Alembic 迁移、FastAPI 后端和 React/Vite 前端全部在容器中运行，同时保留前后端源码热更新，并将开发者的宿主机依赖收敛为 Docker Desktop。

**实现方式：** 为后端和前端分别提供固定运行时版本线的开发镜像，由根目录 `docker-compose.yml` 编排 `mysql`、`migrate`、`backend` 和 `frontend` 四个服务。源码通过 bind mount 挂载，依赖保留在镜像或 Docker Volume 中；前端通过 Compose 内部网络代理后端，后端通过服务名 `mysql` 连接数据库。

**技术栈：** Docker Desktop、Docker Compose、MySQL 8.4、Python 3.12、FastAPI、Alembic、Node.js 22、pnpm 10、React 19、Vite 8。

**实施约束：** 本文档同时承担 spec 与 plan，不再拆分两个文件。实现阶段在当前会话内逐任务执行；除非老大另行授权，不使用 subagent、不提交 Git、不推送远端，也不执行任何会修改或删除数据库数据的命令。

## 1. 需求与已确认决策

### 1.1 默认开发方式

- Windows 使用 `scripts/start-local.ps1`，macOS/Linux 使用 `scripts/start-local.sh`。
- 两个脚本都只依赖 Docker 与 Docker Compose，不再检查或安装宿主机 Python、pnpm、Node.js、MySQL、`curl` 或 `lsof`。
- 两个脚本统一构建并前台启动完整 Compose 开发环境，不再提供本机 MySQL 或宿主机前后端进程模式。
- README 可保留宿主机手动运行命令作为故障排查参考，但不得再描述为默认开发方式。

### 1.2 热更新与依赖隔离

- `backend/` 挂载到后端和迁移容器的 `/app`，Uvicorn 使用 `--reload`。
- `frontend/` 挂载到前端容器的 `/app`，Vite 监听 `0.0.0.0:5174`。
- Windows Docker Desktop 下启用 Chokidar polling，保证 bind mount 文件变化能够触发 Vite 热更新。
- Python 依赖安装到开发镜像的系统 site-packages，不读取宿主机 `.venv`。
- 前端依赖安装到镜像并由命名卷 `frontend_node_modules` 挂载到 `/app/node_modules`，不读取宿主机 `frontend/node_modules`。
- `pyproject.toml`、`package.json` 或锁文件变化后，通过重新构建对应镜像更新依赖；日常源码变化不需要重建。

### 1.3 端口与访问方式

- 前端默认宿主机入口：`http://127.0.0.1:5174`。
- 后端默认宿主机入口：`http://127.0.0.1:8000`。
- `.env` 可通过 `FRONTEND_PORT` 和 `BACKEND_PORT` 覆盖宿主机映射端口；容器内部端口固定为 `5174` 和 `8000`。
- 端口被占用时启动直接失败并输出 Docker 端口绑定错误，不再自动寻找新端口。
- MySQL 不映射宿主机端口，只允许 Compose 网络中的服务访问，避免与 Windows 本机 MySQL 冲突。
- 前端 Vite 代理目标固定为 Compose 服务地址 `http://backend:8000`；浏览器业务请求仍使用同源 `/api`。

### 1.4 数据库与迁移安全

- MySQL 数据继续使用命名卷 `mysql_data`，停止、重建应用镜像或执行普通 `docker compose down` 不删除数据库数据。
- 首次初始化 SQL 创建截至 `20260612_01` 的基础结构，并写入同版本 `alembic_version`；后续结构统一由 Alembic 继续升级。
- `migrate` 服务等待 MySQL 健康后执行 `python -m alembic upgrade head`，成功退出后后端才启动。
- 后端等待迁移成功；前端等待后端健康。
- Compose 和启动脚本不得自动执行 `docker compose down -v`、SQL 导入、数据覆盖、数据库重建或其他清库操作。
- `docker compose down -v` 仅能作为带醒目破坏性警告的人工命令记录在文档中。
- 实施阶段先完成不启动数据库的静态验证。启动 MySQL、运行 Alembic 和做真实链路验收前，必须再次取得老大明确授权。

## 2. 方案选择

### 2.1 采用方案：独立开发镜像 + Docker Compose

后端和前端使用各自的 `Dockerfile.dev`，由 Compose 统一编排。该方案让 Python 与 Node 运行时独立升级、独立缓存和独立排错，最符合当前仓库的 `backend/`、`frontend/` 边界。

### 2.2 未采用方案

- 根目录单一多阶段 Dockerfile：文件数量少，但会把前后端构建逻辑耦合在一个文件中，不利于独立维护。
- Dev Container：适合 IDE 级容器开发，但会绑定特定编辑器，不能替代普通终端中的一键启动入口。
- 生产镜像模式：环境更接近部署产物，但每次源码修改都需要重新构建，不符合本次保留热更新的要求。

## 3. 架构与数据流

```text
浏览器 http://127.0.0.1:5174
              │
              │ 同源 /api
              ▼
frontend:5174（Vite + React 热更新）
              │
              │ http://backend:8000
              ▼
backend:8000（FastAPI + Uvicorn 热更新）
              │
              │ mysql+aiomysql://...@mysql:3306/multichateval
              ▼
mysql:3306（MySQL 8.4 + mysql_data）

启动依赖：mysql healthy → migrate completed → backend healthy → frontend
```

### 3.1 Compose 服务接口

| 服务 | 输入 | 输出/契约 | 生命周期 |
| --- | --- | --- | --- |
| `mysql` | `.env` 中 `MYSQL_*` | Compose 网络端口 `3306`、健康状态、`mysql_data` | 长期运行 |
| `migrate` | 后端镜像、后端源码、`DATABASE_URL` | Alembic 退出码；`0` 才允许后端启动 | 每次启动运行一次 |
| `backend` | 后端源码、`DATABASE_URL`、应用环境变量 | 容器端口 `8000`、`GET /api/health` | 长期运行并热更新 |
| `frontend` | 前端源码、锁定依赖、`VITE_BACKEND_TARGET` | 容器端口 `5174`、页面与 `/api` 代理 | 长期运行并热更新 |

### 3.2 环境变量接口

根目录 `.env.example` 必须定义下列开发环境变量：

```dotenv
MYSQL_HOST=mysql
MYSQL_PORT=3306
MYSQL_DATABASE=multichateval
MYSQL_USER=multichateval
MYSQL_PASSWORD=CHANGE_ME
MYSQL_ROOT_PASSWORD=CHANGE_ME
BACKEND_PORT=8000
FRONTEND_PORT=5174

APP_NAME=MultiChatEval
APP_ENV=development
BACKEND_CORS_ORIGINS=http://localhost:5173,http://localhost:5174,http://127.0.0.1:5173,http://127.0.0.1:5174
JWT_SECRET_KEY=CHANGE_ME
ACCESS_TOKEN_EXPIRE_MINUTES=480
AUTH_COOKIE_SECURE=false
DATABASE_URL=mysql+aiomysql://multichateval:CHANGE_ME@mysql:3306/multichateval
```

约束：

- `MYSQL_HOST` 与 `DATABASE_URL` 的主机名必须为 Compose 服务名 `mysql`，不得使用 `127.0.0.1` 或 `localhost`。
- 用户名或密码含 URL 特殊字符时，`DATABASE_URL` 中对应部分必须百分号编码。
- `.env` 保持 Git 忽略；现有保留管理员账号不能作为官方 MySQL 镜像的 `MYSQL_USER`，实施时将应用账号统一为 `multichateval`，并把数据库主机改为 `mysql`。现有数据库密码、root 密码、JWT 密钥和其他敏感值保持不变。
- Compose 使用 `${VARIABLE:?错误信息}` 校验必填值，缺失配置时在创建容器前失败；两个启动脚本额外拒绝 `.env` 中仍含 `CHANGE_ME` 的配置行。

## 4. 文件与职责

| 文件 | 操作 | 职责 |
| --- | --- | --- |
| `backend/Dockerfile.dev` | 新增 | 固定 Python 开发运行时，安装后端及测试依赖 |
| `backend/.dockerignore` | 新增 | 排除 `.venv`、缓存、测试输出和本地产物 |
| `frontend/Dockerfile.dev` | 新增 | 固定 Node/pnpm 开发运行时，按锁文件安装依赖 |
| `frontend/.dockerignore` | 新增 | 排除宿主机 `node_modules`、构建和测试产物 |
| `docker-compose.yml` | 修改 | 编排 MySQL、迁移、后端、前端、健康检查和数据卷 |
| `.env.example` | 修改 | 将默认数据库地址切换为容器服务名，增加固定端口配置 |
| `.env` | 本机修改，不提交 | 保留敏感值，只切换容器数据库主机并补充端口 |
| `scripts/start-local.ps1` | 修改 | Windows Docker Compose 一键启动入口 |
| `scripts/start-local.sh` | 修改 | macOS/Linux Docker Compose 一键启动入口 |
| `scripts/tests/start-local.Tests.ps1` | 修改 | 验证 PowerShell 启动脚本只依赖 Compose 且不执行破坏性命令 |
| `backend/tests/test_start_local_script.py` | 修改 | 验证 Bash 启动入口的 Compose 契约 |
| `backend/tests/test_react_phase1.py` | 修改 | 移除已过时的宿主机 pnpm/动态端口断言，保留 React 主前端边界断言 |
| `backend/tests/test_docker_development.py` | 新增 | 静态验证 Dockerfile、Compose、环境变量和安全约束 |
| `README.md` | 修改 | 将全栈 Docker 开发环境设为默认运行方式 |
| `docs/README.md` | 修改 | 将本文档加入当前维护文档索引 |
| `docs/architecture.md` | 修改 | 更新本地开发部署拓扑和容器内数据流 |
| `docs/system-features-status.md` | 修改 | 更新开发环境实现状态与维护建议 |
| `AGENTS.md` | 修改 | 移除本机 MySQL 默认启动的过时说明 |
| `docs/docker-development-spec-plan.md` | 新增 | 合并记录需求、方案、接口、步骤和验收标准 |

本次不修改后端 API、数据库表结构、业务数据模型和 `vue-frontend/`，因此不改 `docs/api.md` 与 `docs/database.md`。

## 5. Docker 契约

### 5.1 后端开发镜像

`backend/Dockerfile.dev` 使用以下结构：

```dockerfile
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY pyproject.toml ./
COPY app ./app
RUN python -m pip install --no-cache-dir -e ".[dev]"

ENV PIP_ROOT_USER_ACTION=ignore

COPY alembic.ini ./
COPY migrations ./migrations

CMD ["python", "-m", "uvicorn", "app.main:app", "--reload", "--host", "0.0.0.0", "--port", "8000"]
```

`aiomysql` 已声明对 `PyMySQL` 的传递依赖，能够满足 `backend/migrations/env.py` 使用 `mysql+pymysql://` 执行同步 Alembic 迁移的现有行为，不新增重复依赖。

### 5.2 前端开发镜像

`frontend/Dockerfile.dev` 使用以下结构：

```dockerfile
FROM node:22-bookworm-slim

ENV PNPM_HOME=/pnpm
ENV PATH=$PNPM_HOME:$PATH

RUN corepack enable \
    && corepack prepare pnpm@10.15.1 --activate

WORKDIR /app

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile

COPY . .

CMD ["pnpm", "dev", "--host", "0.0.0.0", "--port", "5174", "--strictPort"]
```

### 5.3 Compose 核心配置

`docker-compose.yml` 的实现必须满足以下契约：

```yaml
name: evalspark

services:
  mysql:
    image: mysql:8.4
    restart: unless-stopped
    environment:
      MYSQL_ROOT_PASSWORD: ${MYSQL_ROOT_PASSWORD:?请在 .env 中配置 MYSQL_ROOT_PASSWORD}
      MYSQL_DATABASE: ${MYSQL_DATABASE:?请在 .env 中配置 MYSQL_DATABASE}
      MYSQL_USER: ${MYSQL_USER:?请在 .env 中配置 MYSQL_USER}
      MYSQL_PASSWORD: ${MYSQL_PASSWORD:?请在 .env 中配置 MYSQL_PASSWORD}
    volumes:
      - mysql_data:/var/lib/mysql
      - ./docker/mysql/init:/docker-entrypoint-initdb.d:ro
    healthcheck:
      test: ["CMD", "mysqladmin", "ping", "-h", "127.0.0.1", "--silent"]
      interval: 5s
      timeout: 5s
      retries: 20

  migrate:
    build:
      context: ./backend
      dockerfile: Dockerfile.dev
    env_file:
      - .env
    command: ["python", "-m", "alembic", "upgrade", "head"]
    volumes:
      - ./backend:/app
    depends_on:
      mysql:
        condition: service_healthy
    restart: "no"

  backend:
    build:
      context: ./backend
      dockerfile: Dockerfile.dev
    env_file:
      - .env
    command: ["python", "-m", "uvicorn", "app.main:app", "--reload", "--host", "0.0.0.0", "--port", "8000"]
    ports:
      - "${BACKEND_PORT:-8000}:8000"
    volumes:
      - ./backend:/app
    depends_on:
      migrate:
        condition: service_completed_successfully
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2)"]
      interval: 5s
      timeout: 3s
      retries: 20

  frontend:
    build:
      context: ./frontend
      dockerfile: Dockerfile.dev
    environment:
      VITE_BACKEND_TARGET: http://backend:8000
      VITE_DEV_PORT: "5174"
      CHOKIDAR_USEPOLLING: "true"
      CHOKIDAR_INTERVAL: "300"
    command: ["pnpm", "dev", "--host", "0.0.0.0", "--port", "5174", "--strictPort"]
    ports:
      - "${FRONTEND_PORT:-5174}:5174"
    volumes:
      - ./frontend:/app
      - frontend_node_modules:/app/node_modules
    depends_on:
      backend:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "node", "-e", "fetch('http://127.0.0.1:5174').then(response => { if (!response.ok) process.exit(1) }).catch(() => process.exit(1))"]
      interval: 5s
      timeout: 3s
      retries: 20

volumes:
  mysql_data:
  frontend_node_modules:
```

Compose 不声明 `container_name`，所有容器通过服务名通信，并由顶层项目名 `evalspark` 生成稳定资源名。

## 6. 启动脚本契约

两个脚本只完成以下步骤：

1. 确认根目录 `.env` 存在。
2. 检查 `.env` 的有效配置行，发现值中仍含 `CHANGE_ME` 时明确报错并退出。
3. 确认 Docker 命令存在且 Docker daemon 可用。
4. 确认 Docker Compose 插件可用。
5. 执行 `docker compose config --quiet`，在创建容器前校验变量与 YAML。
6. 执行 `docker compose up --build` 并保持前台日志。
7. 透传非零退出码；不吞掉镜像构建、端口、健康检查或迁移错误。

PowerShell 脚本不得使用 `Start-Process` 创建宿主机前后端进程；Bash 脚本不得创建 `.venv`、运行 `pnpm install` 或使用 `lsof` 自动选端口。

## 7. 实施计划

### 任务 1：用失败测试固定 Docker 开发环境契约

**文件：**

- 新增：`backend/tests/test_docker_development.py`
- 修改：`backend/tests/test_start_local_script.py`
- 修改：`backend/tests/test_react_phase1.py`
- 修改：`scripts/tests/start-local.Tests.ps1`

**接口：**

- 输入：第 4、5、6 节定义的文件与启动行为。
- 输出：在 Docker 实现缺失时失败、实现完成后通过的静态回归测试。

- [ ] 新增 `test_docker_development.py`，读取 Dockerfile、Compose 和 `.env.example`，断言四个服务、固定镜像版本、热更新命令、源码挂载、前端依赖卷、服务名连接、健康依赖和无 MySQL 宿主机端口。
- [ ] 将 `test_start_local_script.py` 改为断言 Bash 脚本拒绝 `CHANGE_ME`，执行 `docker compose config --quiet` 与 `docker compose up --build`，且不包含 `.venv`、`pnpm install`、`find_available_port` 或 `docker compose down -v`。
- [ ] 将 `test_react_phase1.py` 的启动脚本断言调整为 Compose 启动 React 主前端，继续断言未触碰历史 Vue 入口。
- [ ] 将 PowerShell 测试改为断言 `.env`/`CHANGE_ME` 校验、Docker/Compose 检查、前台 `compose up --build` 和破坏性命令禁用。
- [ ] 运行：

  ```powershell
  backend\.venv\Scripts\python.exe -m pytest backend/tests/test_docker_development.py backend/tests/test_start_local_script.py backend/tests/test_react_phase1.py -q
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts/tests/start-local.Tests.ps1
  ```

  预期：测试因 Dockerfile 缺失、Compose 仅有 MySQL、旧脚本仍启动宿主机进程而失败。

### 任务 2：建立后端与前端开发镜像

**文件：**

- 新增：`backend/Dockerfile.dev`
- 新增：`backend/.dockerignore`
- 新增：`frontend/Dockerfile.dev`
- 新增：`frontend/.dockerignore`

**接口：**

- 输入：`backend/pyproject.toml`、`frontend/package.json`、`frontend/pnpm-lock.yaml`。
- 输出：Compose 可复用的 `backend` 与 `frontend` 开发镜像。

- [ ] 按第 5.1 节创建后端 Dockerfile，并排除 `.venv/`、`__pycache__/`、`.pytest_cache/`、`*.pyc` 和测试输出。
- [ ] 按第 5.2 节创建前端 Dockerfile，并排除 `node_modules/`、`dist/`、`.vite/`、`coverage/` 和 `*.tsbuildinfo`。
- [ ] 运行 `docker build --file backend/Dockerfile.dev --tag evalspark-backend-dev backend`。
- [ ] 运行 `docker build --file frontend/Dockerfile.dev --tag evalspark-frontend-dev frontend`。
- [ ] 预期：两个镜像构建成功，构建上下文不包含宿主机依赖目录。

### 任务 3：编排完整 Compose 开发栈

**文件：**

- 修改：`docker-compose.yml`

**接口：**

- 输入：两个开发镜像构建定义、根目录 `.env`、现有 MySQL 初始化 SQL。
- 输出：`mysql → migrate → backend → frontend` 的健康依赖链、两个持久化卷和固定宿主机入口。

- [ ] 按第 5.3 节补齐四个服务、健康检查、端口、挂载和命名卷。
- [ ] 删除固定 `container_name` 与 MySQL 宿主机 `3306` 映射。
- [ ] 运行 `docker compose config --quiet`。
- [ ] 运行 `docker compose config --services` 与 `docker compose config --volumes`，确认服务和卷清单；通过静态测试确认 MySQL 不含 `ports` 且前端代理指向 `backend:8000`。不得把完整 `docker compose config` 输出到终端或回复，避免展开 `.env` 敏感值。
- [ ] 预期：配置校验通过；此任务不执行 `docker compose up`。

### 任务 4：切换环境变量与统一启动入口

**文件：**

- 修改：`.env.example`
- 本机修改：`.env`
- 修改：`scripts/start-local.ps1`
- 修改：`scripts/start-local.sh`

**接口：**

- 输入：Docker daemon、Docker Compose、根目录 `.env`。
- 输出：两个平台统一的 `docker compose up --build` 前台启动行为。

- [ ] 先备份内存中的 `.env` 键值映射，将 `MYSQL_USER` 与 `DATABASE_URL` 用户名统一为 `multichateval`，把 `MYSQL_HOST` 和 `DATABASE_URL` 主机切换为 `mysql`，补充 `BACKEND_PORT=8000`、`FRONTEND_PORT=5174`；不得在命令输出、补丁或回复中展示敏感值。
- [ ] 按第 3.2 节更新 `.env.example` 及中文说明。
- [ ] 将两个启动脚本精简为第 6 节定义的检查与启动流程。
- [ ] 运行任务 1 的目标测试，预期全部通过。
- [ ] 运行 `git status --short --ignored .env`，确认 `.env` 仍被忽略且不会进入提交范围。

### 任务 5：同步维护文档

**文件：**

- 修改：`README.md`
- 修改：`docs/README.md`
- 修改：`docs/architecture.md`
- 修改：`docs/system-features-status.md`
- 修改：`AGENTS.md`

**接口：**

- 输入：已经实现并通过静态验证的 Compose 行为。
- 输出：默认开发方式、架构图、功能状态与接手说明一致。

- [ ] README 将 Docker Desktop 设为唯一默认开发依赖，说明启动、访问、日志、重建、停止和数据卷安全。
- [ ] README 明确 `docker compose down -v` 会永久删除开发数据库卷，并且任何脚本都不会自动执行。
- [ ] `docs/README.md` 将本文档列入当前维护文档。
- [ ] `docs/architecture.md` 增加第 3 节的容器拓扑、迁移门禁和内部网络说明。
- [ ] `docs/system-features-status.md` 将开发环境状态更新为全栈 Docker + 热更新。
- [ ] `AGENTS.md` 删除“Windows 默认连接本机 MySQL”和宿主机依赖安装的过时描述。
- [ ] 使用 `rg` 检查 README、AGENTS 和当前权威文档，不再把本机 MySQL/宿主机前后端启动描述为默认方式。

### 任务 6：完成不修改数据库的静态验收

**文件：** 不新增业务文件，仅验证当前改动。

- [ ] 运行目标后端测试：

  ```powershell
  backend\.venv\Scripts\python.exe -m pytest backend/tests/test_docker_development.py backend/tests/test_start_local_script.py backend/tests/test_react_phase1.py -q
  ```

- [ ] 运行 PowerShell 脚本测试：

  ```powershell
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts/tests/start-local.Tests.ps1
  ```

- [ ] 运行 Compose 配置校验：

  ```powershell
  docker compose config --quiet
  ```

- [ ] 构建 Compose 开发镜像：

  ```powershell
  docker compose build backend frontend migrate
  ```

- [ ] 在不启动 MySQL 的前提下运行前端测试与构建：

  ```powershell
  docker compose run --rm --no-deps frontend pnpm test
  docker compose run --rm --no-deps frontend pnpm build
  ```

- [ ] 在不启动 MySQL 的前提下运行后端全量测试；当前测试通过依赖覆盖、mock session 或纯函数测试隔离数据库：

  ```powershell
  docker compose run --rm --no-deps backend python -m pytest -q `
    --ignore=tests/test_docker_development.py `
    --ignore=tests/test_start_local_script.py `
    --ignore=tests/test_react_phase1.py `
    --ignore=tests/test_clear_builtin_api_keys_script.py
  ```

  上述四组测试依赖仓库根目录的 Docker、前端或 `scripts/` 文件，统一在宿主机执行后端全量 pytest 时覆盖；后端容器只挂载 `backend/`，不为测试扩大运行时挂载范围。

- [ ] 运行 `git diff --check`、`git status --short`、`git status --short --ignored .env` 与 `git diff -- .env`；确认补丁格式正确、`.env` 仍被忽略且没有进入 Git diff。
- [ ] 预期：所有静态测试、配置解析、镜像构建和容器内前端验证通过，且没有启动 `mysql` 或执行 Alembic。

### 任务 7：经再次授权后完成真实运行验收

**前置门禁：** 必须先向老大说明将启动 Docker MySQL，并由 `migrate` 服务对 Docker 数据库执行 `alembic upgrade head`；得到明确授权后才能继续。

- [ ] 运行 `docker compose up --build -d`。
- [ ] 运行 `docker compose ps`，确认 `mysql`、`backend`、`frontend` 为 healthy，`migrate` 以退出码 `0` 完成。
- [ ] 运行 `docker compose logs migrate backend frontend --tail 200`，确认无迁移、连接、代理或热更新错误。
- [ ] 使用 `.env` 中的实际映射端口请求后端 `/api/health`；默认地址为 `http://127.0.0.1:8000/api/health`，确认返回成功。
- [ ] 使用 `.env` 中的实际映射端口请求 React 页面；默认地址为 `http://127.0.0.1:5174`，确认页面可访问。
- [ ] 通过前端同源 `/api/health` 验证 Vite 到后端的 Compose 内部代理。
- [x] 使用 `apply_patch` 在 `backend/app/main.py` 添加中文临时标记，并在 `frontend/src/config/brand.ts` 使用专用临时值；确认 Uvicorn 自动重载、Vite 返回更新模块后立即恢复，并确认没有留下验收改动。
- [ ] 执行普通 `docker compose down`，重新启动后确认 `mysql_data` 仍存在；不得执行 `down -v`。
- [ ] 完成后按老大选择保持开发栈运行或执行普通 `docker compose down`。

## 8. 验收标准

1. 一条启动命令能够构建并运行 MySQL、迁移、FastAPI 和 React/Vite 四个服务。
2. 宿主机无需安装或运行 Python、Node.js、pnpm 和 MySQL。
3. 前端可通过 `5174` 访问，后端健康接口可通过 `8000` 访问，端口允许由 `.env` 覆盖且冲突时不自动漂移。
4. 后端通过 `mysql` 服务名连接 Docker MySQL，MySQL 默认不暴露宿主机端口。
5. Alembic 成功完成后后端才启动；后端健康后前端才启动。
6. 修改 `backend/` 或 `frontend/` 源码能够触发对应服务热更新。
7. 宿主机 `.venv`、`node_modules` 不参与容器运行，锁文件和镜像固定运行时保证依赖一致。
8. 普通停止、重建和重启不删除 `mysql_data`；项目脚本不包含任何自动清卷逻辑。
9. 启动脚本测试、Compose 配置校验、镜像构建、后端目标测试、前端全量测试和前端构建全部通过。
10. README、AGENTS 与当前权威文档描述一致；API 未变化，因此 `docs/api.md` 保持不动；数据库初始化新增 Alembic 基线元数据，因此同步更新 `docs/database.md`。

## 9. 风险与控制

- **首次镜像构建耗时：** Python 与前端依赖需要下载；通过先复制依赖清单再复制源码保留 Docker 构建缓存。
- **Windows 文件监听差异：** Docker Desktop bind mount 可能不稳定传递原生文件事件；前端显式启用 Chokidar polling，后端使用 Uvicorn reload 实测验收。
- **前端依赖卷陈旧：** 锁文件变化后必须重新构建镜像；如依赖卷仍与镜像不一致，仅允许删除 `frontend_node_modules`，不得连带删除 `mysql_data`。
- **数据库凭据 URL 编码：** Compose 不拼接 `DATABASE_URL`，而是读取 `.env` 中完整连接串，避免 shell 插值改变特殊字符；文档继续要求用户名和密码百分号编码。
- **MySQL 保留账号：** 官方镜像不允许通过 `MYSQL_USER` 创建保留管理员账号；开发环境固定使用普通应用账号 `multichateval`，管理员密码仅用于 MySQL 初始化。
- **迁移失败：** `service_completed_successfully` 阻止后端带着不完整 schema 启动，日志保留具体 Alembic 错误。
- **初始化结构与迁移重复：** 初始化 SQL 必须同步写入其对应的 `20260612_01` Alembic 基线，避免在新卷上重复执行已有字段迁移；该契约由自动化测试覆盖。
- **固定端口冲突：** 不自动改端口，避免团队成员看到不同入口；开发者显式修改 `.env` 后重启。
- **数据库误删：** 所有自动化禁止 `down -v`，真实运行验收前再次取得授权，停止/重启只使用保留卷的命令。
- **历史 Vue 代码：** `vue-frontend/` 不进入 Compose 主流程，也不在本次改动范围内。

## 10. 2026-09-01 真实运行验收记录

- `docker compose up --build -d` 已完成；`mysql`、`backend`、`frontend` 为 healthy，`migrate` 以退出码 `0` 完成。
- 首次运行发现初始化 SQL 未标记 Alembic 基线，迁移在重复添加 `users.role` 时失败；已通过失败测试复现，并由初始化脚本写入 `20260612_01` 基线修复。
- 数据库最终版本为 `20260705_03`，共有 17 张表；验收前后均只有匿名占位用户 1 行、评测任务 0 行。
- 后端健康接口、React 页面和前端同源 `/api/health` 代理均返回 HTTP 200。
- 后端源码变更触发 WatchFiles 自动重载；前端源码变更无需重建镜像即可由 Vite 返回更新后的模块；临时验收改动已恢复。
- 普通 `docker compose down` 后重新启动，迁移版本、表数量和数据计数保持一致；未执行 `down -v`，开发栈按老大要求保持运行。
