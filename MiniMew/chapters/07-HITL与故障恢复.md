# 第 7 章：HITL 与故障恢复

当前学习进度、真实反馈和编码授权只见 [交接记录](../AGENT_HANDOFF.md#当前检查点)。开章计划保留设计口径，实际实现与验证见下方实施记录；完整结章总结待本章结束后填写。

## 开章计划

计划日期：2026-11-14。

### 本章目标与范围

让知识问答在确实需要用户澄清时，持久暂停原 Run；刷新页面或重启进程后，用户仍可补充信息，继续原工具调用并完成原 assistant Message。保留原身份、模型快照、资料版本、Evidence 身份和已消耗预算。

本章承接现有 Agent 外层工具循环、knowledge_search 内部 StateGraph、PostgreSQL Run/Event 和前端事件投影。当前图只有进程内状态；Dispatcher 的过期回收仍是终结过期运行，不是任意节点恢复。不能把现有能力描述成已有持久 Checkpoint。

首版建议每个新 Run 最多一次澄清、等待上限 15 分钟，实际截止时间还须受原 scope 剩余有效期约束。目前 scope 有效期 55 分钟、旧索引保留 1 小时；本章不自动将等待扩大到数小时或跨天，也不在恢复时切换到新版资料。长期等待需要另行设计版本保留与资源回收协议。

### 本章学习节奏与工作包

遵循[开发批次约定](../AGENTS.md#开发批次与教学小节分离2026-11-13-新增)：教学按模块关系讲，开发按功能链连续推进，包内不逐节重启授权或重复全量验收。

- **A 包（7.1–7.3）**：领域状态与迁移 → 可持久化图状态/外层续接数据 → 原子暂停 → 幂等恢复 → 最小前端补充表单，交付一条可刷新、可重启续接的完整链。所有权检查、取消保护和暂停事务正确性属于 A 的必要条件。
- **B 包（7.4–7.6）**：心跳/租约与恢复边界 → 旧 owner 隔离/竞态与故障注入 → 集成验收和结章总结。B 不承担给不正确的 A 补基本事务保证的职责。

新章工作包仍须明确开始反馈；此前第六章修复包不覆盖本章应用编码。实际授权统一记录在交接中。

### 子项安排

| 子项 | 要实现或理解的内容 | 依赖与顺序 | 沿用/新增技术及职责 | 验收场景 |
| --- | --- | --- | --- | --- |
| 7.1 | 等待/待恢复状态、澄清策略冻结、Checkpoint 与恢复记录契约 | 先定不变量，再迁移 | 沿用 Pydantic/SQLAlchemy/Alembic；新增领域状态和恢复幂等记录 | 历史任务升级不变；等待不成为终态，不新增业务消息 |
| 7.2 | 数据化图状态、外层续接数据、持久暂停与事件原子发布 | 依赖 7.1 | LangGraph interrupt、异步 PostgreSQL checkpointer 集成 | Checkpoint、等待状态、提示事件全有或全无；进程退出后仍可读取 |
| 7.3 | 认证恢复、一次性凭据、同 Run 续接与页面等待表单 | 依赖 7.2，完成 A 链 | Command(resume=...)；沿用 API/Dispatcher/Event reducer | 刷新后恢复；重复提交不重复执行；同 assistant/ToolCall/快照 |
| 7.4 | 心跳、租约、fence 和受支持的故障恢复边界 | 基于 A 的真实续接链 | 沿用 PG 所有权协议并扩展；不新建任务框架 | 新 owner 获权后旧 owner 不能提交状态、事件或结果 |
| 7.5 | 取消/恢复/超时竞争及故障注入 | 依赖 7.4 | pytest、真实 PG、同步屏障 | 重复恢复、恢复与取消竞争、暂停事务回滚、过期凭据均有确定结果 |
| 7.6 | 完整链验收、中文总结和实际代码框架 | 汇总前项缺失检查 | 沿用受控 Provider、真实本地检索和浏览器工具 | 重启/刷新/恢复/终态一致；如实记录未支持的重放边界 |

### 整体位置与模块分工

| 模块 | 拥有什么 | 不交给其他模块的原因 |
| --- | --- | --- |
| Run Service / Repository | 身份、状态、恢复资格、owner、事务与唯一终态 | 图节点不能越过用户权限或自己宣布业务终态 |
| Agent 执行器 | 外层模型消息、待完成 ToolCall、Run 预算、最终回答 | 内部图 Checkpoint 不会自动保存外层 Python 调用栈 |
| RAG StateGraph | 检索/评判/改写/澄清节点与节点状态 | 它只拥有知识工具内部策略，不拥有业务 Message 生命周期 |
| Checkpoint 存储集成 | 图游标、可序列化状态、恢复版本与应用续接数据 | 保存图状态与发布业务等待必须协调事务，不能各自写完后假定原子 |
| Dispatcher | 领取可执行任务、创建执行 owner、调度原 Run | 等待用户时释放执行槽；恢复请求不直接在 HTTP 中长期跑模型 |
| Event Journal / 前端 | 持久事件事实 / 根据事实显示等待与补充入口 | SSE 断开、页面关闭或本地倒计时不产生终态 |

LangGraph 的 `thread_id` 拟绑定业务 **Run ID**，避免同一业务 Thread 中的不同 Run 共用图状态。业务 Thread 仍拥有对话和 Message 顺序。首版等待中的 Run 继续占据该 Thread 的执行顺序，后续任务排队；其他 Thread 独立执行。

### 数据流与关键边界

代表性流程（计划）：

1. 用户问“培训差旅能报多少”，原 Run 固定资料版本并调用 knowledge_search。
2. 健康检索发现国内/海外等真实歧义，严格结构化评判给出可回答的澄清问题；有界策略决定进入澄清节点。Provider 故障不能转成等待用户，普通无资料也不自动要求澄清。
3. 图暂停后，在一个短 PG 事务中持久化必要 Checkpoint/续接数据、Run 与 assistant 等待状态及 `hitl.required`。提交后才向用户呈现等待；释放 owner/lease 和执行槽。
4. 页面刷新从持久状态读回提示。用户补充“国内”，认证 API 校验用户、当前 Checkpoint、期限和一次性凭据；同事务消费资格、记录幂等结果并将原 Run 置为待恢复。
5. Dispatcher 领取原 Run、新建 owner，恢复运行依赖，用 `Command(resume=...)` 继续同一图；将补充信息用于有界检索，完成原 ToolCall，接回原 Agent 外层循环。
6. 仍由既有终结事务写原 assistant Message、引用和权威终态事件。用户补充关联恢复记录，不创建另一对业务 Message 或新 Run。

拟议非终态链为 `pending → running → waiting_input → resume_pending → running`；成功、失败、取消继续走唯一终态事务。具体约束与线协议在 A 内从现有真源扩展，不建立平行聊天入口。

必须保持的设计约束：

- **保存数据，不保存活对象**：现有图 State 含结果对象、tuple/frozenset；运行依赖还含闭包、AsyncClient、Semaphore 等。须定义有版本的数据 schema，保存查询、材料身份、图游标、待完成工具调用及必要外层消息；客户端、Secret、DB Session 和 loop 从依赖容器重建。
- **图恢复语义**：interrupt 所在节点恢复时会从节点开头重跑，不能在其前面放无幂等保护的工具执行或预算扣减。拆分节点和持久记录，明确每个可重放边界。依据 [LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts) 与 [persistence](https://docs.langchain.com/oss/python/langgraph/persistence)。
- **原子等待**：默认 checkpointer 自己的连接不等于 Run Repository 的 SQLAlchemy 事务。A 必须落定单一异步存储集成，让可恢复状态与等待事实同提交，并用真实 PG 回滚故障证明；不在模型请求期间持有长事务。
- **保持全异步链**：沿用现有 async Provider / ainvoke，不为照搬参考项目同步图而引入 ProviderLoopBridge。持久适配器须先核对当前 LangGraph 版本接口，再决定复用或实现方式；当前不安装新包。
- **预算跨恢复连续**：4 外层/12 总模型尝试/6 工具/8 检索、一次 Planner/Rewrite/格式纠正的已用额度必须保留；外部调用未知结果不退回尝试额度。等待用户不消耗模型活动时间，持久化剩余执行时间后重建 deadline，不在恢复时重新发放完整时限。
- **资料边界固定**：暂停/恢复均校验原 scope；等待截止须预留剩余执行时间，过期明确终结，不重抓当前版本冒充原任务。Evidence 编号和文档身份不得重绑。
- **恢复权限与幂等**：凭据绑定用户/Run/Checkpoint，数据库只保留校验摘要，Secret 不进入 Journal、Checkpoint、公开字段或日志。刷新后通过认证接口重新取得/轮换资格；不能仅依赖一次 SSE 中的凭据。相同幂等键与相同输入返回同一接收结果，冲突输入或陈旧 Checkpoint 明确拒绝。
- **历史策略不变**：新 Run 使用包含澄清策略的新快照版本；旧 v1–v4 的摘要与执行语义保持，不回写历史任务。新策略不能在恢复时从当前环境重新读取。
- **限定恢复承诺**：承诺已提交等待状态和已接收待执行恢复能跨进程继续；未知结果的外部调用、任意副作用工具不承诺自动安全重放。B 要明确支持边界、过期终结政策和 owner 隔离，不能宣称通用 exactly-once。

### A 包的具体交付与验证范围

实现覆盖 MiniMew 自身模型/迁移、执行器与 Repository、RAG 图、API/Event 真源及生成物、前端等待/补充投影、对应文档。必要的结构化澄清调用也纳入既有总预算，不增加无限追问或另一条执行路径。一次澄清/15 分钟作为本次建议默认，最终冻结规则随新快照保存。

按实际变更运行：离线序列化/策略/恢复幂等测试；相关迁移与历史数据升级；真实 PG 暂停回滚、同 Run/Message/ToolCall/快照/预算续接、重复提交与取消竞争、等待期限及 scope 过期；Event 生成漂移、前端 reducer/类型和刷新后补充的必要浏览器场景。原断言保留，竞态用屏障控制，不凭 sleep 猜测。

模型使用受控 Provider，必要检索使用已准备好的本地模型与 Milvus；不下载新模型、不默认调用收费模型，不自动扩大到 SuperMew 全仓门禁。代码、迁移或协议改变后运行对应检查；无变化且覆盖最终状态的结果可以按原记录复用。

### 计划调整

本次首次立章。实现中的关键方案若改变上述原子性、资料有效期或恢复承诺，先说明具体影响并更新计划；包内一般接线细节不重新索取开始反馈。

## 7.1 实施记录（2026-11-14）

正式 seam 为 Run/Message 数据库状态、私有 RunCheckpoint、冻结 ModelSnapshot 和 Event 真源。0015 增加 waiting_input/resume_pending、不可改写 Checkpoint/已接收恢复记录、凭据摘要及延迟事实约束；等待必须有对应 Checkpoint/提示事件/assistant 等待状态，恢复接收记录与事件也须同提交。沿用 Thread → Run 锁顺序和唯一终态入口。

新 Run v5 在既有 reasoning/repairs 上冻结 HitlPolicy（一轮一次、900 秒）；关闭 MINIMEW_HITL_ENABLED 只影响之后新 Run，旧 v1–v4 的摘要与行为保持。Event v1 按既有扩展规则增加 hitl.required、hitl.accepted、run.resumed 及两个稳定错误码；旧事件和终态 payload 不变，从真源生成 Python/TS，前后端须配套更新。

新增策略/历史摘要检查与 0014→0015 旧数据迁移检查；后者核对 Run、Message、模型快照、答案依据、Journal/Outbox 原样保留并执行 alembic check。检查实际通过，完整命令与包内去重统计在 A 包集中验收记录汇总；未新增生产依赖或修改上级项目。源码见 [模型](../src/minimew/models.py)、[快照](../src/minimew/model_control.py)、[策略](../src/minimew/hitl/policy.py)、[迁移](../migrations/versions/0015_hitl_checkpoints.py) 与 [契约](../contracts/run_event_v1.json)。

## 7.2 实施记录（2026-11-14）

暂停链为“RAG 结构化歧义 → interrupt → Run 私有 saver 导出 → 执行器捕获 → PG 原子发布”。BufferedSaver 实现已安装 LangGraph 的异步检查点接口；暂停之前只有 Run 私有短期缓冲，只有 PG 提交后的等待才是持久事实。保存最新 channel values 和 pending writes，白名单编码已知材料/评判/消息/集合/时间，不使用 pickle 或任意类导入；config 只保留 Run ID 对应的图身份键，排除框架 Runtime 等活对象。整个应用续接文档版本为 1、最多 8 MiB，超限明确失败而非截断。

只在健康 ambiguous 且有非空缺口时澄清，Provider 故障、普通无资料和空提示不触发等待。补充后按原工具查询与补充联合检索，将原问题与补充交给材料评判；过长检索式明确 KNOWLEDGE_QUERY_TOO_LONG。补充也随原工具结果交给外层回答模型，不新增业务 User Message。无论首次还是聚合后暂停，均恢复原图，Planner/Rewrite 不重置，最终 Evidence 仍由原知识工具一次提交。

续接信封保存原模型内部消息、待完成 assistant ToolCall、已经完成的兄弟工具结果、Evidence、原 scope 和全局预算。恢复跳过原模型选工具及已完成工具，不再次扣同一 ToolCall 的次数或追加 tool.started；重新校验当前服务端 Guardrail 后继续待完成调用。用户等待不消耗活动执行时间，恢复时只重建剩余额度对应的 deadline；原 scope 的有效期仍是硬上限。

后端接收接口与调度一起完成，避免另外建立恢复执行链：POST 原 Run 下的 resume-credential 经认证轮换凭据；POST resume 校验用户、Checkpoint、期限、摘要与输入幂等，返回 202 表示已接收。相同幂等键/输入复用接收结果，冲突拒绝；明文凭据不进入 Checkpoint、Journal 或公开 Run 字段。接收事务写 hitl.accepted、恢复记录、原 assistant pending 及 resume_pending；Dispatcher 领取新 owner 后写 run.resumed 并续接。等待或待恢复可立即取消，运行中的恢复沿用既有取消监控；过期等待由既有扫描入口写 HITL_EXPIRED。

审查补充前向迁移 0016：0015 的恢复记录 CHECK 对 nullable 字段存在 SQL NULL 放行缺口；新增显式非空条件，不重写已执行 0015。新增两项受控原始 SQL 回归，分别验证 NULL input_hash/answer 被 ck_checkpoint_receipt 拒绝。此前正常记录/流程继续有效。

实际验证包含：纯 JSON 跨 saver/graph 复原、非白名单拒绝、初次及 Planner/Send 聚合后暂停、未提前登记 Evidence；真实 PG 原子回滚、同 Run/Message/ToolCall/快照/预算、重启执行器、轮换凭据/越权/重复输入、屏障控制的重复恢复与恢复/取消竞争、旧 owner 拒写、Thread 队列、等待与接收后过期、scope 时间不足、控制面变更不改旧任务、数据库缺检查点拒绝及不可改写保护。最终本模块 14 项 PG 行为 + 1 项迁移通过；既有模块与包内去重数字在集中验收列明。

源码：[检查点编码与缓冲](../src/minimew/hitl/checkpoint.py)、[原子发布/接收](../src/minimew/hitl/store.py)、[API](../src/minimew/api/runs.py)、[执行器](../src/minimew/execution/service.py)、[领取/取消](../src/minimew/execution/repository.py)、[图](../src/minimew/rag/workflow.py)、[知识工具](../src/minimew/knowledge.py)。持久等待由应用拥有，图不直接写 Run；API 接收不在 HTTP 请求里执行模型。当前支持已提交等待/待恢复状态的续接，未知外部调用结果仍不自动重放；B 包心跳/更复杂故障边界未开始。

## 7.3 实施记录（2026-11-14）

前端等待与恢复沿用原 Event reducer/Pinia/ChatPanel。hitl.required 投影提示和截止时间，hitl.accepted 进入待恢复，run.resumed 回到运行；等待不投影为完成。刷新后的消息历史和 Journal 重放仍能重建表单。补充输入和忙碌/失败状态由当前页面管理，一次性凭据及重试意图仅在 store 闭包保留，不写 sessionStorage、公开 projection 或日志；提交结果不确定时复用原正文/凭据/幂等键，拒绝后查询同一任务。取消提示仍优先于等待，成功提交不能提前宣布完成。

新增 3 项 reducer 行为检查、1 项 store 刷新/不确定提交检查；更新原精确初始投影断言，增加 waiting=null，没有删弱已有断言。浏览器新增 390/1280 两场景，覆盖刷新等待、补充后续接、没有新 Run/业务 Message，并检查截图。AnswerBody 正确说明等待补充，避免把等待显示成没有正文的已结束回答。

真实本地链由 scripts/check_hitl.py 和受控 API 子进程验收：正式文档上传/独立 Index Worker 发布 → 第一 API 真实检索后暂停 → 第一进程完全退出 → 第二 API 从 PG 读回等待/凭据并恢复 → 原 assistant/ToolCall 完成、原文引用仍指向原 Document Version。两个进程分别实际记录 answer/grade 和 grade/answer，共 4 次模型尝试；9 条 Journal、1 条引用，自有 scope 精确删除与临时库清理。模型回复/歧义判断受控，Embedding/Base/Milvus/PG/HTTP 和进程边界真实；不当作 DeepSeek 澄清准确率或语义质量证明。

### A 包当前代码职责

| 模块 | 当前职责与上下游 | 状态归属 |
| --- | --- | --- |
| hitl/policy.py、model_control.py | 创建工作时冻结 v5 一次澄清/900 秒；旧版本保持 | Run 模型快照 |
| rag/workflow.py | 健康歧义路由、无副作用 interrupt 节点、补充后检索/评判；只导出缓冲 | 原知识工具内部图 |
| hitl/checkpoint.py | 白名单 JSON/版本校验、异步 saver、最新检查点及 pending writes | 当前 Run 的临时缓冲；提交后 PG 为事实 |
| hitl/store.py | 原子暂停发布、凭据摘要、恢复幂等接收、等待过期 | 同一 PG 事务中的 Run/Message/Checkpoint/Journal |
| execution/service.py、repository.py | 外层续接、原工具结果/预算恢复、新 owner 领取、取消与唯一终态 | 既有正式 Run 执行器 |
| api/runs.py | 原 Run 的认证子资源入口，只接收/查询，不在 HTTP 中跑恢复模型 | 复用身份与 Thread 所有权 |
| frontend/src/events、stores/chat.ts、components | Journal 投影、页面补充表单、不确定输入重试与历史刷新 | 服务端事实的页面投影，不另存执行状态 |

没有引入新服务进程类型、ProviderLoopBridge、依赖包或平行聊天 route；图和外层执行的衔接保留在原正式入口。独立 Index Worker 与 API 的既有进程职责不变。完整第七章框架/总结仍待 B 结束后补齐。

### A 包集中验收（2026-11-14）

本包修改持久状态、事务、认证恢复和公开 Event，故覆盖相关真实 PG、并发屏障、迁移与前端消费者；未将整库测试当作每小节固定流程。按 node ID 汇总，**322 个不同 pytest 实际通过（24 新、298 既有，含 112 个不同 PG）**；分节提交、测试复跑或收集统计不增加通过数。另有 4 项历史 HTTP/Redis/独立进程场景在离线命令中收集后跳过，未计通过；本次没有另外运行这 4 项，恢复 HTTP/进程边界由新真实链及相关 PG/调度检查覆盖，不能将其标成已运行。

实际运行记录：

- 离线主批次：`uv run --no-sync pytest -q tests/test_hitl.py tests/test_migrations.py tests/test_model_control.py tests/test_execution.py tests/test_execution_retry.py tests/test_model_messages.py tests/test_agent.py tests/test_reasoning.py tests/test_reasoning_graph.py tests/test_answer_repairs.py tests/test_knowledge.py tests/test_evidence.py tests/test_cancellation.py tests/test_cancellation_http.py tests/test_execution_diagnostics.py`：当时 208 passed/4 skipped。工具结果补充字段与联合检索接线后，对 hitl/reasoning_graph/knowledge/agent/answer_repairs 的 79 项定向复验通过；再补空白提示与并行聚合后暂停，并将策略测试独立分文件，最终 `tests/test_hitl.py tests/test_hitl_policy.py` 9 项通过。离线有效覆盖合计 210，复验不相加。
- PG 第一批：`uv run --env-file .env --no-sync python scripts/check_runtime.py tests/test_hitl_pg.py tests/test_hitl_migration.py tests/test_execution_pg.py tests/test_events_pg.py tests/test_runtime_pg.py`：当时 65 passed。
- PG 第二批：同入口运行 `tests/test_hitl_pg.py tests/test_hitl_migration.py tests/test_agent_pg.py tests/test_reasoning_pg.py tests/test_repair_pg.py tests/test_cancellation_pg.py`：57 passed，包含新增数据库缺检查点拒绝和最终预算/补充传递断言。
- 0016 收紧 NULL 约束后，仅复验受影响的 `tests/test_hitl_pg.py tests/test_hitl_migration.py`：15 passed（14 行为/1 迁移）。三批去重共 112 个 PG，有效结果按修改影响复用，没有把重复项相加。
- `uv run --env-file .env --no-sync python scripts/check_hitl.py`：两个独立 API 进程的真实 CPU/Embedding/Base/Milvus/PG 链通过，收费 0。该真实模型链当时 schema 为 0015；0016 仅补 SQL 非空约束，正常恢复结构已用上述最新 PG 全链复验，不为此重复加载未改变的模型/向量路径。报告最终只有全部断言、子进程退出和资源清理完成后才标 passed。
- `uv run --no-sync ruff check src tests scripts migrations`、`ruff format --check src tests scripts migrations`、Event 生成器 `--check`、正式 OpenAPI 导出与 `npm run contracts` 同步检查通过；没有手改生成物。新增迁移为单头 0016，升级与 alembic check 通过，设备库升级/运行快照只在设备文档记录。
- 前端 `npm run typecheck`、`npm run test`：154 passed（4 新、150 既有）；`npm run build` 通过。定向 Prettier 检查本包全部文件通过；全量 `npm run format:check` 有 39 个未改文件的既有告警，未计通过、未批量格式化。
- `MINIMEW_TEST_BROWSER_CHANNEL=msedge` 下 `npm run test:e2e -- tests/hitl.spec.ts`：最终 2 passed；再次执行因补充等待正文提示与修正测试 Thread 列表接线，原结果不相加。屏幕尺寸与截图仅在设备/临时产物保存。

开发中发生并修正：asyncpg 不接受一次 execute 多条独立 DDL；旧 FakeClaim 没有新恢复字段；新增测试曾误用 facts 返回结构与过短的等待窗口。真实模型链先被 Worker 的旧 head 检查和验收脚本启动阻塞拦截；前者随正式 head 同步，后者将 Windows stdin 控制线程延后到依赖初始化结束，并让本地测试 HTTP 不继承代理、模型仅离线读取。失败运行不计通过，不删除旧断言或放宽生产 15 分钟/模型预算。

未运行：收费 DeepSeek 的歧义判别/澄清质量、整个 MiniMew 全量业务或上级 SuperMew 门禁、B 包心跳/任意运行中节点恢复及外部副作用重放；范围分别属于真实质量评测、未受本包影响模块或尚未授权的后续工作。第六章固定案例/观察 fingerprint 和旧快照不改，不借此重做质量 baseline。新 Event 与 API 消费者、迁移、README 和模块指南已同步。

## 结章文字总结（完成后填写）

待本章结束后填写实际实现、概念取舍、完整流程、验证与限制，以及与评测章节的衔接。A 包实施与验证见上方记录；本节待 B 包结束后按整章实际结果填写，不把 A 包通过当作整章结章。

## 本章结束时的代码框架（完成后填写）

待实现后按真实文件梳理职责、调用关系、数据归属和代表性恢复流程。当前源码导航仅用于定位待扩展的正式 seam：

- [执行器](../src/minimew/execution/service.py)、[Repository](../src/minimew/execution/repository.py)、[Dispatcher](../src/minimew/execution/dispatcher.py)。
- [RAG 图](../src/minimew/rag/workflow.py)、[评判与冻结策略](../src/minimew/rag/reasoning.py)。
- [Run API](../src/minimew/api/runs.py)、[数据库模型](../src/minimew/models.py)、[Event 真源](../contracts/run_event_v1.json)。

A 包已在上述正式入口扩展持久暂停/续接，当前职责见实施记录；完整结章框架与结构整理在 B 包结束后记录。
