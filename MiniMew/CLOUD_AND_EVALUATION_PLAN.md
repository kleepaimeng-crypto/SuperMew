# 当前版本云端部署与检索调优计划

2026-11-23 核对。计划包含单机 Ubuntu 私有试用部署和独立的检索评测。两个方向可以同时准备，生产试用和评测数据各用自己的项目及实验索引。

## 当前版本部署

发布候选是个人仓库 `personal/main` 的 `1e6528fdc3e33d32603e38b895b9fd4cf2b042d7`，已核对远端。迁移头为 `0026`。已有第十章工作区、项目固定模型和个人库切换能力可以随该提交部署。评测继续使用当前已实现的本地检索、受控生成与评分能力。未完成的 11.4 评测绑定和本机未提交界面改动都留在后续版本。

```text
浏览器 → Nginx HTTPS → Vue 静态文件
                     ↘ /auth /v1 /health → FastAPI（回环地址）
FastAPI / Index Worker / Evaluation Worker → PostgreSQL、Redis、Milvus（按职责访问）
Index Worker 与 API 共享原文目录和模型缓存。Evaluation Worker 可按需启动。
```

1. **确认服务器与访问方式。** 确认 Ubuntu 版本、CPU 架构、内存、磁盘、域名或私网入口，以及空库启动还是迁移现有数据。单机小规模试用的起点估计为 4–8 vCPU、16 GiB RAM 和 SSD。先跑实际导入和并发聊天后再决定容量。同机并行索引和评测时重点看内存峰值和 Milvus 容量。此为起点估计，仓库 Milvus 容器当前限制为 3 GiB。试用先按 VPN 或 IP 白名单限制入口，现有 API 有注册路由。[Milvus Standalone 硬件说明](https://milvus.io/docs/prerequisite-docker.md)
2. **准备运行时和配置。** 安装 Docker Engine、Compose v2、Python 3.12、uv、Node 和 Nginx。有域名时先设置 DNS 和 HTTPS。用普通部署用户检出指定提交，服务器端从 `.env.example` 创建自己的 `.env`，权限设为仅部署账户可读。设置数据库和 Redis 密码、JWT 密钥、凭据加密主密钥、模型默认配置与所需 Key。置 `MINIMEW_ENVIRONMENT=production`、`MINIMEW_REFRESH_COOKIE_SECURE=true`，启用知识检索时置 `MINIMEW_KNOWLEDGE_ENABLED=true`；**纯 API 云端部署置 `MINIMEW_LOCAL_MODELS_ENABLED=false`**（不加载本地模型、不装 PyTorch，新建项目和个人库在页面配置百炼 Key 后即可上传检索）。空库可初始化新主密钥。迁移旧加密配置时携带原主密钥和备份。[Docker Ubuntu 安装](https://docs.docker.com/engine/install/ubuntu/)
3. **启动基础设施并显式迁移。** 下方命令在 MiniMew 目录执行。首次上线建议空库，迁移既有数据则先确认备份和恢复方案、结束旧版本活动任务及项目 Embedding 重建，使用原 `.env` 的构建配置应用迁移。若恢复旧历史，备份 PostgreSQL、原文目录、Milvus 数据和主密钥，并核对恢复后原文和向量能按旧版本定位。

```bash
# 纯 API 部署（MINIMEW_LOCAL_MODELS_ENABLED=false）：只需 vector 依赖，无需安装 torch 或下载本地模型
uv sync --locked --no-dev --extra vector
docker compose --profile vector up -d --wait
uv run --env-file .env --no-sync alembic upgrade head
uv run --env-file .env --no-sync alembic current
(cd frontend && npm ci && npm run build)
```

> 若服务器仍需本地 BGE 模型或评测基线（`MINIMEW_LOCAL_MODELS_ENABLED=true`），改用 `uv sync --locked --no-dev --extra embedding --extra vector` 并运行 `python -m minimew.embeddings.cli prepare` 与 `python -m minimew.rag.reranker prepare`。

4. **接入应用和同源代理。** Nginx 托管 `frontend/dist`，Vue 路由刷新使用 `try_files $uri $uri/ /index.html`。将 `/auth`、`/v1`、`/health` 原路径代理给 `127.0.0.1:8001`，保留请求 Host 和 HTTPS scheme。SSE 路径关闭响应缓冲，读超时覆盖流的空闲间隔。上传限制与 API 对齐，当前默认 5 MiB，代理需额外留 multipart 开销。公网使用 HTTPS，SSH 限来源，数据库、Redis、Milvus 和 Uvicorn 端口留在回环。以 systemd 分别托管以下进程，并配置重启、日志和持久目录。[Nginx 代理配置](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_buffering)

```bash
uv run --env-file .env --no-sync uvicorn minimew.app:create_app --factory --host 127.0.0.1 --port 8001
uv run --env-file .env --no-sync python -m minimew.documents.worker
uv run --env-file .env --no-sync python -m minimew.evaluation.worker
```

5. **验收再放开试用。** 确认 `0026`、容器健康和 `/health/live`、`/health/ready`。后者只检查 PostgreSQL。再用真实浏览器按顺序验证登录与刷新、普通聊天流式响应、断线后补读、上传 Markdown/TXT、独立 Worker 发布、知识问答与引用点击、项目隔离、评测 Job 完成。最后验证 systemd 重启后历史和文档可恢复。先用实验账户和少量素材，记录提交、配置摘要与备份位置。

## 候选语料

| 测试切片 | 已定位的公开数据 | 准备方式 |
| --- | --- | --- |
| 跨文档 | [HotpotQA distractor 验证集](https://huggingface.co/datasets/hotpotqa/hotpot_qa)含问题、答案、候选文章和句级支持事实 | 按文章标题拆为独立 Markdown/TXT，并导入同批干扰文章。保留原始证据句映射。从验证集抽需两个来源的题目。原始官网也有下载入口，[HotpotQA 首页](https://hotpotqa.github.io/) |
| 中文长文档 | [LongBench v1 MultiFieldQA-zh](https://huggingface.co/datasets/zai-org/LongBench)含长正文、问题和答案，[文件页](https://huggingface.co/datasets/zai-org/LongBench/tree/main)列出数据包 | 每条长正文保留为一个文档，抽前／中／后段和跨章节题。人工补标答案对应的原文证据。只把正文交给检索 |
| 长文档证据对照 | [QASPER](https://huggingface.co/datasets/allenai/qasper)含英文论文全文、问题、答案和证据 | 首轮选证据为文本段落的题，保留章节结构。若优先做自动证据核对，可先用这组 |
| 贴近实际使用 | 用户认可可测试的自有 Markdown/TXT，例如部署、配置与故障说明各一份 | 若没有附题，人工写跨文件、跨章节、无资料和版本冲突题，同时标参考答案和原文锚点 |

建议首轮固定 30 题，12 题跨文档、12 题长文档、6 题无资料或版本冲突。先用其中 10 题校准标注，20 题留作首次盲验。正式调优时另补留出集，避免持续用同一批盲验题调参。每题保存来源文件、章节、原文证据、参考答案和必须答出的要点。问答与评分标准放在题库，普通文档索引只收原文。第三方原文保留出处和授权信息。上述是已确认的数据入口，这轮未下载或整理完整语料。HotpotQA 官网 JSON 在本次访问返回 502，已有 Hugging Face 发布页可用作替代入口。LongBench 建议从文件页直接下载，避免旧数据加载脚本兼容问题。

## 当前评测怎样操作

1. **先在文档页上传原文并等发布。** 当前 11.4 绑定尚未完成，用本地 Embedding 和本地 Rerank 跑正式工作台基线。Corpus 是从已发布的版本复制出来的独立评测索引，之后用户库更新也不会把它换掉。
2. **准备 Dataset 和映射。** Dataset 是题目和证据标准，`mapping.json` 将样本中的 `source_id` 映射到当前已发布的 Document Version UUID。每个版本的原文长度和哈希需与 Dataset 一致。语料只对所属账号可见，目前保留期为七天。
3. **在 MiniMew 目录准备 Corpus 并启动评测 Worker。**

```bash
read -r -p '评测账号 UUID: ' evaluation_user_id
uv run --env-file .env --no-sync python -m minimew.evaluation.manage prepare --user "$evaluation_user_id" --dataset dataset.json --mapping mapping.json
uv run --env-file .env --no-sync python -m minimew.evaluation.worker
```

4. **打开评测页。** 选择已准备 Corpus，导入 Dataset 和 Scenario，再点「校验输入 → 创建并运行」。Scenario 是用于验收链路的预设生成、改写、澄清和评分响应。现有工作台使用真实本地检索和受控生成与评分。
5. **查看报告。** 先读失败 Case 的源文档和命中证据，再选已完成实验作为基线。Dataset、语料内容和评分版本必须一致，每次只声明实际变化的一个因素。更详细的字段和命令见[现有操作说明](evals/rag/README.md)。

以当前静态示例验证了 CLI 操作。4 个 Case 的 Gate 通过，材料覆盖和引用结构均为 100%，各只覆盖 2 道适用题，语义正确性为「未评价」。这次运行用于读懂报告格式。

## 建议的评测操作和量化结果

把日常操作收敛成「导入文档及题单 → 预检证据 → 运行评测 → 查看漏召回和错答案」。题单用 CSV 或表格，至少包含 `case_id`、`问题`、`类型`、`参考答案`、`必答要点`、`证据文件与章节`、`原文锚点`、`预期结果`。导入器将题单转换为现有评测输入并找出无法定位的证据，继续由现有 Worker、Report 和 Gate 产生权威结果。第一步可做成命令行一个入口和可读模板，随后再决定是否放到网页。

- **先测检索。** 用同一批原文和题目测首轮召回 `Recall@K`、最终材料覆盖、跨文档全证据命中率、长文按证据位置分组的命中率。首轮召回指进入融合或重排之前的候选，最终材料指交给回答模型的片段，两者都记录漏掉的证据。
- **再测答案。** 同一题真实运行且获授权后，用人工量表评正确性、完整性和依据性，各 0–2 分，并记录引用正确率、无资料拒答正确率和运行错误率。当前受控 Judge 的数值用于流程验证。语义质量用实际回答与人工复核得出。
- **记录性能与对照。** 保存每题检索耗时、端到端耗时和失败类型，汇总 p50/p95、降级率和样本数。评测固定提交、源文件、候选预算、生成模型以及评分规则。先跑默认配置，再分别只改变 Hybrid、Rerank 或候选预算。输出「基线 → 调优后 → 差值」和逐题明细。在线费用单独按实际用量报告，受控验收费用为零。

首轮建议先以本地检索和 30 题建立基线。真实百炼对照待 11.4 绑定、适合发送的语料和调用预算确认。本文是实施计划。本轮未连接云服务器、未改应用代码，也未发起真实模型调用。
