# MiniMew Agent 交接：学习约定、知识结构与当前检查点

最后整理：2026-11-04。本文记录真实对话、授权范围与已验证实现，可随 Git 同步。

## 新 Agent 的阅读顺序

1. 仓库根 `AGENTS.md`：项目约束与指南路由。
2. 本目录 `AGENTS.md`：先沟通、检查理解、再编码的规则。
3. 本文：用户背景、已理解内容、待答题与授权边界。
4. `LEARNING_PLAN.md`：九阶段目标、技术职责与验收。
5. 进入具体模块后读取最近的 `AGENTS.md`，按需核对 `../CONTEXT.md`、Contract、ADR 和源码。

全文使用相对路径。不要要求用户重新粘贴原聊天，不要依赖旧设备的 Agent memory、终端状态或运行服务。

## 用户的目标与学习基础

- 目标：从零理解并逐步复现 SuperMew，学习实现放在 MiniMew。
- 已有 Java/Spring 开发经验；具备 Python/FastAPI 基础但不深入。
- 可用 Spring 的熟悉职责解释 Python 工具，但要讲清 async、依赖生命周期、事务等实际差异。
- 学习重点在 Agent、RAG、持久化执行、故障恢复和评测，基础工程按需补充。
- 用户最新明确要求：前端暂时只了解架构，不深入分析语法或实现细节。后续前端讲解聚焦模块职责、状态归属、数据流与前后端衔接，不以 Promise、generation 等底层细节的复述作为继续学习门槛；该偏好不扩大编码授权。
- 用户明确要求频繁询问、充分沟通，确保能理解意思和项目知识结构后，再开始相应阶段编码。
- 用户于 2026-10-30 新增章节约定：每个大章节开始前先计划各子项安排，结束时必须保存文字总结并梳理实际代码框架；讲清每一部分的职责和协作，不追求语法。文件结构混乱、业务整理不足或可读性差时允许按业务职责整理。规则真源见 AGENTS.md，章节文档使用 CHAPTER_TEMPLATE.md；此约定不扩大后续功能编码授权。
- 用户于 2026-10-30 反馈功能开发测试耗时过长，明确要求按影响范围优先定向测试，事务/并发/一致性变更扩大集成覆盖，仅有回归风险或涉及核心模块时完整回归，不为通过而修改或删除断言，不重复执行条件未变的已通过测试，并区分新增、重复、通过、跳过和未执行。完整规则统一维护在 [AI 编程测试策略](AGENTS.md#ai-编程测试策略)，README 与章节模板同步执行口径；本次为文档规则更新，不扩大当前 2.4 的应用编码授权。

## 每次开发前的沟通协议

每个阶段和必要的子步骤，都依次完成：

1. **目标**：用户最终能做什么，本次只实现哪一小步。
2. **知识结构**：它属于哪条业务链，依赖哪些已知对象，与其他模块如何衔接。
3. **技术职责**：哪些库沿用、哪些首次引入；区分框架、数据库、协议、算法与架构模式；说明选它的理由。
4. **数据流**：请求如何进入，身份从哪里来，哪些状态写在哪里，谁负责权威结果。
5. **失败场景**：重复提交、断线、超时、越权、进程退出时应发生什么。
6. **理解检查**：每轮 1–2 道具体场景题，等待用户解释；发现概念混淆后补讲，再换场景检查。不能只问“懂了吗”。
7. **开始编码**：总结已理解的范围与待实现内容，获得明确开始反馈后执行。授权只覆盖该范围。
8. **复盘**：解释代码如何体现已讨论模型，说明实际测试与未验证项，更新本文后再进入下一步。

用户回答“懂了”同时给出正确场景解释，可以确认对应知识点；不能据此宣布整个阶段全部掌握。无法判断理解程度时继续讨论。允许先写学习文档，但不提前生成应用代码、依赖、迁移、配置或测试骨架。

## 章节计划与总结的新增执行约定

- 大章节默认对应九阶段路线的一级阶段，2.1/2.2/2.3 等是章内子项。新章开工先展示并保存子项计划，结章保存文字总结和基于当时真实源码的代码职责梳理；记录位置为 `chapters/NN-章节名称.md`，采用 [模板](CHAPTER_TEMPLATE.md)。详细规则只在 [AGENTS.md](AGENTS.md#大章节的计划总结与代码框架) 维护。
- 第 2 章中途新增本约定后，用户回复“ok，继续之前的任务”。已补录 [第 2 章计划](chapters/02-持久化普通对话.md)，列出 2.1–2.9 的内容、依赖、技术职责与验收安排，并给出当前源码入口导航。该文档不另行维护进度；第 2 章结束后再保存结章总结与完整代码框架，不追认历史总结或未完成验收。
- 为改善可读性可整理 MiniMew 目录与业务职责，并同步引用、测试和文档；不借此加入尚未讲解或授权的业务功能。该规则整理当时只修改文档；后续 2.3 的明确授权和实施见当前检查点与对应记录。

## 已讨论的项目知识结构

### 三条业务链

- **对话执行**：用户 → Thread → Run 与模型快照 → Agent/Tool → Event Journal → SSE → 前端投影 → 持久化最终 Message 与 Run 终态。
- **知识入库与问答**：文档上传 → 不可变 Document Version / Index Job → 独立 Indexing Worker → 解析、分块、Embedding、索引核验 → 原子发布 → RAG → Evidence → 回答与引用。
- **评测**：Dataset 与冻结配置 → Evaluation Job → 独立 Evaluation Worker → 复用正式 RAG → Observation / Judge → Report、baseline 与 Gate。

### 对象与边界

| 对象 | 已讲解的含义 | 容易混淆之处 |
| --- | --- | --- |
| JWT Access Token | 请求的身份凭据 | 它不是对话；JWT 原文不一定需要入库才能验证 |
| Thread | 属于用户的一段持续对话容器 | 不是 JWT，也不是 Java 的执行线程 |
| thread_id | 对话资源标识 | 知道 ID 不代表有访问权限 |
| Message | 对话中的一条消息记录 | 输入校验通过不等于已持久化；写入还需事务提交 |
| Run | 一次可持续追踪的 Agent 执行 | 不等于一次模型 API 调用；其中可调用多次模型或工具 |
| Event | 单个 Run 的有序持久执行事实 | SSE 是传输/投影，断流不是取消或完成 |
| Checkpoint | 可恢复图节点的持久状态 | 不代表整个进程的内存快照 |
| Document Version | 文档的不可变内容版本 | 上传成功不等于已发布可检索 |
| Model Snapshot | 创建工作时冻结的无 Secret 模型配置 | 当前控制面变更不改写已经创建的工作 |

表中部分概念仅在路线介绍时出现，**不代表用户已经通过理解检查**。实际理解状态见下一节。

### 实现层次

- FastAPI / Pydantic：HTTP 入口与输入输出校验。
- Service / Repository / SQLAlchemy / PostgreSQL：业务规则、所有权、事务与持久事实。
- Agent Runtime / Provider / Registry / Guardrail：模型调用、上下文预算、工具可见性与执行授权。
- LangGraph / RAG：条件路由、检索评判、有限改写、子问题并行与检查点恢复。
- Vue / Pinia / Event reducer：按身份与 sequence 投影界面。
- Redis：通知、缓存、跨实例信号与限流；不代替 PostgreSQL 的领域事实。
- Milvus：Dense 与原生 BM25 索引；不代替文档发布目录。

技术细节以当前源码为准，之前的架构图只经过静态阅读，并未证明当前机器服务已正常运行。

## 当前检查点

- 阶段：第 2 章实现、证据核对和本轮结章复盘完成，[整章总结与代码框架](chapters/02-持久化普通对话.md#结章文字总结) 已保存。用户已正确把“Journal 持续增加但页面不更新”优先定位到通知/SSE/浏览器消费链；已校准这不能证明执行器所有行为均无问题。现在进入 [第 3 章开章计划](chapters/03-最小聊天前端.md) 与 3.1 事件投影讨论，尚无第 3 章应用编码授权；历史其他未答题不因此预填通过。
- MiniMew 状态：独立后端、PostgreSQL、Redis 7.4.12、Alembic 0010、认证/Thread/Run/Message、冻结快照、流式执行/租约/原子结果、Journal/Outbox 与私有分页/SSE。重启后的默认 API 自动执行并支持按 Run 取消，含持久取消请求、独立监视、终态竞争和取消感知的过期收尾。通知任务仍不调用模型。开发库已升级，用户已有后端未被重启；Vue 仍只有账户与 Thread 管理，聊天/停止按钮待第 3 章。
- 应用编码授权：第 2 章既有授权已实施；当前仅整理结章和开章文档，尚未授权 3.1 的 reducer/测试，更未授权聊天/停止 UI、HITL/接管或 Agent/RAG。用户既有 DeepSeek 选择及关闭思考的要求保持。
- 当前没有 commit/push 授权。此前创建个人 GitHub 仓库并发布项目是历史单次授权，不沿用到本次实现。

### 已通过的理解检查

用户起初把 Thread 与 JWT 登录凭据混淆。经过解释，用户明确回答：

> thread 是管理对话的容器，thread 要和用户、对话绑定。
> 不需要重新登录，使用同一个 JWT，但是两个对话的 thread_id 不同。
> 不行，thread 中绑定着用户信息，可以被校验。

据此可以确认：

- Thread 是用户拥有的对话容器，与身份令牌不同。
- 一个登录身份可以访问自己的多段对话。
- 有效 JWT 不自动授权读取别人的 Thread，需要服务端校验归属。

说明已经讲过：Pydantic 校验、数据库写入与事务提交是不同步骤；当前项目使用认证依赖识别用户，业务/存储层按 Thread ID 和用户归属限制访问。用户尚未独立复述持久化边界，仍需后续检查。

用户随后回答：

> 1 个 Thread、2 个 Run；没有 ToolMessage 的话是 4 条 Message。
> 1 个 Run，一个 Run 能调用多次工具。

据此确认本轮对象计数与执行边界检查通过。进一步说明：LangChain 图内的 ToolMessage、中间 AIMessage 与产品持久 Thread Message 属于不同层次；当前项目持久用户/assistant Message 的计数不因内部工具调用直接增加。

### 当前疑问与待检查内容

用户追问：以前学习 LangChain/LangGraph 时，没有显式使用业务 Run，每次 invoke 仍能读取历史，为什么还要引入 Run？

本轮重点解释：历史由显式传入的 messages 或同一 thread_id 的 checkpointer 等机制提供；业务 Run 管理本次执行身份、状态、取消、幂等与模型快照。Checkpointer 也支持恢复和容错，不应错误地说它只保存聊天记录；但它不自动定义当前产品的用户归属、Run ledger 和权威终态协议。尚未看到用户当时的具体 invoke 示例，不断言其使用哪一种历史机制。

用户曾认为 Run 的目的主要是持久化，追问既然已有 checkpointer 为什么还需要 Run；并询问“下一次从上次 checkpointer 开始是否算取消”。

本轮补讲重点：Run 是本次业务执行的身份与生命周期记录，checkpointer 是保存/读取图状态的机制；保存状态不等于运行中的协程已停止。两台设备通常都是客户端，连接同一后端，取消请求按 run_id 定位后端执行。Run 记录本身不会自动中断任务，仍需执行器的任务取消、取消信号与检查，以及最终状态事务；从旧 checkpoint 另起执行不停止原执行，也不会回滚已完成副作用。简单本地 invoke 可不定义业务 Run，但平台需求需要相应执行管理职责，不应把自建 Run 描述成框架的强制前提。

用户随后说明 Run 用于增加查询和操作，涉及持久化与中断；回答从旧 checkpoint 开始不会自动停止旧执行，并指出需要 run_id 精确定位。Run 的业务管理用途与恢复不等于取消的方向已理解。

用户澄清“停止进程”只是口误，明确表示取消 A 的任务，B 不受影响；该边界已确认，不再反复检查。已解释 Run ID 不是操作系统 PID，取消仍依赖实际任务引用/取消信号。

关联记录的事务回滚已通过：用户直接对应 Spring 的 `@Transactional`，说明 Run 与其关联 Message 应一起回滚。普通事务原理无需重新教学，编码时解释 SQLAlchemy 写法差异即可。

事务边界已讲解：长事务在简单场景能运行，但等待外部调用仍占数据库连接并延长已持有锁的时间；未提交 Run 通常不可供其它事务查询；数据库回滚不能撤销模型费用或工具外部副作用。用户已认可长耗时模型/多工具任务应拆分，并明确回答模型调用无法被数据库回滚。

用户对重试的补充是“没解决问题也是一样失败，模型看不见第一次失败的对话”。需校准：暂时故障恢复后重试可能成功；模型是否看到前次内容取决于实际传入 messages/恢复状态，不能据此推断前次调用和副作用被撤销。

幂等已讲解：用“创建 Run 成功但 HTTP 响应丢失，客户端重试”解释，同一用户/Thread 下同一提交意图复用 idempotency_key，只创建一个 Run 和一对业务 Message；新的提问意图用新 key，即使文字相同。不同 payload 不能任意复用同一 key；并发去重需要数据库约束和事务。Run 创建幂等不自动保证外部模型/工具只执行一次，后续仍要处理执行恢复与外部副作用。

用户已正确回答：网络超时重发使用原 key；前次回答完成后主动再次提交相同文字使用新 key。该场景检查通过。

Contract 已用 DTO 类比讲解：关键跨端数据的字段、类型、约束和语义约定；JSON Schema 真源生成 Python/TypeScript 消费者，生成类型不能替代运行时校验。用户理解 DTO 类比，回答 TypeScript 不能保证自动拦截，但认为前端会解析失败。已补充：字符串形式的数字仍是合法 JSON，JSON.parse 可成功，必须显式校验才会按契约拒绝；不能把 JSON 语法解析和类型/语义校验混为一谈。该细节已解释，尚无独立复述，不重复考普通 DTO 原理。

用户明确表示 FastAPI 路由容易理解，不用介绍。Depends 写法已补充：接收依赖 callable，由 FastAPI 在请求处理中解析参数、调用并注入返回值；传函数本身，不提前加括号调用。Python async 已解释：async def 不自动启动线程或将同步阻塞调用异步化；等待异步 I/O 时协作让出事件循环；FastAPI 管理的普通 def 路由/依赖会用线程池，直接调用的同步 helper 不会自动转移线程。

用户选择显式线程池，并在追问后确认“会”指显式调用 run_in_threadpool 后才进入线程池；此边界检查通过，不再重复考。随后用户明确授权“开始 1.1”。

### 第 1.1 小步实现与验证

已建立可独立启动的最小后端：MiniMew 内独立 pyproject.toml、uv.lock、虚拟环境；Python 包放在 src/minimew；FastAPI app、pydantic-settings 配置、通过 Depends 提供配置，以及 GET /health/live 存活接口。使用 Uvicorn 工厂模式启动，端口 8001，启动前检查无监听冲突。数据库、迁移和认证属于之后各自讨论的小步，本次范围不包含它们。

数据流：Uvicorn 调用 create_app → Settings 读取 MINIMEW_ 环境变量并校验 → 配置保存在当前 app.state → 注册 health 路由；请求到达后 Depends(get_settings) 返回该应用已有配置 → 路由返回 LivenessResponse。每个应用独立持有不可变配置，不逐请求重读环境变量，不自动加载 .env。配置非法即拒绝创建应用。

复用本机 uv 0.11.14 和 Python 3.12.13，新建 MiniMew/.venv。uv 锁定 FastAPI 0.162.0、Uvicorn 0.54.0、Pydantic 2.14.1、pydantic-settings 2.16.1；开发工具为 pytest、httpx、Ruff。没有复用上级虚拟环境或导入 backend。

实际验证：

- `uv run --no-sync pytest -q`：5 passed；覆盖默认响应、两个 app 配置隔离和读取时机、两个非法配置场景、不隐式读取 .env。
- `uv run --no-sync ruff check src tests`：通过。
- `uv run --no-sync ruff format --check src tests`：6 个文件通过。
- 启动独立 Uvicorn 子进程，真实 HTTP GET 返回 200 与 `{"status":"ok","app_name":"MiniMew"}`；检查后关闭本次创建的进程。
- pytest 有一条第三方弃用警告：Starlette 提示未来使用 httpx2 替代 httpx TestClient 适配；当前测试通过，后续依赖维护时处理。

环境问题：沙箱默认临时目录/缓存不可写，改用忽略的 .artifacts；下载受网络限制后通过工具审批完成。沙箱内测试/启动检查未正常完成，切换完整访问运行后上述检查通过。用户也明确表示已改成完全访问权限。

未运行上级全仓库门禁、数据库或模型集成检查：本次不接入它们。无 Contract 真源、迁移或评测资产变更。README 已记录启动与验证命令；无提交或推送。

### 接下来怎么继续

用户实测 1.1 正常返回；澄清 create_app 是函数名、MINIMEW_APP_NAME 才是环境变量后，回答“不重启还是 A”，并理解 live 仅代表 HTTP 服务可响应，1.1 复盘通过。

用户有 Flyway 经验。已解释 SQLAlchemy Session 是数据库会话而不是登录凭证；用户回答两个请求“各自使用”Session。选择 Docker 独立数据库，并明确“先创建好数据库基础连接后续再建表”。已解释短事务、Engine 共享、请求 Session 独立，以及 live/ready 的区别；故障题回答正确后明确开始 1.2。

### 第 1.2 小步实现与验证

- `compose.yaml`：项目 minimew，官方 PostgreSQL 18 镜像锁定 digest，独立数据库/账号 minimew、数据卷 minimew_postgres_data，本机 127.0.0.1:55432。
- 复用已有 Docker 与 PostgreSQL 18 镜像，新建容器/卷/网络。生成本机随机密码到忽略的 `.env`，没有输出密码；`.env.example` 保留空密码占位。账号是本地开发初始化管理账号，不是生产最小权限方案。
- 增加 SQLAlchemy asyncio、asyncpg、Alembic，使用 uv 更新 manifest 和锁文件。
- `database.py`：URL.create 处理密码特殊字符，应用持有 Engine/Session 工厂；Depends 的 get_session 使用 yield 提供请求独立 Session，关闭时回滚未提交事务，不隐式 commit。lifespan 退出时释放 Engine。
- 配置新增 MINIMEW_DB_*，密码必填且 SecretStr 隐藏表示；通过 `uv run --env-file .env` 显式加载配置，不自动寻找上级 .env。
- `/health/ready` 有界 SELECT 1，数据库不可用返回无敏感信息的 503；不判断迁移状态或模型可用性。`/health/live` 保持不访问数据库。应用启动不连接数据库、不自动迁移。
- Alembic 独立配置和异步迁移环境，初始 0001 无业务 DDL；真实库只有 alembic_version。后续模型引入时再接 target_metadata。

实际命令与结果（2026-10-28）：

- `docker compose up -d --wait`：healthy，数据库保留运行。
- `uv run --env-file .env --no-sync alembic upgrade head`：空库成功；重复执行成功；`alembic current` 为 0001 (head)。
- `uv run --no-sync pytest -q`：13 passed；配置脱敏与必填、存活、ready 成功/故障/超时、请求 Session 隔离与清理、单 head/离线初始迁移；不连接外部基础设施。
- `uv run --no-sync ruff check src tests migrations scripts` 与 `ruff format --check src tests migrations scripts`：通过。
- `uv run --env-file .env --no-sync python scripts/check_database.py --stop-database`：真实 PostgreSQL 临时表验证提交、异常回滚、未提交关闭回滚；验证 ready 200 → 停库 live 200 / ready 503 → 恢复同一 app ready 200。临时表不增加持久业务表；故障检查只停本次新建的 minimew postgres。
- 保留此前的 httpx TestClient 第三方弃用提示，当前测试通过。

已有 8001 监听属于用户启动的服务，未停止或替换；用户需要 Ctrl+C 后按新 README 带 --env-file .env 重启，才能访问新 ready 接口。本次 API 验证通过 TestClient 进入真实应用 lifespan 并访问真实数据库，不声称已替用户重启 8001 服务。

未运行上级全仓库门禁、前端/认证/模型测试，因不在本次范围。无上级 Contract 或评测资产变化；新增迁移和 README 已同步。没有 commit/push。

1.2 复盘已通过：用户回答未提交的写入不会保留，已解释正常返回接口不代表提交，session.begin 正常退出才提交、异常退出回滚。

### 第 1.3 小步实现与验证

用户理解租户是一层业务空间后选择“先做面向个人的”；明确同租户也不能读取他人私人对话。解释外键与授权的区别后，用户回答“只写 WHERE threads.id = :thread_id 还是能越权读取”。随后明确授权“开始 1.3”。不再重复考这些已通过的概念。

实现：

- `models.py`：统一 Base；User(id, created_at)、Thread(id, user_id, title, created_at)。UUID 和带时区时间由 PostgreSQL 默认值生成，INSERT/flush 后取得。title 必填、最长 200 字符，暂允许空字符串，不引入标题业务流程。
- Thread.user_id 非空，命名外键指向 users.id，ON DELETE RESTRICT；索引 ix_threads_user_id。外键不代表访问控制，未实现任何用户查询接口。
- 新增独立 0002 迁移，不改写 0001；迁移环境接 Base.metadata 并比较服务器默认值。没有启动自动建表或 create_all 旁路，无新依赖。
- 更新数据库验收脚本预期表集合和版本；新增 check_models.py 在随机临时数据库验证迁移/ORM/约束，finally 清理，仅对临时库做 downgrade。

验证（2026-10-29）：

- `uv run --no-sync pytest -q`：14 passed，保留已有第三方 httpx 弃用提示。
- `uv run --no-sync ruff check src tests migrations scripts` 与 `ruff format --check src tests migrations scripts`：通过。
- `uv run --env-file .env --no-sync python scripts/check_models.py`：空库至 head、0001 至 head、重复升级、临时库 downgrade、metadata 一致性、ORM 读写、UUID/时区、外键/非空/删除限制/索引均通过；临时库已清理。
- `uv run --env-file .env --no-sync alembic upgrade head`：开发库 0001 升到 0002；`alembic check` 无新增升级操作。
- `uv run --env-file .env --no-sync python scripts/check_database.py`：0002 表集合、事务与清理、真实数据库 ready 200 通过。本次不重复停库检查，未改动健康接口逻辑。

环境处理：发现当前 .env 密码与数据库实际密码不一致，未输出密码或覆盖用户 .env。用户明确选择“将数据库密码更新为当前 .env 的值”，已完成 ALTER ROLE 并用当前 .env 验证。容器创建时的环境记录不会因 ALTER ROLE 自动更新；后续应以实际登录验证为准，不把旧容器环境中的密码视为现行密码。

测试开发中修正了回滚后读取过期 ORM 属性导致隐式异步 IO 的问题，先保存 UUID 再断言；删除保护兼容 PostgreSQL RESTRICT 的 23001 与外键 23503 SQLSTATE。最终通过，不保留失败测试结论。

文档、迁移和验收脚本已同步；无上级 Contract、认证、评测资产或应用改动。未运行上级全仓库/模型/前端检查，超出范围。未 commit/push，也未重启用户 API。

1.3 复盘通过：用户回答“不会，flush 不提交事务，有异常会回滚”。

### 第 1.4 第一小步实现与验证

用户选择用户名＋密码登录。JWT 讨论中最初把验签失败理解为查找不到用户；补讲解析与验签后，用户明确回答“没有密钥不能生成有效签名，卡在签名有效性上”。随后确认不给旧用户默认密码，并明确授权本小步。后续不要重复考这一区别。

实现边界：

- 新增依赖 argon2-cffi 25.1.0、PyJWT 2.15.1，uv 管理声明和锁文件。密码使用带随机盐 Argon2id；通过 run_in_threadpool 计算和验证，避免同步哈希阻塞事件循环。
- `User.username` 非空唯一（uq_users_username），`password_hash` 非空；无明文密码、无 Token ledger。本步注册不自动登录。
- `POST /auth/register` 返回 201 公开用户字段，用户名规范化 trim/lower 后仅允许 3–32 个 ASCII 字母、数字、下划线；注册密码 12–128 字符，不裁剪。并发重名由数据库约束返回 409。
- `POST /auth/login` JSON 输入；错误用户名与错误密码统一 401，不存在用户也验证一次 lifespan 创建的随机虚拟哈希。成功返回 no-store 的短期 access_token、bearer 类型、expires_in。
- `GET /auth/me` 使用 CurrentUser/Depends，固定 HS256 验签并检查 sub/iat/exp/iss/aud/jti/token_type，随后确认用户存在。失败返回 401 + WWW-Authenticate: Bearer。不接受 Token 声明的其他算法，无不验签身份路径。
- `MINIMEW_JWT_SECRET` 必填、至少 32 字符；已仅向本机忽略的 .env 添加独立随机密钥，没有覆盖数据库密码或输出 Secret。Access Token 默认 15 分钟（配置 1–60），前端未来只能放内存。
- 输入校验错误返回通用 422，避免 FastAPI 默认 input 字段回显密码。API 公开模型不包含 password_hash。
- 0003 迁移在 ACCESS EXCLUSIVE 锁内检查旧用户；非空则抛异常并事务回滚，不填默认凭据、不删用户。空库添加凭据字段与唯一约束。0003 禁止 downgrade，避免静默丢弃凭据。

验证（2026-10-29）：

- 开工只读查询 users_count=0，因此真实开发库可直接升级；升级后仍为 0，未留下测试账号。
- `uv run --no-sync pytest -q`：31 passed、3 skipped（默认跳过真实 PostgreSQL）；保留已有 httpx 弃用提示。
- `uv run --env-file .env --no-sync python scripts/check_models.py`：两条临时库迁移路径、metadata 一致性、模型与约束均通过；旧用户/Thread 存在时迁移拒绝且旧记录、0002 版本、列结构保留；临时库已清理。
- 上述脚本显式运行 `tests/test_auth_pg.py`：3 passed。覆盖真实注册→登录→me、数据库盐化哈希/公开响应字段、用户删除后 Token 拒绝、错误密码/未知用户统一响应、并发同名注册仅一条记录。
- 离线安全验证覆盖篡改、过期、错误密钥、错误算法、none、缺失 exp、错误 issuer/audience/用途、非法 subject、未来 iat、畸形 Token；无效 Token 在查询用户之前拒绝。缺失用户和缺失 Bearer 拒绝；密码/签名密钥不回显。
- `alembic upgrade head` / `alembic current`：开发库 0003 (head)；`alembic check` 无新增升级操作；`scripts/check_database.py` 实际数据库 ready 200 与事务清理通过。
- Ruff check / format --check（src tests migrations scripts）：通过；git diff --check 通过。

已更新 README、.env.example、迁移与集成检查脚本。未改动上级 Contract、认证实现或评测资产；未运行上级全仓库、前端、Agent/RAG 检查。未 commit/push，未重启用户现有 API。当前是本地学习认证版本，Refresh/Cookie/撤销、登录限流与浏览器入口保护仍未实现，不宣称生产认证完成。

第一小步复盘：用户理解密码验证不需要解密；补讲盐和参数后，回答相同密码的哈希“不一定”相同，但“可以”各自登录。该理解点已确认。

### 第 1.4 第二小步实现与验证

已通过的讨论：用户明确理解撤销 Refresh Token 只阻止续期，尚未过期的 Access Token 仍有效；已轮换的 R1 不能再次刷新；已自然过期的凭据只拒绝本次请求，不撤销其他凭据。已讲解未过期已撤销凭据重放时撤销用户所有有效 Refresh Token，以及必须先提交撤销再返回 401。用户随后明确授权开始第二小步。

实现：

- 0004 新增 refresh_tokens（摘要主键、用户外键及索引、创建/过期/撤销时间）。数据库只保存 SHA-256 摘要；原始值由 secrets.token_urlsafe(32) 生成，只经 HttpOnly Path=/auth Cookie 传输。用户删除级联清理凭据；迁移不修改既有 User/Thread 数据，禁止降级丢弃撤销记录。
- `refresh.py` 是统一生命周期实现，`auth.py` 在其事务提交后设置 Cookie 或返回 401。签发、轮换与退出统一 User → RefreshToken 锁序，锁等待后读取 PostgreSQL clock_timestamp 判断过期。签名或写入失败会回滚旧撤销及后继插入。
- 登录签发 Access/Refresh 对；注册仍仅创建用户。refresh 自然过期优先；重放撤销用户其他有效刷新凭据后正常提交，再返回 401。缺失/畸形/未知/过期/重放返回统一 401 并清 Cookie。
- logout 只撤销当前 Cookie 指向记录，缺失、重复或畸形也返回 204 清 Cookie，不触发用户级撤销。Access Token 不即时失效。没有 logout-all 接口。
- Cookie 固定 SameSite=Lax、HttpOnly、Path=/auth、无 Domain；默认 7 天（配置 1–30 天），每次轮换新有效期。MINIMEW_REFRESH_COOKIE_SECURE 本机 HTTP 可为 false；production 必须为 true。
- Cookie 自动发送，因此同步加入认证 POST 同源检查（Origin/Referer、Fetch Metadata）和认证正常/可处理错误的 no-store 响应头；不允许跨源 Cookie。没有浏览器元数据的 CLI 可用。
- 不引入新依赖、Redis 或前端；没有自动清理刷新记录，保留撤销记录用于重放判断。登录限流和完整生产入口治理仍待后续。

实际验证（2026-10-29）：

- `uv run --no-sync pytest -q`：43 passed、14 skipped（真实库用显式脚本另跑）；保留已有 httpx 第三方弃用提示。
- `uv run --env-file .env --no-sync python scripts/check_models.py`：14 个真实认证测试通过，覆盖轮换/Cookie/只存摘要、未知凭据/Access Token 不能刷新、过期与已撤销但自然过期、用户级重放撤销提交、其他用户不受影响、退出幂等、两个 app 并发刷新、刷新与退出并发、签名失败和 flush 后失败的原子回滚、跨源拒绝不改凭据。
- 同一 Refresh Token 的两个并发请求得到 200/401，仅生成一个后继，重放处理随后撤销后继；用新数据库连接确认提交。
- 空库→0004、旧无凭据用户阻断 0003、已有 0003 注册用户升级到 0004 保留、重复迁移和 metadata 一致性均通过。测试用随机临时库已清理。
- 开发库 `alembic upgrade head` 完成；`alembic current` 为 0004 (head)，`alembic check` 无新操作。`scripts/check_database.py` 事务清理与真实 ready 200 通过；刷新表记录数为 0，没有留下测试凭据。
- Ruff check / format --check（src tests migrations scripts）及 git diff --check 通过。

重要边界：刷新若先成功，携带旧 R1 的 logout 不会自动变成针对新 R2 的退出。前端必须等待在途刷新完成，再携带最新 Cookie 退出；跨标签刷新也需协调。已将此边界告知用户并写入 README，未宣称前端竞态已解决。Cookie 标志与 HTTP 行为通过 TestClient 验证，未运行真实浏览器 E2E（尚无前端）。

README、.env.example、模型、迁移和验收脚本已同步；不修改上级 Contract/认证/评测资产。未运行上级全仓库门禁或 Agent/RAG 测试，因超出范围。未 commit/push，未重启用户已有 API。

复盘通过：用户回答在 session.begin 块内直接抛 HTTPException(401) 会“回滚”；已确认必须先正常提交撤销再返回 401。

### 第 1.5 小步：Thread 业务入口

用户要求新建对话默认显示“新对话”，之后取第一条用户消息为标题；进一步明确“模型回答失败时，标题仍改为这条用户消息的内容”。已说明第 1.5 只完成默认标题，首条消息触发更新留到第 2 阶段，在消息保存事务内接入。方案包含去除首尾空白、换行合并为空格、超过 30 个字符截断加省略号，后续消息不覆盖。用户随后明确授权“开始 1.5”。

本步复用 FastAPI、Pydantic、SQLAlchemy、PostgreSQL 和 CurrentUser，无新增依赖。唯一 HTTP 入口为 `src/minimew/api/threads.py` 的 `/v1/threads`：POST 接受空对象，owner 从认证上下文获取；GET 列表限制当前用户，默认每页 20、最大 100，按 created_at/id 倒序；GET 单个同时按 UUID 与 user_id 查询，越权与不存在都返回相同 404。创建提交成功后才返回 201，禁止客户端传入 owner/ID/title。列表 offset 翻页不承诺并发插入时的快照一致性。

复用 0002 已有 Thread 表，不修改 ORM 或数据库 schema；不新增迁移，也不提前实现 Message/Run。README 记录接口和边界，测试脚本接入 Thread 的真实 PostgreSQL 检查。上级 Contract 和评测资产不变，MiniMew API DTO 通过现有 FastAPI OpenAPI 暴露，跨端生成契约仍待后续。

实际验证（2026-10-29）：

- `uv run --no-sync pytest -q`：49 passed、18 skipped；真实数据库测试默认跳过，保留已有 httpx 弃用提示。
- `uv run --env-file .env --no-sync python scripts/check_models.py`：18 个真实 PostgreSQL 测试通过，其中新增 4 个 Thread 测试，覆盖默认标题持久化、另一 app 读取、用户隔离与统一 404、分页与参数校验、拒绝伪造归属/ID/标题、相同时间排序及 flush 后失败回滚。两条迁移路径、metadata 一致性和已有认证检查也通过，临时库已清理。
- 未改变开发库结构或添加测试账号；无新依赖、迁移、上级 Contract 或评测资产需要同步。未运行真实浏览器或模型测试（本步未接入），也未运行上级全仓库门禁。未重启用户现有 API，未 commit/push。
- `uv run --no-sync ruff check src tests migrations scripts`、`ruff format --check`（29 文件）与 `git diff --check` 均通过。

复盘通过：用户正确指出 `Thread.user_id == user.id` 阻止越权。

### 第 1.6 第一小步：账户前端

用户了解 Vue 3 结构，以前主要使用 JS，未深入 Pinia。已说明 TS 静态类型与运行时检查区别，Pinia 用于多组件共享状态而非自动持久化。用户正确选择读取同一个 store；解释 localStorage/内存/HttpOnly Cookie 后，用户回答页面刷新无需重新登录，应获取新的 Access Token。用户曾将未带 Token 的响应理解为“跳转登录”，已纠正为后端返回 401、前端决定跳转；恢复尚未结束时应等待。用户正确回答 500 显示服务异常并允许重试，然后明确授权开始本小步。

实现路径：`frontend/src/api.ts`（fetch 与运行时字段检查）→ `stores/auth.ts`（内存状态与共享 Promise）→ `router.ts`/`App.vue`（恢复门禁、错误重试）→ 登录注册/账户视图。Vue 3.5.44、Pinia 3.0.4、Vue Router 4.6.4、TS 5.9.3、Vite 8.3.5；确切依赖以独立 package-lock 为准。复用 Node 24.18.0/npm 11.16.0，新增独立前端依赖与 Playwright Chromium；没有改动上级前端或 Python 生产依赖。

不变量：Access Token 仅在内存；Refresh 仅由 HttpOnly Cookie 携带；恢复完成前不查询账户；401 与 500/网络故障分开；同标签刷新共用 Promise，Web Locks 在支持的浏览器上串行同源 Cookie 操作。退出等待在途登录/刷新，generation 拦截旧响应；退出失败留在重试状态，sessionStorage 只记录非凭据退出标记，页面刷新仍先完成退出。其他标签的已有 Access Token 不即时失效，本步不宣称跨标签即时退出；没有通用业务 401 重试和定时续期。

新增认证 OpenAPI 导出脚本和 openapi-typescript 生成消费者，真源仍为后端路由 DTO；TS 类型与运行时检查分别负责静态和输入边界。没有修改上级 Contract、迁移、模型表或评测资产。开发代理保持 Host 与 Origin 一致，没有关闭后端安全检查。

验证（2026-10-29）：

- `npm test`：8 passed，覆盖并发恢复、401、500/503/网络及畸形响应、退出等待刷新与失败重试、注册冲突。
- `npm run build`：类型检查与构建通过，入口 JS 约 105.72 kB / gzip 40.94 kB；`npm run format:check` 通过。
- `uv run --env-file .env --no-sync python scripts/check_frontend.py`：6 个 Chromium 测试通过，其中 5 个模拟服务浏览器场景、1 个真实 PostgreSQL/FastAPI/Vite 注册→登录→Cookie 恢复→双标签恢复→退出。验证 HttpOnly/Path/SameSite、JS 不可读取 Refresh Cookie、退出失败刷新后的行为、等待恢复门禁、移动端无横向溢出。已查看桌面和移动截图。
- 浏览器测试临时数据库与 8002 后端已清理；没有修改开发库记录或重启已有 8001 服务。`npm audit --omit=dev --audit-level=high` 0 vulnerabilities；Python Ruff check 和 format（31 文件）通过。
- 未运行上级全仓库门禁、模型调用和生产部署验证，均不属于本步。README 与此交接同步，未 commit/push。

复盘反馈：用户询问“组件恢复身份”和 Promise 的含义；已澄清身份恢复由应用统一完成，组件消费结果，Promise 表示异步结果。用户正确回答第二次调用会“等待”已有请求，并明确“前端暂时先只了解架构，不用往深入分析”。到此停止前端语法/并发细节追问，不将此记录为已掌握所有实现细节。后续围绕页面、路由、共享状态、请求层、后端接口的职责交流，学习重点继续放在 Agent/RAG；尚未授权下一小步编码。

### 第 1.6 第二小步：Thread 前端

已讨论页面→Pinia→HTTP→后端→数据库的职责；用户明确回答新建后应等待后端成功返回 Thread ID 再显示正式对话，并授权开始本小步。遵照最新偏好，只在架构层面沟通，不追问前端语法。

实现：新增 `frontend/src/stores/threads.ts`、`threads.ts` 响应检查、`views/ThreadsView.vue`，沿用既有技术栈无新增依赖。`/threads/:threadId?` 为受保护页面，列表与详情均使用现有 `/v1/threads`；地址是选中 ID 的来源，数据库仍保存事实。加载更多按返回 offset 查询并按 ID 去重；刷新列表重新取第一页，失败可重试。新建成功后才显示，阻止重复点击，不自动重试创建，成功后重新对齐第一页。创建等待期间用户主动切换时不抢回选择。

隔离与错误：详情按请求代次和身份检查，旧响应不能覆盖新选择或新账号；身份清空同步清理 Thread store 和在途请求。无效地址和 403/404/422 显示统一不可访问；500/网络故障可重试；业务 401 提示用户恢复身份，成功后保留原地址，Refresh 401 才进入登录页。不承诺 offset 跨请求快照一致性；创建响应丢失应先刷新列表确认，避免重复创建。

前端 OpenAPI 导出扩展到 Thread 路由，并重新生成 TS，未手改生成物。无后端业务或数据库迁移变更，无上级 Contract/评测资产变更。用户的 8001/5174 正在运行，测试独立使用 8002/5175，未停止或重启现有服务。

验证（2026-10-30；状态测试最初于前一天实现时运行）：

- `npm test`：17 passed（原认证 8 + 新增 Thread 9），覆盖旧响应、账号清理、创建等待/失败/重复点击、分页去重/重试、404/500/401 和畸形响应。
- `npm run build` 类型检查/构建通过（入口约 114.37 kB，gzip 43.73 kB），`npm run format:check` 通过。
- `uv run --env-file .env --no-sync python scripts/check_frontend.py`：9 个 Chromium 场景通过；包含真实 PostgreSQL 上 A 注册登录→创建 Thread→刷新恢复→退出→同浏览器 B 登录后列表为空且 A 的地址不可访问。其余模拟场景覆盖分页、创建错误不追加假数据、详情错误重试、401 恢复保留地址及认证回归。已查看桌面与 390px 手机截图，无横向溢出。
- 首次临时后端 readiness 在原 20 秒内未就绪，已清理其资源；验收脚本增加本地启动日志并将启动等待改为 45 秒，重新运行全部浏览器场景通过。临时数据库及后端、测试前端已清理。
- Python Ruff check 与 format（31 文件）通过；未运行上级全仓库门禁、模型或生产部署验证，范围未涉及。没有新增依赖，无需重跑依赖审计。README 与此交接更新，未 commit/push。

本小步功能已实现，但用户尚未反馈实际体验；不记录为用户已验收。下一步应先回顾工程骨架的范围，再讨论第 2 阶段 Message/Run 的最小持久化执行设计，不能直接开始模型或 Agent 编码。

### 第 2.1 小步：原子接收提问

已讨论三段流程：短事务保存提问/Run/回答占位 → 无长事务的模型调用 → 短事务保存结果。用户正确回答模型调用失败后应“保留并记录本次执行失败”。已区分响应丢失的幂等重发与用户明确重新生成的新意图；前者本步实现，后者消息关联方式仍需后续定义。用户明确授权“开始 2.1”。

实现：0005 新增 runs/messages，Thread 增加默认 0 的 last_sequence、归属复合唯一约束。`conversations.py` 在一个事务中按用户归属锁定 Thread，查同键记录，首次创建 pending Run 和 completed 用户消息/pending 空回答一对，连续分配两个 sequence，首次同时更新标题。标题清理空白并限制 30 字符加省略号；原始正文完整保存。重复同键同正文返回原 Run（200），首次 201，正文不同 409；不同 Thread 可以使用相同键。

唯一 HTTP 入口：`POST /v1/threads/{thread_id}/runs`、`GET /v1/threads/{thread_id}/runs/{run_id}`，历史仍为 Thread 所有的 `GET /v1/threads/{thread_id}/messages`。GET 归属/ID 组合错误统一 404，历史默认最新 50 条、最大 100 条，按 sequence 升序返回，previous_cursor/before 获取更早页。run_id + role 唯一约束限制最多一对，应用事务保证写齐；复合 FK 防止跨用户/Thread 关联。应用会拒绝不完整 Run 的读取，不以空结果掩盖损坏。管理员直写数据库仍可能造出孤立 Run，不能绕过正式预留入口。

阶段边界：仅保存 pending，不允许终态更新，没有执行器、模型配置/快照、Event、SSE、取消和恢复。后续启用执行前必须定义冻结快照及无快照历史 pending 的明确处理策略，不得静默使用最新配置执行旧任务。并发不同键可以预留多个 pending；调度/执行并发控制与预期版本检查在执行器阶段讨论，本步不宣称已实现。

验证（2026-10-30）：

- 离线 pytest：61 passed（新增输入、标题及未认证路由验证）；真实库用显式脚本另跑，保留已有 httpx 弃用提示。
- `scripts/check_models.py`：25 个真实 PostgreSQL 测试通过（原 18 + 新增 7），覆盖跨应用重发、同键并发、并发不同正文冲突、不同键顺序、首标题不覆盖、历史游标、跨用户/Thread 拒绝、数据库约束、消息 flush 后异常连同 Run/标题/序号回滚。测试库已清理。
- 空库到 0005、旧无凭据用户阻断 0003、已有注册用户与 0004 Thread 升级保留、重复迁移和 metadata 检查通过。
- 开发库升级到 0005 (head)，升级前后核对原有 1 用户/1 Thread 保留，未新增开发库测试 Run 或消息；alembic check 无新操作；check_database.py 事务/Session 清理与真实 ready 200 通过。未停止或重启现有应用进程，使用新接口需重启后端。
- 从正式路由重新生成前端 OpenAPI/TS；`npm run build` 类型检查和构建、`npm run format:check` 通过。没有前端运行逻辑改动，未重复浏览器测试。
- Ruff check/format（37 文件）通过。无新增依赖、上级 Contract、评测资产修改；未运行上级全仓库、模型或生产门禁。README、验收脚本和此交接同步；未 commit/push。

待复盘：HTTP 响应丢失后使用原 key 重发，应该看到原 Run 和原消息 ID；保存前异常则整个事务回滚，使用原 key 可以重新创建。用户此前理解幂等与事务，本次结合接口实际行为简要回顾即可，不重复基础概念考试。下一步模型执行仍需独立讨论并授权。

### 第 2.2 小步：模型配置与快照

用户在 2.1 完成后回复“继续”。本轮开始讲解模型配置与快照；这不是 2.2 编码授权，也不记录为用户已验收 2.1 的实际体验。

讲解范围：Model Profile 是可以管理和修改的无 Secret 模型配置；Model Snapshot 在创建 Run 的同一预留事务中复制并冻结该次配置，保存 profile identity/version 及实际调用参数，执行时不再动态读取当前 Profile。Provider 是实际发送模型请求、转换响应的适配层，随后再展开执行器、状态与失败事务。冻结调用配置不能保证第三方同名模型永久不变或输出逐字一致。

拟定实现顺序：先用已有 Pydantic、SQLAlchemy、PostgreSQL 和 Alembic 建立最小持久模型配置与 Run 快照，再讨论同一正式执行链的 Provider/执行器；完整模型管理 UI 留待后续。API Key 仍从服务端 Secret 配置获取，不进入 Profile、Run 快照或前端。创建所需配置缺失时应明确拒绝新 Run；2.1 历史无快照 pending 的处理策略必须在启用执行前单独确定，不能使用最新配置补猜。以上仍是待理解、待授权方案。

已提出的理解题：

1. Run A 创建时使用模型 M1、temperature=0.2，开始执行前当前配置改为 M2、temperature=0.8。A 应使用哪一份？如果只保存可变 Profile 的 ID，再执行时查询它，能保证吗？
2. 模型配置改变后，客户端用原幂等键重发原问题，应返回原 Run 和原快照，还是按新配置创建另一个 Run？

用户实际回答：

> 1. 还是 run A 最开始创建的配置，Profile 只是记录和提供查询。
> 2. 返回原 Run。

据此确认：已创建 Run 使用原配置、原幂等键重发返回原 Run 的场景判断正确。本轮补充 Profile 还负责提供新 Run 使用的当前可修改配置；Run 必须保存当时的参数值，不能只保存 Profile ID 后读取当前内容。用户未单独回答“只保存 ID 是否足够”，不扩大为已掌握所有快照实现细节。

向用户说明的 2.2 编码范围：最小持久 Model Profile 与当前配置选择，通过本地管理命令初始化/更新；在现有 Run 预留事务内冻结无 Secret 快照；同键重发复用原 Run/快照；配置缺失或非法时拒绝新建且无部分记录；增加迁移和配置变化/幂等/回滚验证。沿用现有技术栈，Provider/执行器、真实模型调用和模型管理网页仍待后续讨论。历史无快照 Run 不自动补猜配置，在启用执行前明确拦截和确定处理策略。

随后用户明确“开始 2.2”，授权上述范围。实现期间询问首个模型选择，用户回答“使用 deepseek v4 flash 那个，url 帮我写，然后记得把深度思考关了”。已核对官方文档并向用户说明：Base URL 为 `https://api.deepseek.com`；旧 ID `deepseek-v4-flash` 仍接受，但官方实际服务已转到 DeepSeek-V4.1-Flash-1017。保留用户指定 ID，不暗中换为其他 ID，也不宣称冻结了第三方模型权重。官方来源和检查日期记录在 README。

实现与不变量：

- 0006 新增 `model_profiles`（name、单调 version、无 Secret configuration）与 `model_assignments`（当前仅 answer）。迁移建立 profile_id 为 NULL 的选择行作为并发锁锚点，不自动猜测模型。JSONB 保存配置，不新增依赖。
- `model_control.py` 为正式配置管理/冻结/快照读取入口。Pydantic 严格校验、拒绝未知字段、Key/Header、URL 用户名/密码/query/fragment、非法数值；嵌套 DTO 不可修改。`thinking.type` 必须显式为 disabled，拒绝 enabled，不依赖 Provider 默认值。
- `model_cli.py` 提供 `python -m minimew.model_cli set --name ... --file ...`、`show`、`show-run <uuid>`。同名配置改变时 version 加一，相同配置不增加版本；配置更新和当前选择在同一事务提交。仅本地数据库管理入口，无新增 HTTP 管理接口、网页或模型客户端。
- 预留沿用 `/v1/threads/{thread_id}/runs` 与 `conversations.reserve_run`。先验证归属、锁 Thread、查原键，再仅对新 Run 冻结完整快照及确定性 SHA-256 `model_catalog_hash`，与原消息对/标题/序号一起提交。同键重发即使当前配置改变、缺失或损坏仍返回原 Run；不同正文仍 409。新建缺失/非法配置返回 503 和稳定 code `MODEL_CONFIGURATION_UNAVAILABLE`，没有部分业务记录，不暴露原始异常或配置。
- 配置写入与快照读取以相同顺序锁选择和 Profile，读取使用共享行锁。Run 快照与摘要由数据库触发器禁止更新，禁止新 Run 缺快照。2.1 旧无快照 Run 与消息原样保留，NULL 不补猜；`snapshot_for_run` / show-run 明确报 `MODEL_SNAPSHOT_MISSING`，后续执行器必须使用该读取入口；非法数据/摘要不符报 `MODEL_SNAPSHOT_INVALID`。旧 Run 的 HTTP 查询/幂等仍可用。0006 为前向迁移。
- `model-profile.deepseek-v4-flash.json` 保存用户选择的无密钥配置，temperature=0.2、max_output_tokens=4096、timeout_seconds=60、thinking.type=disabled。真实 Provider 接入时必须将快照参数传入模型请求；当前没有实际模型调用或 API Key 校验，不能把配置成功当成在线可用证明。

验证（2026-10-30）：

- `uv run --no-sync pytest -q`：84 passed，32 个真实库测试默认跳过；覆盖输入拒绝、嵌套不可修改、摘要稳定/篡改检测、历史缺失拒绝、CLI 不回显疑似 Secret。原有 httpx 弃用提示保留。
- `uv run --env-file .env --no-sync python scripts/check_models.py`：32 个真实 PostgreSQL 测试通过；新增模型变更后旧快照不变、原键重放、新键新快照、缺失/非法配置无部分记录、数据库触发器、模型更新回滚、并发版本递增、预留与配置更新交错、真实 CLI set/show/show-run。空库到 0006、旧无凭据用户阻断、已有注册用户/Thread/0005 Run 及消息升级保留、旧快照禁止补填、重复迁移及 metadata 检查通过。临时测试库均已清理。
- 开发库升级到 `0006 (head)`；`alembic check` 无新操作。用正式 CLI 写入用户指定 DeepSeek Profile v1 并选为 answer。升级前后原有 1 用户/1 Thread 保留，仍为 0 Run/0 Message，现有 1 个 Model Profile。`scripts/check_database.py` revision/表集合/事务/Session 清理与真实 ready 200 通过。没有重启或停止已有应用进程，试用新逻辑需重启后端。
- 已从正式路由导出 OpenAPI 并运行前端 `npm run contracts`；`npm run build` 和 `npm run format:check` 通过。新增公开 503 描述，不改成功响应或输出私有模型地址。没有前端运行逻辑改动，未重复浏览器测试。
- Ruff check/format（41 文件）与 git diff --check 通过。README、验收脚本、生成契约和此交接同步。无新增依赖、上级 Contract/ADR/评测资产修改；未运行上级全仓库、真实 Provider 或生产门禁。未 commit/push。

复盘实际反馈：对“配置缺失导致创建 Run 返回 503，修好配置后用原幂等键重发，应创建新 Run 还是返回旧 Run”，用户回答“返回旧 Run”。该场景尚未通过。本轮补讲：配置缺失发生在新 Run 的预留事务内，事务失败没有留下 Run/消息/键记录；修复后原键提交应首次成功创建并返回 201。只有原 Run 已提交，重发时才有可返回的原记录；不能仅凭同一个 key 推断已存在 Run，也不能仅凭所有 503 一概推断未提交，当前题目特指 MODEL_CONFIGURATION_UNAVAILABLE 的新建拒绝。

复核题：如果先 flush 了 Run 与消息，随后更新标题报错，导致整个预留事务回滚，再用原 key 重试，应复用原 Run 还是重新创建？用户回答“复用原 Run，已经有 run 了”。该复核仍未通过；不能沿用前面曾正确解释 flush 的记录推定当前已理解。

本轮用 Spring 事务补讲：flush 将 INSERT 发到当前数据库事务中，可获得 UUID，但没有 commit；rollback 会撤销该事务内新增的 Run/消息。内存对象或已获得 ID 不等于回滚后数据库仍有记录。MiniMew 的 reserve_run 在 session.begin 正常退出后才提交，在退出前异常则回滚；重试通过数据库查原 key，找不到就重新创建。此场景没有另外持久化的失败 Run。

新的复核题：Java 的 @Transactional 方法中 INSERT 一条 Run 后抛出 RuntimeException，事务已回滚；方法结束后，另一次请求 SELECT 这个 ID，还能查到该记录吗？用户回答“不能”。据此确认用户已正确判断回滚记录不可查询；进一步串回当前实现：查询原 key 找不到已提交 Run 时重新创建。两次先前错误保留作为学习过程，不再将这个问题标为待答，也不据此声称已掌握后续执行恢复。

### 第 2.3 小步：执行器与 Provider

本轮讲解：执行器负责领取已有 Run、使用其快照、组织历史/本次消息、驱动调用以及持久化结果；Provider 接受配置和消息并返回模型结果或明确错误。Provider 不自行创建业务 Run，不自动从业务数据库恢复聊天历史。先注入可控 fake Provider 验证成功/失败和持久化行为，再以真实 DeepSeek Adapter 接同一执行链，不能新增第二条聊天 route 或把 fake 验证当作在线质量证明。此轮只作职责介绍，未确定调度、历史截点或完整实现范围。

已提出场景：两台后端同时看到同一个 pending Run，是否可以各自调用一次模型，再只保存先返回的答案？用户回答“不清楚，Provider 是什么”。该并发检查未通过，先补讲 Provider 的含义，不继续叠加领取/锁/租约概念。

本轮补讲：Provider 原义为外部模型服务提供方，当前例子为 DeepSeek；代码中的 Provider Adapter 是 MiniMew 内负责与外部服务通信的客户端封装，可类比 Java 的 DeepSeekClient。它根据执行器传入的快照和 messages 组装请求、带服务端密钥调用 API、解析响应或报告错误。Profile 是配置记录，Adapter 是执行调用的代码，两者不能混用。执行器负责 Run 生命周期和持久化，Adapter 报超时本身不会自动修改 Run。

职责复核题：模型请求超时时，应由调用模型的 Adapter 直接决定并保存 Run 失败，还是由管理 Run 的执行器根据错误推进状态和持久化？用户回答“由执行器处理”，该职责判断通过；不扩展为已经理解执行领取和故障恢复。

本轮回到并发领取：创建幂等保证一条业务 Run，并不自动阻止两个执行器同时调用模型；最终写入去重无法撤销重复模型费用或工具副作用。拟采用数据库原子条件更新领取，只有将 pending 改为 running 成功并提交的执行者才能继续调用；影响 0 行者不能执行该 Run。该示意尚未实现，现有状态约束仍只允许 pending。领取后的崩溃恢复还需要执行所有权/过期规则，不能将条件更新宣传为外部调用严格一次执行保证。

领取复核场景：A、B 同时领取同一个 Run，A 的条件更新影响 1 行并提交，B 影响 0 行；B 还应调用 Provider 吗？用户回答“不应该，没有执行资格”。该同时领取的资格判断通过，不据此认定已理解过期 owner、lease/fencing 或外部调用去重。

本轮继续讲解短事务与结果落库：提问/Run 预留已提交；执行器用短事务领取并提交，然后在数据库事务之外调用 Provider；成功后在同一结果事务中更新原 assistant Message 正文/状态、Run 成功状态及对应持久终态事件。模型错误保留原提问和 Run，通过结果事务记录失败。模型返回不等于权威成功，必须结果事务提交；终态事件与 SSE 的完整协议仍待展开，不能先宣布成功后补落库。结果写入还需有效执行所有权，后续继续讲解。

结果事务复核场景：模型已经返回答案，但保存答案和 Run 成功状态的结果事务报错并回滚，此时能向前端宣布 Run 已成功吗？用户回答“能，因为 run 领取是独立的事务，保存结果是新的事务”。该完成事实的判断未通过。用户正确识别了独立事务，但混淆了“领取提交保留 Run/running”与“结果提交持久化 succeeded”。

本轮补讲具体状态：预留事务提交 pending；领取事务提交 running；模型返回本身不改数据库；结果事务拟写入答案和 succeeded，但若回滚，数据库仍保留之前已提交的 running 和未完成回答，不会自动变成 succeeded，也不能据此直接宣告 failed。只有后续有效事务成功提交后才能形成相应权威终态。当前是拟实现的流程，应用仍只支持 pending。

结果事务的新复核题：假定没有其他后续状态更新，结果事务已回滚，此时刷新页面从数据库重新查询，Run 状态应为 running 还是 succeeded？用户回答“running”。该持久状态判断通过；已将“领取成功”与“完成结果提交”区分，不据此推定已掌握终态事件或恢复协议。

本轮讲解模型输入：执行器将数据库中的业务消息转换为 Provider 接受的 messages；Thread ID 本身不代表外部服务知道业务历史。拟采用同 Thread 按提问顺序执行、不同 Thread 可并行的规则。最小上下文示例为此前成功完成的完整问答加本 Run 的用户消息，不发送本 Run 的空 assistant 占位，也不纳入序号位于本次提问之后的用户消息。具体上下文预算、失败轮次及恢复时的输入策略还需后续确定，当前不视为已实现或已授权。

上下文场景复核：同 Thread 中 A 已成功回答，B 已提交，之后 C 的用户消息也已保存；执行 B 时能否把 C 的问题也放入 B 的模型输入？用户回答“不能，按提问顺序执行”。该排除未来消息的判断通过；补充 B 的上下文截点就是 B 的提问，顺序调度仍不能替代查询范围限制。

本轮讲解最小执行所有权：领取时记录 owner token 和有效期限（lease）；最终保存结果时，在同一事务检查仍为 running、owner 匹配且租约有效，拒绝失效执行者的迟到写入。租约过期不证明外部模型请求已停止，不能据此承诺外部调用严格一次执行。拟定最小阶段先对过期执行明确写入失败，不自动重新调用模型；完整续租、接管与恢复在后续独立讨论，尚未编码授权。

租约复核场景：执行器领取的租约已经过期，此时才拿到模型答案，是否还能凭“自己曾经领取成功”将 Run 保存为成功？用户指出此前 A/B/C 指 Run，Agent 后来又把 A 用作执行器，导致“谁领取谁”的歧义；同时正确复述保存时检查 running、owner token 匹配、租约有效，并回答“所以不能保存”。据此确认过期执行者不可写入的条件判断通过；角色命名混乱由 Agent 例子造成，不能当作用户未理解规则。

本轮澄清并统一后续命名：Run 1/2/3 是数据库中的业务任务记录；执行器 E1/E2 是后端处理任务的程序。E1 领取 Run 1，成功后调用 Provider 并按所有权条件写结果；Run 不会自己领取另一个 Run。owner token 表示该次领取资格，不等于用户 JWT。后续不再用同一组 A/B/C 混指 Run、执行器或后端实例。仅补充角色区分，不引入额外并发理解题，不预填全部分布式恢复已掌握。本轮只有学习记录变更，没有新增编码授权。

用户要求恢复任务后，按新增约定先补录第 2 章子项计划，仍处于 2.3 讲解。本轮补讲 Event Journal：Run.status 保存当前状态，Journal 按 Run/sequence 保存可重读的执行事件；最小完成事务要同时保存 assistant Message、Run 终态及 message.completed/权威 terminal Event。2.4 后续在相同真源扩展事件，2.5 的 SSE 只投影已提交事件，不能为了推送方便先宣布完成再补落库。

事件原子性场景：如果回答和 Run 成功状态已经提交，但“完成事件”在另一个事务中写入失败，前端按事件流判断时会发生什么？用户回答“会回滚，前端收到服务器错误”。该场景未通过，需要区分已经提交的结果事务与随后失败的事件事务，以及后台错误与已结束 HTTP 请求。

本轮补讲：假设采用题目中的错误拆分，事务 T1 已提交答案与 succeeded，事务 T2 写终态事件失败，则只回滚 T2，T1 已提交的 succeeded 与答案保留。按事件流投影的前端缺少完成事件，可能仍显示 running；具体连接是否报错取决于传输，不保证得到 HTTP 500，已返回的创建响应也不能因后续后台错误改写。正式实现必须把答案、Message/Run 终态与完成事件放进同一结果事务，避免两种观察不一致。此处为设计反例，不是现有已实现的执行路径。

事件原子性的新复核题：在这个反例中，T1 保存答案与 succeeded 已经 commit，T2 写事件失败并 rollback，T1 已提交的 succeeded 会被撤销吗？用户回答“不会”。该跨事务提交边界判断通过；已解释正式完成事务应同时写入答案、Message/Run 终态和对应事件，避免事件消费者与数据库查询不一致。

本轮向用户汇总待授权的 2.3 具体范围，细则保存到 [章节计划](chapters/02-持久化普通对话.md#23-具体实现范围)：扩展执行/消息状态，最小执行领取与租约，按 Thread 顺序和提问截点组织历史，仅纳入此前成功完整问答并限制大小，注入 fake Provider 验证统一执行链，结果与最小版本化终态 Journal 同事务提交，验证并发/失败/过期/回滚。fake 仅在隔离验收中注入，由可信验收入口调用正式执行器；默认开发 API 继续只预留，后续真实 Provider 接入时再衔接后台调度，不新增聊天或执行旁路 HTTP route。本步不自动重试模型、不引入 Redis/SSE/真实外部调用或完整接管恢复。最终采用的常量和数据结构可在已讲清的边界内实现，不据此增加新的功能范围。

随后用户明确“开始2.3”，授权上述实现范围，已完成以下工作；不能据此沿用到 2.4 或真实模型接入。

实现与不变量：

- 新增业务包 `src/minimew/execution/`：provider 定义异步接口与不可变输入，context 组织有界完整问答，repository 管理领取/输入读取/提交/过期终结，journal 追加并校验事件，service 串联执行。没有搬动认证或新增第二条聊天/执行 route，也没有 import 时启动任务或客户端。
- 0007 扩展 Run 状态为 pending/running/succeeded/failed，增加 owner token、lease、开始/结束时间、稳定 error code 和单 Run 日志序号；assistant 可 completed/failed。所有权与终态不可任意改写，单 Thread 最多一个 running。既有 pending/消息/快照原样保留。
- 领取与结果操作统一 Thread → Run 行锁顺序，条件更新领取；每次操作独立 Session。同 Thread 前序 pending/running 阻挡后序，不同 Thread 可并行。模型调用期间不持有数据库事务。执行保存再次用数据库当前时间、owner token 和状态作条件更新，失败整笔结果回滚。
- 输入使用原 Run 的快照和提问截点，最多最近 20 轮成功完整问答，总正文 60000 字符；按完整轮次裁剪旧历史，保留本次提问，排除失败、未来消息和当前空回答。这是字符/条数预算，不是精确 token 计数。
- 默认租约 330 秒；asyncio 的绝对调用 deadline 取模型 timeout 与剩余租约较小值。超时/Provider 错误/无效响应使用公开稳定 code，迟到返回不能冒充成功。数据库故障不归类为 Provider 失败。`recover_expired()` 显式将过期 running 终结为 EXECUTION_EXPIRED，不自动续租、接管或重跑模型；失联并不证明外部请求已停止。协程中断尝试持久化 EXECUTOR_INTERRUPTED 并继续抛出取消，不等于用户取消接口。
- 新预留在同一事务写 run.created；领取写 run.started；完成事务一起写原 assistant Message、message.completed、run.completed/run.failed 及 Run 终态。唯一索引、append-only Journal 和延迟约束触发器核对最终消息、状态、事件和序号一致；终态记录、已完成消息与日志不可改写/删除。不能仅写 succeeded 后另补事件。
- MiniMew `contracts/run_event_v1.json` 为事件 envelope/payload、状态与错误类型真源；`scripts/generate_event_contract.py` 生成严格 Python 与 TypeScript 消费者，未知结构明确拒绝，使用现有 Ruff 开发依赖并固定 LF。`contracts/AGENTS.md` 说明独立生成路径。HTTP DTO 复用生成状态/错误类型，公开 Run 增加 finished_at/error_code，不出端 owner/租约/私有快照。
- fake Provider 只存在隔离测试辅助模块，通过正式 RunExecutor 注入；`scripts/check_execution.py` 创建随机临时库驱动正式执行链。默认 API 未启动执行循环，仍会返回 pending；真实 DeepSeek、后台周期扫描、SSE、Redis、用户取消及 RAG 尚未实现。
- 旧无快照 Run 执行时明确 MODEL_SNAPSHOT_MISSING，保留原提问/消息归属并记录失败，不调用 Provider，不补猜配置。旧 pending 不补造历史 created 事件，首次执行可从 run.started sequence=1 开始。

验证（2026-10-30）：

- `uv run --no-sync pytest -q`：101 passed，49 个真实库场景默认跳过。新增上下文裁剪/无效输入、严格事件 envelope/payload/版本/时间/ID、生成漂移及不支持 schema 的拒绝测试。随后因生成器校验完善，聚焦 `tests/test_execution.py` 再跑 17 passed。
- `uv run --env-file .env --no-sync python scripts/check_models.py`：认证/刷新/Thread/Run/执行完整 PostgreSQL 回归 49 passed。空库升级、旧无凭据用户原子阻断、已有用户/Thread/Run/消息保留、旧任务拒绝 Provider、重复迁移和 metadata 一致性均通过；临时库清理。
- `uv run --env-file .env --no-sync python scripts/check_execution.py`：最终执行场景 17 passed；覆盖领取竞争、同 Thread 顺序/未来截点、两个并行 Run 的快照/输入/事件隔离、无 DB 长事务、typed/未知错误脱敏、无效输出、正常超时与吞取消迟到、过期回收竞争、错误 owner/用户、结果和事件回滚、部分终态提交拒绝、消息/日志改写删除拒绝、失败历史排除、协程中断及迟到答案不能覆盖回收终态。最后的消息删除保护变更后，此执行集及两种迁移路径已复核。
- 开发库升级到 `0007 (head)`，`alembic check` 无新操作；check_database.py 验证 revision/表集合/提交回滚/Session 清理与真实 ready 200。原有 1 用户/1 Thread/1 个 DeepSeek Profile v1 保留，thinking=disabled；仍 0 Run/0 Message/0 Event，未写开发库测试对话。无残留临时测试库。没有停止或重启用户的现有应用进程。
- 事件生成 `--check`、Ruff check/format（55 文件）、git diff --check 通过。正式 OpenAPI 重新导出，前端 npm run contracts、npm run build（包含类型检查）和 npm run format:check 通过；生成器修正 Windows 换行为 LF，没有手工修改生成物。
- 沿用既有依赖，没有修改 uv/npm manifest 或 lockfile。保留原有 Starlette/httpx 弃用提示。没有前端运行逻辑变更，未重跑浏览器测试；未请求真实 DeepSeek、未跑生产部署/上级全仓库门禁。README、章节职责导航和此交接更新；未 commit/push。

下一步先复盘本步真实职责与数据流，再讲 2.4 如何在同一事件真源上扩展生命周期/正文事件、游标查询和消费者；SSE 在 2.5，真实 Provider 在 2.7。第 2 章整体未结章，不能现在填写其完整结章总结或认定用户已验收全部执行能力。

2.3 实现后的复盘题：保存结果失败，答案仍在内存且执行资格有效时，应重试调用模型还是重试保存答案？用户回答“重试保存答案”。该条件下避免重复模型调用的判断通过，不扩展为进程崩溃后可恢复内存答案，也不视为用户已现场验收全部功能。

### 第 2.4 小步：讨论与理解记录

本节保留实施前讨论，当前能力以检查点与后续验收记录为准。讨论顺序补入 [章节计划](chapters/02-持久化普通对话.md#24-讨论顺序)。讨论开始时已有 2.3 的最小事件/严格契约和追加入口，尚无事件查询、正文增量、SSE 或真实模型流；计划沿用 PostgreSQL、现有 JSON Schema/生成器与 FastAPI，按职责解释，不提前安装新库。

本轮讲解两种序号：Thread 的 Message sequence 跨该对话的 Run 排序用户/assistant 消息；Event sequence 只在单个 Run 内从 1 递增。Run 1 的消息可为 1/2、Run 2 的消息为 3/4，但各自的事件都可以从 1 开始。事件身份/游标应以 run_id 与 sequence 配合，event_id 标识单条事件，不用于推断先后。

事件序号复核场景：已经读到 Run 1 的 sequence=4，接着收到 Run 2 的 sequence=1，能否因为 1 小于 4 就当成重复事件丢弃？为什么？用户于 2026-11-02 回答“不能，因为 event_id，不是同一个 run，只有 event_id 不负责排序”。据此确认用户正确判断不同 Run 的序号不能直接比较，并指出 event_id 不负责排序；补充准确职责：event_id 标识单条事件，run_id 界定事件流，sequence 决定该 Run 内的顺序，游标和序号去重按 Run 分别维护。不将本题扩大为已掌握乱序缺口处理或全部重放协议。

本轮继续讲解拟实现的事件查询：客户端按顺序处理到 Run 1 的第 4 条事件后，记录该 Run 的 cursor=4；服务端先校验当前用户对 Thread/Run 的归属，再查询同一 Run 中 sequence > 4 的已提交事件，按 sequence 升序分页。Run 2 使用独立游标。空页只表示本次查询没有游标之后的新事件，不能据此宣布 Run 完成；终态依赖持久终态事实。以上为设计讨论，尚无事件查询实现。

空页复核场景：Run 仍在调用模型，客户端从 cursor=4 查询后收到空事件列表，是否可以把 Run 标记为成功并停止等待？为什么？用户回答“调用的模型返回空列表？不知道”。尚未通过本题；不能推断用户已区分模型输出与查询事件的 HTTP 响应。

本轮补讲两条调用链：执行器调用 DeepSeek 生成答案；浏览器向 MiniMew 查询，后者从 PostgreSQL 读取已提交的事件，查询本身不调用模型。用一致的最小事件例子说明：已提交 run.created=1、run.started=2，模型仍在生成时，查询 sequence > 2 可以返回 []；这只代表本次暂无新进度，不能证明成功或失败。稍后结果事务提交 message.completed=3 与 run.completed=4，才有可读取的成功终态。该例包含尚待实现的事件查询，并不表示开发 API 已有后台执行或真实 DeepSeek。

空页补讲后的复核题：模型还在生成、事件查询返回空列表时，页面应保持“生成中”，还是显示“完成”？用户回答“生成中”。该场景的状态选择通过；不据此推定已理解分页耗尽、乱序缺口或完整断线恢复协议。

本轮继续讨论正文事件：当前 Provider.generate 仍一次返回完整字符串，尚无正文增量事件。2.4 拟在同一执行链加入 message.delta；示例为依次追加“你好”“，我是”“ MiniMew”，分片需先作为有序事件持久化再供客户端读取。成功终结时 message.completed 携带完整最终正文，前端以它替换临时拼接内容而非再次追加；run.completed 表示该 Run 的成功终态。最终 Message、message.completed 与 Run 终态事件仍同事务提交。尚未确定完整流式 Provider 接口、分片失败保留策略或获得 2.4 编码授权。

最终正文复核题：前端已通过 message.delta 拼出“你好，我是 MiniMew”，随后收到 message.completed，其完整正文也是“你好，我是 MiniMew”；应再追加一次，还是用完整正文替换当前内容？为什么？用户回答“用完整正文替换临时拼接内容”，该处理选择通过；用户随后追问流式正文是否为作者定义的上层输出，并指出 LangGraph 已有 stream 能力。尚未据此推定用户掌握流接口与持久化协议的全部衔接。

本轮依据当前源码与 LangChain 官方流式文档补讲：stream/astream 是同步/异步流式调用方法；LangGraph messages 模式可输出模型消息分片，updates 模式主要输出图状态更新。项目定义的 message.delta、message.completed、run.completed 是产品侧事件协议，不与库流接口互相替代；执行器筛选可公开答案正文，按 Run 的写入资格和顺序持久化，再供查询及后续 SSE 投影。分片可以按大小或时间聚合，不要求一 token 对应一条数据库记录。单独消费库的异步迭代器并直接转发，不会自动建立 MiniMew 的 Journal、用户归属或断线游标协议；不否认 LangGraph 另有持久化及部署能力。

源码核对：原版 backend/agent/runtime.py 调用 agent.astream(stream_mode=["messages", "values"])，转换为内部正文/完成事件；backend/runs/agent_executor.py 消费 runtime.astream，将正文交给批量处理并发布 MESSAGE_DELTA。MiniMew 当前 Provider 仍为 generate -> str，未安装或调用 LangChain/LangGraph，2.4 先定义同一执行链的渐进输出边界，2.7 再确定真实模型 Adapter。官方参考：https://docs.langchain.com/oss/python/langgraph/streaming 。本轮只讨论与记录，未新增依赖或应用实现。

Journal 复核题：如果把 graph.astream 的文字分片直接发给浏览器、完全不写 Event Journal，浏览器断线后能否只凭 run_id 和 sequence 从 MiniMew 数据库补回遗漏的分片？为什么？用户回答“不知道，Journal 是什么”。本题尚未通过，不能将上轮对库流输出与产品协议的介绍视为用户已经理解；先补齐 Journal 与数据库表的对应。

本轮补讲：Event Journal 可称“事件记录表”或“执行事件流水账”，MiniMew 由 PostgreSQL 的 run_events 表保存，用 RunEventRecord 映射、execution/journal.py 的 append_event 追加，随调用方事务提交才成为持久记录。用当前支持的 run.created=1、run.started=2、message.completed=3、run.completed=4 展示同一 Run 的有序记录；模型生成中的 message.delta 尚待 2.4 增加。Run 保存本次执行当前状态，Message 保存问题和最终回答，事件记录表保存已记录的过程及结果事件，供后续按 Run/sequence 补读。后续教学优先称“事件记录表”，首次出现英文名时同时说明中文与存储位置。查询和 SSE 尚未实现，不将示例描述成当前前端已有断线恢复。

补读复核题：事件记录表中已有某 Run 的第 1–4 条事件，浏览器只处理到第 2 条就断线，重新连接后应从数据库读取哪几条？用户回答“第3”。可以确认用户识别了首条未读事件；该简短回答也可能表示从第 3 条开始，不将其直接判为只读一条的错误。补充完整范围为第 3、4 条，查询条件为同一 Run 的 sequence > 2 并升序读取，有分页则继续读取后续页。例中第 3 条提供最终正文、第 4 条提供 Run 成功终态，不能因为找到第一条未读事件就停止补读；不据此推定完整分页和重放协议均已通过。

部分输出复核题：流式回答已经显示一部分正文，随后模型调用超时，能否仅因为已有正文，就把这次 Run 标记为成功？为什么？用户回答“不行，第3条完全回到后保存还没有执行，不能执行第4条执行成功”。据此确认用户正确判断部分正文且尚无最终结果提交不能宣布成功。用户沿用了前一张成功路径表的第 3/4 条命名，补充加入 delta 后具体序号会变化，应以事件类型区分职责；不据此推定用户已理解所有流式失败策略。

本轮补上失败路径：message.completed 表示消息已结束，其 status 可以是 completed 或 failed；模型超时应由执行器在有效写入资格下，将 Message.status=failed、message.completed(status=failed)、Run.status=failed 和 run.failed(error_code=PROVIDER_TIMEOUT) 同事务提交，而非继续等待或伪造 run.completed。如果结果事务本身失败，仍不能宣告已持久终结。当前 2.3 的失败逻辑会清空最终 Message 正文；2.4 分片事件若此前分别提交，后续失败不会自动回滚或删除这些既有事件。失败时是否把部分正文保留为明确未完成的失败消息，仍须讨论后再实现，不能将偏好选择视为编码授权。

失败正文偏好：流式中途失败时，希望保留半截正文并明确标注“回答未完成”，还是清空正文、只显示失败？用户明确选择“保留半截正文并标注‘回答未完成’”。该产品偏好已确认，不等于 2.4 编码授权；当前 2.3 代码仍在失败时清空最终正文，后续需在获准范围内调整。

本轮提出具体持久化方案供理解检查：失败终结时保留同一 Run 已成功提交的 message.delta 按序拼接正文，最终 Message.status=failed，message.completed(status=failed) 携带该部分正文，run.failed 保存失败原因；在同一终结事务提交，并保留执行资格检查。“回答未完成”作为状态提示，与正文分开；沿用当前仅纳入成功完整问答的历史规则，失败正文不作为下一轮的成功历史。尚未提交的内存分片不视为可重放进度，分片先提交再供客户端读取，避免刷新后丢字。此处为尚未实现的拟定方案，保留范围的理解反馈见下一段；事件追加失败与后续失败终结也不能伪装成已成功落库。

已提交正文复核题：第一批“今天”已提交，第二批“天气晴朗”写入事件表的事务回滚，随后本次调用超时；按上述方案，失败正文应保留“今天”，还是“今天天气晴朗”？为什么？用户回答“保留‘今天’，只有今天部分提交成功了”。该场景中以已提交分片作为可靠保留范围的解释通过，不据此推定已掌握所有流式数据库故障与恢复细节。

本轮继续讲解：2.4 的分片追加也要沿用 2.3 最终结果写入的资格条件，在同一追加事务检查 Run 仍为 running、owner token 匹配、租约有效；失去资格即不能再追加正文或改写终态。每批分片使用短事务，不能在整个模型生成期间一直占有数据库事务。当前 journal.append_event 仅为持锁事务内的底层追加入口，新分片写入仍需由 repository 统一执行资格检查，尚未实现该新入口。

分片所有权复核题：执行器 E1 领取 Run 1 后，租约已经过期，数据库仍为 running 且 owner token 未变；此时收到一个新正文分片，还能继续写入事件表吗？为什么？用户回答“不清楚”。该流式追加场景尚未通过；不以 2.3 曾正确判断最终结果写入的记录替代本轮实际反馈。

本轮用截止时间补讲：租约是本次执行许可的有效期限，例中 E1 的 lease_until=14:01，14:01:05 到达的新正文不得继续追加。Run.status=running 只说明尚未持久终结，owner token 匹配只确认同一次领取，两者不能替代租约有效条件；三个条件必须同时满足。时间经过不会自动改写数据库中的 status/owner，后续过期处理事务再形成失败终态；外部模型也可能仍输出，收到分片本身不等于持有写入资格。此处不引入新的续租或接管方案。

租约补讲后的复核题：租约截止时间为 14:05，当前为 14:04，Run 仍为 running 且 owner token 匹配，此时能否保存新分片？用户回答“能”。该三个条件满足的场景判断通过；此前过期场景已补讲，用户本轮尚未单独复述拒绝原因，不预填为所有租约/恢复场景均已掌握。

本轮汇总 [2.4 具体实现范围](chapters/02-持久化普通对话.md#24-具体实现范围)：把 Provider 演进为同一流式入口并在隔离 fake 中验证；增加受执行资格保护的持久 message.delta；失败终结保存已提交前缀；建立按用户/Thread/Run 归属校验的事件分页查询；同步契约生成物、必要迁移与定向/真实 PostgreSQL 测试。沿用既有基础库；SSE 在 2.5、真实 DeepSeek 在 2.7、完整聊天 UI 与“回答未完成”展示在第 3 章。本步将提供后端正文和 failed 状态支持，不预先宣称页面已实现标注。

开始反馈：用户明确回复“开始 2.4”，已授权上述具体范围。保持分片提交后才可读取、每次追加检查有效执行资格、消息与 Run 终态/对应事件同事务、不同 Run 序号及内容隔离；fake 只在隔离验收中注入。该范围现已实现，实际结果如下，不能据此提前开始 2.5。

### 第 2.4 小步：实施与验收（2026-11-02）

实现与保持的不变量：

- `Provider` 只有 `stream(request)` 一个异步流入口；`ProviderDelta` 表示一批公开正文，`ProviderCompleted` 表示 Adapter 确认正常完成的完整结果，二者都是不可变内部对象。旧 generate 方法已移除，现有隔离 fake/旧验收调用迁入同一入口，没有新增执行 HTTP 旁路或真实 SDK。没有完成结果、完成后继续输出、畸形/超量分片与无效最终答案明确记录 PROVIDER_INVALID_RESPONSE。
- 执行器在数据库事务之外逐批读取，沿用唯一绝对 deadline；关闭来源并保留取消语义，拒绝吞取消后的迟到分片。Provider 异常只在外部调用边界归一化，分片存储错误继续传播；来源清理失败不会覆盖原始存储错误，也不将原始异常写入公开事件。
- repository 新增受资格保护的分片短事务，沿用 Thread → Run 锁顺序，检查 running、owner token、租约有效，并在提交前最后一次条件写入检查。每批最多 4096 字符、累计和最终正文各最多 100000 字符，已提交事件才可查询。分片回滚时序号与正文一起撤销，过期或终态后不可追加。
- 失败和可信过期终结从同一 Run 的已提交 message.delta 按序恢复前缀，写入原 assistant Message(status=failed)，与 message.completed(status=failed)、run.failed 及 Run 终态同事务提交。成功最终正文可校正临时拼接内容。失败问答继续排除出后续模型的成功历史。“回答未完成”由第 3 章聊天页面根据 failed 状态展示，本步只提供后端正文和状态。
- 新增 `execution/queries.py` 与唯一正式 `GET /v1/threads/{thread_id}/runs/{run_id}/events?after=0&limit=100`。按用户/Thread/Run 校验归属，读取 sequence > after 的已提交事件并升序分页；响应 items/next_cursor/has_more，空页保留 after，不因 has_more=false 宣布终态。limit=1–100，负数/超界参数或超前游标返回 422，未登录 401，不存在/越权/资源组合不匹配统一 404。保持 no-store，不公开 owner、租约或快照。
- MiniMew 的 JSON Schema 增加 message.delta，从真源重新生成 Python/TypeScript；HTTP 正式路由导出 OpenAPI 并生成前端 HTTP 类型。新前向迁移 0008 增加 delta 类型、插入资格/消息归属/payload 保护及失败前缀与总长度约束；保留 0007 的不可改写日志、连续序号和终态原子性，不改写旧迁移或旧事件。
- 沿用既有生产和开发依赖，没有安装 LangChain/LangGraph、Provider SDK 或其他组件，没有编辑 uv/npm manifest/lock。代码职责走读见 [2.4 源码导航](chapters/02-持久化普通对话.md#24-实现后的职责导航)，README 的能力、迁移和查询用法已同步；上级 SuperMew Contract、ADR、评测资产无变化。

本轮验证与测试范围：

- 新增 `tests/test_events_pg.py`：最终 26 个参数化场景；`test_execution.py` 增加 7 个 delta 契约场景，`test_runs.py` 增加事件路由未鉴权场景。现有执行测试的 fake 改为流式协议，保留原有行为断言；迁移 head 断言随 0008 更新。新增迁移保留检查在显式验收脚本中执行，不冒充额外 pytest 用例数。
- 首轮定向离线：`uv run --no-sync pytest -q tests/test_execution.py tests/test_runs.py tests/test_migrations.py`，39 passed。覆盖契约、生成漂移、输入、鉴权入口及迁移 SQL。后续仅服务的流清理/新增集成用例调整，相关契约/输入/路由未变，未为了形式重复执行这组。
- 首轮 `uv run --env-file .env --no-sync python scripts/check_execution.py`：40 passed，包含旧执行 17 和当时新增事件 23。随后补了来源清理失败保留原错误、两个流式 Run 隔离、写入中租约到期，以及 0007 已有终态事件升级保留检查，因此扩大到完整 MiniMew PostgreSQL 回归。
- 最终 `uv run --env-file .env --no-sync python scripts/check_models.py`：75 passed（49 个原有场景、26 个新增事件场景），无跳过；包含先前执行集，不与首轮 40 累加成用例总数。空库迁移、旧无凭据用户 fail-closed、旧 Run/消息/快照保留、无快照不调用 Provider、0007 终态事件经 0008 原样保留、重复迁移和 metadata 检查通过。所有创建的临时测试库清理。
- `python scripts/generate_event_contract.py --check`、Ruff check、Ruff format --check（58 个 Python 文件）通过；从正式路由导出后，前端 `npm run contracts`、`npm run build`（含 typecheck）、`npm run format:check` 通过。没有前端运行逻辑改动，未重复浏览器和前端交互测试。
- 开发库 `alembic upgrade head` 后为 0008 (head)，`alembic check` 无新操作；`scripts/check_database.py` 验证 revision/表集合/事务/Session 清理及真实 PostgreSQL readiness 200。升级前后均为 1 用户、1 Thread、1 Model Profile、0 Run/0 Message/0 Event，未写入开发库测试对话；残留临时测试库为 0。没有重启或停止用户现有进程，加载新增路由需要重启后端。
- 保留既有 Starlette/httpx 弃用提示。未调用真实 DeepSeek，未运行 SSE/Redis、真实 SDK、生产或上级 SuperMew 全仓库门禁；这些不在本步范围。未 commit/push。

实现后复盘题：Provider 只输出了两批正文，随后流结束，但没有 ProviderCompleted；当前执行器会记成功还是失败，已经提交的正文会怎样处理？用户反馈“好乱”，不清楚 Run、Provider、E1/E2、事件的层级关系；将题意理解为事件未提交，并认为只有存在 ProviderCompleted 才叫已提交正文。用户选择失败，但给出的原因混淆了输出正常结束与数据库提交，本题尚未通过；不能据此宣称已理解 2.4 全部数据流。

本轮补讲并写入 [概念关系](chapters/02-持久化普通对话.md#24-概念关系与提交边界)：Run 是任务数据库记录，Event 是归属于该 Run 的有序数据记录，Message 保存问题和最终或失败的回答；执行器是驱动任务并调用存储逻辑的后端程序，Provider 是调用外部模型并返回内容的客户端代码，E1/E2 仅为执行器的示例编号，不增加新的架构层。后续先以单个“执行器”走完一条主线，确需讲并发时才使用 E1/E2；减少并列新名词，首次同时给中文职责与已有概念对应。

澄清三件独立的事：收到 ProviderDelta 表示收到内存中的文字；执行器把分片变成 message.delta 并在数据库事务 commit 成功后，才叫该分片已提交；ProviderCompleted 表示 Adapter 确认正常输出结束，并不替数据库提交。当前 Run 的分片可以先各自提交，再收到完成结果；也可能已有分片提交但流未正常完成，之后按失败保留该前缀。收到完成结果后，最终结果事务仍可能失败，此时不能宣告 Run 成功。前题省略了“两批均已各自提交”的前提，应明确是假设同一个 Run 的当前分片，而非之前成功对话。

分片提交补讲后的复核题：执行器已把“今天”写入事件表并成功 commit，但模型仍在生成、尚无 ProviderCompleted；“今天”是否已经属于已提交正文？用户回答“算”。该具体场景的判断通过，说明用户能够识别分片可先于正常生成结束而提交；不扩大为角色层次或所有事务失败场景均已理解。

本轮继续用相反方向区分：ProviderCompleted 报告模型客户端正常生成结束；数据库 commit 确认某笔写入已保存；Run 成功要求最终结果事务提交。领取事务已保存 running，而收到完整生成结果后的最终保存事务若回滚，且尚无后续处理，则数据库仍为 running，不会自动变为 succeeded 或 failed。

最终提交反向复核题：领取事务已提交 running，Provider 随后返回 ProviderCompleted，但最终保存事务回滚，尚无其他后续处理；此时查询 Run 应为 running 还是 succeeded？用户回答“running”。该场景判断通过，结合上一题可确认用户在这两个场景中区分了正常生成结束与数据库提交；不据此认定所有执行职责和恢复场景均已理解。

本轮继续沿单执行器主线梳理：执行器和 Provider 是运行的代码，Run 和 Event 是数据库记录；执行器读取 Run，调用 Provider 取得文字，再调用数据库存取代码保存这个 Run 的事件，并组织最终消息/状态的保存事务。Provider 只返回模型输出或错误，不拥有业务 Run 的持久状态。

职责复核题：Provider 已经返回一段文字，接下来由 Provider 还是执行器调用数据库存取代码，把它保存成属于本次 Run 的 Event？用户回答“执行器”。该职责选择通过，结合前面两个提交场景，现可沿单执行器主线继续讲解；不声称用户已独立复述所有角色及故障恢复细节。

本轮把先前题目重新串起来：同一 Run 的两批文字若已分别提交，之后模型流缺少 ProviderCompleted 而结束，执行器记录失败并保留这两批已提交正文。缺少正常生成结束信号与此前分片提交成功可以同时成立；不是指以前的其他完整对话。此处为补讲，不预填该综合题已被用户独立回答。

### 第 2.5 小步：讨论与理解记录

以下保留实施前讨论，当前能力见后续验收记录。讨论顺序见 [2.5 讨论顺序](chapters/02-持久化普通对话.md#25-讨论顺序)。讨论开始时介绍 SSE 为服务器经 HTTP 向浏览器持续发送事件的方式，拟读取 2.4 已提交事件并按游标传输。执行器继续负责模型调用和持久化；SSE 只负责读取/传输，不创建执行器或模型调用旁路。沿用 FastAPI 与 PostgreSQL，Redis 通知仍在 2.6。

断连职责复核题：浏览器断网、SSE 连接中断时，后端 Run 是否也必须取消，还是可以继续执行并保存事件？用户回答“继续执行并保存事件，等浏览器重连后补读”。该职责判断通过，不据此推定已掌握 SSE 帧格式、重连游标与身份过期处理。参考：https://developer.mozilla.org/zh-CN/docs/Web/API/Server-sent_events/Using_server-sent_events 。

本轮继续讨论重连位置：沿用 2.4 的单 Run 游标，浏览器记录最后已经按序处理的事件 sequence；例如处理到 4 后断网，执行器继续提交 5–8，重连时按同一 run_id 和 after=4 查询/发送 5–8，并继续等待后续事件。服务器已发送、浏览器已收到和浏览器已成功处理是不同位置，不能依据服务器最后发送的序号提前推进产品游标；当前仅解释架构，尚未确定 SSE wire id/Last-Event-ID 与参数的具体编码或客户端实现。

重连游标复核题：服务器已发送到第 6 条事件，但浏览器只按顺序处理到第 4 条就断网；重连时应该使用 after=4 还是 after=6？用户回答“重连时应该用 after=4”。该按浏览器已处理位置补读的判断通过，不据此推定已掌握 SSE wire id、游标持久化或乱序缺口恢复细节。

本轮继续讨论终态与补读顺序：同一 Run 在断网期间已成功提交，事件表第 5/6 条为正文分片，第 7 条为 message.completed，第 8 条为 run.completed。浏览器仍从 after=4 重连。SSE 即使读到 Run.status=succeeded，也不能跳过未发事件直接关闭；应按页顺序补发 5–8，包含最终正文与权威成功事件，再结束流。此处是服务端拟定传输行为，不声称当前已有 SSE 或浏览器接收确认机制。

终态补读复核题：重连后第一页只查到第 5、6 条事件，但数据库 Run 已经 succeeded；SSE 能直接关闭，还是应继续补发第 7、8 条？用户回答“补发第 7、8 条”。该先补齐最终正文与权威终态再结束连接的场景判断通过；不推定用户已掌握所有结束/重连竞态。

本轮讨论暂无新事件时的等待：若 Run 尚未结束，SSE 保持连接，2.5 初版拟通过周期性短读取发现已提交事件，每次读取后结束事务、释放数据库连接，等待期间不长期占有 Session/事务。可定期发送 SSE 注释心跳以维持传输活跃；心跳只服务连接，不写事件表、不分配 Run sequence、不改变状态，也不推进浏览器的产品游标。轮询/心跳间隔与具体实现仍待确定，Redis 通知在 2.6 接入；不是当前已实现的运行行为。SSE 注释心跳依据前述 MDN 文档。

心跳复核题：浏览器最后按序处理到第 4 条业务事件，等待期间连接传输了 3 次心跳；重连补读游标应保持 4，还是变为 7？用户回答“应保持 4”。该心跳与业务事件游标分开的判断通过。

本轮继续讨论鉴权架构：SSE 握手沿用内存中的 Bearer Access Token，服务端仍核验身份和 Thread/Run 归属；前端拟用允许设置 Authorization 请求头的 fetch 读取持续响应，不把 Token 放在 URL。原生 EventSource 构造参数没有自定义 headers 入口，前端只解释职责，不展开流解析语法。若重连握手因 Access 过期返回 401 且 Refresh 有效，复用既有身份恢复流程获得新 Access，再对同一 Run 从最后已处理游标继续订阅，不重新创建任务。

本轮只读核对现有认证：verify_access_token 在调用时校验 JWT 到期并只返回用户 UUID，CurrentUser 只返回 User，没有自动监控已建立连接的有效期；公开 Access 到期上下文及长连接到期处理仍待另行讨论。前端 useAuthStore.restore/ensureReady 是现有恢复入口；api.request 固定超时并按 JSON 解析，不能原样承担持续 SSE 读取，应复用身份管理而非新建第二套登录状态。未修改认证、前端或其他应用文件。技术参考：https://developer.mozilla.org/en-US/docs/Web/API/EventSource/EventSource 、https://developer.mozilla.org/en-US/docs/Web/API/Fetch_API/Using_Fetch 。

握手刷新复核题：浏览器已处理到第 4 条，SSE 重连握手返回 401，Refresh Token 仍有效；应创建新 Run 重跑，还是刷新 Access Token 后对原 Run 从 after=4 继续订阅？用户回答“刷新 Access Token 后，对原 Run 从 after=4 继续订阅”。该身份更新与任务/游标保持的场景判断通过。

本轮提出 MiniMew 的长连接规则：建立时验证 Access，连接期间到期则停止该订阅的后续发送并结束连接；客户端按既有身份恢复机制，取得新 Access 后以原 Run/游标重连。到期影响浏览器读取资格，不把正在生成的 Run 标记为失败或取消，执行器继续按自身规则保存事件。这是拟新增行为，当前普通请求的身份依赖不会自动持续检查，也不是用户已授权的实现。

只读对照参考项目后确认：SuperMew 当前 backend/api/routes/runs.py 建连时 get_current_user，向 bus 只传 username/run_id/after；backend/auth/access.py 的已验签返回值不含 exp；backend/events/bus.py 按持久终态退出，无连接中 JWT 到期检查。因此不把上述拟定规则称为直接复用原版已有机制。后续若实现，需在单一正式验签路径保留已验证的到期信息，不能另开不验签解析或把原始 Token 写入事件。

本轮同时说明时机：HTTP 状态在响应开始时发送；SSE 已传输正文后，不能在同一响应里重新返回 HTTP 401。到期时结束当前订阅，后续握手若仍带过期 Token 才返回 401，并按既有机制恢复身份。握手 401 与开始传输后的到期关闭不是同一时机。参考 ASGI 响应起始/正文协议：https://asgi.readthedocs.io/en/latest/specs/www.html#response-start-send-event 。

长连接到期复核题：浏览器 Access Token 在 SSE 已连接期间到期，按拟定规则关闭该订阅后，应把原 Run 标为 failed，还是让执行器继续执行并保存事件？用户回答“让执行器继续执行并保存事件”。该浏览器读取权限与任务执行分离的场景判断通过。

本轮补讲读取故障：SSE 查询事件表超时/出错，表示没有成功取得当前进度，不能冒充健康空页。响应开始前可用普通 HTTP 错误拒绝连接；已开始持续发送时则结束订阅，前端按最后已处理游标重试，不能伪造 message.completed 或 run.failed，也不因读错而修改 Run。若执行器自身写入也发生故障，应走其独立的持久化/过期处理，不能由 SSE 推断或代为终结。当前是待实现规则，未改应用代码。

实现前只读检查指出的技术边界（建议，非已实施）：沿现有 events_after 查询返回内部“分页 + 同次 Run 读取的 status/last_event_sequence”，HTTP 仍投影原 RunEventPage；分页可加本次尾序号上界，SSE 仅在终态且补发到对应最终序号后正常结束。每轮用现有 session factory 独立创建并关闭 Session，再发帧或等待，不保留跨整条流的数据库事务，也不另写第二套归属/事件查询 SQL。

读取失败复核题：浏览器上次处理到第 4 条，这次 SSE 读取事件表超时；能把这次超时当作“查询成功但没有新事件”的空页吗？为什么？用户回答“不行，应该结束本次订阅，之后从原游标重试”。该故障处理选择通过；不据此预填所有网络/慢客户端竞态均已理解。

本轮汇总 [2.5 具体实现范围](chapters/02-持久化普通对话.md#25-具体实现范围)：新增受鉴权的 SSE 只读入口，沿用显式 after 与原事件 JSON；演进同一事件查询取得终态尾序号，补齐后结束、已在终态末尾时返回 204；短读/心跳/有界发送与清理；在正式验签结果保留已验证到期信息并关闭到期订阅；区分握手错误与流中断、订阅故障与 Run 失败；临时库和真实 HTTP 流验收，认证回归、OpenAPI 与文档同步。以 after 为唯一恢复入口、SSE 可不发 id，是本次范围选择，不新增 Last-Event-ID 分支或另一套业务游标。前端实际消费/重连在第 3 章，真实 Provider/调度在 2.7；本步保持现有数据库与 Run Event 结构。

开始反馈：用户明确回复“开始 2.5”，授权上述具体范围。该范围现已实施并验证，保持 SSE 只读、分片先提交、按原游标补齐终态、连接故障不终结 Run、长连接有效期来自正式验签等不变量；不扩大到 2.6。

### 第 2.5 小步：实施与验收（2026-11-02）

实现与正式边界：

- 正式验签 `verify_access_token` 演进为返回冻结 VerifiedAccess(user_id, expires_at)，保留已验证的 UTC 到期信息；拒绝不合法数字时间和不可表示的到期值。`api/auth.py` 的 AuthenticatedAccess/CurrentAccess 统一校验用户存在，CurrentUser 从同一依赖投影，仍是单次验签/查询，不新增不验签解析或平行身份状态。原登录、刷新和 /auth/me 公开协议不变。
- 原 `events_after` 返回内部 EventRead(page, status, last_sequence)，从同一次刷新后的 Run 读取固定尾序号，并将本页事件限制到该序号。HTTP JSON 仍返回原 RunEventPage；SSE 复用同一个归属检查及 SQL。新增测试验证两条 SQL 间并发提交不会混入本次范围，也验证已有 ORM 对象不会遮蔽真实终态。
- 新增 `execution/streaming.py`。每个 EventSubscription 独立持有游标，每轮用应用已有 factory 建立短 Session，读完关闭后再发送/等待；帧包含原 type 与完整契约 JSON，不带新业务身份或可选 id，不处理 Last-Event-ID。显式 after 是唯一恢复入口。心跳是注释，不写日志表/业务 sequence。
- EventStreamResponse 监控独立的发送和断连任务，按背压发送，结束时取消/等待子任务并 aclose 生成器；不调用 Provider、RunExecutor、finish 或其他写入入口。默认每 0.5 秒读库、15 秒空闲心跳、发送等待 10 秒、连接预算 300 秒、每页 100 条；已验签 Access 到期是更早截止。每次发送前显式检查有效期，避免仅依赖协作超时；响应头发送结果不明确时不再尝试第二次发头。
- 新增 `GET /v1/threads/{thread_id}/runs/{run_id}/events/stream?after=0`：握手复用正式身份函数，仅为其存储故障增加受控 503 投影。越权/不存在 404，非法/超前游标 422，身份无效/过期 401；终态末尾已消费返回 204。200/204 保持 no-store，SSE 带 X-Accel-Buffering:no。先补齐持久终态尾序号再正常结束；非终态空页、心跳、HTTP 200 或 EOF 不制造 Run 结束事实。
- 流中读库失败记录稳定类别及 Run ID 后结束订阅；断连、到期、慢发送、取消均只清理该连接，客户端可按最后已处理位置重连。原始数据库异常、凭据和租约不进入帧或公开错误。Token 到期连续限制是 MiniMew 的新增策略，不称为原版已实现行为。
- 沿用现有 FastAPI/Starlette/asyncio/SQLAlchemy/PostgreSQL 依赖，未新增安装、数据表、迁移或 Run Event 类型；0008 结构与事件真源/生成类型字节保持原样。正式路由重新导出 OpenAPI 并生成 HTTP TypeScript 类型，README 与 [2.5 职责导航](chapters/02-持久化普通对话.md#25-实现后的职责导航) 已同步。默认 API 仍只预留 pending，不自动启动执行器或注入 fake；真实模型/调度在 2.7、前端消费与重连在第 3 章接入。

测试变更与实际运行（不将重复运行相加）：

- 原有认证测试保留行为断言，成功返回值断言随内部 VerifiedAccess 更新；新增 3 个认证测试函数及 9 个异常 Token 参数场景。`test_runs.py` 新增 SSE 未鉴权场景。新增 `tests/test_sse.py` 最终 19 个离线场景；`tests/test_sse_pg.py` 10 个真实 HTTP 场景，`tests/test_stream_reads_pg.py` 2 个读取竞态/缓存场景。`tests/sse_helpers.py` 只管理自己创建的临时 socket/Uvicorn 线程与解析器。
- 首次 `uv run --no-sync pytest -q tests/test_auth.py`：29 passed，无跳过。首次 `uv run --no-sync pytest -q tests/test_sse.py tests/test_runs.py`：30 passed（当时 SSE 16 + 路由 14），无跳过。
- 首次 `uv run --env-file .env --no-sync python scripts/check_sse.py`：12 passed（10 个真实 HTTP + 2 个读取边界），并通过空库/旧数据迁移和 metadata 检查。真实连接证明任务结束前可收分片、after 重放、成功/失败终态分页与 204、权限、心跳/空闲资源释放、两个 Run 订阅隔离、JWT 到期断开且 Run 仍可完成、握手 503 与推送后读库失败。
- 因本步改变共享鉴权与正式事件查询，扩大到 `uv run --env-file .env --no-sync python scripts/check_models.py`：87 passed（原有 75 + 本步 12），无跳过。包括原认证/刷新/Thread/Run/执行/事件回归；空库、旧用户 fail-closed、历史消息/快照和 0007 终态事件保留、旧无快照不调用模型、重复迁移与结构一致性通过。
- 收尾复查发现超时对完全不挂起的 send 可能无法及时抢占，补显式发送前检查及尚未发头时的受控响应。新增 2 个对应场景后 `pytest -q tests/test_sse.py`：18 passed；专项真实 HTTP/读取边界 `scripts/check_sse.py` 再次 12 passed。随后只读审查发现“响应头已交给下游但 send 尚未返回”不可重发响应头，修复 start_attempted 判定并加精确 ASGI 用例，最终 `pytest -q tests/test_sse.py`：19 passed。后两次只改连接发送边界，按影响范围定向复核，不重复整套 87；87 与两轮 12 不累加为用例总数。
- 导出 OpenAPI 首次遇到自定义 Response 缺省状态元信息导致的导出错误，已给正式 SSE route 明确 status_code=200 后重新导出。`npm run contracts`、`npm run build`（含 typecheck）、`npm run format:check` 均通过，最终再次比对生成 OpenAPI 与当前正式路由一致。事件生成 --check、Ruff check、Ruff format --check（64 个 Python 文件）通过；依赖 manifest/lock 和事件 JSON/生成物与开工快照完全相同。
- 开发库实测为 0008：1 用户、5 Thread、1 Model Profile、0 Run/0 Message/0 Event，临时测试库残留 0；本轮测试未在开发库创建对话或 Run。`alembic check` 无新操作，`scripts/check_database.py` 的表集合/事务/Session 清理与真实 PostgreSQL readiness 200 通过。仅停止测试自己创建的临时 HTTP 线程/端口，未停止或重启用户应用；加载新路由需要重启后端。
- 保留既有 Starlette/httpx 弃用提示。无前端交互代码变更，未重跑浏览器/前端交互测试；未调用真实 DeepSeek、未接 Redis、未运行生产或上级 SuperMew 全仓库门禁，原因是这些不在本步范围。未 commit/push。

实现后复盘题：SSE 握手返回 HTTP 200，随后连接结束，但浏览器没有收到 Run 的持久结束事件，能否把任务显示为成功？用户回答“不能”。该 SSE 消费场景的判断通过；HTTP 200/EOF 不能单独证明业务成功，不据此推定所有状态恢复场景均已理解。

### 第 2.6 小步：通知职责（讨论中）

讨论安排见 [2.6 讨论顺序](chapters/02-持久化普通对话.md#26-讨论顺序)。2.5 当前每 0.5 秒短读 PostgreSQL；2.6 拟在事件提交后通过 Redis 提醒有新进度，SSE 被唤醒后仍走同一 PostgreSQL/after 查询取业务事件，再发给浏览器。保留有界超时补查，使通知缺失或 Redis 故障只影响及时性，不能把通知当正文/终态的权威来源。先讲通知职责，稍后再讲“待发送通知记录（Outbox）”的事务与重试，避免一次叠加术语。

本轮只读核对参考实现：SuperMew 用 Redis Streams（XADD/XREAD），不是 Pub/Sub；redis_transport.py 当前传输完整事件 JSON，可含正文，但 bus.py 忽略 Redis 返回内容、每次仍按 cursor 从 PostgreSQL 重读。journal.py 在同一事务加入 Event 和 Outbox；outbox.py 发 Redis 成功后独立标记 published_at，失败留待重试，发布标记不是浏览器处理确认。MiniMew 的传输方式与通知载荷细节尚待讨论，不能误称参考实现完全不携带正文。技术参考：https://redis.io/docs/latest/commands/xread/ 。

通知缺失复核题：某 Run 的第 5 条事件已经提交到 PostgreSQL，但 Redis 通知暂时没有送达；浏览器还能在后续补查时读到第 5 条吗？为什么？用户回答“不能，现在 SSE 不会自动扫描，浏览器只会在 Redis 的提醒下补查”。该回答按“完全取消定时读取、仅靠通知”的假设可成立，但与前题保留定时补查的设计前提不同。不能标为已通过；Agent 应明确当前实现与未来方案，并讲清通知接收方。

本轮补讲：当前 2.5 代码仍默认空闲时每 0.5 秒由后端 EventSubscription 读取事件表，2.6 尚未修改代码。2.6 的拟定规则是“通知到达立即读；等到设定时间仍无通知也读”，两条触发路径均调用同一 PostgreSQL/after 查询；具体补查间隔尚未确定。Redis 提醒的是后端读取逻辑，浏览器继续只接 SSE；浏览器不直连 Redis，也不自行扫描数据库。通知丢失时，数据库健康且订阅有效便可在后续补查中读到已提交事件，影响发现及时性而非事件是否存在。先解决该误解，不继续叠加 Outbox 名词。

补查边界复核题：在数据库正常、订阅仍有效且保留定时补查的前提下，Redis 通知丢失影响的是“事件是否存在”，还是“发现事件的及时性”？用户回答“发现事件的及时性，超时也会补查”。该通知与超时补查关系的解释通过；不扩大为已理解所有 Redis 传输和重连细节。

本轮介绍待发送通知清单（Outbox）：它是 PostgreSQL 中的数据记录，记录某条 Event 还需要发提醒。拟在写入 Event 的同一个数据库事务里保存待发送通知，成功则一起提交，失败则一起回滚；提交后，由后台发通知的代码读取清单并发送 Redis，再记录发布结果。Redis 发送在数据库事务之外，不把数据库事务和外部 Redis 操作说成可一起回滚。这样进程在提交后、发送前退出，重启仍能从持久清单恢复待发工作；已提交业务事件仍可通过超时补查读取。这里只说明拟定职责和事务边界，当前尚无 Outbox 表或发送器。

Outbox 本地事务复核题：同一个 PostgreSQL 事务中，Event 的 INSERT 已执行成功，但待发送通知的 INSERT 报错；应只保留 Event，还是两项一起回滚？用户回答“两项一起回滚”，该本地原子性判断通过。用户同时追问“Outbox 适合做这种有并行操作的但是要保证事务一致性的工作是吗”，需要限定用途与一致性范围，不把该推论直接标为通过。

本轮澄清：Outbox 关注数据库保存业务事实与对外消息/通知的双写衔接，完全顺序执行也会使用；业务事务内 Event/Outbox 一起提交，之后发送 Redis，再用独立事务标记发布结果。它保存可恢复的发送待办，不把 PostgreSQL 和 Redis 变成同时提交/同时回滚的一个事务。若 Redis 暂不可用，已提交业务事实保留，待办留待恢复后重试；不承诺外部永久故障时必然成功或恰好只发布一次。参考：https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/transactional-outbox.html 。

提交后发送失败复核题：Event 和 Outbox 已经一起 commit，接下来 Redis 发送失败，此时应该回滚 Event，还是保留 Event 和待办、稍后重试发送？用户回答“还是保留 Event 和待办、稍后重试发送”。该跨系统故障场景判断通过；不据此预填重复发送、发布确认与浏览器处理确认均已理解。

本轮继续讨论另一处宕机窗口：Event/Outbox 已提交，发送代码成功把提醒发到 Redis，但尚未在 PostgreSQL 把 Outbox 标记为已发送就退出。恢复后清单仍显示待发送，后续重试可能再次发送同一提醒；这里的外部发送与本地确认是两个动作，不能承诺恰好一次。复核题：重新扫描待办时会不会再次发送这条提醒？再次发送提醒是否意味着 PostgreSQL 自动新增了一条业务 Event？用户回答“会，是”。第一项判断通过，第二项有误，不能标记重复发送边界已完全理解。

本轮补讲：在拟定流程里，执行器通过 Journal 追加 Event，并在同事务写入 Outbox；发送代码随后读取已有待办、发送 Redis、更新待办的发布状态，不再次调用事件追加入口。SSE 收到提醒也只按原 after 查询 PostgreSQL。因此同一条提醒可以重复发送，但原业务 Event 的身份和 sequence 不变，Event 表不会因重复提醒自动多一行。例子为第 5 条 message.delta("今天") 已提交，重试只是再次提醒有新进度，不会创建第 6 条相同正文。复核题：浏览器已处理第 5 条，同一提醒再次到达后，后端用 after=5 补查，还会读出第 5 条吗？用户回答“会”，该场景尚未通过。

本轮核对现有 execution/queries.py：查询条件是 RunEventRecord.sequence > after，空结果时 next_cursor 保持原 after。补讲 after=5 表示第 5 条已处理，只查第 6 条及之后；若数据库当前最大序号为 5 则为空页。Redis 可能重复提醒同一进度，但只决定何时查询，不能绕过游标把提醒直接作为正文再次转发，也不会给原 Event 重新编号。复核题：同一个 Run 的事件表已有第 1–8 条，after=6 时应返回哪些序号？用户回答“7,8”，该严格游标边界场景通过；此前错误保留为学习过程，不据此推定所有重复发送场景均已掌握。

本轮继续区分发布确认与消费：拟定 Outbox 的“已发送”仅表示向 Redis 发送提醒的操作返回成功，不表示浏览器已收到或已处理事件；没有浏览器在线时也可以发布成功。浏览器重连仍用自己最后已处理的序号补读 PostgreSQL，事件查询不按 Outbox 是否已发布进行过滤。复核题：浏览器处理完第 4 条后断网，第 5 条 Event 已提交且其 Outbox 已标记已发送；浏览器重连用 after=4，还能读到第 5 条吗？用户回答“能”，该发布状态不阻止补读的场景判断通过。

本轮开始介绍 Redis Streams：它是 Redis 自带的数据结构；XADD 追加提醒，XREAD 可在限定时间内等待后续提醒。计划沿用参考项目的 Streams 方向，后端 SSE 等待被唤醒后仍查 PostgreSQL；等待超时也补查。首次接入的组件是独立 Redis 服务（计划 Docker）与 Python 异步客户端，具体版本、连接配置及启动方式还未决定。多订阅需求题：电脑和手机同时查看同一个 Run 时，应让两条 SSE 连接都能收到提醒并各自补查，还是只把提醒分给其中一条？用户追问“这个需求是什么意思，两条 SSE 是哪两条”，尚未作出场景判断；不把该追问视为通过，也不继续叠加消费者组等术语。

本轮补讲：这里假设后续两个页面登录同一账号并查看同一正在生成回答的 Run，电脑页面到后端是一条 SSE HTTP 长连接，手机页面到后端是另一条；同一电脑的两个标签页也可说明这个关系。Run 仍只有一个、模型任务只执行一次，两个页面各通过独立连接接收同一组已提交事件，目的是让两页都看到进度。此处是后端多订阅的讨论场景，不代表已实现聊天页面或启动手机端开发。复核题：电脑页面关闭、电脑那条 SSE 连接断开后，手机那条连接应继续接收还是跟着断开？用户回答“继续接收”，该连接独立场景通过。

本轮补充拟定接入关系：同一 Run 的提醒写入同一 Redis Stream，每条 SSE 连接独立等待并保有自己的发送游标。XREAD 读取不会取走 Stream 条目，多个等待者均可读取相关提醒（参考 Redis 官方 XREAD 文档）。只读复核 backend/events/redis_transport.py 与 bus.py，参考项目确实把完整 Event JSON 写 Redis，但订阅端忽略其正文、按 PostgreSQL Journal 补读。本轮建议为便于对照沿用完整 Event JSON 的通知形式，最终具体范围尚未确认，不把 Redis 中的副本当作发送正文或终态的依据。复核题：同一 Run 在 PostgreSQL 已有第 5、6、7 条，Redis 暂时只有第 5 条的提醒，当前 after=4；被唤醒后应只发送 Redis 中的第 5 条，还是从 PostgreSQL 补发第 5、6、7 条？用户回答“从 PostgreSQL 补发第 5、6、7 条”，该 Journal 为事实来源的场景判断通过。

本轮只读核对 MiniMew app.py、settings.py、compose.yaml、pyproject.toml 和既有 Journal/SSE：当前仅 PostgreSQL 基础设施，应用生命周期只持有数据库资源，未有 Redis 依赖或后台通知任务。已保存 2.6 接入草案，建议在现有 lifespan 启动持久待办扫描任务，短事务读取后发送，成功后短事务标记，失败有界重试；退出停止任务并关闭资源，重启继续读取未完成待办。新增的是通知发送代码，不提前启动 2.7 的 Run 执行调度。redis-py 异步客户端需显式关闭，已核对官方文档 https://redis.readthedocs.io/en/stable/examples/asyncio_examples.html 。故障复核题：Redis 停机一分钟，但 PostgreSQL 与模型调用均正常，此时应让 Run 失败还是继续执行并靠 SSE 超时补查？尚未发送的 Outbox 应删除还是保留重试？用户回答“继续执行，并让 SSE 超时补查”“保留重试”，两项判断通过。

2026-11-03 实施前只读核对：Docker Server 29.4.3，端口 56379 当时无监听；已有 Redis 官方镜像为 7.4.9（7-alpine/7.4-alpine 指向同一镜像），不把它误记为目标补丁已准备好。Redis 官方镜像页当前列出 7.4.12-alpine，redis-py/PyPI 当前版本 8.1.0、Requires-Python >=3.10，官方客户端说明支持 Redis 7.4；MiniMew Python 3.12 满足此版本要求。具体范围拟用服务端 7.4.12 与客户端 8.1.0，授权后再拉取/锁定镜像 digest 和通过 uv 安装锁定。参考：https://hub.docker.com/_/redis 、https://pypi.org/project/redis/ 、https://redis.readthedocs.io/en/stable/retry.html 。已将端口/命名空间、lifespan 扫描、重试所有者、5 秒补查、Stream 有界保留、Redis 故障与验证范围写入章节；不要求背诵配置数值。当前待反馈：用户是否明确开始上述 2.6 具体范围；没有新的待答理解题，也没有收到开始授权。本轮仅更新文档及只读检查，未拉取/启动 Redis、修改依赖/Compose、创建迁移或应用代码，未跑应用测试、未 commit/push。

### 第 2.6 小步：实施与验收（2026-11-03）

开始反馈：用户明确回复“开始 2.6”，授权章节中的 [具体范围](chapters/02-持久化普通对话.md#26-具体实现范围)。修改共享 Journal 与新增迁移前已说明影响及不变量；以 MiniMew 自身为边界，保留此前未提交内容，不修改上级应用源码。开工快照在本机忽略的 artifacts 中，不作为跨设备学习依据。

正式实现：

- 新增 Redis 7.4.12 Alpine 官方镜像，锁定 digest `sha256:8da19c7399dca900c6822a1cf30f9d2285585e2c4b457ff4095b0f4500054d3f`，独立容器 `minimew-redis-1`、端口 `127.0.0.1:56379`、数据卷 `minimew_redis_data`。通过 uv 新增并锁定 redis 8.1.0，原有依赖版本未变。只在忽略的 `.env` 追加独立 Redis 配置/随机密码，不输出原文；入口脚本生成哈希 ACL，公开字段/日志不带密码。
- 新增 EventOutbox 和 0009 迁移：仅关联原 Event，保存发布/尝试/下次重试/稳定错误类别。原 append_event 同事务加入 Event/Outbox；延迟约束拒绝新事件缺少待办，禁止删除未发布待办或回退已发布状态。历史事件不修改、不补造通知；原 Message/Run/终态事务保持。
- 新增 execution/outbox.py：短事务读待办，释放后发送，成功再短事务确认；CAS 仅更新未发布行，两个发布者重复发送也不能重开已发布待办。空闲扫描 0.5 秒、批量最多 20、周期预算 10 秒；失败保留待办，1 秒起、最多 30 秒退避。显式关闭 SDK 隐式重试，由发布代码拥有重试。日志只有受控类别；非预期任务退出有明确日志。
- 新增 execution/notifications.py：每 Run 独立 Stream，保存完整公开 Event JSON，以原 sequence 的 `<sequence>-0` 为通知 ID。Lua 原子追加与 TTL；相同/更旧序号由现有较新提醒覆盖，不重复造业务事件。近似保留 1000 条、24 小时空闲过期；发布/等待分别最多 4/32 个连接。关闭时尝试并等待两个池，任一失败仍关闭另一个。Redis 副本不进入正式正文发送链。
- app.py 的 lifespan 创建 Redis 资源、做有界探测、启动待办扫描，退出先停止等待任务、再关闭 Redis 和数据库。Redis 启动时或运行中不可达均明确记录，仍保留数据库业务路径；/health/ready 继续只探测 PostgreSQL。没有启动 Run 领取循环、Provider 或真实模型。
- 同一 EventSubscription 在空闲时 XREAD，收到提醒/超时后都调用同一 events_after；默认最多 5 秒补查，积压页立即补齐。每订阅另有内部已见提醒位置以避免空查循环，该位置不推进业务 after。Redis 故障时有界等待再补查，浏览器仍只连接 SSE。业务归属、原 Event JSON、终态尾序号、断连/背压/到期清理保持。
- 原查询与发布共用 Journal 的公开序列化；正式 Event Schema、OpenAPI、Python/TypeScript 生成物无需变化。README 与 [职责导航](chapters/02-持久化普通对话.md#26-实现后的职责导航) 已写明实际源码、数据流、失败行为与运行边界。

新增/修改测试及实际运行（分组记录，重跑不相加）：

- 新增 tests/test_notifications.py 最终 13 个离线场景，tests/test_outbox_pg.py 6 个 PG 场景，tests/test_redis_integration.py 6 个真实 Redis/PG/HTTP 场景，以及 test_sse.py 1 个期限任务用例（该文件最终 20 个场景）。新增容器 helper 与 check_outbox.py/check_notifications.py；原有行为断言保留。conftest 显式给普通测试注入无外部通知资源，真实 Redis 只由可信入口开启，避免离线测试隐式联网。
- 首次 `uv run --no-sync pytest -q tests/test_notifications.py tests/test_sse.py tests/test_migrations.py tests/test_app.py tests/test_database.py`：45 passed，无跳过（当时通知 12、SSE 19）。新增“双池关闭部分失败”后定向重跑 `pytest -q tests/test_notifications.py`：13 passed；此次重跑对应 Redis 关闭实现变化，不累计为新的 25 个用例。
- 首次 `uv run --env-file .env --no-sync python scripts/check_outbox.py`：5 个 Outbox PG 用例通过，但脚本随后在旧数据夹具失败，整条命令当时未通过。原因是最新 Journal 需要 0009，迁移前夹具也不能在 0007 创建无快照的新 Run。已改为在 0005 创建真实旧 pending、在 0007 用当时 SQL 构造旧终态，再迁移到 head 验证逐字段保留；升级后正式执行器仍明确拒绝旧无快照 Run、且 Provider 不被调用。以 `uv run --env-file .env --no-sync python -c "import asyncio,sys; sys.path.insert(0,'scripts'); from check_models import main; asyncio.run(main(test_files=[]))"` 单独复验迁移通过，没有为此重复已通过的 5 个事务用例。
- 首次 `uv run --env-file .env --no-sync python scripts/check_notifications.py`：3 passed、2 failed。已通过真实重复发布/大整数/裁剪/TTL、多次发送同一身份，以及错误 Redis 副本不能替代 PostgreSQL 正文。失败来自测试容器自动 HostPort 在重启后变化（只读复现实测 51024→51027），以及新连接池断言在并行任务正常短借连接时采样。测试 helper 改为本次容器固定临时端口；断言保留零借用检查，改在服务器 loop 中有界观察归还。原有历史用例的断言未删减。
- 修复后对两个失败的 HTTP 节点定向运行 check_notifications.py：2 passed，并通过空库/旧数据迁移。没有同时重复其余三个已通过节点；后续因关闭资源代码变化才扩大复核相关路径。
- `uv run --no-sync pytest -q tests/test_auth.py tests/test_refresh.py tests/test_threads.py tests/test_runs.py tests/test_model_control.py tests/test_execution.py`：108 passed，无跳过；这些模块是本轮首次执行。
- 因共享 Journal/schema/应用启动改变，扩大至原有 8 个 PG 文件（auth、refresh、threads、runs、execution、events、sse、stream_reads），再加入新双发布者节点及 4 个真实 Redis 节点，通过 check_notifications.py 显式选择执行：90 passed、2 failed，共 92。两个失败分别为原 JWT 到期断言与新并发观察点，不能把这轮写成全绿。
- 到期问题实测本机 Python 3.12 的 monotonic 使用 GetTickCount64、分辨率 0.015625 秒；一次性将 UTC exp 换算为单调 timeout 可能提前几毫秒结束。保留原 `time.time() >= expires_at` 断言，修改正式连接期限任务：唤醒后复核 UTC、发送前仍检查已验签到期，退出统一清理期限/发送/断连三个任务。新并发观察点移到两次短读均释放、任一确认事务开始前，仍断言零借用，避免把另一发布者的合法确认事务判为泄漏。
- 上述改动后 `uv run --no-sync pytest -q tests/test_sse.py`：20 passed；用 check_notifications.py 仅选择原到期节点与新双发布者节点：2 passed。随后复验受连接期限变化影响的其余 SSE HTTP 及四个真实通知 HTTP 节点：`uv run --env-file .env --no-sync python scripts/check_notifications.py -- tests/test_sse_pg.py tests/test_redis_integration.py::test_http_two_subscriptions_wake_before_poll_and_disconnect_is_independent tests/test_redis_integration.py::test_http_redis_lag_and_untrusted_copy_still_replay_whole_journal tests/test_redis_integration.py::test_http_redis_down_keeps_run_and_pg_replay_then_restarts_pending_outbox tests/test_redis_integration.py::test_api_starts_while_redis_is_down_and_later_publishes_pending -k 'not verified_access_expiry'`：13 passed、1 deselected。该排除项是刚独立通过的同一到期节点，不是跳过或失败。
- 当前各个受影响用例均有上述实际通过记录；没有在最后再重复整套 92 个或默认完整集，不把各轮重跑累加为总用例数。迁移已反复证明空库/重复升级/metadata、旧用户 fail-closed、旧快照/消息/终态保留和旧无快照不调用模型；每轮临时库均在 finally 清理。
- `uv run --no-sync python scripts/generate_event_contract.py --check`、Ruff check 与 format --check（73 个 Python 文件）通过。逐字节比对事件真源/Python/TS、HTTP OpenAPI/生成 TS 和前端依赖声明/锁文件保持原样；重新组装正式路由比对 OpenAPI 语义一致。uv.lock 只新增 redis 8.1.0，原有包版本未变。
- 收尾检查发现可配置补查间隔最大为 30 秒，而 Redis 读 socket 若固定 6 秒会在健康阻塞等待中误报故障。已改为“配置补查间隔 + 1 秒”，外层单次等待预算仍生效；默认配置下仍是 6 秒。给有界连接池用例加入最大补查配置检查后，通知离线集再次 13 passed，健康双订阅 HTTP 节点再次 1 passed；这两次是该资源配置修改的定向复核，不计为新增测试总数。最终 Ruff/事件生成检查、文档空白与相对链接检查通过。
- 开发库先读取基线，再升级 0008→0009；alembic check 无新操作，check_database.py 的预期表/提交回滚/清理与真实 ready 200 通过。升级后实测仍为 1 用户、5 Thread、1 Model Profile、0 Run/0 Message/0 Event，Outbox 为 0；临时测试库、测试 Redis 容器残留均为 0。Redis authenticated probe 成功，redis-server --version 为 7.4.12，开发 PostgreSQL/Redis 两个容器均 healthy。只启停自己的验收资源，未重启用户已有 API；原进程需重启加载新配置和写入逻辑。
- 保留既有 Starlette/httpx 弃用提示。未跑前端构建/浏览器交互（无前端代码/公开协议变化，类型与 OpenAPI 已核对）；未调用真实 DeepSeek、未执行上级全仓库或生产门禁，原因是不在 2.6 范围。未 commit/push。

原实现后复盘题询问通知任务重启应重发原 Event 还是重调 Provider，用户没有作答，转而要求“详细介绍下目前的完整链路”。本轮已按真实源码从启动、身份/Thread、Run 原子预留、领取/快照/历史、Provider 流、分片事务、Outbox/Redis/SSE，到最终结果与失败恢复进行完整走读，保存到 [截至 2.6 的完整链路走读](chapters/02-持久化普通对话.md#截至-26-的完整链路走读)。特别说明 Vue 还没有聊天输入/SSE 消费，自动运行的是通知任务，RunExecutor 仍由可信验收驱动；不把这次解释当作理解检查通过或 2.7 授权。

用户没有回答上述职责题，进一步反馈“event、redis 提醒、通知待办具体起到什么作用，整个程序运行流程还是有点模糊”。因此仍处于 2.6 职责理解复盘，不能记录为已通过。当前改用同一轮“你好”的两段回答举例：假设可信验收入口已驱动执行器，前两条 Event 已读到；第 3 条正文 Event 与关联 Outbox 在 PostgreSQL 同事务提交，后台通知任务读待办后向 Redis 发提醒，再标记发布，SSE 被唤醒后从 PostgreSQL 按 after=2 读回正文并发送客户端。明确 Event/Outbox 是数据库记录，Redis 是通知服务，执行器/通知任务/SSE 才是执行代码；Outbox 发布成功不代表浏览器已收到，通知任务重试不重新调用模型。默认 POST Run 尚不自动执行、Vue 尚未接聊天流的边界保持。

通知故障复核题：第 3 条 Event 和待办已经提交、Redis 此时停机，SSE 还能从哪里取得这段正文？Redis 恢复后通知任务应重发原第 3 条 Event 的提醒，还是重新调用模型？用户最新回答“Redis 停机了那不就没人告诉 SSE 要去读消息了吗？”“通知任务应重发原事件的提醒”。第二项通过；第一项仍按“只能靠 Redis 唤醒”的前提理解，超时补查尚未通过。

最新用户明确回答此前完整链路走读的两题：“1. 不会，调用模型由 RunExecutor；2. RunExecutor”。据此只将“默认 POST 不自动调用模型”与“Provider 调用由 RunExecutor 负责”标为通过，不将其当作上面的通知故障题答案。已预告 2.7 将讨论真实模型客户端、自动领取 pending Run、故障/重试与端到端验收；正式范围、依赖选择和开始授权均未确定。下一轮先用通知故障场景闭合事实/提醒边界，再继续 2.7 小步讨论；没有新增编码授权。本轮仅更新交接记录，未改应用代码、运行业务测试或重启服务。

用户随后要求按“后续接入模型和前端”的前提走读一次完整的“你好”。本轮以已登录、已有 Thread、模型分两批返回正文为例，区分浏览器 POST 创建 Run 与另一个 SSE 长连接；补上的后台调度负责调用现有执行器，执行与订阅并行，订阅晚建立也可补放。沿用原子预留、冻结配置、执行器领取与 Provider、分片 Event/Outbox、通知任务/Redis/SSE 补查、最终 Message/Run/终态事件事务的同一链路。调度与真实 Provider、聊天前端仍是未来范围，不把目标流程写成当前已上线能力，不确定尚未讨论的调度实现。

本轮只问一个新的完整链路场景：模型已经生成并提交两段正文，前端此时才建立 SSE，是否会丢掉前两段，为什么？用户尚未回答；此前通知故障两题继续待答。仅补充学习交接，未开始 2.7、未改应用或执行业务测试。

本轮针对上述误解只读核对 execution/streaming.py 与 settings.py：默认补查间隔为 5 秒；有效订阅在 Redis 等待返回或超时后继续 read_page，Redis 失败时也等待本轮剩余间隔再读，避免紧循环。计时与查询属于后端 EventSubscription，浏览器不直连 Redis，也不负责轮询数据库。补讲只要 PostgreSQL 正常、订阅仍有效，即使 Redis 持续停机，也会在后续补查读到已提交第 3 条；提醒缺失主要影响发现及时性。复核题询问 Redis 连续停机时是否仍会查库、由什么触发，用户回答“会超时补查”，该读取判断通过。其他关于晚建立 SSE 的问题保留待复核，不因本次回答预填全部通过。

### 第 2.7 小步：真实 Provider（讨论中）

已补充 [2.7 讨论与小步安排](chapters/02-持久化普通对话.md#27-讨论与小步安排)，按真实 Provider、配置/失败边界、自动 Run 驱动、端到端验收推进，未获任何 2.7 编码授权。本轮核对现有模型配置仍为 openai_compatible / deepseek-v4-flash / https://api.deepseek.com，thinking.type=disabled。官方当前仍接受旧模型名，但映射为 DeepSeek-V4.1-Flash-1017，不静默改用户配置或声称固定旧模型服务。

本轮介绍候选 SDK 为 langchain-deepseek 的 ChatDeepSeek，官方支持 native async/流式。LangChain 当前建议 DeepSeek 的扩展字段使用专用集成而非通用 ChatOpenAI；现有 Run Snapshot 的 provider 标识不因此自动改变。准备编写的 DeepSeekProvider 是适配器：接收既有 ProviderRequest，把快照/消息转换为 SDK 请求，再把响应转换为 ProviderDelta/ProviderCompleted；RunExecutor 继续拥有执行与持久化，不让 SDK 或通知任务决定 Run 状态。思考参数须显式禁用，密钥仅从服务端独立配置取得。依赖组合/版本、正常完成判定、超时与重试规则仍需讨论确认。

真实流失败复核题：DeepSeek 已输出“你好”，随后连接中断、没有确认正常结束，适配器能否发送 ProviderCompleted？执行器应如何处理已经提交的文字？用户回答“1. 不能；2. 等待”。第一项判断通过；第二项需要先限定“暂时无新分片”与“SDK 已明确报错终止”，不能直接记录为失败收尾已理解。此前仅更新文档和核对官方资料，未安装 SDK、修改应用、调用模型或开始自动调度实现。官方参考：https://docs.langchain.com/oss/python/integrations/chat/deepseek 、https://docs.langchain.com/oss/python/integrations/chat/openai 、https://api-docs.deepseek.com/guides/thinking_mode/ 。

本轮只读核对现有 execution/service.py 与 repository.py：模型流暂时没有下一项时可在绝对 deadline 内等待；SDK 已抛连接错误结束时，将其转换为 ProviderFailure，由 RunExecutor 调用既有 finish。数据库正常且 owner/lease 仍有效时，从已提交 delta 恢复原 assistant Message 的可靠前缀，保存 failed 与 Run 失败终态，不能无限等待已结束的流或伪造 ProviderCompleted。当前 Provider 接口没有恢复断开流的方法，再发模型请求属于另一次调用，重试策略仍待讨论。复核题询问明确报错后应继续 running 等待还是失败收尾，用户回答“执行器将 run 保存为 failed 并保留正文”，该失败状态与可靠前缀的场景判断通过。没有应用修改、依赖安装、业务测试或新的编码授权。

用户进一步询问“Redis 起到什么作用，为什么使用 Redis”。本轮聚焦引入动机：Redis 是后端 SSE 的进度唤醒通道；仅靠 PostgreSQL 定时补查也能工作，但频繁查库与发现延迟存在取舍。已有实现采用 Redis 提醒提前唤醒、默认 5 秒超时补查；Outbox 发布任务仍默认每 0.5 秒扫描数据库，不能解释成完全不轮询。Redis Streams 也能让不同后端进程中的订阅共享提醒，同一 Run 的多个订阅各自补读，当前没有决定新增执行部署形态。明确 Redis 不决定任务完成，Event Journal 仍提供正文和重放。本轮未获得新的理解答案或编码授权。待用户用自己的话解释：Redis 持续不可用而 PostgreSQL 与 SSE 正常时，靠什么继续发现新事件？仅更新交接记录，未改应用或运行业务测试。

用户继续追问“Redis 是怎么做到唤醒后端 SSE 的”。本轮核对当前 notifications.py/streaming.py 与 Redis 官方 XREAD 文档，沿一条订阅解释实际机制：SSE 处理协程通过已有 Redis TCP 连接发送 XREAD BLOCK 等待更大的 Stream ID；无新提醒时 await 暂停该协程，事件循环仍处理其他请求；通知任务从另一 Redis 连接执行 XADD 后，Redis 向等待的读连接返回新记录，异步客户端完成等待，SSE 协程继续调用同一 PostgreSQL read_page 查询，再向浏览器的 HTTP SSE 连接发送 Event。提醒在等待前已存在时可直接读到；超时返回也继续补查。没有 Redis 主动调用 Python 函数或新建 HTTP 回调。此前“会超时补查”的通过结论保持，本轮机制追问不表示新的编码授权。新增复核题只针对阻塞等待：最多等 5 秒，但第 0.2 秒已有提醒，是否还需要等满 5 秒才查库？尚未作答。本轮仅更新交接，无应用改动或业务测试。

本轮继续讨论 2.7 的模型重试草案：未提交正文的暂时性连接失败可在原 Run/快照内有限重试；已有已提交正文则失败并保留，不从头调用模型后直接拼接；密钥/参数错误不盲目重试，存储错误不能伪装为 Provider 失败重调模型。建议初始最多 2 次模型尝试（首次加 1 次重试），SDK max_retries=0，由 RunExecutor 单独控制所有尝试与退避，沿用最初绝对 deadline 和执行资格，不每次重置预算。现有执行器尚没有该重试循环，本轮只是待确认设计，重试也不保证外部服务只生成/计费一次。预算复核题询问总预算 60 秒、首次已耗 50 秒后是否重置，用户回答“10s，不会”，该总预算判断通过；重试等待等开销仍从剩余预算扣除。尚无 2.7 编码授权，没有安装 SDK、修改应用或调用真实模型。

本轮开始讨论自动 Run 驱动：拟新增的 Run 调度器随应用生命周期运行，从 PostgreSQL 找到 pending 候选后，在有界并发下调用既有 RunExecutor.execute；执行器内部的 repository.claim 短事务才决定实际执行资格，扫描本身不授予资格。同 Thread 先后次序仍由既有领取规则约束，不同 Thread 可并发；重启可再次发现未领取的持久 pending，不依赖内存队列作为唯一任务来源。与现有通知任务分工明确：前者处理待执行 Run、后者处理待发布 Outbox；Redis 继续用于 SSE 提醒，不成为模型调度的事实队列。复核题询问两个调度器同时发现 pending Run 是否都能调模型及资格来源，用户回答“不会，通过 claim 事务领取资格”，该场景判断通过。这里只讨论架构，尚未决定具体扫描参数，未编写调度器或获得 2.7 授权。

本轮介绍应用启停的拟定边界：正常退出先停止领取新任务，有界等待活跃执行后取消剩余任务、尽力失败收尾并关闭资源；硬退出可能来不及写终态，不能声称一定已经 failed。重启发现 pending 可重新走 claim；running 且 lease 未过期不可抢占/重置；只有 running 的租约已过期，才通过既有 recover_expired/expire 原子失败终结并保留已提交前缀，不重新调用模型。后两种状态以数据库当前时间和原 owner/lease 为准，防止干扰其他仍有效的执行者。当前已有可信过期终结方法，但自动周期驱动尚待 2.7 实现；不扩大为第 7 章恢复/接管能力。复核题询问重启后能否把租约仍有效的 running 改回 pending，用户回答“不行，保留原执行资格，不重新调用模型”，该处理选择通过。无 SDK 安装、应用改动、模型调用或新增编码授权。

用户质疑“那不就是从扫描数据库变成了扫描 Redis 读取吗”。本轮区分高频空查与 XREAD BLOCK 的单次挂起读取：没有新数据时，读请求留在 Redis 等待，有新记录或到期才返回，之后才发下一次读取；不能把它描述成完全没有读取循环或没有连接开销。同时承认当前实现保留数据库轮询：每个应用实例的通知任务默认 0.5 秒扫 Outbox，各 SSE 无提醒时默认 5 秒兜底补查。收益应表述为集中扫描待办、阻塞等待提醒与低频补查，减少为及时性进行的逐连接高频空查；未宣称所有负载都更低。用户尚未独立复述该区别；此前通过项及 2.7 无编码授权边界保持。本轮仅更新学习交接，不改应用或运行业务测试。

实施前只读核对：现有 Python 环境为 FastAPI 0.162.0、Pydantic 2.14.1、httpx 0.28.1，尚无 LangChain/模型 SDK。PyPI 当前 langchain-deepseek 为 1.1.1（Python >=3.10,<4，要求 core>=1.4.7、openai 集成>=1.3.1），候选最新依赖见章节范围；OpenAI SDK 3.x 与 LangSmith 的当前版本会增加 httpx2，不误称现有 httpx 必须被替换。第一次 urllib 源码元数据请求遇到 SSL EOF，未关闭证书验证；随后用既有 httpx 只读取得官方 wheel，验证 SHA-256 并在内存检查 ChatDeepSeek/集成客户端字段、max_retries 与资源引用，没有安装或执行 wheel 内容。DeepSeek 官方当前 finish_reason=stop 与异常/截断含义也已核对。

本机只返回了 MiniMew/.env 中 MINIMEW_DEEPSEEK_API_KEY 是否配置的布尔状态，检查当时为未配置，未显示内容。通过异步问题告知仅本机填写，用户选择“现在在本机配置”，这是准备意向，尚无“已配置”的完成反馈，也不是编码授权。缺少 Key 时保留身份/Thread 功能、新提问 503、已有同键 Run 仍返回的提案已说明。当前没有新的待答理解题，待反馈为本机密钥准备状态及是否明确“开始 2.7”。没有安装 SDK、修改 manifest/lock、实现 Adapter/调度器或调用真实模型；本轮仅核对资料和更新提案文档。

### 第 2.7 小步实现与验证（2026-11-04）

用户明确反馈“已配置，开始 2.7”。只检查本机 Key 是否存在，结果为 true，未显示内容，未改用户 `.env`。已实现原 Provider 接口上的 `DeepSeekProvider`、执行器两次上限重试、自动 RunDispatcher、缺 Key 的新预留 503/旧 Run 幂等优先、定期过期失败终结和有界停机清理。每个模块职责及完整实际链路见 [2.7 走读](chapters/02-持久化普通对话.md#27-实现后的职责走读2026-11-04)。

技术与不变量：uv 引入 langchain-deepseek 1.1.1 / core 1.7.4 / openai 集成 1.7.3 / SDK 3.44.0 / langsmith 0.14.17 / httpx2 2.16.0；比讨论时 SDK 候选 3.42.1 新，但属于已授权兼容解析，28 个新锁定包包含平台条件依赖，旧依赖版本未变。快照继续使用 openai_compatible / deepseek-v4-flash / https://api.deepseek.com / thinking.disabled；SDK 请求显式关闭思考、默认身份继承、重定向、自动重试与外部追踪。密钥只在配置和此次调用内使用，不进入快照、事件或公开状态。

自动调度默认扫描 0.5 秒、每实例最多两个活跃执行；候选排除被同 Thread 前序工作阻塞的后继，最终仍由原 claim 事务裁决。原 owner、租约、deadline、消息对和冻结输入贯穿重试。只有暂时失败且无已提交正文时允许再尝试一次；已有正文则失败保留前缀。每 5 秒有界处理至多 100 个过期 running；有效 running 不抢占，过期终结不重调模型。正常关闭停止扫描，先等 5 秒，再取消并有界等待清理。库写入故障不触发模型重试，无法保存的 running 等过期终结。

本轮新增 **45 个不同测试场景**：SDK 协议 25、重试 8、调度器生命周期 3、真实运行集成 9。各组实际结果如下；按不同用例统计，本轮总计 **210 个取得通过记录**，不是一次全量 210 passed，也不把复验加入不同用例数量：

| 实际命令 / 组 | 首次或定向结果 | 复验与原因 |
| --- | --- | --- |
| `uv run --no-sync pytest -q tests/test_execution.py tests/test_runs.py tests/test_app.py tests/test_model_control.py` | 66 passed | 既有受影响离线行为，本轮实际运行 |
| `uv run --no-sync pytest -q tests/test_deepseek.py --tb=short`（最初 19 个场景） | 18 passed、1 failed | LangChain 默认未显式发送 n；实现补充 n=1，保留原参数断言 |
| `uv run --no-sync pytest -q tests/test_execution_retry.py tests/test_deepseek.py::test_sdk_parameters_public_stream_and_close --tb=short` | 9 passed | 其中 8 个重试新场景首次执行，1 个为上述失败的定向复验 |
| `test_deepseek.py -k 'two_calls or cancel_closes or initialization_error or redirect'` | 4 passed、19 deselected | 后续新增的隔离/关闭/重定向场景；未机械重跑已通过协议场景 |
| `test_deepseek.py -k stream_network_error` | 2 passed、23 deselected | 后续新增的流中网络读取错误/超时归一化 |
| `uv run --env-file .env --no-sync python scripts/check_runtime.py` | 65 passed、1 failed；该次命令失败 | 含新增运行 9 + 原 runs/execution/events PG 57。失败是新用例复用旧 facts helper 时跨 READ COMMITTED 语句看到不同提交时点；未改旧 helper/断言 |
| `check_runtime.py tests/test_runtime_pg.py::test_two_dispatchers_compete_but_call_provider_once` | 1 passed | 新等待逻辑只轮询单个 Run.status，终态后再核对原有完整事实断言；不重跑其余 65 个已通过用例 |
| `uv run --no-sync pytest -q tests/test_dispatcher.py tests/test_sse.py tests/test_notifications.py tests/test_database.py tests/test_migrations.py --tb=short` | 45 passed | 含新生命周期 3 + 原相关离线 42；并非上级或整个 MiniMew 全量回归 |

上述实际运行没有 skipped；`-k` 排除的是 deselected。既有 Starlette/httpx 弃用提示仍保留；故意畸形的 SDK 数字正文会有 Pydantic serializer warning，测试明确拒绝该协议，未通过削弱断言屏蔽失败。

其他实际检查：

- `uv run --env-file .env --no-sync python scripts/check_deepseek.py --online`：真实模型返回 `OK`；通过正式 POST 自动执行、5 条连续 Journal 事件、完整 SSE 和游标补读。128 输出 token / 60 秒的临时 Profile，不改开发 Profile。该调用只有短提示，不含业务文档或用户数据。
- `ruff check src tests scripts`、`ruff format --check src tests scripts`：通过，71 个 Python 文件格式一致；最初格式/导入检查发现的问题已修复，最终为通过。
- `python scripts/generate_event_contract.py --check`：通过；事件真源与 Python 生成物同实施前逐字节一致，未修改事件 TS 消费者。执行 `scripts/export_frontend_contract.py` 和 `npm --prefix frontend run contracts` 成功，HTTP 生成物仅同步 503 描述，无 wire 字段变化。
- 临时运行验收只做空库 upgrade head；本次无 ORM/表/迁移变更，保留 0009，未重复历史迁移验收。评测资产无需同步。
- 开发库前后均为 1 User / 5 Thread / 1 ModelProfile，Run/Message/Event/Outbox 均 0；临时数据库与临时 Redis 容器残留均 0。没有改开发模型配置或重启/停止用户已有后端。
- 新增环境示例、README 使用/失败边界、章节实际职责走读与本交接记录。保留其他会话的未提交文件，无 commit/push。

未执行：整个 MiniMew 全量、上级 SuperMew 门禁、前端构建/浏览器回归、生产部署和线上故障注入。本次不改 UI，HTTP 类型只改描述；事务/并发/流的风险由上述定向组覆盖。真实在线通过只证明当次 DeepSeek 与完整运行链路可用，不代表所有 Provider 故障已在线验证。用户要在已有服务体验需自行重启后端，使新配置与代码生效；取消 API、Tool/RAG 与聊天页面仍未实现。

本节复盘题：同一 Thread 的 Run A 正在生成，又提交 Run B；另一个 Thread 同时提交 Run C，本实例还剩一个执行名额时是否都等 A。用户反馈“thread应该是独立的把”，提出了不同对话独立性的疑问，尚未完整解释 B/C 的处理。已补讲：同一 Thread 的 B 按顺序等待 A，其他 Thread 的 C 可使用空位与 A 并行；不同 Thread 的数据/执行顺序隔离，但共享每实例最多两个活跃执行的资源上限。未把该简短反馈预填为整个复盘通过。

用户对“两个执行名额均被占满时，第三个 Thread 的新 Run 能否立即开始”回答“等待空位”，该并发资源边界检查通过。同一 Thread 的 FIFO 已补讲，本轮未要求其独立复述，不把单个答案扩大为所有执行/恢复知识均已通过。

### 第 2.8 小步：取消的讨论起点（2026-11-04）

目标是停止用户指定的原 Run，保留其他 Run 的正常执行。沿用 FastAPI、PostgreSQL、asyncio 与同一 Journal/SSE；拟增加持久取消请求、cancelled 终态和取消/完成竞争规则，不需新增服务。实际字段、Contract、迁移、API 行为及完整编码范围均待讨论确认，不提前创建实现。

本轮先区分：HTTP 接收并保存取消意图，与执行器确认取消并提交权威终态，是两个时点。拟沿同一正式链路：取消入口校验用户/Thread/Run 归属并持久记录请求 → 正在执行的执行器发现请求，尝试关闭 Provider 流 → 通过受资格保护的事务保存已提交正文、消息状态和取消终态事件 → SSE 读取该持久事件。不能因客户端点击停止或收到“请求已接收”就提前宣布已取消，也不能保证撤回上游已发生的生成或计费。

用户回答“取消中，等数据库提交 cancelled 了，再 SSE 传回来”，已正确区分请求接收与取消终态提交，该场景检查通过。SSE 传递已提交结果；断线后同一 Journal 补读或 Run 查询也可确认持久状态，不把 SSE 接收作为数据库终态生效条件。

继续讨论取消/完成竞争的拟定规则：沿用同一 Thread → Run 锁顺序与短事务确定顺序。成功终态及事件先提交，则后来的取消请求只返回已有结果，不能改写终态；取消请求先提交，则后续成功提交路径必须在受锁事务内重新检查取消标记并按取消收尾，不能仅因 Provider 已返回完整答案就写 succeeded。此规则为待场景确认的设计，当前代码尚无取消字段/终态或 API。

用户回答“不能，取消请求入库了，执行器读到了就会写入为 cancelled”，取消先提交、成功尚未提交的场景检查通过。补充落实方式：在保存结果的受锁事务内重读取消标记并决定终态，不能仅依赖模型调用前或早先读到的内存值；现有 owner/lease 写入资格仍须检查。

继续讨论未领取 pending 的取消方案：不存在正在执行的模型调用，因此取消服务可在同一短事务中核对归属和 pending 状态，直接保存原 Run 的 cancelled、原 assistant Message 的取消结果、完成/取消事件及 Outbox；不等执行名额，不先 claim 或调用 Provider，也不伪造 owner/lease。未来迁移需明确允许未开始的取消终态，不沿用“所有终态必已执行”的旧约束。取消与 claim 沿同一 Thread → Run 锁顺序裁决；取消事务先提交后，旧候选仍须经过 claim，并因已非 pending 而无法领取；claim 先提交则转为已有 running 的持久取消请求流程。以上仍为待授权设计，没有修改 schema 或应用。

用户对“已扫描的 pending Run 先被取消提交，随后才 claim，能否调用模型”回答“不能，已经取消了”，该场景检查通过。候选可以过时，实际领取仍以数据库状态为准。

继续讨论 running 的取消发现方案：取消接口把请求写入共享 PostgreSQL；拥有该 Run 执行资格的执行器为本次执行管理一个轻量异步监视任务，计划每 0.5 秒用短查询检查取消/执行资格，不依赖下一批模型正文到达。发现取消后尝试停止本 Run 的模型读取并按原资格原子取消收尾，其他 Run 不受影响。模型读取与取消监视属于同一次执行且退出时都要有界清理；正文写入/终态事务也重查标记，监视任务不能替代存储屏障。不同后端进程共享数据库，因此取消请求落在哪个进程不决定谁能发现；不新增全局 task map 或取消专用 Redis 事实队列，Redis 仍用于 SSE 提醒。此处是待授权方案，现有代码尚无该监视任务。

用户回答“能，靠默认定时查询”，已正确说明模型无新输出、Redis 停机但 PostgreSQL 正常时取消监视仍可发现请求，该场景检查通过。

继续讨论重复取消与取消接口故障：同一 Run 已有取消请求时返回当前取消进度，已 cancelled 时返回原取消结果，不追加第二套完成/取消事件或新建 Run；已 succeeded/failed 时保持原终态。重复取消始终使用原 run_id。取消入口数据库操作失败时返回受控 503，页面不能据此宣布已取消；提交响应丢失也可能使结果不确定，恢复后可查原 Run 或重试同一取消请求。单次取消 HTTP 失败不直接决定原 Run 的成功/失败/取消状态，须读取持久事实。

用户回答“不能，这个取消操作没有写入数据库不能算生效”。“不能因取消接口 503 就把原 Run 显示为 failed”的结论正确，但不能据此断言取消一定没写入。已补充结果未确认的例子：数据库已经提交取消请求，提交确认送回 API 前数据库连接断开，API 返回 503；该取消请求仍可能被执行器发现并最终取消原 Run。503 本身不能区分未提交与确认丢失，界面应保留未知状态，恢复后查询原 Run 或对同一 run_id 重试取消，不新建 Run。

用户回答“查询原 Run 的持久状态来确认”，已纠正“503 一定没提交”的推断，该不确定结果的复核通过。

2.8 接着讨论两个故障边界的拟定方案：一是执行器取消监视读库失败时，无法确认取消和资格，不能把异常当作“没有取消请求”继续正常生成；应停止本 Run 的模型读取、有界清理，并有界尝试按当前资格保存中断结果（若已提交取消标记则优先取消），不得伪装成 Provider 故障或重新调用模型。数据库持续不可用就无法确认收尾成功，原持久状态可能仍为 running；数据库恢复后由既有过期终结路径处理，不把本地停止等同于已提交终态。二是即使读到取消请求，旧 owner 的 lease 已过期也不得写 cancelled；它只能停止本地调用，过期清理事务按当前持久取消标记决定 cancelled 或 EXECUTION_EXPIRED，保留已提交前缀且终态仅一次，不重跑模型。关闭模型流出错不撤销持久取消请求，清理也须有界；数据库写入屏障继续拒绝迟到分片和结果。以上均为待授权设计，尚无应用/迁移改动。

用户明确回答“1.不能；2.旧执行器只能停止本地调用，不能再写终态”。监视读库故障不能当作无取消请求、取消不绕过过期资格，这两项场景检查通过。仅记录这些实际判断，不扩大为后续 HITL/接管知识已经掌握。

已基于当前源码形成 [2.8 具体实现范围](chapters/02-持久化普通对话.md#28-具体实现范围)：0010 与取消时间/状态约束；同一 Run 的取消 API（pending/终态 200、running 请求 202、受控 503）；run.cancel_requested/run.cancelled 与严格跨端生成；独立监视/终结竞争/存储故障与过期处理；SSE/幂等及定向验证。沿用现有依赖，未增加应用代码、迁移、配置、Contract 或测试。当前没有新的待答理解题，待反馈为用户是否明确“开始 2.8”。本轮只更新章节提案和本交接、检查文档差异；不跑业务测试、不调用模型、不 commit/push。

### 第 2.8 小步实现与验收（2026-11-04）

用户明确反馈“开始2.8”，按已确认范围实施完成。职责导航、取消实际链路与取舍保存于 [2.8 代码走读](chapters/02-持久化普通对话.md#28-实现后的职责走读2026-11-04)，不是第 2 章结章总结。取消始终操作原 Run/消息对；请求与终态分开，持久数据库是权威，SSE 仅传递。

实现结果：

- POST `/v1/threads/{thread_id}/runs/{run_id}/cancel`：pending 原子取消返回 200；running 保存请求后 202；重复取消不重复写请求/终态事件；已有终态 200 原样返回。身份/归属沿用正式认证与 owned Thread，取消受控响应 no-store，存储异常 503，结果不确定时查原 Run。
- `cancel_requested_at`、Run/assistant 的 cancelled、新事件 run.cancel_requested/run.cancelled、EXECUTION_STORAGE_UNAVAILABLE、SSE cancelled 尾部/204 和生成类型均已接入。pending 不伪造 owner/lease/run.started；正文只从已提交 Journal 恢复；已取消轮次不进后续成功问答上下文。同键同正文重发仍返回原取消 Run。
- 原 Repository 统一锁、追加与终结事务；finish 返回实际保存的终态，执行器不会将取消误报成功。普通结果仍要求 owner/有效 lease，独立 expire/recover_expired 路径用数据库过期条件裁决，有请求则取消、否则 EXECUTION_EXPIRED，不恢复模型执行。
- 每 Run 的模型消费/监视任务由该次执行持有，默认查询间隔 0.5 秒。初始检查在模型预算开始前完成，后续监视先等待间隔，避免重复首轮查询撞上模型启动；所有尝试/退避共用原 deadline，外层等待也有截止保护。停止/异常清理有界，预算结束可再发取消并收集异常。监视存储故障停止消费且不重调模型，保存失败时仍可能留下 running 等过期终结。
- 0010 前向迁移已用于开发库；旧迁移逐字节未改，旧状态/快照/正文/事件/Outbox 保留。未新增生产依赖，pyproject.toml / uv.lock 与实施前一致。无真实 DeepSeek 在线调用，未改 `.env` 密钥、开发 Profile 或写开发测试对话。

本轮新增 **38 个不同测试场景**：取消离线控制/契约 8、PG 25、真实 HTTP/Redis/双进程 4、0009→0010 保留 1。按不同用例统计，本轮 **241 个获得通过记录**（离线 127 + PG 扩大组 100 + HTTP 4 + 自动驱动 9 + 迁移保留 1），不是一次全仓库 241 passed；以下复验不重复累加用例数。

| 实际运行组 | 首次/定向结果 | 复验及原因 |
| --- | --- | --- |
| `pytest -q tests/test_execution.py tests/test_execution_retry.py tests/test_runs.py --tb=short` | 46 passed | 首批受影响离线检查，后续扩大组包含这些场景 |
| `test_cancellation.py` 初始 7 场景 + `test_migrations.py` | 9 passed | 新契约/控制流与迁移 head；之后新增绝对截止场景 |
| `check_runtime.py tests/test_cancellation_pg.py` 初始 14 场景 | 7 passed、7 failed，命令失败 | pending 的 server-generated finished_at 被隐式异步加载触发 MissingGreenlet，事务回滚；改显式 refresh 后原失败 7 场景定向 7 passed，保留断言 |
| 新增竞争/退避/数据库屏障 7 场景 | 6 passed、1 failed | 新测试漏导入 AsyncMock；补导入，随后与 3 个新增监视存储故障场景一起 4 passed，原断言保留 |
| 真实 SDK 受控 transport + 正式执行器取消 | 1 passed | 客户端/流在模型消费所属任务中关闭，取消前缀保留；后续扩大组也覆盖 |
| `check_runtime.py tests/test_cancellation_http.py` 初始 3 场景 | pytest 3 passed，但停机场景故障注入未 await，有 RuntimeWarning，不能算停机验收通过 | 改为等待 stop 并断言容器确已停止；该节点 1 passed。之后新增并行隔离场景 1 passed；最终完整 HTTP 组 4 passed |
| 新增绝对截止/吞取消场景 + 原重试 8 | 1 failed、8 passed | 清理超时后首次取消被吞，closed 原断言失败；收尾再发停止并收集任务，原用例定向 1 passed。组合命令虽最终退出码为 0，仍按该 pytest 子步骤失败记录 |
| 离线扩大组：cancellation/execution/execution_retry/runs/sse/notifications/migrations/app/database/model_control/dispatcher | 127 passed | 不是整个 MiniMew/上级全量；之后监视启动时机调整涉及 cancellation/retry，相关 16 场景复验 16 passed，其余相关行为未变 |
| `check_models.main(test_files=[runs_pg, execution_pg, events_pg, outbox_pg, sse_pg, stream_reads_pg, cancellation_pg])`（文件均在 tests） | 首轮 98 passed、2 failed，命令失败 | 原连接释放断言、极短预算下 Fake 调用次数断言暴露首轮监视查询时机；未改原断言，初始检查移到预算前、监视从首个间隔开始。两类原场景与监视故障/SDK 共 7 场景定向通过；同一扩大组最终 100 passed |
| 同一 check_models 入口空库与历史升级路径 | 最终完整通过 | 含 legacy 无凭据原子拒绝、旧快照/消息/事件保留、禁止补猜、无历史 Outbox 回填、旧 Run 不调用 Provider、重复升级与 metadata。历史 fixture 改为显式读取旧 schema 已有字段，避免用新 ORM 列读旧库；断言不弱化 |
| `check_runtime.py tests/test_cancellation_migration.py` | 1 passed；SQL 夹具格式调整后定向复验 1 passed | 真实 0009 中 pending/running/succeeded/failed、快照、分片、终态及 Outbox 在 0010 下逐行一致，旧取消时间全 NULL |
| `check_runtime.py tests/test_runtime_pg.py` | 9 passed | 验证既有自动执行、同 Thread FIFO、跨 Thread 上限和领取竞争等调用方 |

本轮实际选中场景没有 skipped；没有用“收集但跳过”冒充通过。旧执行器/事件/SSE 回归断言未删除或弱化；只按新 schema 更新 head 断言，并让旧内部 mock 实现新增控制接口及实际终态返回值。最初格式/导入问题已修复，最终检查通过。

其他实际检查与环境结果：

- `ruff check src tests scripts migrations`、`ruff format --check src tests scripts migrations`：通过，87 个 Python 文件；`generate_event_contract.py --check` 通过。
- `export_frontend_contract.py`、`npm --prefix frontend run contracts`、`npm --prefix frontend run typecheck`：通过。Event/HTTP 消费者均从真源生成，没有手改生成文件；前端只同步类型。
- 生成物 Prettier 检查最初发现新 RunStatus 别名断行与规则不一致；已修正生成器的“赋值另起一行、union 仍可同一行”分支，再从真源生成。Python 输出逐字节未变；原生成器回归用例定向 1 passed，`generate_event_contract.py --check`、三个生成文件的 Prettier 检查及前端 typecheck 均通过。该格式修正不改变 wire 或后端执行，未机械重跑运行集成组。
- 开发库 `alembic upgrade head`、`alembic check`：通过，0010，无结构漂移。显式调用 check_database 的真实事务检查函数通过（预期表、提交/回滚/Session 清理），未通过该探测启动业务执行任务。
- 开发库前后都是 1 User / 5 Thread / 1 ModelProfile，Run/Message/Event/Outbox 均 0；临时数据库残留 0、临时 Redis 容器残留 0。双进程测试用独占端口/stdin 正常停止并检查退出码，不终止用户进程。
- README、环境示例、章节走读、脚本 revision 与本交接已同步，默认 check_models PG 验收纳入取消。评测资产无变更。保留其他会话文件，无 commit/push。

未执行：整个 MiniMew/上级 SuperMew 全量、浏览器/前端构建、生产部署、真实模型在线取消。新功能仅新增后端控制及类型，前端 typecheck 已覆盖类型联动；真实 LangChain/SDK 资源行为由内存 HTTP transport 验证，未把它写成远端生成/计费已经停止。用户已有后端需重启加载新代码，开发库迁移已完成。

用户对“同一 Thread 使用原幂等键和相同正文再次提交已经 cancelled 的提问”回答“返回原 Run”，该取消后幂等复核通过。已补充：主动重新提问是新意图，使用新键；重复请求不能隐式恢复或重跑被取消的 Run。

### 第 2.9 小步：章节复盘讨论（2026-11-04）

按已有章节计划进入只读走读和文档整理：先核对已保存验收证据，再串起请求预留、模型执行、事件传递与取消控制，最后保存整章总结和实际代码框架。只在相关源码/测试/依赖/配置/结构或环境变化时确定新验收需求，不机械重复已有检查；不将本轮文档讨论描述为新测试通过，不启动第 3 章编码。

本轮先梳理：API/conversations 接收并保存提问；RunDispatcher 扫描候选，RunExecutor 领取并组织执行，Provider 调模型；执行器经 Repository 保存 Message/Run/Event/Outbox；OutboxPublisher 发布 Redis 提醒；SSE 按游标读取同一 PostgreSQL Journal 并传给浏览器。Run/Message/Event/Outbox 是持久数据，调度器/执行器/发布任务/SSE 是运行代码。提交成功、模型完成、数据库终态提交、浏览器收到结果仍是不同时间点。

整体场景复盘已得到用户回答：“算，SSE 超时补读，从数据库里面拿到答案”。成功终态不受 Redis/浏览器故障影响、答案由数据库恢复的判断通过。已补充限定：连接仍有效时 SSE 才会超时补查；浏览器已经关闭时旧连接不会自行工作，重新打开须恢复身份后建立新订阅，历史答案可查 Message、执行过程可按游标补读 Journal。该补充不代表第 3 章浏览器重连已实现或用户已独立复述全部前端机制。

2026-11-04 本轮完成只读核对并保存 [结章文字总结](chapters/02-持久化普通对话.md#结章文字总结) 与 [实际代码框架](chapters/02-持久化普通对话.md#本章结束时的代码框架)：

- 对照 app、api/runs、conversations、model_control、models、dispatcher、service、repository、context、provider/deepseek、journal/queries、outbox/notifications/streaming 和现有 ThreadsView；区分持久数据、执行代码、HTTP 请求及应用生命周期，不引入第二套流程。
- 说明 assistant Message 预留与结束时落库，生成中分片先在 Journal；Thread 的 Message.sequence 与 Run 的 Event.sequence 不同，事件身份不等于排序。
- 沿提问预留、领取、模型输入、分片、终结事务和通知/SSE 六步保存完整链路，补齐取消竞争、存储失败、过期和断线行为；以职责划分排查入口。
- 验收按原运行记录引用：2.7 的真实 DeepSeek 普通对话发生在 0009；2.8 的 0010/取消包括受控 Provider、真实 SDK transport、PG/HTTP/Redis/双进程。没有把受控测试当成真实模型在线取消，也没有把历史结果算成本轮新通过或实时运行证明。
- 2.9 只更新章节文档和本交接，未改应用、测试、Contract、迁移、依赖、配置或评测资产；业务测试新增/修改 0、重复运行 0，未进行本轮业务测试收集，因此没有新的 skipped 记录。未启动/重启服务、调用模型、移动业务文件或 commit/push。
- 目前目录职责可读，本轮没有结构重排。第 3 章将复用已有 Vue/TS/Pinia/fetch/认证/Thread 基础，新增事件投影和聊天交互；新章仍先计划与讨论，再按明确授权编码。此处 HTTP 技术名已按下文源码核对结果从 Axios 修正为 fetch。

本轮实际文档验证：`git diff --check -- MiniMew/AGENT_HANDOFF.md MiniMew/chapters/02-持久化普通对话.md` 通过；使用本项目 Python 仅以标准库读取两份 Markdown，检查行尾空白、最终换行、相对文件与标题锚点，141 个本地链接全部存在，问题 0。未导入应用、加载 `.env` 或访问基础设施。未运行新的业务检查，原验收仍是原记录。

最后一个源码职责场景已答。用户说：“数据库能持续增加正文说明 run 和执行器没有问题，可能是 redis 通知或者超时补查没起作用或者 SSE 失效”。排查方向正确：正文事件持续提交时优先检查通知/SSE/游标及浏览器消费，不能仅凭页面不更新重跑模型。已校准推论范围：此证据说明执行链正在产出并持久提交，不能保证超时、取消或最终收尾等所有执行行为均正常；Redis 故障单独不应使有效 SSE 永久停更，因为还有补查。第 2 章本轮计划内复盘据此结束，不将其他历史未答场景一并改成通过。

### 第 3 章：开章计划与 3.1 讨论（2026-11-04）

已先在对话列出 3.1–3.6 安排，再保存 [第 3 章计划](chapters/03-最小聊天前端.md)：事件投影 → 消息历史与提交 → SSE/重连 → 最小聊天视图 → 按 Run 停止 → 刷新/切换隔离与验收。每项记录依赖、沿用/新增技术、验收场景和职责边界。仍按用户偏好讲架构，不要求复述 Promise 或语法。

本轮只读核对 MiniMew/frontend/package.json、api.ts、auth/threads store、生成事件类型和相关后端消息入口：Vue/TS/Pinia/Router/Vite 及测试工具已有，HTTP 实际使用原生 fetch，没有安装 Axios。上轮章节衔接/交接和原路线把 Axios 当作已有或计划首次引入的表述不准确，已明确告知并修正；没有借修正文档新增依赖。当前普通请求函数按 JSON 解码且预算 15 秒，不能直接承担 SSE 消费，3.3 单独讨论长连接。

当前只讨论 3.1：事件 reducer 是“当前页面状态 + 已校验事件 → 新状态/补读需求”的规则，不调用模型、不发网络请求、不操作数据库。拟用纯 TS 和现有 Vitest 处理同 Run 去重、缺口、正文追加/最终覆盖与请求/终态区分；不创建应用文件、测试或目录。3.1 明确开始反馈尚未取得。

待答理解题：前端已经连续处理到第 10 条事件，重连又收到第 10 条正文事件，随后直接收到第 12 条；两条分别怎么处理，游标最多能推进到哪里？用户尚未回答，不预填通过。

本轮变更限于章节 3 开章文档、章节 2 衔接、路线、README 导航和本交接。没有应用/测试/Contract/迁移/依赖/配置/评测资产修改，没有业务测试收集或复跑，不启动服务/基础设施、不调用模型、不 commit/push。实际文档检查：上述五份文档的 `git diff --check` 通过（已有 LF/CRLF 提示无错误）；标准库检查最终换行、行尾空白、相对文件和标题锚点，209 个本地链接通过、问题 0。第 3 章验证仍无新业务测试通过记录。

### 第 2 章架构图与源码对应补充（2026-11-04）

用户要求“画下目前第二章结束后的架构图，介绍各部分作用，然后告诉我代码目前的结构是怎么和这些相对应的”。本轮按该请求回看第 2 章，3.1 的重复/缺口题继续待答，没有开始前端编码。

只读复核 app.py、提问预留、Run 路由、调度/执行/存储/Journal、Outbox/Redis/SSE 及当前 ThreadsView，保存 [第二章结束时的架构图](chapters/02-持久化普通对话.md#第二章结束时的架构图2026-11-04) 与数据/代码对应表。强调一个 FastAPI 应用进程中的模块/异步任务、独立 PostgreSQL/Redis 和外部 DeepSeek 的边界；客户端以调试/验收工具表示，聊天 UI 不画成已接入。提问、执行、传递和取消沿同一正式链路，没有新建平行入口。

图中 Run/Message/Event/Outbox 都是 PostgreSQL 数据，Dispatcher/Executor/Provider/Publisher/SSE 是运行代码；Provider 输出经 Executor/Repository 保存才成为已提交事件。创建和终结的图上分支属于各自同一个事务，不代表拆成多个提交；Redis 提醒只提前唤醒 SSE 查 Journal，超时同样补查。没有预填用户对新图的理解结论。

本轮仅补充章节图文与本交接，未改业务代码或运行环境，不调用模型、不运行业务测试、不 commit/push。`git diff --check` 通过；两份文档的行尾/换行、151 个相对文件及锚点链接均无问题。初次组合检查误以为整章只有一张 Mermaid，被原有 2.6 图触发失败；未删除旧图，改为限定本次新增架构节后验证通过：13 个已声明节点、19 条边，全部引用存在。这里只检查图源结构与引用，没有运行 Mermaid 渲染器或将其写成图像渲染验收通过。3.1 题仍待答，授权边界不变。

## 换设备与同步

### 获取仓库

仓库目标：`https://github.com/kleepaimeng-crypto/SuperMew`，完整保留 SuperMew 参考实现与 MiniMew 学习目录。私有仓库需要先在新设备登录有访问权限的 GitHub 账号。

```bash
git clone https://github.com/kleepaimeng-crypto/SuperMew.git
cd SuperMew
git status --short
```

已有工作副本时先检查未提交修改，再使用 `git pull --ff-only` 获取已提交进度；有冲突或本地未提交内容时先处理，不能通过 reset/覆盖丢弃学习成果。

### 恢复 Agent 上下文

将整个仓库作为工作区打开，给 Agent 下面这段提示即可：

> 请先读取根目录 AGENTS.md、MiniMew/AGENTS.md、MiniMew/AGENT_HANDOFF.md 和 MiniMew/LEARNING_PLAN.md，按当前检查点继续教学。先沟通项目知识结构、技术职责与本次实现范围，用场景题确认我的理解，得到明确开始反馈后再编码。不要依赖旧聊天记录，也不要重新生成已完成内容。

根 AGENTS 已加入导航，MiniMew/CLAUDE.md 也导入本地 AGENTS。不同 Agent 的自动发现行为可能不同，因此上述提示保留显式阅读顺序。

### 同步范围

- Git 同步源码、文档、迁移、Contract、依赖声明与锁文件以及已记录的学习进度。
- `.env`、Secret、虚拟环境、node_modules、模型缓存、数据库/向量数据和运行产物不随 Git 提交。
- 新设备可以直接继续阅读和讨论；实际运行还需按 README/Runbook 准备依赖和本机配置。学习进度可移植不代表服务和数据库会自动迁移。
- 学习结束或换设备前，更新本文并在用户授权下 commit/push；未推送的本地内容不会出现在另一台设备。
- 后续普通对话不自动授予 commit/push 权限；本次提交发布是用户明确授权的例外。

## 参考实现与开发边界

原项目来源：`https://github.com/icey1287/SuperMew`。保留原 LICENSE 和来源说明。发布前发现上游历史曾提交含疑似凭据的 `.env`，因此个人仓库从当前完整源码快照开始，不复制上游历史；本机原 main 分支及其历史保留作为参考，不应推送到个人仓库。

首次发布设备使用干净的 `codex/personal-main` 分支和 `personal` remote，推送到个人仓库的 `main`：`git push personal HEAD:main`。原 `origin` 仍指向参考项目；新设备克隆个人仓库后使用其 `main`，`origin` 指向个人仓库。每次推送前检查 `git remote -v` 与当前分支，不要盲目照搬旧设备的 remote 名称，也不要将上游旧历史合并进个人发布分支。

MiniMew 学习代码只在其独立范围内按确认后的方案编写。保留现有 SuperMew 用户修改，不把现有源码完成度当作 MiniMew 已完成。发布本机快照也不等于所有线上与集成检查通过。
