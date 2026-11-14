# MiniMew 分阶段学习与实现计划

本计划对应 [九阶段路线](README.md)。互动规则以 [AGENTS.md](AGENTS.md) 为准；每个阶段开工前重新核对目标与范围。

“首次引入”指相对于前面的阶段，而不是假定学习者从未接触。库、框架、协议、算法和架构模式分别说明，避免把每个新名词都当成一个需要安装的组件。以下是路线计划；实际完成范围见 AGENT_HANDOFF.md，具体版本和实现选择在相应阶段讨论确认。

## 每个大章节的执行方式

大章节默认对应下文的九个一级阶段。依据 [学习约定](AGENTS.md#大章节的计划总结与代码框架)，每章按以下顺序组织：

1. 开始前先给出本章各个子项的编号、目标、技术职责、依赖顺序和验收安排，保存到 `chapters/NN-章节名称.md`。
2. 按 [分层推进节奏](AGENTS.md#分层教学与推进节奏2026-11-09) 和[最新架构教学约定](AGENTS.md#架构理解优先2026-11-12-最新调整)实施：工程合并工作包、简讲并集中验收；智能化重点讲整体位置、模块关系和取舍，通过自然讨论回应疑问。工作包明确授权后连续推进，计划调整不扩大授权。
3. 结束时在章节文档保存文字总结，并基于实际源码梳理目录/模块职责、调用关系、状态归属及代表性业务流程；以架构理解为主，不追求语法复述。
4. 遇到影响阅读的目录混乱或业务职责交叉，可按 [代码整理规则](AGENTS.md#按业务职责整理代码) 整理结构并验证，随后修正文档与链接。

具体记录结构见 [章节计划与总结模板](CHAPTER_TEMPLATE.md)。章节完成情况、待答问题和下一步授权统一见 [当前交接记录](AGENT_HANDOFF.md#当前检查点)。

## 学习重心与提速安排（2026-11-09）

依据用户要求，减少前置工程的逐题教学，把主要讨论时间留给智能化搭建。2026-11-12用户进一步要求架构理解优先：不再主动提简单场景/异常边界判断题，重点讲整体设计、模块关系和选择原因。下文“理解题”保留为历史素材，不作为当前讲解或编码的逐题门槛；具体执行以[最新教学约定](AGENTS.md#架构理解优先2026-11-12-最新调整)为准。必要不变量、功能和验证标准不缩减。

| 范围 | 推进方式 | 重点 |
| --- | --- | --- |
| 第 1–2 章已有工程 | 不重教已确认内容，按需要回看代码地图 | 保留持久事实、执行与传输的边界 |
| 第 3 章剩余 3.2–3.6 | 合成“前端收尾”工作包，一次确认后连续实现、集中验收 | 历史、提交、流式、重连、取消、切换隔离形成可用聊天闭环 |
| 第 4 章文档入库 | 常规 Job/Worker/进度工程集中实现，RAG 前置知识重点讲解 | 分块、Embedding、索引、版本发布与引用定位怎样影响后续检索 |
| 第 5–6 章 Agent / RAG | 核心学习阶段，按关键决策深入讨论与案例验证 | Tool calling、权限、召回/融合/重排、证据、LangGraph 路由和有界改写 |
| 第 7–8 章恢复与评测 | 重点讲同一 Run 的图状态恢复，以及可比较的质量闭环 | HITL/Checkpoint、恢复竞态、Dataset、指标、Judge 与失败案例 |
| 第 9 章扩展与交付 | 管理 UI/部署工程加快；新能力和执行边界重点讨论 | Skill、Web/SQL/Sandbox 的能力与授权边界 |

上述为推进方式与工作包安排；实际开始反馈、完成范围及验证结果统一见交接。节奏调整本身不作为应用编码授权，章节总结与源码框架继续保存。

## 1. 工程与领域骨架

### 目标与技术职责

先搭出可迁移、可鉴权、边界清楚的应用骨架，为后续 Run 和 RAG 提供基础。

| 技术或概念 | 负责什么 | 与 Java/Spring 的对照 |
| --- | --- | --- |
| Python 3.12+、uv | 运行时、独立环境、依赖与锁文件 | 类比 JDK 与 Maven/Gradle 的部分职责，工具机制不同 |
| FastAPI、Uvicorn | HTTP 路由、依赖注入入口与 ASGI 服务运行 | 分别类比 Spring MVC/Web 层与服务容器的部分职责 |
| Pydantic、pydantic-settings | 输入输出模型、数据校验、配置解析 | DTO + Bean Validation、配置绑定的部分职责 |
| SQLAlchemy、PostgreSQL | ORM、查询、事务和持久化 | JPA/Hibernate + 关系型数据库的部分职责 |
| Alembic | 把数据库结构变更记录为可执行迁移 | 类比 Flyway/Liquibase |
| Vue 3、TypeScript、Vite、npm | 页面组件、静态类型、开发构建与前端依赖 | 浏览器应用层，与后端独立构建 |
| JSON Schema / Contract | 定义跨端可交换数据的形状与版本 | 接口契约，不是 ORM 表结构，也不是数据库迁移 |
| pytest、Ruff、mypy | 行为验证、格式与静态检查 | 测试与质量工具；各自保证的范围不同 |

新架构概念：User/Tenant 身份边界、薄 HTTP 路由、服务与存储分工、单一 Contract 真源。JWT/Cookie 是认证机制，不代替资源所有权校验。

### 建议小步

1. 讨论 HTTP 请求经过哪些层，说明 FastAPI 与 Spring 注入、生命周期和 async 的差异。
2. 确定 MiniMew 独立的包名、目录、入口、环境、端口和数据存储边界。
3. 建立后端健康接口与最小前端壳；此时不完成聊天 UI。
4. 接 PostgreSQL、SQLAlchemy 与 Alembic，先验证空库升级；按实际小步引入表，不一次复制原项目全部表。
5. 建立最小认证、User/Tenant 资源隔离与角色约束。Access Token 只在浏览器内存，Refresh Token 仅通过 HttpOnly Cookie 传输，服务端保存 hash。
6. 解释领域对象关系，起草本阶段用到的 Contract；后续使用 Event 时从真源生成消费者类型，不手写两套契约。
7. 配置校验、缺失 Secret 时的失败行为和最小测试一起完成。

验收：空库迁移成功；受保护接口拒绝未认证访问；用户不能访问他人资源；配置错误明确报错；前后端契约一致。

理解题：Pydantic 校验通过为什么不等于保存到数据库？用户 A 已登录，为什么仍然不能读取用户 B 的 Thread？数据库迁移与 API 契约变化有什么区别？

参考：[领域语言](../CONTEXT.md)、[Thread 决策](../docs/adr/0021-canonical-thread-lifecycle.md)、[认证决策](../docs/adr/0022-browser-auth-and-inbound-rate-limits.md)。

## 2. 持久化普通对话

本章子项编号、依赖与验收安排见 [第 2 章计划](chapters/02-持久化普通对话.md)。实际进度与小步编码授权以 [交接记录](AGENT_HANDOFF.md#当前检查点) 为准。

### 首次引入的技术与概念

- 模型 Adapter：计划使用 LangChain 的消息/模型接口与 OpenAI-compatible Adapter，具体组合在编码前讨论；它们不负责业务 Run 的持久化。
- SSE：服务端到浏览器的单向事件流协议，用于投影执行事件。
- Redis：低延迟通知与跨进程信号；PostgreSQL 仍是事实来源。
- Event Journal、事务 Outbox、幂等键、append version、Model Snapshot：架构与一致性模式，不是新服务器。
- asyncio、取消与 deadline：Python 的并发与资源生命周期概念，不能简单等同于 Java 线程池。

已有技术继续承担：FastAPI 接口、Pydantic 输入、SQLAlchemy 事务与 Alembic 迁移。

### 建议小步

1. 定义 Thread、Message、Run 的身份、状态和一对消息归属，先画状态与事务边界。
2. 实现 Thread 生命周期与 Run 原子预留：幂等键、预期 Thread version、模型配置与无 Secret 快照。
3. 用注入的 fake Provider 跑通同一正式执行链，验证终结事务，不另外新增简化聊天 route。
4. 持久化有序 Event，并实现按 sequence 查询和 SSE 重放。
5. 增加 Outbox/Redis 通知；即使通知丢失，仍可由 Journal 读回事实。
6. 接入真实模型，关闭 SDK 隐式重试，设立唯一重试所有者、绝对 deadline 与取消检查。
7. 增加基本执行所有权和过期写入保护；第 7 阶段再展开多实例恢复、HITL 和复杂竞态，不能此前允许旧执行者任意写入。

验收：重复提交只产生同一 Run；一轮只有一对归属消息；终态事务一致；断线后可补放；两个 Run 不共享可变请求状态。

理解题：模型返回了答案是否就能在前端宣布完成？SSE 断开为什么不等于 Run 取消？模型配置改了以后，一个已创建 Run 应使用哪一份配置？

参考：[Run Service](../backend/runs/service.py)、[执行器](../backend/runs/agent_executor.py)、[Event Contract](../contracts/run_event_v1.json)、[模型快照决策](../docs/adr/0024-model-control-and-rag-evaluation-runtime.md)。

## 3. 最小聊天前端

本章子项、已有基础与验收安排见 [第 3 章计划](chapters/03-最小聊天前端.md)。实际进度与小步编码授权以 [交接记录](AGENT_HANDOFF.md#当前检查点) 为准。

### 沿用技术与本章新增概念

- Vue、TypeScript、Pinia、Vue Router、Vite 和原生 fetch 沿用阶段 1 的实际实现；当前没有引入 Axios，不为聊天重新安装 HTTP 客户端。
- Event reducer、cursor、gap detection：本章新增的事件投影算法，不是新增后端服务。
- Vitest、Playwright 已在现有依赖清单中，本章扩展状态转换和浏览器交互验证。
- 带 Bearer 的 SSE 长连接与普通 JSON 请求分别处理；流读取、身份恢复和重连边界在对应小步编码前讨论。

### 建议小步

1. 先写纯 Event reducer：按 run_id/sequence 去重，遇到 gap 补放，不提前推进 cursor。
2. 复用已有内存认证、刷新恢复和 Thread 管理，增加消息历史与提交 Run。
3. 接入 Run 创建、流式正文与最终正文覆盖，区分连接状态与 Run 状态。
4. 实现 Stop：提交取消请求后等待权威终态。
5. 处理页面刷新、切换 Thread、退出或切换账号时的状态隔离与订阅清理。

验收：刷新可恢复；Event 重复或补放不会重复追加；不同 Run 不串消息；旧账号响应不能写入新账号状态。

理解题：已应用 sequence=10 又收到 10 应怎么办？下一条直接是 12 呢？为什么网络断开不能把 UI 的 Run 标为 completed？

参考：[前端事件投影](../frontend/src/events/runEventReducer.ts)、[事件流](../frontend/src/events/runEventStream.ts)、[前端质量门禁](../docs/runbooks/frontend-quality-gates.md)。

## 4. 文档入库与发布

本章子项、工程工作包、RAG 前置讨论与验收安排见 [第 4 章计划](chapters/04-文档入库与发布.md)。实际进度与编码授权以交接记录为准。

### 首次引入的技术与概念

- 文档解析器、text splitter、Embedding 模型：先选一种受控文本/HTML 格式，逐步扩展；不一次引入全部文档解析依赖。
- Milvus、pymilvus：向量索引存储与客户端。使用本地 standalone 时，其 etcd/MinIO 属于基础设施依赖，不是应用层领域事实库。
- Embedding 可通过 HuggingFace/Sentence Transformers Adapter 实现；模型 revision、维度与下载/缓存策略须显式确定。
- 独立 Worker、持久 Index Job、candidate/current、manifest、CAS publish：任务与跨存储一致性模式；不默认新增 Celery/RabbitMQ。

### 建议小步

1. 区分 Document 的稳定身份与不可变 Document Version。
2. 上传只保存源文件并提交持久 Job，不在 HTTP 请求中完成解析与嵌入。
3. Worker 领取任务，完成版本化分块、父块元数据、Embedding 与 candidate 索引。
4. 核验 ID/数量与 manifest，再通过数据库事务原子切换 current pointer。
5. 将失败、取消、删除与旧版本清理纳入持久任务和精确版本范围。

验收：可查询进度；重启不会遗忘已提交 Job；新版本失败不污染旧版本；候选发布前不可检索。

理解题：向量已经写进 Milvus，为什么不能立即对用户可见？API 返回接收成功后 Worker 崩溃，任务应保存在哪里？

参考：[发布决策](../docs/adr/0015-document-version-publication.md)、[持久 Worker 决策](../docs/adr/0016-persistent-indexing-worker.md)。

## 5. 基础知识问答

本章子项、已有代码连接点与首轮Agent循环讨论见[第五章计划](chapters/05-基础知识问答.md)。实际理解与编码授权统一见交接记录。

### 首次引入的技术与概念

- Tool calling、Tool descriptor/Registry、Guardrail：在已有模型 Adapter 上增加工具发现、结构化调用与确定性授权。
- Dense retrieval、top-k、Evidence、带来源引用的生成：检索与答案组织方法，不是又一个数据库。
- 基础 RAG 使用单一正式 pipeline，先只启用 Dense；后续策略在同一入口扩展。

### 建议小步

1. 定义最小 Tool 输入输出契约、可见性与执行前权限检查。
2. 将固定 Retrieval Scope 的 Dense 检索封装成知识库 Tool。
3. 返回带 Tenant、Document Version、chunk、内容 hash 的 Evidence，限制上下文预算。
4. Agent 基于 Evidence 作答与引用，明确拒答/证据不足/Provider 错误的语义。
5. 建立最小固定案例集，从此阶段开始记录质量与失败案例，第 8 阶段再产品化评测。

验收：答案可追溯版本；越权查询拒绝；无关问题不会伪造引用；Provider 失败不返回“无知识”。

理解题：普通模型对话与 RAG 各多了哪些步骤？模型看见某个 Tool schema 是否等于有权执行？为什么只保存文件名不能构成稳定引用？

参考：[知识库 Tool](../backend/tools/knowledge.py)、[Evidence](../backend/rag/evidence.py)、[Tool Contract](../contracts/tool_result_v1.json)。

## 6. 完整 RAG 策略

本章6.1–6.7子项、A/B/C工作包、现有Dense连接点及新增策略职责见[第六章计划](chapters/06-完整RAG策略.md)。第六章6.1–6.7已完成并结章；固定案例/对照、实际退化与源码框架见章节总结，真实DeepSeek语义质量未测，章后完整异常反馈专项待实施。具体授权、实际验证与反馈见交接记录；沿用第五章的Run/工具/Evidence/引用链，不另建问答入口。

2026-11-13已收敛[A包实施与验收范围建议](chapters/06-完整RAG策略.md#a包最终实施范围建议2026-11-13待明确开始)：6.1–6.3单一Pipeline/Hybrid索引升级/RRF/有界补全/本机Base重排，保留原片段引用，建议先现有CPU环境基线。尚待明确开始，后续不重问已选模型，也不自动实施GPU/其他模型/外部API。

### 首次引入的技术与概念

- LangGraph StateGraph、条件边、并行 Send：用于有界 RAG 流程编排；与模型 API、业务 Run 和持久 Job 分工不同。
- BM25、RRF、Auto-merging、Rerank：分别解决关键词召回、排名融合、上下文完整性与候选重排。
- Grader 结构化输出、Step-back、HyDE、子问题拆解：证据判断和查询策略。
- Milvus 原生 BM25 复用已有基础设施，不默认加 Elasticsearch；2026-11-13用户已选本机笔记本`BAAI/bge-reranker-base`，保留原片段引用。其他本地模型/设备部署与外部Rerank API须等用户明确提示再执行；选型不代表已授权A包编码或模型下载，实际范围见交接记录。

### 建议小步

1. 用同一批问题比较 Dense 与关键词检索，明确各自失败场景。
2. 在同一检索接口增加 BM25/RRF，再增加有边界的父块合并与 Rerank。
3. 用 Grader 决定回答、无知识、证据不足、澄清或重写。
4. Step-back 与 HyDE 二选一，最多重写并复评一次；不做无界循环。
5. 复杂问题只规划一次，子问题并行检索与评判，再确定性聚合和记录覆盖缺口。
6. 澄清先定义结构化结果；第 7 阶段补齐持久暂停与恢复，不用再次发送原问题冒充 HITL。
7. 每增加一种策略，比较质量、p50/p95、模型/工具调用次数与成本，不假设复杂策略必然更好。

验收：固定案例覆盖各路线；允许的降级可审计；全部 Provider 失败与部分覆盖可区分；预算限制有效。

理解题：RRF 为什么融合名次而不是直接相加两路分数？Rerank 与 Embedding 检索分别做什么？改写为何需要次数上限？LangGraph 和 Run 各自负责什么？

参考：[正式 RAG 图](../backend/rag/pipeline.py)、[Provider 错误语义](../docs/adr/0012-provider-error-and-retry-semantics.md)。

### 第六章结束后的必做专项：完整异常反馈

2026-11-14已交付日志、中文错误说明、实时/历史一致性及故障验证，见[实施记录](chapters/06-完整RAG策略.md#章后专项异常反馈实施记录2026-11-14)。未引用导语与聚合覆盖算法仍待独立改进；下段保留原始范围。

用户于2026-11-13明确要求在第六章结束后补齐后端日志和前端异常展示。范围包括：带Run/阶段/错误码的可关联诊断日志、必要异常堆栈及Secret保护；前端中文失败原因与错误码、适当的恢复操作；实时SSE和刷新历史的一致展示；区分失败、取消、健康无资料及读取/订阅故障。用故障注入与对应后端/前端测试验收，不以HTTP 200/201或仅保存failed状态代替反馈完整性。详细范围与验收见[第六章专项待办](chapters/06-完整RAG策略.md#第六章结束后的必做补齐异常反馈2026-11-13待执行)。当前仅记录，未实现。

## 7. HITL 与故障恢复

### 首次引入的技术与概念

- LangGraph interrupt、Command(resume=...)、PostgreSQL checkpointer：图暂停、恢复与持久检查点。
- lease、heartbeat、fencing token、一次性 HITL token、恢复幂等：在阶段 2 基础上补齐完整恢复与并发协议。
- 同步图与异步 Provider 的桥接是线程/loop 边界设计；是否对齐参考项目的 ProviderLoopBridge，在理解运行模型后确认。

### 建议小步

1. 持久化同一 Run 的 Checkpoint、waiting_input 与 hitl.required，明确事务边界。
2. 提交一次性恢复凭据，恢复原节点，保留原模型快照与 assistant Message。
3. 验证取消、完成、恢复、owner 失效的交错顺序。
4. 验证旧 owner 恢复后被 fence 拒绝，过期任务按已声明协议恢复或终结；不能把任意工具副作用重放描述为安全恢复。
5. 用同步屏障控制竞态，而不是靠固定 sleep 猜调度结果。

验收：刷新/进程重启后可恢复受支持的暂停状态；重复 resume 不重复执行；旧 owner 不能追加；只有一个权威终态。

理解题：用户补充信息为什么不能创建新 Run？旧 Worker 网络恢复后如何证明它无权继续写？Checkpoint 是否等于整台进程的内存快照？

参考：[Checkpoint Runner](../backend/rag/checkpoint_runner.py)、[Resume](../backend/runs/resume.py)、[Run Repository](../backend/runs/repository.py)。

## 8. RAG 评测闭环

### 首次引入的技术与概念

- Dataset/Observation/Report schema、fingerprint、provenance、Gate、baseline：评测事实、可比性与发布政策。
- Evaluator 角色与结构化 Judge：模型辅助评分；仍需人工期望和失败审查。
- 独立 Evaluation Worker 复用 Job/lease/fencing 模式，不另建在线 RAG 实现。
- Vue/Pinia、Pydantic、PostgreSQL 沿用已有栈，评测工作台本身不要求换前端框架或新增评测 SaaS。

### 建议小步

1. 扩展阶段 5 的固定案例，覆盖可回答、无知识、歧义、冲突与部分覆盖。
2. 先验证离线评分与 fingerprint mismatch，再显式运行隔离 live evaluation。
3. 冻结 Dataset、模型、语料版本、索引与策略，记录可比 provenance。
4. Evaluation Job/Worker 逐 Case 调用正式 RAG，生成 Observation、Judge 与 Report。
5. 增加取消、进度、失败 Case、baseline 比较和前端工作台。

验收：离线结果可确定性重建；不可比 baseline 被拒绝；live 报告来源可追踪；评测故障不会变成零知识案例。

理解题：静态 Observation 评分通过为什么不能证明真实检索有效？换了语料或 Dataset 后能否直接宣称比旧 baseline 提升？LLM Judge 为什么不能代替所有人工验收？

参考：[评测说明](../evals/rag/README.md)、[模型与评测决策](../docs/adr/0024-model-control-and-rag-evaluation-runtime.md)。

## 9. 扩展能力与交付

### 首次引入的技术与概念

- Skill manifest、版本/hash pin、按需工具披露：能力封装与加载协议。
- Tavily Search/Extract、Run-local Source ID：Web Research 的受控来源获取。
- SQLGlot AST 与独立只读数据库账号：SQL 工具的语句与权限边界。
- Docker Sandbox、固定 digest、资源限制：已授权代码执行的隔离设施。
- Docker/Compose、同源静态托管、readiness、进程监督和依赖审计：交付与运行治理。
- 模型/能力控制面 UI 复用既有 Vue、Pinia、数据库与快照机制，并非新架构替代品。

### 建议小步

1. 先补完整模型/能力管理 UI、Skill 加载和安全文件读取。
2. 按独立子步骤讨论 Web、SQL、Sandbox 的收益、网络与权限边界，每项通过理解检查后再实现。
3. Web 只使用同一 Run 的 Source ID；SQL 使用只读账号、AST、allowlist 与预算；Sandbox 在 Guardrail 之后运行。
4. 完成 API、Indexing Worker、Evaluation Worker 的独立启动、共享存储与关闭协议。
5. 执行迁移、Contract 生成检查、离线质量门禁，再在明确环境运行 PostgreSQL/Redis/Milvus/Sandbox 与真实模型检查。

验收：拒绝/审批发生在 handler 前；密钥不进入公开数据；三个进程可以独立运行；真实基础设施故障被显式报告。

理解题：Sandbox 为什么不能替代授权？模型中心修改后为什么不能影响已经暂停的 Run？容器 healthy 与业务端到端可用有什么区别？

参考：[Skill/Tool](../docs/runbooks/skill-tool-registry.md)、[Web Research](../docs/runbooks/web-research.md)、[SQL](../docs/runbooks/sql-assistant.md)、[Sandbox](../docs/runbooks/guardrails-and-sandbox.md)、[部署](../docs/runbooks/deployment.md)。

## 每个小步的完成记录

每次只记录实际发生的结果：

- 用户已能解释的目标、数据流与失败场景，以及仍有疑问的地方。
- 用户明确授权的实现范围。
- 改动的正式入口、必须保持的不变量与相关迁移/Contract。
- 实际新增或修改的测试，以及运行命令和结果。
- 未运行的集成/在线检查及原因，不用 fake 结果替代真实协议证据。
- 下一小步的讲解目标与待回答问题。
