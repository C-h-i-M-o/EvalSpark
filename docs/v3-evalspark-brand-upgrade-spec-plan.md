# EvalSpark React 品牌升级 Spec 与实施计划

**目标：** 将当前 React 主前端中用户可见的项目名称从 `MultiChatEval` 统一升级为 `EvalSpark`，并通过独立品牌常量模块集中维护 React 组件使用的品牌文案。

**实现方式：** 新增 `frontend/src/config/brand.ts`，导出具有完整 TypeScript 类型的品牌常量；React 页面只导入并渲染这些常量。`frontend/index.html` 无法直接导入 TypeScript 模块，其浏览器标题使用静态 `EvalSpark`，并通过生产构建产物验证标题结果。

**技术栈：** React 19、TypeScript 6、Vite 8、Vitest 4。

## 1. 需求与决策

### 1.1 用户可见结果

- 浏览器标签标题显示 `EvalSpark`。
- 登录页和注册页品牌名称显示 `EvalSpark`。
- 桌面侧栏品牌名称显示 `EvalSpark`。
- 移动端顶部栏和导航抽屉标题显示 `EvalSpark`。
- Logo 的无障碍替代文本显示 `EvalSpark 标志`。
- 不再向用户展示 `MultiChatEval` 或 `MultiChatEval React`。
- 不再把 `React` 技术栈名称作为品牌文案展示给用户。

### 1.2 品牌常量接口

新增 `frontend/src/config/brand.ts`，提供以下只读常量：

```ts
export const BRAND_NAME = "EvalSpark" as const;
export const BRAND_LOGO_ALT = `${BRAND_NAME} 标志` as const;
```

React 组件必须从该模块导入品牌名称和 Logo 替代文本，禁止在 TSX 文件中重复硬编码 `EvalSpark`。

### 1.3 明确不在范围内的内容

- 不修改 `vue-frontend/`；该目录作为历史版本保留，后续不再开发。
- 不修改后端应用名、Judge 提示词、Python 包信息或命令行说明。
- 不修改 MySQL 数据库名、数据库用户、连接字符串或数据库数据。
- 不修改 Docker 容器名和默认环境变量。
- 不修改 HttpOnly Cookie 名，现有登录会话保持兼容。
- 不修改 `frontend/package.json` 中的内部包名。
- 不修改现有 React Logo 图片；该图片不含旧项目名称。
- 不增加环境变量、运行时品牌切换或多品牌能力。

## 2. 文件与职责

| 文件 | 操作 | 职责 |
| --- | --- | --- |
| `frontend/src/config/brand.ts` | 新增 | 唯一维护 React 组件使用的品牌名称和 Logo 替代文本 |
| `frontend/src/config/brand.test.ts` | 新增 | 服务端渲染真实认证页和应用布局，校验用户看到统一的新品牌文案 |
| `frontend/index.html` | 修改 | 设置浏览器标签标题 |
| `frontend/src/layout/AppLayout.tsx` | 修改 | 桌面侧栏、移动端顶部栏、抽屉和 Logo 替代文本统一读取品牌常量 |
| `frontend/src/pages/AuthPage.tsx` | 修改 | 登录页、注册页和 Logo 替代文本统一读取品牌常量 |
| `docs/v3-evalspark-brand-upgrade-spec-plan.md` | 新增 | 合并记录需求、接口、范围、实施步骤和验收标准 |

## 3. 数据、接口与兼容性

- 本次不新增或修改后端 API。
- 本次不新增或修改前端业务数据结构。
- 本次不改变浏览器 Cookie、Local Storage 或 Session Storage。
- 本次不执行数据库迁移，也不读写业务数据库。
- 品牌常量仅参与 React 渲染，不进入请求参数、接口响应或持久化数据。

## 4. 实施计划

### 任务 1：建立品牌常量与回归测试

**文件：**

- 新增：`frontend/src/config/brand.test.ts`
- 新增：`frontend/src/config/brand.ts`

- [x] 先创建 `brand.test.ts`，使用 `renderToStaticMarkup` 渲染真实 `AuthPage` 和 `AppLayout`，断言界面显示 `EvalSpark`、Logo 替代文本为 `EvalSpark 标志`，且不再显示 `MultiChatEval`。
- [x] 运行 `corepack pnpm test -- src/config/brand.test.ts`，确认测试因真实界面仍渲染旧品牌而失败。
- [x] 新增 `brand.ts`，按第 1.2 节定义两个只读品牌常量。

### 任务 2：替换 React 用户可见品牌文案

**文件：**

- 修改：`frontend/index.html`
- 修改：`frontend/src/layout/AppLayout.tsx`
- 修改：`frontend/src/pages/AuthPage.tsx`

- [x] 将 `frontend/index.html` 的标题改为 `<title>EvalSpark</title>`。
- [x] 在 `AppLayout.tsx` 导入 `BRAND_NAME` 和 `BRAND_LOGO_ALT`。
- [x] 用 `BRAND_NAME` 替换移动端品牌、桌面侧栏品牌和抽屉标题，用 `BRAND_LOGO_ALT` 替换两处 Logo 替代文本。
- [x] 在 `AuthPage.tsx` 导入两个品牌常量，用 `BRAND_NAME` 和 `BRAND_LOGO_ALT` 替换登录/注册页品牌文案及 Logo 替代文本。
- [x] 运行 `corepack pnpm test -- src/config/brand.test.ts`，确认品牌回归测试通过。

### 任务 3：完整验证与范围审计

**文件：** 不新增业务文件，仅验证现有改动。

- [x] 在 `frontend/` 执行 `corepack pnpm test`，确认 React 全量 50 项测试通过。
- [x] 在 `frontend/` 执行 `corepack pnpm build`，确认 TypeScript 检查和 Vite 生产构建通过。
- [x] 执行 `rg -n -i "MultiChatEval|MutiChatEval" frontend/index.html frontend/src -g "!*.test.ts" -g "!*.test.tsx"`，确认 React 生产展示层不存在旧名称。
- [x] 执行 `git diff --check`，确认不存在空白或补丁格式错误。
- [x] 检查 `git status --short` 和改动清单，确认没有修改 `vue-frontend/`、`backend/`、数据库、Docker、Cookie 或其他内部标识文件。

## 5. 验收标准

1. 当前 React 主前端所有用户可见品牌名称均为 `EvalSpark`。
2. React TSX 文件通过品牌常量模块使用项目名称，不重复硬编码品牌字符串。
3. 浏览器标题为 `EvalSpark`，页面不显示 `React` 技术栈后缀。
4. React 源码和入口 HTML 中不存在用户可见的 `MultiChatEval` 或拼写错误 `MutiChatEval`。
5. React 全量测试、TypeScript 检查和生产构建通过。
6. Vue、后端、数据库、Docker、Cookie、前端内部包名和持久化数据均未改变。

## 6. 风险与控制

- `index.html` 不能复用 TypeScript 常量，存在静态标题与常量不一致的可能；通过生产构建后检查 `dist/index.html` 的实际标题控制该风险。
- 旧名称仍会保留在明确排除的内部标识和历史 Vue 代码中；验收搜索严格限定 `frontend/`，并排除内部包元数据。
- 本次不改变 Logo 图片，因此不存在图片重制、尺寸变化或缓存兼容风险。
