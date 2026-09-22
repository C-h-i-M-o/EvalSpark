# 系统功能与实现状态

当前执行方式：老大已明确本设备 Docker 不可用，仅检查代码，完成功能后提交 GitHub。本轮未运行测试或构建，不再尝试修复 Docker；下方历史通过记录不覆盖后续新增代码。

新增代码已接入：普通/RAG 单轮有界逐项评分、正式复评保留分段、RAG 资料独立评审及超预算未知、分支歧义双来源引用校验、报告版本3远距离窗口对和精确锚点机会合并。静态审查修正了单轮 unknown 子包规范校验、RAG 结果变量、复评丢分段和三组资料聚合范围。最新失败分支重试/显式跳过已接入双工作台与 HTTP；保留旧回答和费用，新尝试独立上下文与评分，generation_epoch 拒绝旧进程收尾新尝试。迁移20260922_01仅编写，未执行。两窗口关联不能替代任意多窗口全局证明，不同引文的语义问题不自动合并。浏览器、Worker 硬崩溃和真实供应商均未在本轮验证。

以下多轮进度与通过数字为分阶段历史记录；功能现状以本页顶部和合并规格计划顶部为准，历史“未实现”清单不覆盖2026-09-22的代码收尾。

问题解决状态已接入三组协调、计费、报告汇总和 React 展示，仍待联合验收。隔离状态/报告专项9 passed、294 warnings（9.01 秒），验证已修正不抹去历史零分、无效状态仍入账、分歧列未知及重复执行零新增调用。前端新增状态展示和测试尚待 Docker 恢复后执行，浏览器未验收。

跨窗口关系仍为有界实现：版本3在相邻边界之外增加完整非相邻窗口对，最多32次额外提取；预算或次数不足记录缺口，不截断。此前版本2隔离准备/报告7 passed（232 warnings，10.35 秒），验证三次准备、十二次正式分段调用、225 个测试 Token 入账及重复执行零新增调用。版本3尚未运行测试，不能声称全局评分已完成。

上下文状态已接入普通/RAG 实时事件与历史恢复：按模型、RAG 阶段显示压缩标记、估计 Token 与历史读取范围，缺失显示未知，不返回内部消息。状态投影专项后端 2 passed、隔离会话 7 passed（40 warnings）；RAG 实时事件与上下文隔离专项 9 passed（278 warnings，9.68 秒），前端 105 passed。真实浏览器验收尚未完成。

## 2026-09-20 报告与历史入口增量：部分实现

普通/RAG 工作台共用容量提示、预算推荐与输入控件，创建后展示冻结预算；模型可用列表返回容量及输出预留，服务端仍最终校验。前端 102 passed及类型/生产构建通过；隔离容量与会话13 passed；最新后端全量532 passed、115 skipped（20.54 秒）。浏览器实际交互仍待验收。

管理员总上下文容量字段、CRUD 和多轮创建预算校验已接入；容量/会话/迁移隔离 14 passed，前端 99 passed及生产构建通过。新迁移 20260920_01 仅隔离库升级，业务库未升级。未知容量的工作台提示与预算推荐已接入，浏览器容量验收仍待完成。

覆盖审核统计已与对话机会分列，不再计入不适用/未知机会数；覆盖未知仍影响最终评分。报告列表刷新已连接作者今日用量刷新。前端 99 passed、类型/生产构建通过，报告隔离 3 passed，最新后端全量 526 passed、111 skipped（16.02 秒）。浏览器轮询/切页与用量联合效果待验收。

语义报告真实队列执行已验证：独立 Redis 队列、生产 Worker 和 HTTP 假模型，两个用例通过（112.02 秒）。新链路七次请求、168 Token/0.000196 CNY 测试费用入账，重复消息不再调用；生产恢复线程完成失活轮次收尾。临时 Worker 已移除，卷保留。最新全量 525 passed、111 skipped（17.66 秒）。默认 HTTP 发布、硬崩溃重启和浏览器未由此覆盖。

新报告已启用语义准备、原子固定及三组复评；覆盖审核不计入成功机会数，原有跨窗口未知约束继续保留。隔离报告等回归 11 passed，补充故障后的报告专项 3 passed；最近全量 524 passed、109 skipped（17.12 秒）。真实供应商、完整跨窗口关系发现及浏览器联合验收仍未完成。





专用真实队列验收已完成：隔离 Redis、Celery Worker、HTTP 假模型执行正式评分及幂等入账，生产恢复线程自动结束失活轮次，1 passed（53.54 秒）。临时 Worker 已移除，卷保留。最新后端全量 519 passed、106 skipped。默认 HTTP 投递路由、硬崩溃重启及浏览器仍待验收，不能将本次专用队列结果视为全部上线验收。

生成恢复已接入隔离验证：60 秒心跳、四小时失活后的锁内原子收尾、已知费用幂等结算、未知阶段保留和迟到写入拦截。成功分支继续独立历史，恢复不自动重跑模型。最新全量 499 passed、105 skipped；隔离生成/恢复/摘要/历史 18 passed，最终恢复专项 3 passed，未迁移跳过专项 1 passed。真实 Worker 崩溃演练尚未完成。

会话报告装配、提交/分页 API、三组分段评审和 React 面板已接入；历史列表已增加普通/RAG 会话分页入口。报告固定候选分支及截止轮次，显示生成失败和评价限制，支持有界刷新与分段依据。前端 Docker 全量 99 项通过、生产构建通过。本次后端全量 496 passed、100 skipped、144 warnings（22.26 秒）；隔离报告/分段/要求版本/RAG 评分回归 9 passed、275 warnings（10.56 秒）。已验证要求退休边界、全部范围原文、来源白名单和逐次费用。已接入生成率、两种覆盖率、逐组机会和 RAG 趋势快照展示；跨窗口联合判断、解决状态追踪和浏览器联合验收尚未完成，不能宣称完整交付。

## 2026-09-18 普通/RAG 多轮升级：部分实现

普通/RAG 共用多轮 HTTP、独立历史与有界摘要、要求版本、历史引用回溯、新评分及正式复评已接通。RAG 历史资料重新检查作者、分支、实际引用与知识版本后纳入当前证据；旧标签对应关系保留到评分输入。零新检索命中仅在明确历史引用有效时继续，正式复评复用同一材料。

React 双工作台复用 ConversationPanel，支持按模式创建/恢复、分页回答与新评分、正式复评及逐次原文依据。RAG 新建携带知识库 ID，续聊使用持久化模型配置；阶段、检索资料及实时文本复用现有流式卡片逻辑，新建条件不限制已恢复会话。多轮结束与评分刷新同步今日用量。

最新验证记录以本页顶部及合并计划为准；前端最近全量 99 passed、生产构建通过，本轮后端恢复改动未重复运行前端检查。全部 Docker 串行执行，未使用性能豁免。

仍需完成报告全局跨窗口联合判断、问题解决状态追踪、单轮长评分分段、分支歧义要求、失败重试/跳过、上下文容量配置、真实 Worker 恢复演练及浏览器联合验收。报告和历史入口已有部分实现。单元中的环境跳过不视为通过，真实供应商/TEI/容量另行验证；当前不能视为完整交付。业务库未执行 `20260918_01`，不自动迁移或调用真实收费模型。完整范围见 [合并规格与计划](multiturn-spec-plan.md)。

## 2026-09-09 RAG 公开与历史管理补充

已实现：RAG 支持选择公开/私有（默认私有），公开任务向所有登录用户展示完整回答、评分与任务引用证据；知识库和源文档仍私有。普通历史 `/history` 和 RAG 历史 `/rag/history` 为独立页面与导航，作者可在详情修改自己任务的可见性。移除 RAG 资料发送提示与固定私有标签，按钮为“开始评测”。新接口与跨用户权限已通过自动回归；浏览器连接故障，本轮页面实测尚未完成。此记录优先于下方早期“RAG 强制私有”描述。

> 当前状态（2026-09-09）：全局 Embedding API 与本地兼容接入已实现，业务库已备份并迁移，前后端已启动。本文阶段 1—7 的早期冻结/未执行描述为历史记录，最新验证及未覆盖范围以 docs/v3-rag-spec-plan.md 顶部为准。

最后更新：2026-09-03

当前版本：**v2 已结束并冻结**。demo-v1 核心评测闭环保持可用，账号体系、权限控制、公开/私有评测、管理员模型配置、Token 额度和反馈统计等已实现能力继续保留。React 前端已经完成对原 Vue 前端的功能替代，后续新功能和样式维护默认使用 `frontend/`。

本文档根据当前代码实现梳理 MultiChatEval 的系统功能、模块边界和完成情况。状态说明：

- 已实现：代码已经接入主流程或可直接运行。
- 部分实现：已有结构或界面，但能力仍有占位、缺口或未持久化。
- 未实现：当前代码中尚未落地。

## 1. 系统定位

### V3 RAG 实施状态

阶段 7 已补充独立 Docker 验收入口与测试代码，Windows 使用 `scripts/verify-rag.ps1`，Linux/macOS 使用 `bash scripts/verify-rag.sh`，分别提供 `unit` / `integration`。配置解析、PowerShell 语法和 Git 差异检查通过，未启动 Docker 或运行新增测试。阶段 1—6 代码已提交，RAG 整体仍为“代码交付、运行验收未完成”；资源充足设备按 `v3-rag-spec-plan.md` 第 11 节完成回归、浏览器、真实模型和性能门禁。

状态：**RAG 代码已接入主流程，完整联调待验收**。新增 TEI CPU/Qwen3-Embedding-0.6B、Qdrant、Redis、Celery Worker 配置和固定版本只读分词缓存；客户端支持双上限分批、向量校验、错误脱敏及归属/版本过滤。阶段 2 增加私有库创建/列表/修改/删除、文档上传/下载/列表/重试及整库重建作业；每库 100 份、每份 20 MB，管理员无私有内容读取豁免。详见 `v3-rag-spec-plan.md` 的阶段验收记录。

阶段 3 已实现四格式深层解析、来源映射、Qwen Token 切分、租约/冷却/有限重试、幂等索引与三存储清理。阶段 4 已实现逐模型改写、独立 Top-5、版本屏障、历史证据快照、流式生成与分阶段用量。阶段 5 已接入三轮回答质量/忠实度/引用正确性/引用完整性联合评审、Decimal 基础分、反馈重算、`taskType=rag` API 分派、历史筛选和中断恢复；管理员互动明细在 SQL 中排除他人 RAG。阶段 6 新增 React 知识库管理和 RAG 工作台，接入导航、历史类型筛选、每回答独立引用快照、四维评分、分阶段/币种用量、私有评论及取消/过期响应保护。当前 Docker 已关闭，阶段 5—6 测试代码已补充但未执行，前端构建、浏览器、真实模型全链路与部署延期；不能称完整联调已通过。迁移 `20260902_01/02` 已在隔离测试库验证，业务库尚未应用；旧问题/回答/得分不改。Agent 工具评测和安全测试集尚待单独设计。

MultiChatEval 是一个面向多模型问答的对话质量评估系统。用户输入同一个问题后，可以选择多个模型并发回答，系统展示每个模型的回答内容、耗时、输出长度、成本估算和规则评分，帮助用户横向比较不同模型的回答质量。

当前版本优先保证多模型、规则评分、LLM Judge、逐 token 流式展示、历史任务查询和用户反馈的完整链路。系统已经可以从前端发起评测请求，并由后端并发调用真实 OpenAI-compatible 模型接口；各模型生成过程会以 NDJSON 增量事件实时展示，单个模型回答完成后进入“评分中……”，评分完成后更新为最终结果。评测任务、回答、评分、点赞/点踩和公开评论会写入 MySQL，并可在历史任务页继续查看和操作。

当前 React 主前端已落地：`frontend/` 已提供 React 19 + TypeScript + Vite 工程、Tailwind CSS、Ant Design、Recharts、GSAP、Vite `/api` 开发代理、类型化 API 客户端、系统健康检查、登录态恢复、登录、注册、退出、受保护业务路由、基础业务布局、按角色导航、`/` 评测工作台、`/history` 历史任务、`/models` 管理员模型配置、`/users` 用户额度和 `/feedback` 反馈统计页面。评测工作台已支持模型列表、今日 Token、公开/私有、思考模式、LLM 评审、多模型逐 token NDJSON 展示、评分中状态、卡片内 Markdown 与数学公式渲染、`<think>` 默认展开、评分详情和点赞/点踩反馈。历史页已支持分页、详情加载、状态标记、超时提示、反馈操作和公开评论分页、发布、删除。管理员页面已支持模型配置维护、连接测试、用户搜索筛选、服务端分页、封号/解封和普通用户每日 Token 额度调整。反馈统计已支持普通用户个人统计和管理员全局统计、每日趋势、互动明细。React 前端已补充品牌 logo、深色侧栏、主题色层和遵循减少动态效果偏好的 GSAP 页面/卡片/弹窗动画。Windows 使用 `scripts/start-local.ps1`，macOS/Linux 使用 `scripts/start-local.sh`；两者统一在 Docker Compose 中启动 MySQL、迁移、后端和 React 前端。V3 增加 RAG 内网服务；历史 Vue 脚本仅用于回看旧版本，不参与后续开发或本次验收。

## 2. 前端功能

### 2.0 账号与权限

状态：已实现

已实现能力：

- 开放注册、登录、退出和登录态恢复；注册要求二次确认密码，密码至少 8 位且包含数字、小写字母和大写字母。
- 普通用户和管理员导航、页面权限隔离。
- 评测任务支持公开和私有模式。
- 管理员模型配置页面仅管理员可访问。

### 2.1 多模型评测工作台

状态：已实现

入口路径：`/`

入口文件：`frontend/src/pages/EvaluationPage.tsx`

已实现能力：

- 展示“多模型评测工作台”主界面。
- 支持输入用户问题。
- 支持通过复选按钮选择已启用且已配置 API Key 的模型配置。
- 支持 LLM 评审开关的界面交互。
- 点击“开始评测”后调用后端创建评测任务接口。
- 提交按钮会在请求期间进入 loading 状态。
- 输入为空、未选择模型或系统内没有可评测模型时禁止提交。
- 当系统内没有任何已配置 API Key 的模型时，首页会提醒用户进入“模型配置”页面填写自己的 API Key。
- 支持全局“思考模式”开关，开关状态会随评测请求提交给后端。

当前限制：

- 模型推荐属于已冻结的 v2 后续规划，不再继续开发。

### 2.2 模型配置页面

状态：已实现

入口路径：`/models`

入口文件：`frontend/src/pages/ModelConfigsPage.tsx`

已实现能力：

- 侧边栏“模型配置”可进入模型配置管理页面。
- 系统不自动创建内置模型记录。
- 新建配置提供 DeepSeek、MiniMax、GLM、Qwen、Xiaomi MiMo、OpenAI 和 OpenAI-compatible 空白模板。
- 默认展示前三个供应商，其余供应商通过“更多供应商”展开。
- 用户可新增、编辑、启用/禁用和删除 OpenAI-compatible 模型配置。
- 基础区填写供应商、API Key、模型名、展示名和启用状态；Base URL、模型参数、币种及四类价格位于“高级选项”。
- 支持测试已保存配置或未保存草稿配置的连接。
- API Key 输入框留空时保留原密钥。
- 列表只展示密钥状态和掩码，不展示原始 API Key。

### 2.3 用户 Token 额度

状态：已实现

已实现能力：

- 普通用户默认每日 100,000 总 Token，管理员不限额，按北京时间自然日统计。
- 评测页展示今日已用、剩余和每日额度，额度耗尽时禁用提交。
- 同步与 NDJSON 流式接口在任务开始前共用额度检查。
- 每个模型回答持久化时，同一事务写入总 Token 流水并原子更新每日汇总。
- 管理员可在 `/users` 按用户名、角色和状态搜索筛选用户，通过服务端分页查看用户角色、状态、今日用量，封号或解封用户，并调整普通用户每日额度。

### 2.4 请求等待态

状态：已实现

已实现能力：

- 请求期间展示“模型调用中”提示。
- 展示本次模型调用的完成进度。
- 每秒更新等待耗时。
- 为每个待返回模型展示等待卡片和占位动画。
- 等待态中显示临时耗时、输出等待中、成本待估算。
- 已完成模型会立即从等待卡片替换为真实回答卡片。

### 2.5 结果对比展示

状态：已实现

已实现能力：

- 根据后端返回的 `responses` 渲染模型回答摘要。
- 评测页使用平衡摘要网格，历史任务详情页使用单列紧凑列表；两种模式都展示模型名称、状态、分数、耗时、输出、成本和回答预览。
- 回答卡片内直接展示可滚动 Markdown 内容，详情弹窗内展示完整 Markdown 回答、默认展开的“思考过程”、评分详情、点赞/点踩和公开评论。
- 评测页的四个模型固定使用两行两列；五至九个模型按 `3+2`、`3+3`、`3+2+2`、`3+3+2`、`3+3+3` 平衡分行，保证每行铺满。
- 评测网格根据结果容器自身宽度降为两列或单列，而不是只依赖浏览器视口宽度。
- 长回答只在摘要卡中展示固定高度预览，不再直接拉伸评测页或历史详情页。
- 每张卡片展示：
  - 模型名称
  - 调用状态
  - 最终分或失败状态
  - 回答预览
  - 响应耗时
  - 输出 token 数
  - 估算成本
- 摘要卡展示相关性、完整性、清晰度关键评分条；格式与安全性评分在全文详情中展示。
- 摘要卡展示点赞、点踩按钮和计数。
- 调用失败时展示失败状态和后端返回的错误信息。
- 全文详情弹窗展示规则分、LLM Judge 分、基础分、反馈分、最终分、命中项、评审理由、用户反馈摘要和公开评论。
- 支持分页查看、发布和删除公开评论。

当前限制：

- demo-v1 旧匿名数据继续保留，新任务、反馈和评论归属真实登录用户。

### 2.5.1 历史任务分页页

状态：已实现

入口路径：`/history`

入口文件：`frontend/src/pages/HistoryPage.tsx`

已实现能力：

- 侧边栏“历史任务”可进入历史任务页。
- 进入历史页时分页加载最近评测任务。
- 支持切换页码和每页 10/20/50 条。
- Ant Design 使用中文语言配置，分页容量和页码控件使用中文交互。
- 点击历史任务后加载完整回答和评分详情。
- 历史任务详情使用单列紧凑列表，每行展示模型摘要、关键指标、反馈操作和全文入口，不使用多列卡片。
- 历史任务详情页可以继续对回答点赞或点踩。
- 历史任务详情页可以分页查看、发布和删除回答评论。
- 历史任务时间固定按北京时间展示。
- `pending` 历史任务默认展示为“进行中”，创建超过 120 秒后仍未完成才展示为“超时未完成”。
- 对于超时未完成、进行中或无回答任务，详情区会显示明确占位说明，不展示空白页面。
- 分页或详情加载失败时展示错误提示。

### 2.5.2 反馈统计页面

状态：已实现

入口路径：`/feedback`

入口文件：`frontend/src/pages/FeedbackStatsPage.tsx`

已实现能力：

- 所有登录用户均可从侧边栏进入反馈统计页。
- 普通用户查看本人创建任务的评分、收到的点赞/点踩/评论、按模型表现、每日趋势和本人互动汇总。
- 管理员查看全局汇总、按模型表现、每日趋势以及分页互动明细。
- 管理员明细支持按点赞、点踩、评论和模型筛选。
- 支持最近 7 天、30 天和全部历史范围；日期边界按北京时间自然日计算。
- 页面提供加载态、空状态、刷新、响应式布局和 Recharts 交互图表。

当前限制：

- 暂不支持任务类型维度、自定义日期、导出和预聚合缓存。
- 模型推荐逻辑尚未实现，且 v2 冻结后不再继续开发。

### 2.6 Markdown 与思考过程渲染

状态：已实现

入口文件：`frontend/src/components/MarkdownRenderer.tsx`

已实现能力：

- 使用 `markdown-it` 渲染模型回答中的 Markdown 内容。
- 支持自动链接识别、换行、标题、列表、代码块、表格和 `$...$` / `$$...$$` 数学公式。
- 使用 `DOMPurify` 清洗 HTML，降低模型输出导致的 XSS 风险。
- 外部链接自动添加 `target="_blank"` 与 `rel="noopener noreferrer"`。
- 自动解析 `<think>...</think>` 内容，并默认展开展示为“思考过程”面板。
- 未闭合的 `<think>` 内容也会被识别为思考过程。
- 没有正式回答内容时展示“暂无回答内容”。

### 2.7 前端路由与布局

状态：已实现

相关文件：

- `frontend/src/layout/AppLayout.tsx`
- `frontend/src/components/ModelResponseCard.tsx`
- `frontend/src/components/MarkdownRenderer.tsx`
- `frontend/src/routes/RouteGuards.tsx`
- `frontend/src/animations/pageMotion.ts`
- `frontend/src/styles.css`

已实现能力：

- 前端采用统一侧边栏布局。
- 已配置 `/login`、`/register`、`/`、`/models`、`/users`、`/history`、`/feedback` 路由；`/models` 和 `/users` 仅管理员可访问，`/feedback` 面向所有登录用户并按角色分流数据。
- 未匹配路径会重定向到 `/`。
- 模型回答摘要组件支持平衡网格和单列紧凑列表两种模式，分别供评测结果和历史任务详情使用。
- 完整回答、评分详情和评论在同一个详情弹窗中展示，避免主页面被长回答拉伸。
- 使用 GSAP 实现结果进入、等待卡替换、评分条和详情弹窗动画，并根据减少动态效果偏好降低动画强度。
- 桌面端侧边栏固定在可视区域内，“默认流程”保持在侧栏底部；移动端恢复普通文档流。

## 3. 后端 API 功能

### 3.1 应用入口与路由

状态：已实现

相关文件：

- `backend/app/main.py`
- `backend/app/api/routes.py`
- `backend/app/api/v1/health.py`
- `backend/app/api/v1/evaluation.py`

已实现能力：

- 创建 FastAPI 应用。
- 根据环境变量配置 CORS 允许来源。
- 所有业务接口统一挂载在 `/api` 前缀下。
- 已注册健康检查、评测和模型配置路由。

### 3.2 健康检查

状态：已实现

接口：

```http
GET /api/health
```

响应：

```json
{
  "status": "ok"
}
```

测试覆盖：

- `backend/tests/test_health.py` 已覆盖健康检查接口。

### 3.3 创建评测任务

状态：已实现

接口：

```http
POST /api/evaluation/tasks
```

相关文件：

- `backend/app/api/v1/evaluation.py`
- `backend/app/schemas/evaluation.py`
- `backend/app/services/evaluation_service.py`

请求字段：

- `conversationId`：可选，会话 ID。
- `prompt`：用户问题。
- `modelIds`：模型 ID 列表。
- `enableJudge`：是否启用 LLM 评审。启用时评审模型必须是未参与本次测评的空闲模型。
- `enableThinking`：是否启用全局思考模式。

已实现能力：

- 接收前端提交的问题、模型列表和 LLM 评审开关。
- 根据 `modelIds` 从 `model_configs.id` 动态解析要调用的模型。
- LLM 评审候选会排除本次被测模型；如果所有可用模型均被测，前端会禁用 LLM 评审开关并提示用户保留一个空闲模型。
- 未传模型时默认选择已启用的 DeepSeek 和 MiniMax；不可用时回退到前两个已启用模型。
- 如果传入的模型 ID 全部无效，也按同样规则回退。
- 使用 `asyncio.gather` 并发调用多个模型。
- 发送给模型前，会在用户原始问题前拼接系统内置提示词；数据库和接口响应仍保留用户原始问题。
- 根据 `enableThinking` 对所有模型统一发送 `thinking.type=enabled` 或 `thinking.type=disabled`。
- 只要至少一个模型调用成功，任务状态返回 `completed`。
- 所有模型均失败时，任务状态返回 `failed`。
- 返回每个模型的回答、耗时、输出 token、成本估算、状态、规则评分和可选 LLM Judge 结果。
- 创建真实数据库任务记录，并返回真实 `taskId`。
- 将模型回答和规则评分结果写入 MySQL。

当前限制：

- `conversationId` 已在请求模型中定义，会写入任务记录；会话管理功能尚未完善。
- `enableJudge` 默认开启；后端会选择未参与本次测评的空闲模型执行三轮 Judge。少于 2 次 Judge 成功、没有可用评审模型或成功分差超过 2.0 时，会写入对应评分状态，最终分为空，并从统计中排除。
- `POST /api/evaluation/tasks` 仍为同步等待所有模型完成后一次性返回。
- 思考模式不提供思考程度选择。

### 3.3.1 创建评测任务并渐进返回模型结果

状态：已实现

接口：

```http
POST /api/evaluation/tasks/stream
```

相关文件：

- `backend/app/api/v1/evaluation.py`
- `backend/app/schemas/evaluation.py`
- `backend/app/services/evaluation_service.py`

已实现能力：

- 请求字段与 `POST /api/evaluation/tasks` 一致。
- 响应类型为 `application/x-ndjson`。
- 先返回 `task_started` 事件。
- 模型生成过程中持续返回 `model_delta` 事件。
- 单个模型回答完成后立即返回 `model_answer_completed` 事件，前端展示“评分中……”。
- 评分和持久化完成后返回最终 `model_response` 事件。
- 所有模型结束后返回 `task_completed` 事件。
- 单个模型失败只影响该模型事件，不中断其他模型调用。

当前限制：

- 该接口是逐 token 流式返回，单个模型回答结束后会先进入评分中状态。

### 3.4 查询评测任务

状态：已实现

接口：

```http
GET /api/evaluation/tasks/{task_id}
```

已实现能力：

- 从数据库读取评测任务、模型回答和规则评分。
- 不存在的任务返回 404。
- `responses[].id` 使用真实 `model_responses.id`。
- `responses[].modelConfigId` 使用 `model_configs.id`，供前端维持模型顺序。

### 3.4.1 分页查询历史评测任务

状态：已实现

接口：

```http
GET /api/evaluation/tasks?page=1&pageSize=10
```

已实现能力：

- 按 `created_at desc, id desc` 返回历史任务。
- 返回 `items`、`total`、`page`、`pageSize`。
- 每条任务包含任务 ID、状态、问题、创建时间、完成时间和回答数量。

### 3.5 提交用户反馈

状态：已实现

接口：

```http
POST /api/evaluation/responses/{response_id}/feedback
```

请求字段：

- `feedbackType`：反馈类型，当前支持 `like` 和 `dislike`。

已实现能力：

- 反馈会真实写入 `user_feedback`。
- 新反馈写入当前登录用户 ID，旧匿名反馈继续归属 `user_id = 0`。
- 同一用户对同一回答只能保留一个当前反馈。
- 重复提交同一回答的同类反馈会取消该反馈，提交另一类型会在点赞和点踩之间切换。
- 接口会返回 `active` 和当前回答的 `feedback` 状态。
- 接口会重算并持久化最终分，同时返回更新后的 `score`。
- 历史任务详情会返回每条回答的点赞/点踩状态和数量。
- 不存在的回答返回 404，非法反馈类型返回 422。

当前限制：

- 尚未实现模型推荐。

### 3.5.1 回答公开评论

状态：已实现

接口：

- `GET /api/evaluation/responses/{response_id}/comments`
- `POST /api/evaluation/responses/{response_id}/comments`
- `DELETE /api/evaluation/comments/{comment_id}`

已实现能力：

- 评论独立写入 `user_comments`，不与点赞/点踩记录混用。
- 同一用户可以对同一回答发布多条评论。
- 评论正文去除首尾空白并限制为 1–1000 个字符。
- 评论按最新优先分页返回。
- 用户可以硬删除自己的评论，不支持编辑。
- 评论不参与评分。

当前限制：

- 不支持评论点赞、审核和富文本。
- demo-v1 历史匿名评论继续归属 `user_id = 0`。

### 3.5.2 反馈统计

状态：已实现

接口：

- `GET /api/feedback-stats/me`
- `GET /api/admin/feedback-stats`

已实现能力：

- 个人接口只统计当前用户创建任务的表现与当前用户主动提交的互动，不返回其他用户身份明细。
- 管理员接口通过 RBAC 返回全局汇总、模型统计、每日趋势和分页互动明细，普通用户访问返回 403。
- 全局统计包含公开任务、私有任务和 `user_id = 0` 的历史匿名互动。
- 支持 `7d`、`30d` 和 `all`；评分与调用按回答创建时间，互动按各自提交时间统计。
- 点赞率无数据时返回 `null`，Judge 均分忽略无 Judge 的记录，空库返回稳定零值结构。
- 点赞、点踩和评论分别查询后再按模型与日期合并，避免多表联接造成重复计数。

当前限制：

- 不支持任务类型维度、自定义日期、导出、缓存和模型推荐。
- 本次能力直接读取现有表，没有数据库结构变化或迁移。

### 3.6 模型配置接口

状态：已实现

接口：

- `GET /api/models/available`
- `GET /api/admin/model-configs`
- `POST /api/admin/model-configs`
- `PUT /api/admin/model-configs/{model_config_id}`
- `DELETE /api/admin/model-configs/{model_config_id}`
- `POST /api/admin/model-configs/test`

已实现能力：

- 不自动补齐或写入任何供应商模型。
- 支持新增、编辑、删除和启用/禁用 OpenAI-compatible 模型配置。
- 支持温度、最大输出、模型级超时、备注、币种和四类 Token 单价。
- 支持测试模型连接。
- API Key 当前以 `plain:<api_key>` 格式明文落库，业务代码通过统一 helper 保存、读取和掩码展示，保留未来升级为 `enc:v1:<ciphertext>` 的空间。

## 4. 模型调用功能

### 4.1 模型适配器接口

状态：已实现

入口文件：`backend/app/adapters/base.py`

已实现能力：

- 定义统一模型请求对象 `ModelRequest`。
- 定义包含输入、输出、缓存命中、缓存创建和总量的 Token 用量对象 `ModelUsage`。
- 定义模型回复对象 `ModelReply`。
- 定义抽象模型客户端 `ModelClient`，约束所有模型客户端必须实现：
  - `chat`
  - `get_model_name`
  - `estimate_cost`

### 4.2 OpenAI-compatible 客户端

状态：已实现

入口文件：`backend/app/adapters/openai_compatible.py`

已实现能力：

- 兼容 `/chat/completions` 协议。
- 流式评测接口使用 `stream: true` 请求。
- 自动拼接 `{base_url}/chat/completions`。
- 使用 Bearer Token 认证。
- 请求参数包含：
  - `model`
  - `messages`
  - `max_tokens`
  - `temperature`
  - `stream: true`
- 支持通过 `extra_body` 追加模型供应商特定参数。
- 流式解析 `choices[0].delta.content` 作为回答增量，并兼容 `choices[0].delta.reasoning_content`。
- 兼容常见 OpenAI-compatible usage 字段，将输入总量拆分为普通输入、缓存命中和缓存创建。
- 当供应商未返回 usage 时各类 Token 记为 0，不虚构输出 Token。
- 记录模型响应耗时。
- 根据输入、输出、缓存命中和缓存创建四类单价计算分项费用与总费用。
- API Key 或 Base URL 缺失时抛出明确错误。

当前限制：

- 价格由管理员按官方资料填写，系统不内置价格、不换汇。
- 暂未做供应商级别重试、限流、熔断或错误码归一化。

### 4.3 模型运行配置

状态：已实现

入口文件：`backend/app/services/model_config_service.py`

已实现能力：

- 从数据库读取管理员创建并启用的模型配置。
- 当前首页会在系统内没有任何 API Key 时提醒用户先进入模型配置页。
- 每个模型调用使用对应配置的 `temperature`、`max_tokens` 和 `timeout_seconds`。
- 各供应商按 OpenAI-compatible 协议调用。
- 每次评测请求都会根据全局思考模式追加：

```json
{
  "thinking": {
    "type": "disabled"
  }
}
```

关闭思考模式时，`type` 为 `disabled`；开启思考模式时，`type` 为 `enabled`。该字段会以嵌套 JSON `thinking.type` 传输，不使用 `thinkingmode`。第一版不发送思考程度参数。如果某个供应商不支持该字段并返回错误，该错误会作为对应模型的失败结果展示。MiniMax 等 OpenAI-compatible 供应商可能在收到 `thinking.type=disabled` 后仍返回 `<think>` 或 reasoning 内容，当前前端会继续默认展开展示这些内容。

系统内置提示词当前为 9 条通用回答要求，覆盖直接作答、中文表达、结构化格式、不编造、高风险谨慎、安全边界、简洁完整和含糊问题处理。

## 5. 评分功能

### 5.1 规则评分器

状态：已实现

入口文件：`backend/app/services/rule_evaluator.py`

当前评分维度：

- 相关性 `relevance`
- 完整性 `completeness`
- 清晰度 `clarity`
- 格式 `format`
- 安全性 `safety`
- 综合分 `final`

实现规则：

- 评分输入：规则评分前会移除完整的 `<think>...</think>` 区块；若 `<think>` 未闭合，则忽略该标签及其后续内容。数据库、接口和前端仍保留完整原始回答，仅最终回答内容参与规则评分。
- 相关性：使用字符 n-gram 相似度、关键词覆盖、用户问题意图识别、意图覆盖、回答聚焦度、显式要求对齐和离题惩罚，不恢复旧的关键词交集主算法；评分详情会展示识别出的用户意图。
- 完整性：按问题类型检查必要要素，例如解释题的定义/机制、对比题的对象/维度、代码题的代码片段、排错题的路径。
- 清晰度：评估分段、列表/代码/表格/标题结构、句子数量、重复段落、长段落和长文本分段情况。
- 格式：识别表格、代码、JSON、步骤和对比等显式格式要求，严格检查合法 JSON、Markdown 表格和代码块。
- 安全性：使用危险输出控制、拒答质量、高风险领域谨慎性和隐私/凭据保护四类本地信号合成；覆盖网络攻击、恶意代码、凭据泄露、自伤、武器/爆炸物/毒品、违法行为、仇恨骚扰、未成年人性内容、隐私侵犯、普通问题过度拒答和高风险专业建议缺少提醒等情况。
- 命中项明细：每个维度会返回 `score.details`，供前端评分详情弹窗展示。
- 综合分：

```text
final =
  relevance * 0.20
+ completeness * 0.30
+ clarity * 0.20
+ format * 0.15
+ safety * 0.15
```

当前限制：

- 相关性是轻量本地规则评分，不等价于语义模型或 LLM Judge。
- 安全性是轻量本地规则评分，不替代专业安全、医学、法律或金融判断。
- 语言一致性、事实准确性、有用性等更细维度尚未真正参与当前接口返回。
- 事实准确性仍主要依赖 LLM Judge，尚未引入外部事实核验源。

### 5.2 最终分合成

状态：已实现

- 未启用 Judge 时，`baseFinal = ruleFinal`。
- 至少 2 次 Judge 成功且成功分数分差不超过 2.0 时，`judgeFinal` 取成功评分平均值，`baseFinal = ruleFinal * 0.30 + judgeFinal * 0.70`。
- `model_failed`、`judge_failed`、`judge_unstable` 和 `manual_required` 状态下不生成可参与统计的最终分。
- 管理员评分配置页读取词表时会从 `backend/app/services/scoring/default_rule_seed.json` 幂等维护 7 个内置规则词典和 390 条词条，用户第一次部署空库后会自动导入当前完整词表；词表覆盖格式要求、用户问题意图、拒答表达、安全替代建议、高风险领域、专业提醒、危险输出和隐私凭据，代码/格式词表覆盖常见编程语言、脚本、查询语言、前后端框架和测试框架，专业提醒覆盖医疗、法律、金融、安全和心理健康等高风险场景，并会合并历史并发初始化留下的重复默认数据。
- 没有点赞/点踩时，`final = baseFinal`。
- 有反馈时，`feedbackScore = 10 * likeCount / (likeCount + dislikeCount)`。
- 有反馈时，`final = baseFinal * 0.90 + feedbackScore * 0.10`。
- 评论不参与评分。

## 6. 数据库与持久化

### 6.1 数据库结构

状态：部分实现

相关文件：

- `backend/app/models/*.py`
- `docker/mysql/init/001_schema.sql`
- `docs/database.md`

已建模的数据表：

- `users`：用户。
- `conversations`：评测会话。
- `model_providers`：模型供应商。
- `model_configs`：具体模型配置。
- `evaluation_tasks`：评测任务。
- `model_responses`：模型回答。
- `evaluation_results`：评分结果。
- `user_feedback`：用户反馈。
- `user_comments`：公开评论。

已实现能力：

- SQLAlchemy 模型已覆盖核心业务实体。
- Docker MySQL 初始化 SQL 已创建核心表结构。
- 数据库连接和异步 Session 工厂已配置。
- Alembic 基础目录和配置已存在。
- 创建评测任务时写入 `evaluation_tasks`。
- 模型回答完成后写入 `model_responses`。
- 规则评分结果写入 `evaluation_results`。
- 用户反馈写入 `user_feedback` 并归属当前登录用户。
- 历史任务列表和任务详情从数据库读取。

- 点赞和点踩反馈已写入 `user_feedback`，并支持重复点击取消或互斥切换。
- LLM Judge 结果写入 `evaluation_results.judge_score` 和 `evaluation_results.judge_comment`。
- 点赞/点踩变化会重算并持久化 `evaluation_results.final_score`。
- 公开评论写入 `user_comments`，支持分页查询、发布和硬删除。

## 7. 配置与运行

### 7.1 环境配置

状态：已实现

入口文件：`backend/app/core/config.py`

已实现能力：

- 后端固定读取项目根目录 `.env`。
- 从项目根目录或 `backend/` 目录启动都能读取同一份配置。
- 支持配置：
  - 应用名称和环境。
  - 数据库连接。
  - CORS 来源。
- `scripts/clear-builtin-api-keys.py` 用于一次性清空历史数据库中内置 DeepSeek、MiniMax、GLM 的 API Key，避免继续沿用旧的 `.env` 自动导入密钥。

### 7.2 本地运行

状态：已实现基础脚本与说明

已有能力：

- `docker-compose.yml` 保留 `mysql`、`migrate`、`backend`、`frontend` 四个原开发服务，新增 `embedding`、`qdrant`、`redis`、`rag-worker`；RAG 内部服务不影响普通后端启动依赖。
- `scripts/start-local.ps1` 与 `scripts/start-local.sh` 都只依赖 Docker Desktop 和 Docker Compose，可校验 `.env` 后构建并启动完整 React 主前端全栈环境。
- MySQL 只开放 Compose 内部端口，数据保存在 `mysql_data`；Alembic 迁移成功后后端才启动，后端健康后前端才启动。
- 后端与 React 前端均挂载本地源码并保留热更新；Python 依赖位于镜像中，前端依赖位于 `frontend_node_modules` 命名卷中。
- `scripts/start-local-vue.sh` 可自动准备 `.env`、启动 MySQL、安装依赖、执行 Alembic 数据库迁移，并启动后端和历史 Vue 前端开发服务。
- `scripts/verify-react-rewrite.sh` 可统一执行后端测试、React 测试、React 构建、Vue 测试、Vue 构建和 `git diff --check`。
- 后端宿主机端口默认为 `8000`，React 前端默认为 `5174`；可由 `.env` 显式覆盖，端口冲突时不会自动漂移。
- React 前端通过 `frontend/pnpm-workspace.yaml` 固定 `picomatch@4.0.4`，避免依赖解析漂移。
- 历史 Vue 前端可在 `vue-frontend/` 下通过 `pnpm dev` 启动；`vue-frontend/pnpm-workspace.yaml` 的 `onlyBuiltDependencies` 已允许 `esbuild`、`vue-demi` 执行 pnpm 10 必要的依赖构建脚本。
- 前端通过 Vite 代理或同源 `/api` 访问后端接口。

## 8. 测试覆盖

状态：部分实现

已实现：

- 后端健康检查测试：`backend/tests/test_health.py`。
- 评测 API 测试覆盖 Judge 参数校验、历史列表、任务详情和评论接口。
- 评测服务测试覆盖同步创建、逐 token 流式返回、评分中状态、单模型失败隔离、评分持久化、Judge 合成、反馈重算和评论操作。
- 模型配置、API Key、启动脚本、迁移兼容和 LLM Judge 解析均有对应单元测试。
- 规则评分器测试覆盖空回答、排除完整或未闭合的 `<think>` 思考内容、相关性、格式和安全性等主要规则。
- React 阶段一至五结构、启动脚本、API 客户端、导航权限、评测 NDJSON 解析、回答内容处理、历史状态、反馈状态合并、管理员 API 和构建链路已有测试覆盖。

当前缺口：

- 前端已覆盖价格格式、缺失费用类别归零和供应商预设结构；费用浮层等组件交互仍缺少浏览器自动化测试。
- V3 已增加隔离 MySQL 迁移、权限、租约、并发记账及完整索引/API 集成测试；前期已执行项和阶段 5—7 未执行项见合并计划，不能概括为全部数据库验收通过。

## 9. 当前主流程状态

状态：已实现 demo-v1 核心链路

当前实际链路：

```text
用户输入问题
  ↓
前端从 GET /api/models/available 加载可选模型
  ↓
前端选择模型并提交 POST /api/evaluation/tasks/stream
  ↓
后端按 model_configs.id 读取模型配置
  ↓
并发调用管理员启用的 OpenAI-compatible 模型
  ↓
写入模型回答、四类 Token、总 Token、分项费用、总费用、参数快照和调用状态
  ↓
执行规则评分并写入评分结果
  ↓
按模型完成顺序渐进返回结果，任务结束后返回完整任务
  ↓
前端以摘要网格、单列历史列表、Markdown 详情和评分条展示
  ↓
前端历史任务页分页查询任务并加载详情
```

已废除的 v2 规划链路：

```text
结合规则评分、LLM Judge 和用户反馈生成推荐
```

该链路未实现，原 v2 开发计划废除后不再继续开发。

## 10. 后续前端维护建议

v2 新功能开发已结束，React 已完成对原 Vue 前端的功能替代。后续建议：

1. 后续新功能、样式优化和交互修复默认修改 `frontend/`。
2. `vue-frontend/` 仅作为历史版本保留，默认不再承接新功能。
3. Windows 开发环境使用 `.\scripts\start-local.ps1`，macOS 或 Linux 使用 `./scripts/start-local.sh`；两者都启动完整 Docker Compose 开发栈。
4. 启动历史 Vue 版本使用 `./scripts/start-local-vue.sh`。
5. V3 统一使用 `scripts/verify-rag.ps1` / `bash scripts/verify-rag.sh`；旧 `verify-react-rewrite.sh` 包含宿主机运行时和 Vue 检查，仅保留为历史脚本，不用于本次验收。
6. 后续如要移除 Vue 前端，需要单独任务决策。

## 2026-09-09 V3 恢复开发状态

已实现管理员全系统单一 Embedding 配置、密钥保留/轮换/清除、连接测试、OpenAI-compatible 云端/本地接入、字符切块、按模型配置隔离向量、全局变更强制重建、并发保存保护与 React 管理入口。内置 TEI 为可选 profile。

已实际完成：基线后端 332 passed/43 skipped、前端 61 passed 与构建；隔离 MySQL 迁移及回归 38 passed；新协议/配置定向测试；真实存储/Worker 的两候选评测与四格式索引重建删除共 3 项联合用例。后续完整复验结果以合并计划顶部为准。确定性模型只验证接口和链路，不代表真实模型效果；Embedding API 费用尚未计价，界面明确显示已知小计。

## 2026-09-09 交互回归更新

已实现普通/RAG 今日 Token 共用刷新逻辑，修复管理员恒显示 0；四类二级导航与模型/Embedding 合并页签已落地，RAG/知识库/设置 UI 已统一。RAG 禁止自我评审（包含同供应商同模型的重复配置），默认开启思考模式。验收证据见 v3-rag-spec-plan.md 的评测一致性改进记录；浏览器工具连接失败，未将实际点击验收记为通过。
