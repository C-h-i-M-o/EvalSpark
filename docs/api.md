# API 说明

## V3 私有知识库管理（阶段 2）

以下接口已接入后端，部署前需要应用 `20260902_01/02` 迁移。阶段 3 已注册异步作业，通知成功后不再固定为 `dispatchPending=true`；是否处理完成必须读取作业状态。四格式解析、索引与物理删除代码已实现，真实 Worker 全链路尚在验收；`taskType=rag` 仍返回 422，React 暂无知识库入口。

所有接口要求登录，只能操作自己的库和文档，管理员也不能读取他人私有内容。未登录 401、账号禁用 403、他人或不存在资源统一 404。领域错误结构为 `{"detail":{"code":"document_limit","message":"每个知识库最多保留 100 份未清理文档"}}`；请求字段错误仍使用 FastAPI 标准 422。分页为 `page=1&pageSize=20`，每页上限 100。

| 方法及路径 | 请求/响应 |
| --- | --- |
| `POST /api/knowledge-bases` | JSON：`name`、可选 `description`、`chunkSize=800`、`chunkOverlap=120`；201 库详情 |
| `GET /api/knowledge-bases` | 分页自己的库：`items,total,page,pageSize` |
| `GET /api/knowledge-bases/{id}` | 库详情 |
| `PATCH /api/knowledge-bases/{id}` | 部分更新上述字段；200 库详情 |
| `DELETE /api/knowledge-bases/{id}` | 202 删除作业；立即停止修改和下载 |
| `POST /api/knowledge-bases/{id}/reindex` | 202 整库重建作业 |
| `GET /api/knowledge-bases/{id}/documents` | 分页文档及当前作业进度 |
| `POST /api/knowledge-bases/{id}/documents` | multipart 仅一个 `file`；202 文档索引作业 |
| `POST /api/knowledge-bases/{id}/documents/{docId}/retry` | 202 失败文档重试作业；相同活动作业幂等复用 |
| `DELETE /api/knowledge-bases/{id}/documents/{docId}` | 202 删除作业；立即阻止下载，文件清理异步进行 |
| `GET /api/knowledge-bases/{id}/documents/{docId}/download` | 原文件附件；`nosniff`、`private, no-store`，不公开物理路径 |

名称去除两端空白后 1—120 字；说明至多 2000 字，可设 null 清空。切块大小为 128—2048 的整数，重叠为非负整数且小于切块大小；不接受布尔值、未知字段或用户传入的 `userId`。PATCH 未传字段保持原值，名称/切分参数不能显式设 null。修改切分参数会使非空库进入 `reindex_required`；有活动任务时拒绝修改。空库不能重建；整库重建期间拒绝新上传/单文档重试，返回 409。

上传仅支持 PDF、DOCX、UTF-8/BOM TXT/Markdown，单文件最多 **20,000,000 字节**，请求总量最多再加 65,536 字节表单开销；不信任 Content-Length 或文件名声明。先鉴权再解析表单，流量超限 413，空文件/非法编码/伪装格式/宏或加密 DOCX 返回 415，多文件、多字段、不完整表单返回 422。每库最多 100 份尚未完成清理的文档，使用数据库行锁保证并发上限。上传只做轻量校验，Worker 深层解析失败通过文档/作业 `errorCode` 返回，例如 `encrypted_document`、`pdf_no_text`、`invalid_file`、`parse_timeout`、`document_too_complex`，不把异步失败改写成上传 HTTP 请求失败。

作业阶段包括 `parsing/splitting/embedding/indexing/verifying/cleanup_old/deleting_vectors/deleting_file/completed`；进度计数表示当前文档的块处理数，整库操作不能只凭最后一份文档计数判断完成。`status=succeeded` 才是本作业完成；`dispatchPending` 仅表示通知是否待补投。自动重试最多 3 次，不确定写入保留冷却期；人工重试失败清理生成新目标版本/作业，重复活动请求仍复用原作业。删除期间原文下载不可用，物理清理完成才标记 `deleted`；未来历史评测快照不依赖源文件。

库详情字段：`id,name,description,chunkSize,chunkOverlap,status,contentRevision,documentCount,chunkCount,available,errorCode,createdAt,updatedAt`。仅 `ready` 且有块时 `available=true`。文档字段：`id,knowledgeBaseId,originalName,mediaType,sizeBytes,status,indexRevision,chunkCount,errorCode,currentJob,createdAt,updatedAt`，不返回哈希、存储键、用户 ID 或服务器路径。

202 响应示例：`{"documentId":12,"jobId":"UUID","status":"queued","dispatchPending":true}`。整库作业的 `documentId=null`。`currentJob` 包含作业 ID、库/文档 ID、操作、目标版本、状态、阶段、已处理/总块数、尝试次数、安全错误码和时间。队列通知发生在事务提交后；通知失败保留文件和作业等待补投，不将已提交文档误删。删除中的资源保留墓碑与版本；重复请求返回相同删除作业。

## 认证约定

除健康检查、注册和登录外，业务接口均要求浏览器携带后端签发的 HttpOnly Cookie。未登录返回 `401`，普通用户访问管理员接口返回 `403`。

### 注册

```http
POST /api/auth/register
```

```json
{
  "username": "demo_user",
  "password": "Password123",
  "confirmPassword": "Password123"
}
```

注册密码至少 8 位，并且必须同时包含数字、小写字母和大写字母；`confirmPassword` 必须与 `password` 一致。注册成功返回当前用户并设置登录 Cookie。重复用户名返回 `409`。

### 登录

```http
POST /api/auth/login
```

请求结构：

```json
{
  "username": "demo_user",
  "password": "Password123"
}
```

登录成功返回当前用户并设置登录 Cookie；凭据错误返回 `401`，禁用用户返回 `403`。

### 当前用户

```http
GET /api/auth/me
```

```json
{
  "id": 1,
  "username": "demo_user",
  "role": "user",
  "status": "active"
}
```

### 退出

```http
POST /api/auth/logout
```

成功时清除登录 Cookie 并返回 `204 No Content`。

## 健康检查

```http
GET /api/health
```

响应：

```json
{
  "status": "ok"
}
```

## 创建评测任务

```http
POST /api/evaluation/tasks
```

请求：

```json
{
  "conversationId": 1,
  "prompt": "帮我解释什么是设计模式",
  "modelIds": [1, 2, 3],
  "enableJudge": false,
  "enableThinking": false,
  "visibility": "public",
  "judgeModelId": null
}
```

`modelIds` 使用 `model_configs.id`。如果不传，后端选择前两个已启用且已配置 API Key 的模型。

`visibility` 支持 `public` 和 `private`，默认 `public`。公开任务可被所有登录用户查看，私有任务只对创建者可见。

`enableJudge` 默认为 `true`。传入 `judgeModelId` 时，该模型必须已启用、配置 API Key，并且不能出现在本次 `modelIds` 中；不传时后端会自动选择一个未参与本次测评的空闲模型作为评审模型。若没有可用评审模型，回答会保存为 `judge_failed`，最终分为空，并从反馈统计中排除。

`enableThinking` 为全局思考模式开关，不区分具体模型。关闭时，后端会对所有模型请求统一追加：

```json
{
  "thinking": {
    "type": "disabled"
  }
}
```

开启时，后端会对所有模型请求统一追加：

```json
{
  "thinking": {
    "type": "enabled"
  }
}
```

第一版不提供思考程度选项，也不会发送 `thinkingEffort` 或 `reasoning_effort`。

注意：关闭思考模式时后端确认传输的是 `thinking.type=disabled`，不是 `thinkingmode:disabled`。MiniMax 等部分 OpenAI-compatible 供应商即使收到该参数，也可能仍在 `content`、`reasoning_content` 或 `<think>...</think>` 中返回思考内容；这属于供应商接口行为，不代表前端开关未传递。

规则评分会在评分入口排除完整的 `<think>...</think>` 区块；若标签未闭合，则忽略 `<think>` 及其后续内容。原始回答仍会完整保存和返回。该规则仅描述本地规则评分输入，不改变可选 LLM Judge 的候选回答输入。

发送给模型前，后端会在用户原始问题前追加系统内置提示词，用于统一回答质量、安全性和格式要求。接口响应和数据库中的 `prompt` 仍保留用户原始问题。

当前内置提示词：

```text
你是一个严谨、清晰、负责任的 AI 助手。请基于用户问题直接作答，并遵守以下要求：

1. 优先回答用户真正的问题，不要回避核心诉求。
2. 保持中文表达清晰、自然、结构化；如果用户明确要求其他语言，则按用户要求回复。
3. 如果问题适合分步骤、分点或对比说明，请使用清晰的段落、列表或表格组织答案。
4. 如果用户要求代码、JSON、表格、步骤、方案或对比，请严格遵守对应格式。
5. 不要编造不确定的信息。遇到无法确认的事实、数据、时间、版本或来源时，请明确说明不确定性。
6. 对涉及医疗、法律、金融、安全等高风险内容的问题，请给出谨慎、一般性的信息，并提醒用户寻求专业意见。
7. 避免输出违法、有害、危险操作指导、隐私泄露、凭据泄露或恶意攻击相关内容。
8. 回答应兼顾完整性和简洁性：必要时解释原因、给出示例或注意事项，但不要无意义冗长。
9. 如果用户问题本身含糊，请先基于最合理的理解回答，并指出关键假设；不要反复追问导致无法推进。

用户问题如下：
```

响应：

```json
{
  "taskId": 1001,
  "status": "completed",
  "prompt": "帮我解释什么是设计模式",
  "createdAt": null,
  "completedAt": null,
  "responses": [
    {
      "id": 5001,
      "modelConfigId": 1,
      "modelName": "deepseek-v4-flash",
      "provider": "deepseek",
      "answer": "回答内容",
      "latencyMs": 856,
      "outputTokens": 120,
      "estimatedCost": 0,
      "status": "success",
      "score": {
        "relevance": 8,
        "completeness": 8,
        "clarity": 8,
        "format": 8,
        "safety": 10,
        "ruleFinal": 8.4,
        "judgeFinal": null,
        "baseFinal": 8.4,
        "feedbackScore": null,
        "final": 8.4,
        "details": {
          "relevance": ["字符 n-gram 相似度 0.58", "覆盖解释意图", "回答聚焦于用户问题"],
          "completeness": ["回答长度处于有效区间"],
          "clarity": ["使用换行分隔内容"],
          "format": ["未指定格式，回答使用了可读结构"],
          "safety": ["未命中明显危险输出", "未发现拒答质量风险", "未涉及高风险专业建议"]
        },
        "judgeComment": null,
        "judgeDetails": {}
      },
      "feedback": {
        "liked": false,
        "disliked": false,
        "likeCount": 0,
        "dislikeCount": 0
      }
    }
  ]
}
```

该接口会先写入 `evaluation_tasks`，等待所有模型调用完成后写入 `model_responses` 和 `evaluation_results`，再一次性返回完整结果。`responses[].id` 是 `model_responses.id`，`responses[].modelConfigId` 是 `model_configs.id`。

## 创建评测任务并渐进返回模型结果

```http
POST /api/evaluation/tasks/stream
```

请求字段与 `POST /api/evaluation/tasks` 一致。响应内容类型为 `application/x-ndjson`，每一行都是一个独立 JSON 事件。模型回答生成阶段会持续返回增量文本；单个模型回答结束后先返回“回答完成”事件，前端展示“评分中……”，评分和持久化完成后再返回最终模型结果。

任务开始事件：

```json
{
  "type": "task_started",
  "taskId": 1,
  "prompt": "帮我解释什么是设计模式",
  "modelIds": [1, 2, 3],
  "total": 3
}
```

单个模型回答增量事件：

```json
{
  "type": "model_delta",
  "modelConfigId": 1,
  "delta": "增量回答片段"
}
```

单个模型回答完成、进入评分事件：

```json
{
  "type": "model_answer_completed",
  "modelConfigId": 1
}
```

单个模型评分和持久化完成事件：

```json
{
  "type": "model_response",
  "response": {
    "id": 5001,
    "modelConfigId": 1,
    "modelName": "deepseek-v4-flash",
    "provider": "deepseek",
    "answer": "回答内容",
    "latencyMs": 856,
    "outputTokens": 120,
    "estimatedCost": 0,
    "status": "success",
      "score": {
        "relevance": 8,
        "completeness": 8,
        "clarity": 8,
        "format": 8,
        "safety": 10,
        "ruleFinal": 8.4,
        "judgeFinal": null,
        "baseFinal": 8.4,
        "feedbackScore": null,
        "final": 8.4,
        "details": {
          "relevance": ["字符 n-gram 相似度 0.58", "覆盖解释意图", "回答聚焦于用户问题"],
          "completeness": ["回答长度处于有效区间"],
          "clarity": ["使用换行分隔内容"],
          "format": ["未指定格式，回答使用了可读结构"],
          "safety": ["未命中明显危险输出", "未发现拒答质量风险", "未涉及高风险专业建议"]
        },
        "judgeComment": null,
        "judgeDetails": {}
      },
      "feedback": {
        "liked": false,
        "disliked": false,
        "likeCount": 0,
        "dislikeCount": 0
      }
    }
  }
```

任务完成事件：

```json
{
  "type": "task_completed",
  "task": {
    "taskId": 1,
    "status": "completed",
    "prompt": "帮我解释什么是设计模式",
    "createdAt": null,
    "completedAt": null,
    "responses": []
  }
}
```

如果某个模型调用失败，会以 `model_response` 事件返回该模型的失败状态，不会中断其他模型。失败模型不会参与“评分中”阶段。

## 分页查询历史评测任务

```http
GET /api/evaluation/tasks?page=1&pageSize=10
```

响应：

```json
{
  "items": [
    {
      "taskId": 1001,
      "status": "completed",
      "prompt": "帮我解释什么是设计模式",
      "createdAt": "2026-06-03T12:00:00",
      "completedAt": "2026-06-03T12:00:12",
      "responseCount": 3
    }
  ],
  "total": 42,
  "page": 1,
  "pageSize": 10
}
```

`page` 从 1 开始，`pageSize` 默认 10，最大 100。列表按 `created_at desc, id desc` 排序。

## 查询可评测模型

```http
GET /api/models/available
```

所有登录用户可访问，只返回已启用且已配置 API Key 的模型精简信息。

## 查询模型配置

```http
GET /api/admin/model-configs
```

响应：

```json
[
  {
    "id": 1,
    "providerName": "deepseek",
    "displayName": "deepseek-v4-flash",
    "modelName": "deepseek-v4-flash",
    "baseUrl": "https://api.deepseek.com",
    "enabled": true,
    "hasApiKey": false,
    "maskedApiKey": "",
    "maxTokens": 1024,
    "temperature": 0.7,
    "timeoutSeconds": 60,
    "notes": "",
    "currency": "CNY",
    "priceInput": 0,
    "priceOutput": 0,
    "priceCacheHit": 0,
    "priceCacheCreation": 0
  }
]
```

该接口仅管理员可访问。列表接口不会返回原始 API Key。

## 创建模型配置

```http
POST /api/admin/model-configs
```

请求：

```json
{
  "providerName": "my-openai-compatible",
  "displayName": "自定义模型",
  "modelName": "custom-chat-model",
  "baseUrl": "https://example.com/v1",
  "apiKey": "sk-example",
  "enabled": true,
  "maxTokens": 1024,
  "temperature": 0.7,
  "timeoutSeconds": 60,
  "notes": "用于日常对比评测",
  "currency": "USD",
  "priceInput": 0,
  "priceOutput": 0,
  "priceCacheHit": 0,
  "priceCacheCreation": 0
}
```

四类价格的单位均为每 100 万 Token。系统不自动写入供应商预设；前端预设只帮助管理员填写官方兼容地址和资料入口。

## 更新模型配置

```http
PUT /api/admin/model-configs/{modelConfigId}
```

请求字段与创建接口一致，均为可选字段。`apiKey` 为空字符串或不传时表示保留原密钥。

## 删除模型配置

```http
DELETE /api/admin/model-configs/{modelConfigId}
```

所有模型配置均允许删除；已有回答通过可空外键和参数快照保留历史信息。

## 测试模型配置连接

```http
POST /api/admin/model-configs/test
```

测试已保存配置：

```json
{
  "modelConfigId": 1
}
```

测试未保存草稿：

```json
{
  "providerName": "my-openai-compatible",
  "modelName": "custom-chat-model",
  "baseUrl": "https://example.com/v1",
  "apiKey": "sk-example",
  "maxTokens": 128
}
```

响应：

```json
{
  "success": true,
  "message": "连接测试成功",
  "latencyMs": 856
}
```

失败时 `success` 为 `false`，`message` 返回失败原因。

## 查询今日 Token 用量

```http
GET /api/token-usage/me/today
```

普通用户响应：

```json
{
  "usageDate": "2026-06-12",
  "usedTokens": 24000,
  "dailyLimit": 100000,
  "remainingTokens": 76000,
  "unlimited": false
}
```

管理员的 `dailyLimit` 和 `remainingTokens` 为 `null`，`unlimited` 为 `true`。

## 查询管理员用户列表

```http
GET /api/admin/users?page=1&pageSize=10&keyword=test&role=user&status=active
```

查询参数：

- `page`：页码，从 1 开始。
- `pageSize`：每页数量，最大 100。
- `keyword`：按用户名模糊搜索，可选。
- `role`：按角色筛选，支持 `user` 和 `admin`，可选。
- `status`：按状态筛选，支持 `active` 和 `disabled`，可选。

响应：

```json
{
  "items": [
    {
      "id": 7,
      "username": "test_user",
      "role": "user",
      "status": "active",
      "usageDate": "2026-06-12",
      "usedTokens": 24000,
      "dailyLimit": 100000
    }
  ],
  "total": 1,
  "page": 1,
  "pageSize": 10
}
```

匿名占位用户 `id = 0` 不返回。

## 修改用户状态

```http
PATCH /api/admin/users/{userId}/status
```

请求：

```json
{
  "status": "disabled"
}
```

`status` 支持 `active` 和 `disabled`。禁用用户不能继续登录；管理员不能修改匿名占位用户，也不能封禁当前登录的管理员账号。

## 修改用户每日额度

```http
PUT /api/admin/users/{userId}/quota
```

请求：

```json
{
  "dailyLimit": 200000
}
```

`dailyLimit` 必须大于等于 0。仅普通用户可配置额度；管理员账号始终不限额。

## 查询评测任务

```http
GET /api/evaluation/tasks/{taskId}
```

响应字段与创建评测任务一致，会从数据库读取任务、模型回答和规则评分，并返回任务的 `createdAt` 与 `completedAt`。任务不存在时返回 404。

## 提交用户反馈

```http
POST /api/evaluation/responses/{responseId}/feedback
```

请求：

```json
{
  "feedbackType": "like"
}
```

`feedbackType` 当前只支持：

- `like`：点赞
- `dislike`：点踩

该接口采用互斥状态式切换语义。同一登录用户对同一回答只能保留一个当前反馈：重复提交相同类型会取消，提交另一类型会从点赞切换为点踩或反向切换。反馈写入当前登录用户 ID；demo-v1 旧匿名反馈继续归属 `user_id = 0`。

反馈提交后会重算并持久化最终分：

```text
baseFinal = ruleFinal                           # 未启用 Judge 时
baseFinal = ruleFinal * 0.30 + judgeFinal * 0.70 # 至少 2 次 Judge 成功且稳定时
feedbackScore = 10 * likeCount / (likeCount + dislikeCount)
final = baseFinal                               # 暂无反馈
final = baseFinal * 0.90 + feedbackScore * 0.10 # 已有反馈
```

`model_failed`、`judge_failed`、`judge_unstable` 和 `manual_required` 状态的回答不会生成可参与统计的最终分。评论不通过该接口提交，也不参与评分。

响应：

```json
{
  "responseId": 5001,
  "feedbackType": "like",
  "active": true,
  "feedback": {
    "liked": true,
    "disliked": false,
    "likeCount": 1,
    "dislikeCount": 0
  },
  "score": {
    "relevance": 8,
    "completeness": 8,
    "clarity": 8,
    "format": 8,
    "safety": 10,
    "ruleFinal": 8.4,
    "judgeFinal": null,
    "baseFinal": 8.4,
    "feedbackScore": 10,
    "final": 8.56,
    "details": {},
    "judgeComment": null,
    "judgeDetails": {}
  }
}
```

当 `responseId` 不存在时返回 404；当 `feedbackType` 不是 `like` 或 `dislike` 时返回 422。

### 评分状态字段

评测任务详情、流式最终 `model_response` 事件和反馈重算响应中的 `score` 会返回评分状态：

- `scoreStatus`：`scored`、`model_failed`、`judge_failed`、`judge_unstable`、`manual_required` 或 `judge_disabled`。
- `final`：最终分；排除统计的状态下为 `null`。
- `excludedFromStats`：是否从反馈统计中排除。
- `judgeRuns`：三轮 Judge 原始评分摘要。
- `judgeScoreRange`：三轮 Judge 分差。
- `ruleDictionaryVersion`：本次规则评分使用的词库版本。

三轮 Judge 中至少 2 次成功且成功分数分差不超过 2.0 时，使用成功评分的平均值作为有效 Judge 分。有效 Judge 分按 `0.30 * ruleFinal + 0.70 * judgeFinal` 计入基础分；少于 2 次成功为 `judge_failed`，成功分差超过 2.0 为 `judge_unstable`。

## 管理员评分配置接口

以下接口均需要管理员权限，统一前缀为 `/api/admin/scoring`：

- `GET /rule-dictionaries`：查看规则词库。
- `GET /rule-terms?dictionaryId=1`：查看词条。
- `POST /rule-terms`：新增词条。
- `PUT /rule-terms/{termId}`：更新词条。
- `DELETE /rule-terms/{termId}`：删除词条。
- `GET /judge-prompt-groups`：查看 Judge Prompt 分组和模板。
- `PUT /judge-prompt-templates/{templateId}`：更新 Prompt 模板。
- `POST /judge-prompt-groups/{groupId}/validate`：校验分组下三轮 Prompt 是否齐全且可用。

首次读取规则词库或词条时，后端会从 `backend/app/services/scoring/default_rule_seed.json` 幂等维护内置规则词典和词条，保证用户第一次部署空库后会自动导入当前完整词表，管理员页面不是空白起点。默认词表包含 7 个词典、390 条词条，覆盖格式要求、用户问题意图、拒答表达、安全替代建议、高风险领域、专业提醒、危险输出和隐私凭据；其中代码/格式词表覆盖常见编程语言、脚本、查询语言、前后端框架和测试框架，专业提醒覆盖医疗、法律、金融、安全和心理健康等高风险场景。若历史并发初始化留下重复默认数据，会保留最早记录并合并重复项后再返回列表；已有管理员修改不会被默认种子覆盖。

## 查询回答评论

```http
GET /api/evaluation/responses/{responseId}/comments?page=1&pageSize=20
```

评论按 `created_at desc, id desc` 返回。`pageSize` 取值范围为 1–100。

响应：

```json
{
  "items": [
    {
      "id": 301,
      "responseId": 5001,
      "userId": 0,
      "username": "anonymous",
      "content": "这个回答的步骤很清楚。",
      "createdAt": "2026-06-06T10:30:00",
      "canDelete": true
    }
  ],
  "total": 1,
  "page": 1,
  "pageSize": 20
}
```

回答不存在时返回 404。

## 发布回答评论

```http
POST /api/evaluation/responses/{responseId}/comments
```

请求：

```json
{
  "content": "这个回答的步骤很清楚。"
}
```

评论会去除首尾空白，正文长度限制为 1–1000 个字符。每个用户可以对同一回答发布多条评论。成功时返回新建评论，回答不存在时返回 404，正文为空或超长时返回 422。

成功响应：

```json
{
  "id": 301,
  "responseId": 5001,
  "userId": 0,
  "username": "anonymous",
  "content": "这个回答的步骤很清楚。",
  "createdAt": "2026-06-06T10:30:00",
  "canDelete": true
}
```

## 删除回答评论

```http
DELETE /api/evaluation/comments/{commentId}
```

只能删除当前用户自己的评论。成功时返回 `204 No Content`；评论不存在或不属于当前用户时返回 404。

## 查询个人反馈统计

```http
GET /api/feedback-stats/me?range=30d
```

所有登录用户均可访问。`range` 支持 `7d`、`30d` 和 `all`，默认 `30d`。7 天和 30 天按北京时间自然日计算；评分与调用按回答创建时间统计，点赞、点踩和评论按各自提交时间统计。

`summary` 只统计当前用户创建的公开及私有任务及其回答收到的互动；`myInteractions` 只统计当前用户主动提交的互动。个人接口不返回其他用户身份或全局互动明细。

```json
{
  "scope": "personal",
  "range": "30d",
  "startAt": "2026-05-20T00:00:00+08:00",
  "endAt": "2026-06-18T12:00:00+08:00",
  "summary": {
    "taskCount": 8,
    "callCount": 20,
    "scoredCount": 18,
    "averageFinalScore": 8.31,
    "likeCount": 12,
    "dislikeCount": 3,
    "likeRate": 0.8,
    "commentCount": 6
  },
  "myInteractions": {
    "likeCount": 5,
    "dislikeCount": 1,
    "commentCount": 2
  },
  "models": [
    {
      "modelConfigId": 1,
      "modelName": "DeepSeek Chat",
      "callCount": 10,
      "scoredCount": 9,
      "averageFinalScore": 8.52,
      "averageRuleScore": 8.4,
      "averageJudgeScore": 8.7,
      "likeCount": 7,
      "dislikeCount": 1,
      "likeRate": 0.875,
      "commentCount": 3
    }
  ],
  "trend": [
    {
      "date": "2026-06-18",
      "callCount": 3,
      "averageFinalScore": 8.6,
      "likeCount": 2,
      "dislikeCount": 0,
      "commentCount": 1
    }
  ]
}
```

无反馈时 `likeRate` 为 `null`；无有效评分或 Judge 分时对应均分为 `null`。`range=all` 时 `startAt` 为 `null`。

## 查询管理员反馈统计

```http
GET /api/admin/feedback-stats?range=30d&activityType=all&page=1&pageSize=20
```

仅管理员可访问，普通用户返回 `403`。查询参数：

- `range`：`7d`、`30d` 或 `all`，默认 `30d`，影响全部统计。
- `activityType`：`all`、`like`、`dislike` 或 `comment`，默认 `all`，只筛选互动明细。
- `modelConfigId`：可选模型配置 ID，只筛选互动明细。
- `page`：明细页码，默认 1。
- `pageSize`：明细每页数量，范围 1–100，默认 20。

响应的 `summary`、`models` 和 `trend` 与个人接口使用相同统计字段，但覆盖全部公开、私有和历史匿名数据。`activities` 按 `createdAt desc, activityId desc` 分页返回：

```json
{
  "scope": "global",
  "range": "30d",
  "startAt": "2026-05-20T00:00:00+08:00",
  "endAt": "2026-06-18T12:00:00+08:00",
  "summary": {},
  "models": [],
  "trend": [],
  "activities": {
    "items": [
      {
        "activityId": 91,
        "activityType": "comment",
        "userId": 7,
        "username": "demo_user",
        "taskId": 1001,
        "responseId": 5001,
        "modelConfigId": 1,
        "modelName": "DeepSeek Chat",
        "prompt": "帮我解释什么是设计模式",
        "content": "结构清晰，示例也很实用。",
        "createdAt": "2026-06-18T10:30:00"
      }
    ],
    "total": 1,
    "page": 1,
    "pageSize": 20
  }
}
```

点赞和点踩明细的 `content` 为 `null`。历史匿名互动的 `userId` 为 0、`username` 为 `anonymous`。本次统计能力直接读取现有持久化数据，不新增数据库表或迁移。
