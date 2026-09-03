# 开源项目复用说明

V3 阶段 7 的验收继续复用现有 pytest、httpx ASGITransport、FastAPI/StreamingResponse、Docker Compose 和前端 Vitest，不新增依赖或另接评测框架。确定性模型假服务仅承载隔离测试协议，不属于生产推理服务，也不能用于证明真实 Judge 的语义质量。入口通过显式环境文件排除业务 `.env`，依据 [Compose 环境文件规则](https://docs.docker.com/compose/how-tos/environment-variables/variable-interpolation/)；测试配置已解析，运行验收延期。

## V3 RAG 实际引入（阶段 1—3）

推理、分词、解析、切分、向量存储和队列使用现有开源组件；项目补充来源映射、输入限制、状态一致性和归属校验。RAG 评分仍在后续阶段。

| 组件 | 固定版本 | 当前用途 |
| --- | --- | --- |
| [Qwen3-Embedding-0.6B](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B) | revision `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3` | 私有 1024 维向量，查询指令与文档输入分开 |
| [TEI](https://github.com/huggingface/text-embeddings-inference/releases/tag/v1.9.3) | CPU `1.9.3`，镜像系列 `cpu-1.9` | 官方 HTTP 推理服务、模型缓存 |
| [Qdrant](https://github.com/qdrant/qdrant/releases/tag/v1.19.0) / Python SDK | `1.19.0` / `1.19.0` | 带用户、库和文档版本过滤的向量检索 |
| [Redis](https://github.com/redis/redis/releases/tag/7.2.16) / redis-py | `7.2.16-bookworm` / `6.4.0` | Celery broker；服务选 7.2 维护线 |
| [Celery](https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/redis.html) | `5.6.3` | JSON 队列和单并发 Worker 配置 |
| [Hugging Face Hub](https://huggingface.co/docs/huggingface_hub/en/guides/manage-cache) / tokenizers | `1.29.0` / `0.23.1` | 固定 SHA 的只读本地分词缓存与 Token 计数 |
| [python-multipart](https://pypi.org/project/python-multipart/0.0.32/) | `0.0.32` | 复用 Starlette 流式表单解析；项目仅加鉴权、输入总量限制和文件生命周期管理 |
| [pypdf](https://github.com/py-pdf/pypdf) | `6.16.2` | 文本 PDF 提取和页码；不做 OCR，拒绝加密和无文本文件 |
| [python-docx](https://python-docx.readthedocs.io/en/latest/api/document.html) | `1.2.0` | 按正文顺序读取段落、表格与嵌套单元格，不伪造页码 |
| [semantic-text-splitter](https://github.com/benbrandt/text-splitter) | `0.32.0` | 自然边界和 Markdown 切分；共享真实 Qwen Tokenizer，额外预留 EOS 后再独立复核 |

解析器在 Linux Docker 子进程中受 120 秒、768 MiB 地址空间和提取字符上限约束。依赖无法替代这些资源边界；没有把无法解析的文件或超限内容截断当成功，也没有做完整供应链漏洞扫描。Celery 复用官方 [Bootsteps 生命周期](https://docs.celeryq.dev/en/stable/userguide/extending.html)，只增加每 60 秒扫描 MySQL 的受控恢复线程，不新增独立调度平台。

镜像完整 digest 固定于 `docker-compose.yml`，新增 Python 依赖固定于 `backend/pyproject.toml`。Kombu 5.6.2 的 Redis extra 要求 redis-py `<6.5`，因此不直接采用 redis-py 最新大版本。各阶段实际验证见 `v3-rag-spec-plan.md`。

2026-09-02 已核对官方公告：Qdrant 的 [GHSA-f632-vm87-2m2f](https://github.com/qdrant/qdrant/security/advisories/GHSA-f632-vm87-2m2f) 修复于 1.15.6，当前固定 1.19.0 不在该公告受影响区间；Redis 7.2.16 发布说明列出安全修复。TEI 和 Celery 官方仓库当时未列出公开公告，不等同于没有漏洞；本次没有做全镜像 CVE 扫描或完整供应链审计。

## OpenCompass

OpenCompass 是通用大模型评测平台，适合参考其评测体系、模型接入方式和 benchmark 表述。

本项目不直接基于 OpenCompass 开发，原因是课程项目需要的是面向用户的在线多模型对话评测平台，而 OpenCompass 更偏离线模型能力评测。

可借鉴内容：

- 模型评测术语
- 评测维度组织方式
- 模型适配思路
- 答辩中的相关工作说明

## promptfoo

promptfoo 是 LLM 输出测试与评测工具，适合参考规则评分、结构化评测和 LLM-as-a-Judge 的配置思路。

本项目 LLM Judge 借鉴其“规则检查 + LLM Judge 叠加”的思路：本地规则评分先给出可解释的硬规则结果，LLM Judge 再通过三轮 Prompt 输出结构化 JSON 评审结果；同时把候选回答视为不可信输入，要求评审模型忽略回答内的反向指令。三轮中至少 2 次成功且成功分差稳定后才参与最终分合成。

## FastChat

FastChat 提供多模型聊天和 Chatbot Arena 相关实践，适合参考多模型对比和人工偏好反馈思路。

本项目当前采用类似 MT-Bench single-answer grading 的轻量方式：每个候选回答单独评分，不做 pairwise 对战，避免第一版引入过高成本和复杂排序逻辑。

## 本项目复用策略

第一阶段不直接嵌入大型评测框架，而是自研轻量主流程：

```text
用户提问 → 多模型回答 → 客观指标 → 规则评分 → 可选 LLM 评审 → 用户反馈
```

这样更容易控制范围，也更能体现课程设计中的系统分析、数据库设计、接口设计和工程实现能力。
