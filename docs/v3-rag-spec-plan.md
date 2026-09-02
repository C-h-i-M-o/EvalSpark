# EvalSpark V3 RAG 需求规格与分阶段实施计划

> 执行约定：使用 `superpowers:executing-plans` 按阶段执行、验证与汇报；未经老大单独同意，不使用子代理。规格和实施计划合并在本文，不另建平行版本。

**目标：** 在现有多模型评测中增加私有知识库 RAG 链路，支持文档切分、私有 Embedding、逐模型检索、引用溯源和忠实度评估。

**架构：** 保留 FastAPI、MySQL 和 React 主链路；新增 Qdrant、TEI CPU、Redis 和 Celery Worker。文档处理异步执行，评测复用现有任务、NDJSON、历史与反馈接口；检索证据按回答独立保存。

**技术栈：** Docker Compose、Python、FastAPI、SQLAlchemy、Alembic、pytest、Celery、Redis、Qdrant、Hugging Face TEI、Qwen3-Embedding-0.6B、React、TypeScript、Vitest、pnpm。

**阶段 2 构建约定：** 新增 multipart 依赖后，首次镜像构建遇到 PyPI 大包读取超时。后端依赖安装使用 BuildKit 下载缓存及 120 秒读取超时，缓存不进入镜像或 Git；仍从官方包源下载。验收须包含完整镜像构建及 `pip check`，临时安装依赖的测试不能替代该验收。

**规格位置：** 本文第 1—8 节；实施与验收位置：第 9—12 节。

**状态：** 2026-09-02 用户已确认本文并批准开发；阶段 0 提交为 `63d7777`，阶段 1—2 代码和验收完成（提交 ID 见 Git 日志），阶段 3 代码已实现，按下述资源受限约定交付；阶段 4—7 继续实施。按用户要求直接使用当前 `dev`，不创建 worktree；允许依赖/镜像/模型下载及新增服务、测试容器运行和测试数据写入。各阶段单独提交，不自动推送。

**资源受限验收调整（2026-09-02）：** 老大明确允许跳过部分测试和本地部署，优先完成开发，后续在资源充足设备验收。当前不再启动重型 Embedding 或完整开发栈；保留测试代码及运行说明，在资源允许时执行轻量检查。阶段代码可在记录已执行证据和延期项后提交、进入下一阶段，不以真实模型全链路阻塞开发。阶段 7 区分“测试及交付材料已准备”和“实机验收通过”；未执行项不得勾选通过。业务库升级、外部模型调用费用和私有资料传输的授权边界不变。

## 全局约束

- 用户可见品牌使用 EvalSpark；内部包名、数据库名、账号、旧数据标识保留原名。
- 仅维护 `frontend/` React 主前端；不修改 `vue-frontend/`。
- 本文只覆盖 V3 第一部分 RAG；Agent 工具评测和 AI 安全测试集另行设计，不提前建设通用插件、工具执行或测试集平台。
- React 新增 `.tsx` 仅承载 UI 和方法导入；行为、请求、状态转换放入 `.ts`，禁止无必要的新增 `any`。
- 优先复用现有代码、开源解析器和官方客户端，不手写 PDF/DOCX 解析器、向量数据库或 Embedding 推理服务。
- 前后端、数据库、迁移、测试及新增服务均通过 Docker 执行，不恢复宿主机 Python/Node/pnpm 开发流程。
- 文件修改需明确授权；数据库写入、迁移、测试数据创建和删除需明确且范围匹配的授权。
- 阶段 0 仅创建文档；后续开发已获批准。业务库结构迁移前仍需列明实际迁移内容、备份和恢复方案，不把测试写入许可当作清空业务数据的许可。
- 已获准下载依赖、镜像和模型，并运行新增服务与隔离测试。不得自行写入真实模型配置、发送用户私有文档到未经指定的外部服务或进行无关数据库操作。
- 不执行 `docker compose down -v`，不删除既有 Volume，不重命名或重建现有数据库，不自动推送、合并或改写 Git 历史。
- 详细功能状态、API、数据结构、架构随实施阶段同步更新；不得把本计划中的能力提前标记为“已实现”。

## 1. 已确认的范围与验收目标

### 1.1 功能范围

| 编号 | 已确认要求 | 验收所在阶段 |
| --- | --- | --- |
| R01 | 可复用的用户私有知识库；所有内容访问校验归属 | 2、6、7 |
| R02 | 支持可提取文本的 PDF、DOCX、TXT、Markdown；不做 OCR | 3、7 |
| R03 | 默认约 800 Token 切块、120 Token 重叠，知识库级可配置；使用 Qwen 分词器 | 1、3 |
| R04 | 每文件 20 MB、每库 100 文档、100,000 块上限 | 2、3、7 |
| R05 | CPU 私有部署 Qwen3-Embedding-0.6B，1024 维 Cosine；模型首次下载后缓存 | 1、7 |
| R06 | Qdrant 单集合 `rag_chunks_v1`，租户、知识库、文档隔离 | 1、3、4、7 |
| R07 | Redis + Celery 异步解析、索引、重试、重建和清理，MySQL 保存权威状态 | 1、3、7 |
| R08 | 每个候选模型独立改写一次查询、独立检索 Top-K=5；不设相似度阈值 | 4、7 |
| R09 | 每个回答保存查询、检索证据、位置、相似度与引用快照 | 4、6、7 |
| R10 | 必须选择空闲 Judge，三轮共同评审回答质量、忠实度及引用质量 | 5、7 |
| R11 | 使用专属 RAG 评分公式，失败或不稳定无最终分、不计入得分统计 | 5、7 |
| R12 | RAG 强制私有，复用任务、流式输出、历史、反馈和评论 | 4、5、6、7 |
| R13 | 分阶段记录模型 Token、费用、耗时；防止重试重复入账 | 4、5、7 |
| R14 | 删除当前知识库内容后保留已完成任务的独立历史证据 | 3、4、6、7 |
| R15 | 检索依赖不可用时普通评测仍可用；兼容旧任务和旧评分 | 1、5、7 |

### 1.2 明确不做

不做 OCR、网页抓取、URL 导入、知识库分享、混合检索、重排序、多查询扩展、HyDE、人工相关性标注平台、知识图谱或跨库检索。不将相似度当作正确率、概率、检索 Precision/Recall；本期没有计算这些指标所需的标准相关文档标注。

不执行用户文档内的指令，不运行文档脚本、宏或工具。RAG 输入隔离和隐私保护属于本期必要边界，但不代表已完成 V3 第二部分安全测试集。

## 2. 知识库与文档规则

### 2.1 额度与切分

- 上传大小在接口和流式读取时双重校验。本计划把界面“20 MB”明确为十进制 `20,000,000` 字节，前后端使用同一常量，不混用 MiB。
- 一个知识库最多 100 份未完成物理清理的文档，包括排队中、失败和删除中的记录；库行锁下预留名额，防止并发超限。
- 一库发布的块总量上限 100,000；重建时按目标版本统计，不把临时旧版本与新版本相加作为业务配额。临时存储峰值另行监控。
- 默认 `chunkSize=800`、`chunkOverlap=120`；接口校验 `128 <= chunkSize <= 2048`、`0 <= chunkOverlap < chunkSize`。这两个范围是本计划的实施校验值，非模型理论上限。
- 按标题、段落、句子逐级切分，长单元最后按 Qwen Token 边界切开。重叠可能因自然边界略小于配置值，但最终块不得超过配置 Token 数；不能以字符数假冒 Token。
- 修改切分配置后原索引不可继续用于新评测，必须完成整库重建。界面提交前展示影响范围。
- 上传新文档、重建、删除期间，将知识库标为不可开始新评测；完成发布后恢复。解析失败的文档明确列出，不伪装成已索引。

### 2.2 文件与来源

使用 `pypdf` 提取 PDF 文本、`python-docx` 提取 DOCX 段落及表格内容、标准库读取 UTF-8/UTF-8 BOM TXT 与 Markdown。非 UTF-8 文本返回明确错误，不静默替换乱码。

按扩展名、媒体类型和文件结构联合校验；DOCX 限制解压后累计大小与条目数，拒绝宏文档、加密文档、压缩炸弹及伪装类型。PDF 无可提取文本时返回“暂不支持扫描件，请上传可提取文本的 PDF”，不自动联网 OCR。解析在 Worker 中受时间和内存约束，不能在 API 请求里执行重型解析。

原文件位于 `rag_documents` Volume，由服务端生成存储键；原始文件名仅作展示，禁止用其拼接磁盘路径。上传先写受控临时文件，校验通过后原子改名；中途失败清理当前临时文件，不碰其他文档。

来源位置使用有类型的结构：

| 文件 | 来源结构 | 展示含义 |
| --- | --- | --- |
| PDF | `kind=pdf, pageStart, pageEnd` | 从 1 开始的页码 |
| DOCX | `kind=docx, blockStart, blockEnd, blockType` | 按正文顺序编号的段落或表格块；不伪造分页 |
| TXT/Markdown | `kind=text, lineStart, lineEnd` | 从 1 开始的原文行号 |

跨段或跨页块保存覆盖区间；保留提取文本到来源的映射，规范化空白后仍能追溯。Markdown 是文本内容，不跟随外链、不执行 HTML。

### 2.3 删除与历史

删除先在 MySQL 标记 `deleting`、递增内容修订号并阻止新检索，再由 Worker 清理对应 Qdrant 点、MySQL 当前块和原文件。只有三个存储的清理均确认完成后才标记 `deleted`；失败保留任务和可重试状态，不向用户报告已物理删除。

历史任务中的证据片段、文件名和位置是独立快照，不通过级联删除移除。用户确认文案必须说明：**“删除后不可再检索该文档；已完成评测中的引用片段仍会保留。”** 原文件下载在删除后失效，历史引用仍可读；两种行为不能混淆。

## 3. Docker 与依赖设计

### 3.1 服务职责

| 服务 | 职责 | 数据和网络 |
| --- | --- | --- |
| 既有 `mysql` | 权威元数据、状态、文本、证据、评分和账单 | 保留现有 `mysql_data`、数据库名与账号 |
| 既有 `backend` | 鉴权、知识库接口、评测编排、查询 Embedding | 挂载原文件卷；不加载 Embedding 权重 |
| 既有 `frontend` | React 管理与评测界面 | 保留现有端口和依赖卷 |
| 新增 `embedding` | TEI CPU 模型推理 | `rag_model_cache`；仅 Compose 内网 |
| 新增 `qdrant` | 1024 维 Cosine 检索 | `qdrant_data`；仅 Compose 内网 |
| 新增 `redis` | Celery broker，不作业务状态源 | `rag_redis_data` 开启持久化；仅 Compose 内网 |
| 新增 `rag-worker` | 解析、切分、批量 Embedding、索引和清理 | 复用后端镜像；原文件卷；模型缓存只读 |

不新增宿主机服务或额外模型初始化服务。新增服务不得成为普通后端健康检查的无条件依赖；模型下载失败不应阻断登录、普通评测或旧历史读取。RAG 接口另行返回依赖就绪状态。

### 3.2 模型、缓存与版本

- 模型 ID 固定 `Qwen/Qwen3-Embedding-0.6B`，输出固定 1024 维；TEI CPU 镜像从官方 `cpu-1.9` 系列验证，不使用 GPU 镜像。
- 首次启动允许的下载行为必须在实际执行前获得授权；成功后使用 `rag_model_cache`。TEI 显式配置 `--huggingface-hub-cache /data`，Worker 同一缓存挂载只读。
- 模型 revision 使用不可变提交而非浮动 `main`。阶段 1 核对模型仓库提交、所需文件和 TEI 兼容性后，将实际 SHA 写入配置样例及版本清单。
- Worker 使用 `huggingface_hub` 的本地缓存解析和 `tokenizers` 加载同一 revision 的 `tokenizer.json`；`local_files_only=True`，禁止 Worker 重复下载模型或加载权重。缓存缺失时等待/失败可见，不退回其他分词器。
- 查询使用 Qwen 的指令/查询格式，例如 `Instruct: 根据问题检索能够支持回答的文档片段\nQuery: 文档中的报销申请需要哪些材料？`；指令固定、查询取实际改写结果。文档 Embedding 不加查询指令。
- 明确关闭静默截断，校验输入长度、向量数量、1024 维和有限数值；模型返回错误维度时不创建不同规格集合、不覆盖旧向量。
- Qdrant、Redis、TEI 镜像及新增 Python 依赖在阶段 1 从官方版本中选择，检查兼容性和公开安全公告，固定版本与镜像 digest，并在构建记录中写明实际值。这里不把未经构建验证的版本宣称为已验证基线，不使用 `latest`。

### 3.3 资源与降级

初始 Worker 并发为 1、预取为 1，Embedding 每批最多 16 块，同时设置按 Token 的批量上限；TEI CPU 线程及批量配置保守起步，依据 i5-1345U/16 GB 实测调整。100,000 块是容量上限，不是 CPU 索引时延承诺。

服务超时有上限，重试限于超时、暂时不可用等瞬态错误；文件损坏、格式不支持、超限、向量维度错误为永久失败。错误响应不泄漏内部地址、原文、凭据或上游完整响应。

Redis 不提供“业务恰好一次”。Celery 使用延迟确认和有界重试；visibility timeout 与任务时间上限配套，不能仅靠把超时设得极大掩盖恢复问题。正确性由 MySQL 作业状态、版本条件和幂等写入保证。

## 4. 数据模型与一致性

以下为拟新增结构，实际 Alembic revision 在实施前重新核对唯一 head；不编辑已运行的历史迁移，不修改初始化脚本去重建现存数据库。

| 表/字段 | 主要内容与约束 |
| --- | --- |
| `knowledge_bases` | `id, user_id, name, description, chunk_size, chunk_overlap, status, content_revision, error_code, created_at, updated_at`；按用户索引 |
| `knowledge_documents` | `id, knowledge_base_id, user_id, original_name, storage_key, media_type, size_bytes, content_hash, status, index_revision, chunk_count, error_code`；存储键唯一 |
| `knowledge_chunks` | `id, document_id, knowledge_base_id, user_id, index_revision, chunk_index, text, token_count, source_json`；`(document_id,index_revision,chunk_index)` 唯一 |
| `rag_jobs` | UUID 主键；`knowledge_base_id, document_id, operation, target_revision, status, stage, processed_count, total_count, attempt, lease_until, error_code, timestamps`；同操作目标和版本幂等 |
| `evaluation_tasks.task_type` | 非空，默认 `chat`；新 RAG 为 `rag`，不改旧记录原问题、归属、可见性或分数 |
| `rag_response_details` | `response_id` 唯一；知识库 ID/名称快照、内容版本、文档版本清单、改写查询、证据、阶段用量、Judge 轮次、RAG 分项、基础分和公式版本等 JSON/数值字段 |

`rag_response_details` 对回答建立关系；知识库/文档标识在证据里仅作来源标识，历史记录不得依赖知识库外键级联删除。`score_version=rag-v1`、`embedding_revision` 和切分配置随快照保存。JSON 使用 Pydantic 严格结构定义，不能成为无类型的任意数据桶。

### 4.1 索引生命周期

知识库状态为 `empty/indexing/ready/reindex_required/failed/deleting/deleted`：无已发布文档为 empty；有活动索引作业为 indexing；改切分参数为 reindex_required；没有活动作业且存在完整已发布文档时可为 ready，失败文档仍单独展示；整库重建失败为 failed，不使用配置不匹配的旧索引。

文档状态：`queued → parsing → embedding → indexing → ready`；任一阶段可到 `failed`。删除为 `deleting → deleted`。作业状态为 `queued/running/succeeded/failed`，`stage` 保存解析、切分、Embedding、写索引或清理的具体进度。上传、修改切分配置、重建请求和删除在事务内递增知识库 `content_revision`；同库作业串行执行，活动作业全部结束后再计算库的可用状态。

1. 接口在库行锁内检查归属、容量、活动任务与版本，落元数据和 `rag_jobs` 后提交，再发送 broker 消息。不得持有数据库锁等待模型、文件解析或 Qdrant 网络调用。
2. 单个库同一时刻只执行一个索引类作业；其他作业排队。Worker 获取有期限的作业租约，以短事务更新进度，崩溃后可重领。
3. 块 ID 与 Qdrant point ID 根据文档 ID、目标索引版本和块序号确定，重试采用 upsert。Payload 至少含 `user_id, knowledge_base_id, document_id, index_revision, chunk_index`，不存原文副本。
4. 创建上述过滤字段索引；用户字段作为租户索引使用。查询过滤器由服务端生成，不能接受客户端任意 Qdrant Filter。
5. MySQL 先保存目标版本块，分批写 Qdrant；核对数量并确认写入后，再在版本条件成立的事务里发布文档 `ready`。任一中间失败不能发布部分索引。
6. 已发布文档清单由 MySQL 构造：每项为文档 ID + `index_revision`；查询显式限定清单中的组合。旧版本残留不能参与检索，也不能先全库 Top-5 再过滤导致正确证据被丢弃。
7. 重建先撤销可用状态、更新目标版本，再处理新块；成功切换后异步清理旧版本。旧 Worker 提交前核对目标版本，不能覆盖新版本状态。
8. 删除标记优先于索引发布。清理作业等待正在持租约的写入退出；旧作业每批写入和发布前核对删除/版本状态，最终清理再按文档过滤删除全部版本并确认，防止晚到 upsert 留下孤儿。
9. Worker 启动及定时恢复循环读取 MySQL 中已提交但未发布到 broker 的排队作业、过期租约作业并重投；不因 Redis 丢消息永久卡住。此循环由同一 Worker 进程的受控恢复入口触发，不另建业务调度平台。

临时磁盘不足、块超限等失败保留明确状态；可重试清理自己的半成品。不删除用户其他知识库数据，不把 Qdrant 的最终一致性描述成 MySQL 跨存储事务。

### 4.2 评测快照与并发修改

任务开始固定库的 `content_revision` 和可检索文档版本清单。每个模型独立改写、Embedding、检索，再从 MySQL 读取命中的文本与位置。

所有候选完成检索或明确失败后，用短事务再次核对库归属、就绪状态与内容版本，将成功候选的证据分别保存到回答快照；该屏障完成后才开始回答生成。这样同任务候选使用相同资料版本，但不共享检索结果。

如果期间上传、重建或删除改变库版本，本次未固定的证据作废，返回“知识库内容已变化，请重新评测”，不拼接两个版本，不静默改查最新库。已经固定的快照允许本次生成和评审继续；后续删除不影响这些历史证据。

每份证据保存 `label, documentId, documentName, chunkId, indexRevision, text, similarity, source`，`label` 从 `S1` 顺序编号，最多 5 条。失败候选保留自己的失败阶段，不取消其他候选。

## 5. API、流式事件和权限

### 5.1 知识库接口

所有接口复用 Cookie 登录与用户状态校验；`user_id` 从当前登录用户取得，不接受客户端指定。不存在与无权访问均返回 404，防止枚举。

| 方法与路径 | 请求/响应与状态 |
| --- | --- |
| `POST /api/knowledge-bases` | `name, description?, chunkSize=800, chunkOverlap=120`；201 返回库元数据 |
| `GET /api/knowledge-bases` | `page,pageSize`；只返回自己的库及索引状态 |
| `GET /api/knowledge-bases/{knowledgeBaseId}` | 元数据、已发布块数、文档数、可用性和错误摘要 |
| `PATCH /api/knowledge-bases/{knowledgeBaseId}` | 修改名称/描述或切分参数；改切分参数使库不可评测，等待显式重建 |
| `GET /api/knowledge-bases/{knowledgeBaseId}/documents` | 分页文档、各自状态、当前作业进度；供轮询使用 |
| `POST /api/knowledge-bases/{knowledgeBaseId}/documents` | multipart 单文件；202 返回 `documentId,jobId,status=queued` |
| `POST /api/knowledge-bases/{knowledgeBaseId}/documents/{documentId}/retry` | 失败文档重试；202 返回现有或新目标版本作业；活动作业不重复创建 |
| `POST /api/knowledge-bases/{knowledgeBaseId}/reindex` | 202 返回整库重建作业；重复请求复用活动作业 |
| `DELETE /api/knowledge-bases/{knowledgeBaseId}/documents/{documentId}` | 202 返回清理作业；重复删除幂等 |
| `DELETE /api/knowledge-bases/{knowledgeBaseId}` | 202 返回清理作业；包括当前文档，不级联历史快照 |
| `GET /api/knowledge-bases/{knowledgeBaseId}/documents/{documentId}/download` | 仅归属者下载原文件；删除开始后 404，响应为 attachment |

字段错误沿用 422；超大文件 413；不支持的文件 415；容量/索引状态冲突 409；依赖不可用 503。业务错误增加稳定 `code` 与中文 `message`，不改变既有普通评测错误结构。

### 5.2 评测请求与响应

沿用 `POST /api/evaluation/tasks` 与 `POST /api/evaluation/tasks/stream`。新增可选 `taskType`，缺省 `chat`；`rag` 必须传 `knowledgeBaseId`、有效候选模型和不参与候选的 `judgeModelId`，且 `enableJudge=true`。

```json
{
  "taskType": "rag",
  "knowledgeBaseId": 12,
  "prompt": "文档中的报销申请需要哪些材料？",
  "modelIds": [3, 5],
  "enableJudge": true,
  "judgeModelId": 8,
  "enableThinking": false,
  "visibility": "private"
}
```

RAG 的有效可见性由服务端强制设为 `private`，即使旧客户端带入 `public` 也绝不发布；响应明确返回 `private`。普通 `chat` 仍沿用原规则。库无内容、未就绪、无空闲 Judge 等在创建任务和进入 NDJSON 前拒绝，不制造空任务或先返回 200 再报前置错误。

任务列表/详情增加 `taskType`；`GET /api/evaluation/tasks` 增加可选 `taskType=chat|rag` 过滤，不传保留现有全部可见任务行为。回答增加可选 `rag` 对象，普通任务为 `null`；内容对应第 4.2、6、7 节的结构，历史重读必须返回同样的快照和基础分。

引用读取直接复用已鉴权的任务详情中的 `response.rag.evidence`，不另开未鉴权静态文本地址；原文件下载使用独立受保护接口。

### 5.3 NDJSON 扩展

保留既有 `task_started, model_delta, model_answer_completed, model_response, task_completed` 事件及字段。在任务事件中加入 `taskType`，新增下列事件：

```ts
type RagStage = "rewriting" | "retrieving" | "answering" | "judging";

interface RagStageEvent {
  type: "rag_stage";
  modelConfigId: number;
  stage: RagStage;
}

interface RagRetrievalEvent {
  type: "rag_retrieval";
  modelConfigId: number;
  rewrittenQuery: string;
  evidence: RagEvidence[];
}
```

`RagEvidence` 的准确字段见第 4.2 节，前端使用来源类型判别联合。检索事件仅在快照固定之后发送。阶段失败通过最终 `model_response.rag.failureStage/errorCode` 表达；持久化成功后才能发送最终模型结果。

必须给现有 `mergeStreamEvent` 增加显式类型分支，不能让新增事件落入旧代码最后的 `event.task` 分支。继续使用已有流式批处理、取消与最终刷新行为，不能把每个字符更新扩散为整页重渲染。

### 5.4 隐私与内容边界

- RAG 的任务、回答、引用、原文下载、反馈和评论均仅归属者可访问；管理员身份不是内容读取豁免。管理用户/模型和运行状态不意味着能读用户文档。
- 管理员反馈统计允许继续展示无正文的聚合计数/分数，但互动明细不得返回其他用户 RAG 的 prompt、评论、回答、文件名或片段；在数据库查询层过滤，不能只在 UI 隐藏。
- RAG 评论复用原接口但继承私有任务访问控制，不显示“公开评论”；普通任务的公开评论行为不变。
- 文档、查询和候选输出均视为不可信输入；使用固定系统指令与清晰的数据边界，JSON 结构化 Judge 结果严格校验，Markdown 沿用净化渲染，不使用原始 HTML。
- 私有 Embedding 不等于完全离线。开始评测前明确提示：**“检索片段将发送给所选回答模型与评审模型；若使用外部 API，片段将离开本地部署环境。”** 不发送整个原文件，不暗示提示词隔离能完全消除注入风险。
- 不记录完整文档、片段或上游请求体到普通日志；状态错误使用脱敏错误码。API Key、文件磁盘路径与容器配置不返回浏览器。

## 6. 逐模型 RAG 执行与用量

### 6.1 调用顺序

```text
原始问题 + 同一知识库版本
  → 各候选模型独立改写为一条查询
  → 本地查询 Embedding → 各自按授权过滤器检索 Top-5
  → 各自证据落库，同任务版本复核
  → 各候选依据自身证据流式回答，使用 [S1]…[S5]
  → 规则评分 + 空闲 Judge 三轮联合评审
  → 专属 RAG 公式 → 持久化 → 历史/反馈
```

改写是一次模型请求，只输出一条非空查询；失败不回退原问题、不调用多个查询。检索不足 5 条按实际条数回答；零条直接失败，不无证据生成。相似度不设阈值；同分时使用稳定次序便于复核。

生成和 Judge 都接收固定的原始问题与已保存证据，不能在评审时再次检索变化后的知识库。原问题在 `evaluation_tasks.prompt` 原样保留，改写内容仅保存在 RAG 明细。

局部失败阶段为 `rewrite/embed/retrieve/snapshot/generate/judge`。前五类错误使用现有 `model_failed` 得分状态并附阶段原因；Judge 失败/不稳定使用既有 `judge_failed/judge_unstable`。已产生回答不能因 Judge 失败被清空。

### 6.2 Token、费用与耗时

复用现有 `ModelClient`、`ModelReply`、`ModelUsage` 和费用计算，不另建候选模型适配层。改写、回答和三轮 Judge 分别保存模型配置快照、输入/输出/缓存 Token、耗时、费用及币种；Judge 解析失败仍保留已经返回的用量。

本地 Embedding 只记录输入 Token 与处理耗时，不伪造外部 API 费用或计入外部模型配额。`rag.stageUsage` 保存各阶段，`rag.externalTotalTokens` 汇总外部调用；费用按币种分组，不能直接把人民币和美元相加。

为兼容现有展示，回答顶层 Token/费用/耗时字段继续代表候选的回答生成阶段；RAG UI 另外展示全链路汇总及明细，并标明范围。候选模型生成耗时不得用包含 Judge 的总耗时替代后继续标为“回答耗时”。

当前 `token_usage_logs` 对 `response_id` 唯一。因此 RAG 最终入账按回答汇总已知外部用量，仅调用一次账单写入；不能为改写和三轮 Judge 分别插入相同 `response_id`。每次阶段结果先保存在回答明细，最终账单与终态在同一事务内提交，通过回答锁及已有唯一约束避免重放重复累计。

RAG 回答记录在阶段执行前创建，异常结束也可保存已有用量。进程恢复只补记已持久化的用量、结束中断任务，不自动重复已经可能计费的外部模型调用。上游未返回或进程在响应持久化前崩溃时用量标记未知，不能宣称精确为零或绝对不丢账。

额度沿用现有“启动前检查、结束后计入北京时间当日”的规则，不扩大为额度预留/强制中断系统；并发或长任务可能超出剩余额度，界面不承诺硬上限。普通评测原有账单逻辑不变。

## 7. Judge、评分和统计

### 7.1 三轮联合评审

同一空闲 Judge 执行三轮，每轮同时输出四个 0—10 分维度：`answerQuality, faithfulness, citationCorrectness, citationCompleteness`，并给出逐断言证据明细。不是先做三轮质量再做三轮 RAG。

每轮断言结构包含 `claim, evidenceLabels, invalidCitationLabels, supported, needsCitation, citationSupported, reason`。质量评审沿用现有评价目标；忠实度只衡量是否被给定资料支持，不等价于外部世界事实正确。

引用正确性必须判断引用片段能否支持所连断言，不能只检查 `[S1]` 是否存在。引用完整性判断需证据支撑的断言是否引用。Judge 自己在 `evidenceLabels` 伪造未知证据、输出非有限数值、缺字段或越界分数时，该轮无效，不夹紧分数掩盖非法输出。候选回答中的未知标签则记入 `invalidCitationLabels` 并扣引用分，不能把候选引用错误混同为 Judge 解析失败而跳过评价。

无可评估断言或没有适用引用评价项时返回明确不可评分原因，作为无效轮处理；不因分母为零自动满分。回答需要依据却未引用时引用完整性可以为 0，不能以“没有引用”为由跳过扣分。

至少两轮完整有效才可评分；四个维度分别计算有效轮的 `max-min`，**任一维度大于 2.0** 判定 `judge_unstable`，等于 2.0 仍稳定。维度均使用同一组完整有效轮求均值，不能各维度挑有利轮次。

少于两轮有效为 `judge_failed`。失败和不稳定均保留原始结构化轮次、脱敏解析错误及范围，`final=null, excludedFromStats=true`；不以 0 代替缺失，也不因点赞恢复最终分。

### 7.2 公式与精度

```text
RagFinal = 0.50 × Faithfulness + 0.30 × CitationCorrectness + 0.20 × CitationCompleteness
BaseFinal = 0.20 × RuleFinal + 0.30 × JudgeFinal + 0.50 × RagFinal
FeedbackScore = 10 × LikeCount / (LikeCount + DislikeCount)
Final = BaseFinal                                      # 没有反馈
Final = 0.90 × BaseFinal + 0.10 × FeedbackScore          # 有反馈
```

其中 `JudgeFinal` 是有效轮的 `answerQuality` 均值。使用 Decimal 计算，保留未显示舍入的分项/基础值；最终数据库展示分按两位小数、四舍五入保存。反馈重算从原始基础值出发，不能反复使用上次最终分。

验算：Rule=8、Quality=7、Faithfulness=9、Correctness=8、Completeness=7 时，RagFinal=8.3，BaseFinal=7.85；只有一赞时 Final=8.065，展示/保存最终分为 8.07。

普通评测维持当前 `0.30 × RuleFinal + 0.70 × JudgeFinal` 及原有无 Judge 规则，不回算历史任务。RAG 明细持久化 `ragFinal, baseFinal, scoreVersion`；序列化与反馈重算必须按 taskType 分流，避免旧 schema 的默认公式覆盖 RAG 值。

### 7.3 统计边界

仅 `scored` 且未排除的 RAG 回答进入得分聚合。错误调用仍可进入调用数/失败数，但不进入平均分分母。全局统计只展示允许的聚合信息；正文与互动详情遵守第 5.4 节。不能把“有评分”描述成检索准确率。

## 8. React 页面与模块划分

### 8.1 页面

- `/knowledge-bases`：自己的库、容量、文档上传、处理进度、失败原因、重试、切分参数、重建与删除确认。轮询在离开页面时停止；旧请求不得覆盖切换后的库。
- `/rag`：问题、已就绪知识库、候选模型、空闲 Judge、思考模式与外部 API 提示；隐藏公开开关，默认明确“私有”。展示改写、检索、回答、评分阶段及逐回答证据。
- `/history`：增加 chat/rag 筛选；RAG 详情展示原问题、模型独立改写、来源、相似度、引用定位、四项 Judge 结果、失败原因和费用明细；普通历史保持现状。

复用现有布局、导航、表单、模型卡片、Markdown 净化和流式批处理。引用按钮打开当前回答快照中的对应片段；引用无效时显示异常提示，不能跳到其他回答的同名 `S1`。加载、空库、依赖不可用、失败、删除中均有明确状态。

### 8.2 文件职责

| 新增路径 | 职责 |
| --- | --- |
| `backend/app/schemas/knowledge_base.py` | 知识库、文档、作业和来源结构 |
| `backend/app/schemas/rag.py` | 证据、阶段用量、Judge 轮次、RAG 详情 |
| `backend/app/models/knowledge_base.py` | 知识库、文档、块、作业模型 |
| `backend/app/models/rag.py` | 回答快照与评分明细模型 |
| `backend/app/api/v1/knowledge_bases.py` | 授权、请求校验与响应映射 |
| `backend/app/services/knowledge_base_service.py` | 库/文档生命周期、归属和事务 |
| `backend/app/services/rag/clients.py` | TEI HTTP 与 Qdrant 官方客户端的窄封装 |
| `backend/app/services/rag/documents.py` | 原文件安全存储、解析、来源映射和 Token 切分 |
| `backend/app/services/rag/indexing.py` | 作业执行、版本发布、重建与清理 |
| `backend/app/services/rag/evaluation.py` | 改写、检索、快照、回答与阶段用量编排 |
| `backend/app/services/rag/judge.py` | 三轮结构化评审与专属分数计算 |
| `backend/app/worker.py` | Celery 配置、任务入口和恢复循环 |
| `frontend/src/api/knowledgeBases.ts` | 知识库 API，复用 client 中现有鉴权请求基础 |
| `frontend/src/features/knowledge-bases/types.ts`、`useKnowledgeBases.ts` | 类型及管理页行为 |
| `frontend/src/features/rag/types.ts`、`rag.ts`、`useRagEvaluation.ts` | 证据/状态类型、纯状态转换、评测行为 |
| `frontend/src/pages/KnowledgeBasesPage.tsx`、`RagEvaluationPage.tsx` | 两个新页面 UI |
| `frontend/src/components/RagEvidencePanel.tsx`、`RagScoreDetails.tsx` | 证据与评分展示 |

新增包目录包含必要的 `__init__.py`。既有 `evaluation_service.py` 只增加分派、持久化/读回、权限和反馈接点，不把解析器和索引 Worker 塞进这个大文件。不存在实际复用需求时不增加 Repository 基类、通用工作流引擎或插件注册器。

## 9. Git 阶段与交付门禁

当前检查基线：`dev`，HEAD `e31f3a4`（Docker 全栈开发环境）；其前的 `879c059` 为品牌升级。编写本文前工作区和暂存区干净。实施前再次核对，不用本段代替实时 Git 检查。

采用 Conventional Commits：`类型(范围): 中文说明`。每个阶段的业务实现、测试及对应说明组成一个可审阅的本地提交；红色测试只在工作过程中出现，不提交已知失败状态。发生阻塞时保留未完成阶段，不为凑提交数宣称完成。

| 阶段 | 独立交付物 | 计划提交标题 | 状态 |
| --- | --- | --- | --- |
| 0 | 合并规格/计划、授权边界与验收矩阵 | `docs(rag): 明确 V3 RAG 设计与分阶段实施计划` | 本次文档基线 |
| 1 | Docker 依赖、类型化客户端与分词缓存接入 | `build(rag): 增加私有检索基础设施与客户端` | 代码和验收完成 |
| 2 | 私有知识库与文档管理、增量模型迁移 | `feat(rag): 新增私有知识库与文档管理` | 已完成并验证（仅隔离库迁移，未业务部署） |
| 3 | 可恢复的异步解析、切分、索引和清理 | `feat(rag): 实现异步索引与可恢复生命周期` | 代码交付；四格式真实模型全链路延期 |
| 4 | 逐模型检索回答内核、快照和用量 | `feat(rag): 实现逐模型检索回答与证据快照` | 未开始 |
| 5 | 三轮评分、正式 API 接通、历史与反馈一致性 | `feat(rag): 接入忠实度引用评审与评分持久化` | 未开始 |
| 6 | React 知识库、RAG 工作台和历史详情 | `feat(rag): 完成知识库与 RAG 评测界面` | 未开始 |
| 7 | Docker 集成、恢复/权限验证及交付记录 | `test(rag): 完成 Docker 集成验收与交付文档` | 未开始 |

阶段 1—3 不开放 RAG 任务执行入口；阶段 4 只交付可测试的内部链路，在阶段 5 评分和持久化闭环完成后才开放 `taskType=rag`，不向用户返回临时伪分数。阶段 6 才加入导航入口。

每阶段提交前按路径检查差异，只暂存本阶段文件；禁止 `git add .` 混入其他任务。每次记录提交 ID、通过的验证和未覆盖事项；不自动 push。旧版本回退要考虑新增数据，不能通过数据库 downgrade 或删卷“回滚”数据。

## 10. 逐阶段实施任务

### 阶段 0：设计基线

- [x] 读取现有 Docker、评测、账单、前端流式类型与 Git 状态，确认仅 React 和内部标识保留。
- [x] 将已确认范围、数据/接口、错误与恢复规则、阶段和验收合并到本文。
- [x] 自检需求覆盖、公式样例、文件路径、类型命名与授权门禁；暂存差异检查通过，确认只有本文进入本次提交。

交付动作：形成阶段 0 本地提交，请老大复核文档；实际提交 ID 以 Git 日志和交付回复为准，不在提交自身内回填哈希。

### 阶段 1：Docker 与客户端

**文件：** 修改 `docker-compose.yml`、`.env.example`、`backend/pyproject.toml`、`backend/app/core/config.py`，复用现有 `backend/Dockerfile.dev`（无需修改）；新增 `backend/app/services/rag/__init__.py`、`clients.py`、`backend/app/worker.py` 和 `backend/tests/test_rag_clients.py`、`test_rag_configuration.py`。新增依赖为 `qdrant-client`、`celery[redis]`、`huggingface-hub`、`tokenizers`；HTTP 复用已有 `httpx`。同步 Docker、开源复用、架构、功能状态、README/AGENTS 与本文版本记录。

**接口：** `EmbeddingClient.embed(texts: list[str], kind: Literal["query", "document"]) -> list[list[float]]` 为异步方法；`load_tokenizer() -> tokenizers.Tokenizer` 只读本地缓存。`VectorClient.search(user_id: int, knowledge_base_id: int, versions: list[tuple[int, int]], vector: list[float], limit: int = 5)` 为异步查询，返回包含 chunk ID、版本和相似度的类型化列表；不返回任意用户传入的过滤器。

本阶段实施参数：TEI/Qdrant/Redis 采用 Compose 内网地址；后端与 Worker 只读挂载模型缓存到 `/data`，原文单独挂载到 `/documents`。查询指令纳入 Token 计数，每批最多 16 条、默认总计 2048 Token，超出单批 Token 上限的单条输入直接拒绝，不截断。网络超时默认 60 秒。Qdrant 集合及维度固定，不随任意客户端请求变化。

TEI 1.9.3 资源实测调整：初始 8192 Token CPU 预热已占满 4 GiB 容器；32K 方案不进入实机运行。根据[官方启动实现](https://github.com/huggingface/text-embeddings-inference/blob/v1.9.3/router/src/lib.rs)与[预热实现](https://github.com/huggingface/text-embeddings-inference/blob/v1.9.3/backends/src/lib.rs)，改为服务端/客户端共用 `RAG_EMBEDDING_MAX_BATCH_TOKENS=2048`，服务端 `--auto-truncate true` 仅用于允许低于模型 32K 的运行上限。项目始终调用 `/embed` 并显式发送 `truncate=false`；[官方请求处理](https://github.com/huggingface/text-embeddings-inference/blob/v1.9.3/router/src/http/server.rs)优先使用请求字段，超限由客户端或 TEI 拒绝，不能通过省略字段或改用 `/v1/embeddings` 绕过。提高上限必须单独验证资源，不代表 32K 能力已通过。

2026-09-02 固定版本：Qdrant 服务/客户端 `1.19.0`；TEI `1.9.3`（CPU 镜像系列 `cpu-1.9`）；Redis 服务 `7.2.16-bookworm`；Celery `5.6.3`、redis-py `6.4.0`、huggingface-hub `1.29.0`、tokenizers `0.23.1`。新增依赖已在 Python 3.12 后端镜像成功安装。模型 revision 为 `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`；官方公告核对与限制见 `open-source-reuse.md`。

实际拉取并固定的 `linux/amd64` 镜像 digest：

| 镜像 | SHA256 |
| --- | --- |
| TEI `cpu-1.9` | `c26a226262ad4ff3330fb30b76653c1bb65da2fcf413b92284545a010e0a8a48` |
| Qdrant `v1.19.0` | `6c0652f8d6925b22f2f6f0e0a5365a6c9dbc8768bd6e70ccc1cdc14847e452a0` |
| Redis `7.2.16-bookworm` | `e17e3a1993da428251cbd88dbdb3de8c8d4007f840d7350eb17a2d8695fa705f` |

阶段 1 验证记录（2026-09-02）：

- 客户端/配置由预期红色测试转绿（48 项），覆盖实际请求结构、错误响应、超时、数值溢出和越权/过期元数据。
- Docker 全量后端回归：`201 passed, 4 skipped, 6 warnings`。4 项跳过原因是测试容器不安装 Docker CLI；将宿主机实际解析并脱敏的 Compose JSON 通过 stdin 传入断网测试容器，单独执行相同 4 项契约，全部通过。6 项 warning 为既有 Starlette/httpx 弃用提示与测试 JWT 短 key，不是新增失败。
- 后端新增依赖镜像构建成功，源码按开发 Compose 的 bind mount 方式验证。无业务库迁移、无业务表写入、未调用收费外部模型 API；API/数据库说明已核对，本阶段无需改动。
- 独立 `--network none` 容器中的真实 Uvicorn `/api/health` 返回 `200 {"status":"ok"}`，验证后已停止并自动移除该临时容器；该检查不代替普通评测真实模型端到端回归。
- 真实 Qwen 分词器在后端、Worker 的只读共享缓存均加载成功。首次权重下载约 1029 秒；重建 TEI 容器后权重命中缓存（日志耗时约 58 微秒），无需重下权重。TEI 启动仍会检查部分仓库文件，本阶段不宣称无网络冷启动通过。
- TEI `/info` 确认版本 `1.9.3`、固定模型 SHA、`float32`、`last_token` pooling、2048 输入上限；CPU 后端实际内部批次限制为 4（客户端请求可含 16 条，由服务排队执行）。2048 配置加载约 19 秒、预热约 68 秒，预热采样约 2.64 GiB，不是内存峰值测量。
- 非敏感样本“北京是中国的首都。”/“香蕉是一种水果。”及查询“中国的首都是哪里？”返回三个 1024 维向量，范数约 1，相关/无关相似度约 `0.6616 / 0.1790`。两次 Embedding 请求及健康/超限检查合计约 0.869 秒；这不是大文档或并发性能基准。
- 超限输入在客户端直接拒绝；绕过客户端向 TEI 发送 `truncate=false` 也返回 `422`，没有静默截断。初次实测脚本曾误期望 `413`，按服务实际验证错误码修正断言后通过，未改动服务错误处理。
- Qdrant `/readyz` 返回 200，Redis PING 与 Celery inspect ping 返回 PONG，Worker 正常在线。未创建业务向量集合或登记文档作业，这些属于后续阶段。
- 完成当前阶段代码自审、README/AGENTS 和配套文档一致性检查；未使用子代理、未修改 Vue、未推送远端。

- [x] 写客户端测试，以 `httpx.MockTransport` 截获 TEI 请求，验证 query 有指令、document 无指令、截断关闭；对 1023 维、NaN、数量不符响应断言失败。
- [x] 在 Docker 单元测试环境运行新增测试，确认失败来自尚缺实现，而非错误数据库配置。
- [x] 用官方客户端/HTTP API 实现上述窄接口，加入内部服务地址和有界超时配置；普通 health 不探测 TEI。配置私有 Volume 与固定版本流程。
- [x] 在获取下载/构建许可后，核对官方版本和公告，固定 digest/revision，构建镜像；未获许可则停在此门禁，不宣称客户端实连通过。
- [x] 运行单元测试和 Compose 静态校验，保存返回向量维度、缓存复用和断开 TEI 后普通 health 的授权实测记录，再审查本阶段差异并提交。

核心拒绝测试应包含实际断言：

```python
# validate_vectors 位于 clients.py，嵌入响应进入后续步骤前必须调用。
def test_rejects_wrong_embedding_dimension():
    with pytest.raises(ValueError, match="向量维度"):
        validate_vectors([[0.0] * 1023], expected_count=1)
```

### 阶段 2：知识库、文档 API 与数据结构

**文件：** 新增第 8.2 节的两个 schema、两个 model、知识库 API 和 service；`documents.py` 先实现存储部分。修改 `backend/app/models/__init__.py`、`backend/app/models/evaluation.py`、`backend/app/schemas/evaluation.py`、`backend/app/api/routes.py`、`backend/pyproject.toml`（增加 `python-multipart`）；新增 `backend/migrations/versions/20260902_add_rag_knowledge_tables.py`（revision `20260902_01`，父版本实施前核对）。新增独立 `docker-compose.rag-test.yml` 的测试 MySQL/runner 基础配置。测试 `backend/tests/test_knowledge_base_api.py`、`test_knowledge_base_service.py`、`test_rag_storage.py`。更新 `docs/api.md`、`docs/database.md`、`docs/system-features-status.md`。

**接口：** `KnowledgeBaseCreate(name, description, chunkSize, chunkOverlap)`；`require_owned_knowledge_base(db: AsyncSession, knowledge_base_id: int, user_id: int)` 返回已鉴权库，否则抛统一未找到异常。`store_upload(upload: UploadFile) -> StoredDocument` 返回随机存储键、字节数、类型和内容哈希。作业创建只落权威状态，执行依赖阶段 3。

实施细节（2026-09-02，基线 `9627894`）：

- 当前迁移唯一 head 为 `20260705_03`；新 revision `20260902_01` 只新增五张 RAG 表和 `evaluation_tasks.task_type`。旧初始化 SQL 的关联主键为 BIGINT，新外键按真实数据库类型保持一致，不照搬旧 ORM 的隐式 Integer。证据表仅关联回答，不以外键关联知识库/文档。
- 名称去除首尾空白后为 1—120 字符、描述最多 2000 字符；新请求拒绝未知字段，尤其不接受客户端 `userId`。PATCH 未传字段保持原值，切分字段显式 null 无效，两个切分参数合并现值后再次校验。
- 文件读取在授权后执行。使用 Starlette `MultiPartParser`，仅允许一个名为 `file` 的文件、无额外表单字段；受限异步输入流最多读取 20,000,000 字节加 64 KiB multipart 开销。文件本身再按实际字节计数限制 20 MB，忽略不可信的 Content-Length 或上传元数据不能绕过限制。
- 文件复制、哈希与轻量类型检查在线程池中进行，不在数据库行锁内执行。原名规范为展示名，磁盘只用服务端 UUID 存储键。PDF 检查文件签名；DOCX 检查 ZIP 结构、必需条目、加密/宏、重复路径、最多 1000 条目和 100 MB 解压总量；文本校验 UTF-8/UTF-8 BOM。深层解析及扫描件识别交给阶段 3 Worker。
- 文件写入受控目录后再锁库行计数并提交元数据/作业；并发上传不得超过 100 份未清理文档。事务提交异常后重新核实文件引用，只清理已确认无引用的本次文件；连接故障无法确认时保留待检查，避免提交成功但响应丢失造成误删。提交成功之后的队列故障不能删除文件或把已接受请求改成失败。
- 作业使用由库、文档（整库为 0）、操作和目标版本生成的确定性 UUID，数据库保存 `dispatched_at`。阶段 2 尚无索引任务注册时不向 Worker 投递未知任务，响应诚实保持 queued/dispatchPending；阶段 3 注册任务后恢复循环补投。
- 同库多个上传可以排队；切分修改及整库重建与活动索引冲突时返回 409，整库重建期间不插入新上传/单文档重试。重复重建、重试和删除复用相同活动作业；删除立即阻止下载，但物理清理由阶段 3 完成。
- 修改操作在取得库行锁前结束鉴权的只读事务，避免 MySQL REPEATABLE READ 的旧快照掩盖其他请求刚提交的作业；不只刷新 ORM 对象。删除不清除 `reindex_required/failed` 标记；文档进度按当前版本查询，并关联活动整库作业。
- 新增测试优先使用受控临时文件和数据库替身；并发配额与真实 SQL 外键/迁移最终在独立 `evalspark-rag-test` MySQL 验证。不得仅用内存锁测试声称 MySQL 行锁有效。业务库升级另行报告，不执行既有迁移历史的 downgrade。

- [x] 先写 schema、两用户越权、管理员无豁免、超大文件、路径穿越、重复删除、并发配额测试；使用临时目录和独立测试库，不接触业务数据。
- [x] 运行测试确认预期红色，再实现最小 API、存储与事务；生成增量迁移，只在已获授权的隔离测试库执行。
- [x] 校验缺省任务类型是 chat，新字段不改旧任务；本阶段请求 schema 的 taskType 只允许 chat，显式拒绝未开放的 rag，不能忽略该字段后按普通公开任务执行。阶段 5 再扩为完整联合类型。容量预留与元数据事务失败时只清理当前且确认无引用的临时文件。
- [x] 使用假 broker 与独立数据库连接验证“数据库先提交、消息后发布”及发布失败可恢复；状态必须诚实为排队，不能标记索引成功。
- [x] 建立第 11.3 节约束下的独立测试 Compose，不读取业务 `.env`；实际迁移在隔离测试库写入授权后验证。文档更新为“知识库管理部分实现”；本阶段按表中 Conventional Commit 单独提交，不推送。

阶段 2 验收记录（2026-09-02）：

- 独立项目 `evalspark-rag-test` 的 MySQL 从原初始化基线升级到 `20260705_03`，写入测试用旧任务/回答/7.85 分，再升级 `20260902_01`；新表存在，旧值与默认 `chat` 保持一致。未对业务 `mysql` 执行迁移、备份或写表。
- 真实 MySQL 验证同库 99 份时两个并发上传只有一个成功；验证鉴权快照不掩盖新提交作业、PATCH 合并参数、重建/失败重试/删除幂等、队列通知先后顺序和失败保留、提交确认丢失时保留已引用文件。
- API 验证登录门禁、所有者完整管理流程、其他管理员全部资源路径 404、原文附件及字段脱敏。存储验证实际 20 MB 边界、伪造大小、UTF-8 分段边界、路径/符号链接、DOCX 结构/宏/压缩流损坏、multipart 多字段/多文件/超限/断流清理。
- 完整后端（含隔离 MySQL 测试）：`docker compose --env-file .env.example -f docker-compose.rag-test.yml run --rm runner python -m pytest -p no:cacheprovider -q`：**264 passed，4 skipped，24 warnings**。4 项跳过源于 runner 无 Docker CLI；另将宿主机实际 Compose JSON 脱敏后传入断网测试容器，原 4 项契约全部通过。警告来自既有 Starlette TestClient、Alembic 配置和旧用户模型 UTC 默认值。
- 仅在容器验证，未安装宿主机 Python/Node，未调用收费模型，未修改 Vue 或 React 页面。自审按技能清单执行，遵守用户约束未启用子代理；修正了旧快照竞态、重建进度、重建标记误清除与不确定提交后误删的测试发现。
- 首次依赖构建在 PyPI 下载读取超时；增加 BuildKit 缓存和 120 秒超时后构建成功，`pip check` 无损坏依赖。最终源码再次构建成功，缓存复用后的依赖安装步骤约 129 秒；镜像配置 ID 为 `sha256:d21d25897ae76cd1baff277f25e988ab20b953b9a4fef845755b402b6110221a`。普通健康检查在断网容器返回 200，知识库 OpenAPI 和仅允许 chat 的任务 schema 验证通过。
- 断网容器执行默认完整测试（不设置集成开关）：**252 passed，16 skipped，1 warning**；12 项真实 SQL 测试按开关跳过，4 项因无 Docker CLI 跳过。没有用跳过结果替代上述真实 MySQL/Compose 验证。Git 差异检查通过，React/Vue 无本阶段改动；Embedding 与 Worker 暂停以释放内存，模型缓存/文档/业务卷均保留，Qdrant、Redis 和独立测试 MySQL 保持运行。

```python
def test_rejects_overlap_not_smaller_than_chunk_size():
    with pytest.raises(ValidationError):
        KnowledgeBaseCreate(name="制度库", chunkSize=800, chunkOverlap=800)
```

### 阶段 3：异步解析、索引和恢复

作业实施约定：租约 300 秒，每 30 秒续租；失联后额外等待 300 秒再重领，覆盖有界外部请求的在途窗口。每次领取递增 `attempt`，旧领取代次不得更新进度或发布；自动重试最多 3 次。瞬态写入结果不确定时保留同库冷却窗口，不允许删除任务立即越过在途写入。人工重试失败清理会生成递增目标版本的新作业，保留旧作业及其代次，不重置旧 `attempt` 造成代次复用。

同库排队、重试冷却及运行状态均由 MySQL 决定，Redis 仅通知。完成向量及文本数量核对后发布；旧版清理属于同一异步作业的收尾，收尾结束前知识库不开放新评测。整数 `user_id` 使用 Qdrant `IntegerIndexParams(is_principal=True)` 组织租户数据，其他四个过滤字段建整数索引；不把只适用于 keyword 的 `is_tenant` 错用于整数。

阶段 3 起 Worker 启动不以 Embedding 健康为门槛，模型离线时仍能处理清理与恢复；索引任务按依赖错误码有限重试。Redis/Qdrant 仍按 Compose 就绪条件启动，普通后端仍不等待 RAG 服务。

真实 Qwen 分词实测：切分库只统计正文 Token，Qwen 推理还追加 1 个 EOS；切分容量需扣除 `num_special_tokens_to_add(False)`，最终仍以含特殊 Token 的完整编码复核，不静默截断。

存储实测补充：79,203 字节的空白密集文本可被真实 Qwen 切为一个不超过 2048 Token 的块，但 MySQL `TEXT` 写入报 1406。因此阶段 3 新增 `20260902_02`（父版本 `20260902_01`），只将 `knowledge_chunks.text` 扩为 `MEDIUMTEXT`，不改历史迁移、不截断正文；仅在隔离测试库执行，业务升级仍遵循备份审批门禁。

实施细化（2026-09-02，基线 `78788c8`，进行中）：

- 解析复用 `pypdf==6.16.2`、`python-docx==1.2.0`；切分复用 `semantic-text-splitter==0.32.0` 的 Hugging Face Tokenizer 接口，不引入 LangChain 服务、远程解析/OCR或额外模型。文本与 Markdown 使用各自的自然边界切分器，再独立复核完整 Qwen Token 数和来源覆盖。
- `SourceBlock` 除正文和类型化来源外，仅携带 Markdown/标题提示；由源块拼接的字符区间映射到输出块的原始行、页、DOCX 逻辑块区间。DOCX 段落/表格按正文次序提取，表格内递归读取嵌套表格，合并单元格不重复拼接；不伪造 Word 页码。
- 解析在 Worker 子进程执行，120 秒墙钟超时、768 MiB 地址空间上限，输出最多 20,000,000 字符；超限明确失败，不截断当成功。TXT/Markdown 保留一份全文及行区间，切分时用字符偏移换算精确行号，避免大量短行变成海量 Python 源块。PDF 加密文档拒绝，无可提取文本返回扫描件提示；DOCX 复用上传的 ZIP 结构限制，XML 解析不加载外部实体。原文仅经本地进程管道传递，不写日志。
- 子进程的资源限制只在 Linux Docker 内设置；不恢复宿主机解析。纯解析/切分测试使用临时合成资料，真实 Qwen 分词验收只读现有缓存卷；禁止再次下载另一版分词器作为降级。
- Worker 租约、版本检查、幂等写入与删除恢复按第 4.1 节实现；不得将纯解析测试通过标成完整索引联调通过。按资源受限约定，代码交付和延期实机验收分别记录，每阶段代码提交一次。

**文件：** 完成 `documents.py`、`indexing.py`、`worker.py`，新增窄职责模块 `rag/parsing.py`、`rag/jobs.py`、`rag/recovery.py`，补充 `knowledge_base_service.py` 生命周期接点；新增 `20260902_expand_rag_chunk_text.py` 迁移。在 `backend/pyproject.toml` 增加 `pypdf`、`python-docx`、`semantic-text-splitter`。扩展 `docker-compose.rag-test.yml` 的独立 TEI/Qdrant/Redis/Worker 及测试卷。新增文档、向量、作业、索引、Worker、块存储和真实生命周期测试。更新架构、数据库、API、开源复用与功能状态文档。

**接口：** `parse_document(path: Path, media_type: str) -> list[SourceBlock]`；`SourceBlock(text: str, source: SourceLocation)`；`split_blocks(blocks: list[SourceBlock], tokenizer: Tokenizer, chunk_size: int, overlap: int) -> list[DocumentChunk]`；`DocumentChunk(text, token_count, source)`；`run_rag_job(job_id: str) -> None` 由 Worker 调用。第 2 阶段来源 schema 提供 `SourceLocation`。

- [x] 在临时目录中构造四种最小文档；先写中文/英文/长句/表格/跨页位置和 Token 上限测试，验证扫描件、损坏 DOCX、超限解压失败。
- [x] 运行红色测试；使用开源库实现解析和来源映射，用共享 Tokenizer 完成自然边界递归切分。
- [ ] 用假 TEI/Qdrant 测试批次中断、重复消息、MySQL 提交前崩溃、写入后未发布、过期租约、重建旧作业和删除晚到写入。
- [x] 实现确定性点 ID、租约/版本校验、发布核对和清理流程；加入 MySQL 排队作业恢复入口，不能依赖消息只投递一次。
- [ ] 运行所有新增测试；获得隔离存储写入授权后执行真实 upsert/search/delete 及重启恢复，核对无重复点、无旧版本命中。更新文档、审查并提交。

阶段 3 代码交付记录（2026-09-02，真实模型全链路延期）：

- 四格式来源、混合语言/长句切分、损坏/扫描/加密拒绝、隔离解析和实际 20 MB 文本通过；真实缓存 Qwen 测试通过，发现并修复 EOS 容量差异；空白密集合规块超过 TEXT 字节上限，通过增量 MEDIUMTEXT 迁移完整存储。
- 真实 MySQL 验证同库互斥、过期代次隔离、漏投恢复、有限重试和在途冷却；索引测试覆盖发布前故障、提交前回滚、重复 upsert、重建旧版清理、删除期间禁止发布、清理失败保留原文/墓碑、块配额与重建失败不可用。
- 独立 `evalspark-rag-lifecycle-test` 的真实 Qdrant 验证重复建索引、五个 payload 索引（user_id 主索引）、幂等 upsert、用户/版本过滤与精确清理，通过。另在 Embedding 关闭时停启真实 Celery Worker：预先模拟过期清理租约，启动后从 MySQL 恢复为第 2 次领取，完成原文及墓碑清理，通过；这不等同于真实模型索引全链路通过。
- 首轮全后端为 297 passed、7 skipped；后续新增验证的最终计数见本节补充记录。4 项 Compose 契约已用宿主机实际解析且脱敏的配置传入容器验证通过；Worker 不再以 Embedding 健康为启动门槛。Qwen 分词和真实服务用例必须显式运行，不能把默认跳过算作通过。
- 实际启动测试 TEI 时宿主机可用物理内存下降至约 400 MB，模型容器采样约 2.38 GiB，系统明显变慢。为保护工作环境主动停止本次测试 Embedding（Docker 停止后退出 137），缓存和所有数据卷保留；未修改业务 MySQL。老大随后允许延期部分测试及部署；`test_real_worker_indexes_four_formats_recovers_notification_and_cleans_up` 留待高内存设备执行，不再要求当前设备释放内存继续联调。不得据前序单独 TEI 实测与替身流水线测试推定本次全链路通过。
- 按用户约束未使用子代理；依据评审技能清单自审，补充人工清理重试新代次、冷却期和成功后清除旧错误等回归。阶段 3 按上述延期验收约定提交后进入阶段 4，不标记整项 V3 完成。

本轮最终验证补充：完整后端 **299 passed、9 skipped、63 warnings**；9 项为容器无 Docker CLI 的 4 项、需真实缓存的 2 项、需完整服务/停启配合的 3 项。4 项 Compose、2 项真实 Qwen 分词、真实 Qdrant 以及 Worker 停启恢复已分别执行通过，仅四格式真实模型索引全链路尚未执行完成。警告来自原有测试客户端/Alembic/UTC 默认值，以及 Qdrant 本地模式不启用 payload 索引的提示（真实服务索引另已验证）。最终源码镜像构建成功，镜像配置 ID `sha256:aa50b488ddba0808f9e3a4a56fa435b1ae37ba49ad4572bc5b41fc250b06c0f7`，`pip check` 通过；Git 差异检查无空白错误，React/Vue 无改动。

用户调整验收边界后，提交前再次用断网、512 MB/1 CPU、源码只读且无数据库连接的容器执行 `test_rag_documents.py`、`test_rag_vectors.py`、`test_rag_worker.py`、`test_rag_clients.py`：**54 passed、3 skipped、1 warning**。3 项为真实缓存/服务开关未开启；警告为 Qdrant 本地索引提示。未重启 Embedding、测试 MySQL 或全栈。

```python
# chunks 由测试中的中文长段落和已加载的同版 tokenizer 切分得到。
assert chunks
assert all(0 < chunk.token_count <= 800 for chunk in chunks)
assert all(chunk.source.kind == "text" for chunk in chunks)
assert sum(chunk.token_count for chunk in chunks) >= original_token_count
```

其中 `original_token_count` 是同一测试原文的 Qwen Token 数；测试还需从来源区间验证原文覆盖，不单凭总数判断未丢文。

### 阶段 4：逐模型链路、快照与用量

**文件：** 新增 `backend/app/services/rag/evaluation.py`、`backend/tests/test_rag_evaluation.py`、`test_rag_usage.py`；扩展 RAG schema/model 与 `evaluation_service.py` 的内部持久化接点。必要时在 `token_quota_service.py` 增加受回答锁保护的 RAG 汇总入账入口，不改变普通计费路径。更新架构、数据库和本文。

**接口：** `rewrite_query(client: ModelClient, request: ModelRequest) -> ModelReply`；`retrieve_evidence(db: AsyncSession, user_id: int, knowledge_base_id: int, versions: list[tuple[int, int]], query: str) -> list[RagEvidence]`，均为异步方法。

`RagTaskContext` 在 RAG schema 中定义，字段为 `task_id: int, user_id: int, knowledge_base_id: int, content_revision: int, document_versions: list[tuple[int, int]], prompt: str, enable_thinking: bool`。`PreparedRagResponse` 保存 `response_id: int, model_config_id: int, rewritten_query: str | None, evidence: list[RagEvidence], failure_stage: str | None, error_code: str | None`；failure_stage 使用第 6.1 节 Literal 集合。

`prepare_rag_snapshots(db: AsyncSession, context: RagTaskContext, models: list[RuntimeModelConfig]) -> list[PreparedRagResponse]` 为异步方法，保存证据屏障及失败候选结果；`stream_rag_answers(context: RagTaskContext, prepared: list[PreparedRagResponse], models: list[RuntimeModelConfig]) -> AsyncIterator[RagStreamEvent]` 为异步生成器。`RagStreamEvent` 在 `schemas/rag.py` 定义为第 5.3 节阶段/检索事件与既有模型 delta、answer_completed、response 事件的判别联合，JSON 字段与现有 NDJSON 保持一致。

- [ ] 用两个假候选分别返回不同查询，验证仅各自的一条查询进入 TEI，两份证据不串用；所有过滤均带当前用户、库和版本清单。
- [ ] 运行红色测试；实现改写、Top-5、版本复核屏障、快照和生成。零命中、改写失败不调用后续生成；其他候选继续。
- [ ] 测试检索中删库/重建导致快照拒绝，以及快照完成后删库不影响读取；测试文档中的“忽略指令”始终作为资料输入而非系统指令。
- [ ] 保存各调用用量与配置快照；异常分支保留已知用量，按币种汇总，回答终态重复持久化不能二次增加每日配额。
- [ ] 单元测试验证每候选一次改写、一次生成，断流能保留可恢复状态；不开放未完成评分的公共 RAG 执行路径。更新文档、审查并提交。

明确的调用计数断言：

```python
assert candidate_a.rewrite_calls == 1
assert candidate_b.rewrite_calls == 1
assert evidence_a[0].label == evidence_b[0].label == "S1"
assert evidence_a[0].chunk_id != evidence_b[0].chunk_id
assert persisted_task.prompt == original_prompt
assert quota_total_after_replay == quota_total_after_first_finish
```

这些计数由测试假客户端显式记录；文档不要求生产客户端新增测试专用字段。

### 阶段 5：三轮评审、API 闭环与回归

**文件：** 新增 `backend/app/services/rag/judge.py`、`backend/tests/test_rag_judge.py`、`test_rag_evaluation_api.py`、`test_rag_history_access.py`；修改 `backend/app/api/v1/evaluation.py`、`schemas/evaluation.py`、`services/evaluation_service.py`、`services/feedback_stats_service.py` 及其对应现有测试。按需要扩展 RAG 结构迁移，不改旧任务分值。同步 API、数据库、架构和功能状态文档。

**接口：** `RagJudgeRun` 为完整一轮结果；`aggregate_rag_runs(runs: list[RagJudgeRun]) -> RagJudgeAggregate` 提供四维均值、范围和状态；`calculate_rag_base(rule: Decimal, quality: Decimal, faithfulness: Decimal, correctness: Decimal, completeness: Decimal) -> Decimal` 计算未舍入基础分。反馈使用 `scoreVersion` 和已保存 `baseFinal`，不重新 Judge。

- [ ] 先写 3 轮稳定、1 轮失效仍成功、2 轮失效、任一维度范围超限、边界 2.0、缺字段、越界、伪造引用、无可评估项测试。
- [ ] 运行红色测试，实施联合 Judge prompt、严格解析和 Decimal 公式；保留各轮 usage 与无效原因。
- [ ] 先写旧 chat 请求缺省、RAG 强制私有/强制 Judge、任务类型筛选与 NDJSON 合约测试，再正式接通 RAG 路由分派。
- [ ] 验证实时结果、历史重读、点赞/取消点赞的分项与基础分一致；Judge 失败仍显示回答，反馈不使失败变有效。
- [ ] 验证 owner/其他用户/admin 对任务、引用、下载、评论的完整权限矩阵及管理员互动明细过滤；普通公开任务回归不变。
- [ ] 运行 RAG 及原有 evaluation/feedback/token 测试，更新文档、审查并提交。

```python
def test_rag_formula_keeps_unrounded_base():
    base = calculate_rag_base(
        Decimal("8"), Decimal("7"), Decimal("9"), Decimal("8"), Decimal("7")
    )
    assert base == Decimal("7.85")
    final = (base * Decimal("0.90") + Decimal("10") * Decimal("0.10"))
    assert final.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) == Decimal("8.07")
```

### 阶段 6：React 页面与交互

**文件：** 新增第 8.2 节全部前端文件及相邻 `.test.ts`；修改 `frontend/src/App.tsx`、`features/navigation/navigation.ts`、`api/client.ts`、`features/evaluation/types.ts`、`features/evaluation/evaluation.ts`、`features/history/history.ts`、`pages/HistoryPage.tsx` 和 `components/ModelResponseCard.tsx`。现有组件只增加必要扩展点，新业务回调在 `.ts`。更新功能状态、API 使用说明和 README 入口。

**接口：** `useKnowledgeBases()` 输出管理页有类型的数据和动作；`useRagEvaluation()` 输出提交、取消、模型/知识库选择与流式状态；`mergeRagStage(state, event)` 为纯状态转换；`RagEvidencePanel` 消费当前回答证据，不自行绕过任务 API 请求原文。

- [ ] 先写 API 模拟与纯状态测试：无空闲 Judge 禁止提交、库处理中禁止提交、强制 private、切换库取消旧请求、流式 rag_stage 不进入 task_completed 分支。
- [ ] 运行红色测试，实现 `.ts` hooks 和状态转换；复用现有鉴权请求、NDJSON 批处理和清理机制。
- [ ] 新建页面与证据/评分 UI，接入导航、任务类型筛选；删除确认和外部 API 提示使用本文文案。
- [ ] 补充实际组件交互测试或已授权浏览器验收，验证引用对应当前回答、键盘操作、错误态、加载态、移动端和长文滚动；不能仅扫描源码字符串宣称交互通过。
- [ ] 在 Docker 前端服务运行 Vitest 和 `pnpm build`，再回归普通评测/历史/管理入口；更新文档、审查并提交。

```ts
// API mock 捕获真实提交体，不只检查页面上是否出现“私有”文字。
expect(sentBody.taskType).toBe("rag");
expect(sentBody.visibility).toBe("private");
expect(sentBody.enableJudge).toBe(true);
expect(sentBody.modelIds).not.toContain(sentBody.judgeModelId);
```

### 阶段 7：隔离集成与交付

**文件：** 完成已有 `docker-compose.rag-test.yml`，新增 `backend/tests/integration/test_rag_docker.py`；按确实需要新增 `scripts/verify-rag.ps1`、`scripts/verify-rag.sh`，两者调用同一 Compose 测试定义；不复制一套业务逻辑。更新 `docs/system-features-status.md`、`docs/api.md`、`docs/database.md`、`docs/architecture.md`、`docs/open-source-reuse.md`、`docs/docker-development-spec-plan.md`、`docs/README.md`、`README.md`、`AGENTS.md` 和本文验收记录。

- [ ] 重新确认当前数据库和 Volume 的实际名字；复核隔离项目 `evalspark-rag-test` 不读取业务 `.env`，测试凭据仅在测试配置中，数据库不使用 external Volume 或生产连接串。
- [ ] 集成测试默认跳过，必须显式 `RAG_INTEGRATION_TESTS=1` 才运行；启动前检查数据库名为 `multichateval_rag_test`、服务名和测试项目匹配，否则立即拒绝写入。
- [ ] 获得对隔离测试库/索引/文件的写入、重启与清理授权后，初始化测试环境，验证迁移、四格式索引、真实 TEI 向量、Qdrant 过滤、模型假服务和 API/React 闭环。
- [ ] 执行重复消息、Worker 中断、broker 中断、索引部分失败、重建与删除并发、快照后删除、越权矩阵；断开 RAG 依赖后复测普通评测。
- [ ] 获得独立外部模型调用与费用授权后，用老大指定的非敏感文档和现有模型配置做一次真实改写—检索—回答—三轮 Judge；不自行新建用户或写入业务模型配置。
- [ ] 测量指定样本的块数、解析/Embedding/索引耗时、检索延迟及内存峰值；记录机器和规模，未测 100,000 块则明确标记，不能外推容量/性能已通过。
- [ ] 完成代码、文档及 README/AGENTS 一致性检查，填写第 12 节实际证据后提交。未获数据库或真实调用许可时保留对应验收“未执行”，不标记整个阶段完成。

## 11. 验证命令与执行边界

### 11.1 当前文档阶段

以下 Git 命令不涉及业务数据库；仅在确认暂存区没有其他任务文件后提交：

```powershell
git status --short --branch
git diff --check
git add -- docs/v3-rag-spec-plan.md
git diff --cached --check
git diff --cached --name-only
git diff --cached --stat
git commit -m "docs(rag): 明确 V3 RAG 设计与分阶段实施计划"
git status --short --branch
```

### 11.2 后续单元验证

下列命令仅在代码实施、镜像构建及容器执行获得授权后运行。`--no-deps` 避免启动 migrate，显式假数据库 URL 防止普通单元测试意外接入真实库；禁止单元测试自行连接此 URL。

```powershell
docker compose config --quiet
docker compose config --services
docker compose run --rm --no-deps -e DATABASE_URL=mysql+aiomysql://test:test@invalid:3306/unit_test backend python -m pytest tests/test_rag_clients.py tests/test_rag_configuration.py -q
docker compose run --rm --no-deps -e DATABASE_URL=mysql+aiomysql://test:test@invalid:3306/unit_test backend python -m pytest tests/test_knowledge_base_api.py tests/test_knowledge_base_service.py tests/test_rag_storage.py -q
docker compose run --rm --no-deps -e DATABASE_URL=mysql+aiomysql://test:test@invalid:3306/unit_test backend python -m pytest tests/test_rag_documents.py tests/test_rag_indexing.py -q
docker compose run --rm --no-deps -e DATABASE_URL=mysql+aiomysql://test:test@invalid:3306/unit_test backend python -m pytest tests/test_rag_evaluation.py tests/test_rag_usage.py tests/test_rag_judge.py tests/test_rag_evaluation_api.py tests/test_rag_history_access.py -q
docker compose run --rm --no-deps -e DATABASE_URL=mysql+aiomysql://test:test@invalid:3306/unit_test backend python -m pytest tests/test_evaluation_api.py tests/test_evaluation_service.py tests/test_feedback_stats_api.py tests/test_feedback_stats_service.py tests/test_token_quota_service.py tests/test_token_usage_api.py -q
docker compose run --rm --no-deps frontend pnpm test
docker compose run --rm --no-deps frontend pnpm build
```

每个阶段只运行已经创建的测试文件。需要仓库根目录的现有启动脚本/Compose 静态测试，使用测试 runner 的只读全仓挂载，不能因 backend 容器只挂载 `/app` 而跳过后宣称全量测试通过。

不得输出完整 `docker compose config`，避免 `.env` 展开泄密。不得在未授权时执行项目启动脚本、`docker compose up` 或 Alembic upgrade：现有 Compose 会自动运行迁移服务。

### 11.3 集成门禁

隔离 Compose 文件必须完全独立，不通过覆盖现有生产服务的部分字段来“猜测”是否隔离。测试数据操作在测试文件中使用明确连接配置，执行前打印脱敏项目/服务/库名供核对，不打印密码。

集成首次启动、实际业务库迁移和测试清理是三项不同操作。即使隔离测试迁移通过，升级业务库仍需先报告迁移内容、备份位置、预期锁表影响和恢复方案，再获得明确许可。未经许可不生成或读取数据库转储。

## 12. 验收证据与交接规则

| 检查项 | 必须记录的证据 | 当前结果 |
| --- | --- | --- |
| 文档基线 | 差异检查、覆盖自检、提交 ID | 阶段 0：`63d7777`；阶段 1 见 Git 日志 |
| 依赖版本 | 镜像 digest、模型 SHA、包版本、兼容性结果 | 阶段 1 已验证，见第 10 节版本与实测记录 |
| 数据升级 | 隔离库迁移前后结构、旧任务兼容、无历史改分 | 阶段 2 独立 MySQL 通过；业务库未迁移 |
| 索引 | 四格式来源、块上限、重复/中断恢复、版本过滤 | 解析/真实 Qwen 分词、MySQL 故障与版本、真实 Qdrant、Worker 停启恢复通过；四格式真实模型全链路延期 |
| 评测 | 两候选不同查询与证据、Top-5、失败隔离、流式事件 | 未执行 |
| Judge | 有效轮数/范围边界、引用语义判定、公式样例、反馈重算 | 未执行 |
| 权限 | 所有者/其他用户/管理员的内容与互动访问矩阵 | 知识库/文档 API 权限矩阵通过；RAG 评测及互动待阶段 5 |
| 删除 | 当前文件/文本/向量清理，历史快照保留且不可越权 | 删除墓碑/幂等、立即禁下载、真实 Qdrant 清理与 Worker 过期租约物理清理通过；完整索引后删除延期，历史快照待阶段 4—5 |
| 前端 | Vitest、TypeScript/Vite 构建、浏览器关键路径 | 未执行 |
| 实际部署 | Docker 实连、普通评测降级隔离、模型调用结果 | 基础依赖实连、CPU Embedding、Worker、断网 health 通过；完整 RAG 未执行 |
| 性能与容量 | 实测样本规模、耗时、内存；未测上限明确披露 | 仅阶段 1 短文本/预热采样；大文档、并发、100,000 块容量与峰值未测 |

每阶段交付使用“提交 ID + 已完成范围 + 验证证据 + 未覆盖/下一门禁”的简短格式。代码实现状态与测试执行状态分开记录；只有对应测试和实际环境证据存在时才标记验收通过，测试代码写好不等于测试执行通过。资源不足延期项允许随阶段代码交付，但必须保留供后续设备执行的说明。

### 设计资料

资料用于开源复用与接口核对，不代表本机已部署或测试通过。实施阶段仍需核对实际固定版本。

- [Qwen3-Embedding 模型卡](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B)：模型维度、查询指令与用法。
- [Qwen3-Embedding 官方仓库](https://github.com/QwenLM/Qwen3-Embedding)：模型与分词/推理说明。
- [TEI 支持模型与硬件](https://huggingface.co/docs/text-embeddings-inference/supported_models)、[TEI CLI 参数](https://huggingface.co/docs/text-embeddings-inference/en/cli_arguments)：CPU 镜像、revision、共享缓存及批量参数。
- [Qdrant 多租户分区](https://qdrant.tech/documentation/tutorials/multiple-partitions/)、[集合与向量配置](https://qdrant.tech/documentation/manage-data/collections/)：单集合 Payload 隔离和 Cosine 规格。
- [Celery Redis broker 文档](https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/redis.html)：重复投递、visibility timeout 与恢复限制。
- [Conventional Commits 中文规范](https://www.conventionalcommits.org/zh-hans/v1.0.0/)：阶段提交格式。
