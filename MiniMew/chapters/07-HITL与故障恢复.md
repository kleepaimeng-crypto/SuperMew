# 第 7 章：HITL 与故障恢复

当前学习进度、真实反馈和编码授权只见 [交接记录](../AGENT_HANDOFF.md#当前检查点)。开章计划保留设计口径，实际实现与验证见下方实施记录；本章总结和实际源码框架见文末，开章/B 包待开始文字保留为历史。

## 开章计划

计划日期：2026-11-14。

### 本章目标与范围

让知识问答在确实需要用户澄清时，持久暂停原 Run；刷新页面或重启进程后，用户仍可补充信息，继续原工具调用并完成原 assistant Message。保留原身份、模型快照、资料版本、Evidence 身份和已消耗预算。

本章承接既有 Agent 外层工具循环、knowledge_search 内部 StateGraph、PostgreSQL Run/Event 和前端事件投影。立章时图只有进程内状态；A 包之后的持久暂停/续接见实施记录。Dispatcher 对运行中失联任务的过期回收仍是终结，不是任意节点恢复。

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

## B 包具体范围与架构走读（2026-11-14，待明确开始）

本轮用户在 A 包完成后回复“继续”，据当前源码整理 7.4–7.6 的具体方案，不追认 B 包编码授权。A 包的最后提交为 95d1435；暂停、恢复接收、原消息与工具调用续接已经落地，本包只围绕剩余租约协议、故障验证及结章推进，不重做 A 包。

### 为什么还需要 B 包

当前 ExecutionRepository 领取后固定授予 330 秒租约，没有运行期续租；执行器的模型计算期限与这段租约取较小值，再用本机 monotonic clock 控制等待。Dispatcher 约每 5 秒扫描过期运行，写既有失败/取消终态；它没有从任意运行中节点重放任务。数据库 guard 还将 running 中的 owner_token、lease_until、started_at 一并视为不可改写，不能只加一个 UPDATE 心跳就声称完成续租。

短租约能较快发现失联，但健康任务可能运行得比单次租约久。因此需要把两个职责分清：

| 机制 | 回答的问题 | 本包中的归属 |
| --- | --- | --- |
| owner_token 与条件写入 | 此刻哪一个执行者可以提交这个 Run 的事实 | PostgreSQL Run 行；Claim 只是携带身份，不是事实副本 |
| lease 与 heartbeat | 执行者是否仍及时证明自己的资格 | 每个运行中 Run 的监控协程与短 PG 事务 |
| 活动执行 deadline | 这段执行还允许计算多久 | 拟持久化到 Run；从冻结配置/原 Checkpoint 剩余预算取得 |
| Checkpoint | 从哪个明确持久边界可以继续 | A 包的 RunCheckpoint 与原子发布事务 |
| Dispatcher | 谁领取可执行任务、谁检查失联任务 | 既有 API 进程内调度模块，不新增一种 Worker 进程 |

owner_token 是服务端的执行者身份，与用户登录 Access Token、用户提交的一次性 HITL 凭据各自独立。这里继续使用现有 UUID owner_token 对 PG 写入作相等条件隔离；它不是交给任意外部系统使用的递增 fencing token，也不能撤回已经发出的网络请求。未来若接写操作工具，还需要目标系统参与的幂等/隔离协议，本包不提前实现。

### 7.4：短租约、心跳与活动时间

建议默认每 5 秒续租，单次租约 20 秒；保留当前取消轮询和约 5 秒的回收扫描。数值是本项目建议配置，不表示机器、网络或启动期间有固定恢复 SLA。模型总期限仍由原冻结 Profile 与 Checkpoint 剩余活动时间决定，不因续租、进程更换或恢复而重发额度。

在开始 Provider/检索调用前，显式持久登记本段绝对活动截止时间（拟为 execution_deadline_at），以 PG 时钟计算；本机 monotonic deadline 仅用于取消等待，并采用保守的时长换算。领取后的短准备租约与活动预算激活要有明确协议，准备未完成不得发出外部调用；测试替身同步正式 Repository 协议，不用生产 fallback 绕过这一步。

还要给终结事务保留独立的有界窗口：建议最多 10 秒，只允许清理与保存终态，不允许继续调用模型、工具或格式纠正。若租约与计算 deadline 同时到期，正常的模型超时可能已经失去保存失败结果的资格，因此不能把两者简单设为同一时刻。活动阶段续租上限拟为 `min(PG当前时间 + 20秒, 本段活动deadline + 10秒)`；活动时间耗尽后不能靠心跳无限延长运行。

必须同时修改领取、时间剩余查询、执行监控、条件提交与数据库 guard：同一执行段 owner/原 started_at 不改，只允许尚未过期、未取消的当前 owner 按协议续租；过期 owner 不能自我复活。Claim 中领取时的 lease_until 会在正常续租后落后，提交资格必须读取当前 PG 行，不能继续把旧 Claim 时间当作权威期限。

心跳与取消监控归当前 Run 持有。暂停前停止并收集监控任务，再按 A 包事务释放 owner/lease；waiting_input 不续租，其等待期限仍由原 Checkpoint 管理。恢复时取原剩余预算登记新的本段 deadline，保持原 scope、引用及 4/12/6/8 次数限制。DB 不可用时停止新的外部调用并尝试保存已知失败；不能把“无法确认资格”解释成健康或偷偷续用本地资格。

预计需要新的前向迁移，保留已执行 0015/0016。旧终态历史不补造 deadline，既有 waiting_input/resume_pending 保持可续接；迁移前对运行中的旧协议任务做显式检查，正常排空后升级，不从未知进度猜剩余预算，也不擅自取消未获相应授权的任务。当前方案不改变 ModelSnapshot v1–v5、Checkpoint 文档 v1 或公开 Event/API 语义；若实现中发现确有契约变更需求，再说明具体影响并同步真源。

### 运行中崩溃与等待中重启的区别

A 包只在暂停时提交图与外层预算快照。用户补充后的执行一旦开始，后续模型尝试、检索和工具进度仍在当前执行上下文中；原暂停 Checkpoint 不是这些新进度的持续日志。直接回到它会把恢复后已消耗的额度退回，还可能重复未知结果的外部请求。因此本包采用明确的分界：

| 持久状态/故障位置 | B 包拟保证的行为 | 为什么 |
| --- | --- | --- |
| waiting_input 已提交，API 退出 | 保留原等待，重启后继续读提示并接收补充 | 完整等待事实与 Checkpoint 已同提交 |
| resume_pending 已接收、尚未领取 | Dispatcher 继续领取原 Run，只接收一次补充 | 尚未获执行资格，不允许提前调用 Provider |
| running 中进程失联，包括已经开始恢复 | 等资格过期后写 EXECUTION_EXPIRED；已有取消则取消获胜，不自动退回旧 Checkpoint | 无法证明外部调用结果及恢复后的剩余额度 |
| 暂停/接收/终结事务提交回包丢失 | 以 PG 实际状态、幂等记录和 Journal 判定；不能另建 Run 或盲目重跑工具 | 客户端没收到响应不等于事务没有提交 |
| 失权的旧进程又返回结果 | 所有增量、工具观测、Checkpoint 和终态写入被资格条件拒绝 | Python 协程是否还活着不决定提交权 |

这仍然是“同一正式执行链上的受支持边界恢复”。本包不增加自动从 running 抢占/重放、逐模型调用的持久工作流日志、任意外部副作用 exactly-once 或跨天资料保留。若以后要求运行中任意位置无损续跑，必须另列外部调用账本、预算持久扣减与重放协议范围，不能通过重置为 resume_pending 偷换实现。

### 7.5：按确定的交错顺序验证

新增检查围绕实际改变的资格协议，用同步屏障或进程握手控制顺序，不用固定 sleep 猜谁先抢到锁：

- 健康任务跨越首个 20 秒租期后仍能继续；心跳没有重置原活动 deadline、模型/工具次数和 HITL 等待期限。
- 心跳与取消、过期扫描、终结/暂停交错：同一个 Run 只有一个合法结果；暂停后没有残留心跳重新占有 Run。
- 旧 owner 被阻塞后恢复：增量、工具观测、暂停发布和终态均拒写；另一 Thread 的运行不共享其监控、预算或 Evidence。
- 两个 API 调度器同时观察同一任务，只能有一个当前 owner。恢复接收后进程退出但尚未领取，可由新进程续接；一旦进入未知结果的 running，则只终结、不回滚额度再执行。
- 暂停/恢复接收/终结提交回包不确定时，分别验证实际已提交和实际回滚的情况，最终事实来自原事务；重复提交与恢复/取消不重复执行。
- PG/通知短暂故障、拒绝配合取消的受控 Provider、资源关闭和活动时间耗尽不产生静默成功或孤儿监控任务。通知故障不改变 PG 事实。
- 原 v1–v5 快照/已有终态和暂停记录的迁移保持；直接 SQL 不能绕过新 owner/lease/deadline 约束。

### 7.6：包末集成与结章交付

先跑新增租约/执行监控定向单元与真实 PG 迁移、资格/取消/事务检查，再用两个独立进程验证可恢复边界与运行中崩溃终结。A 包已经覆盖且条件未变的 RAG/界面结果按记录复用；本包改变的是执行资格、取消和终态路径，因此补相应 HTTP/PG/进程检查，并安排 A 包尚未运行的 4 项历史 HTTP/Redis/独立进程场景。全局回收计数用隔离临时库验证，不改旧断言来掩盖其他测试过期 Run 的干扰。

模型回答继续受控，收费 0；必要的真实检索使用既有本地 Embedding/Base/Milvus，不下载模型或新增任务框架。前端、契约若未变，不因切分 7.4/7.5/7.6 重跑全部浏览器；结章验证用真实后端进程链与已有效覆盖的 UI 结果共同说明完整行为。若发现消费者行为受影响，再扩大对应检查，不能省略未覆盖的部分。

交付更新 README 的重启/迁移操作与恢复边界、章节中文总结和基于最终源码的模块/进程框架；运行及环境快照仍只写 devices/。真实理解/授权/当前进度只在 AGENT_HANDOFF.md，不预填本包通过。小节完成后按持续授权分别提交并推送个人 main，不重跑仅因切分 Git 产生的整套验收。

本轮只读源码与修改学习文档，没有应用/依赖/配置/迁移/测试代码变更，没有运行新的业务验收或提交推送；A 包既有结果不计作本轮新通过。待用户明确开始上述 B 包后连续实施。

## 7.4 实施记录（2026-11-14）

用户明确“开始第七章 B 包”，本次覆盖已列明 7.4–7.6；上述 B 方案是授权前记录。正式 seam 是 ExecutionRepository 的领取/激活/续租/写入资格、RunExecutor 的私有监控和 PG guard，沿用原 Dispatcher，无新 Worker 类型或任务框架。

Run 新增 nullable `execution_deadline_at`。claim 只给准备租约；request 与恢复状态校验完成后，activate 在外部调用前登记本段绝对期限，不能超过冻结 Profile/原 Checkpoint 剩余预算，重复调用只读原期限。PG 时钟决定资格；转换到本机 monotonic 时先记查询起点，往返开销不额外发放时间。默认心跳 5 秒、租约 20 秒，配置要求心跳小于租期。心跳事务持 Thread→Run 锁并校验同一 owner、未取消、租约尚有效；deadline/已用次数/原 started_at 不变。

续租上限为 `min(PG now+lease, activity_deadline+10秒)`，不缩短已续租期。活动时间耗尽不续租；正文、工具观测、Checkpoint 仍须活动期限有效。终态事务只在当前租约有效时收尾，允许最多 10 秒清理/保存；不是给模型额外计算时间。Claim 的初始 lease 不再决定写入资格。暂停前收集监控任务，再同事务清空 owner/lease/本段 deadline；恢复只用已保存余额登记新段，4/12/6/8 计数、scope、Evidence、原 Run/Message/ToolCall 均保留。

[0017](../migrations/versions/0017_active_execution_leases.py) 显式锁表并拒绝旧 running，先用旧协议排空；不会从未知外部进度猜预算。终态历史新列为 NULL，原 pending/waiting_input/resume_pending、消息、快照、Journal、Outbox、答案依据与接收记录保留。数据库同时拒绝 owner 重绑、deadline 延后、过期复活、非法租期及无活动资格的写入。未知 running 崩溃仍只终结；waiting_input 与未领取 resume_pending 才能继续。0015/0016 历史迁移不改。

新增 [离线租约检查](../tests/test_leases.py)、[真实 PG 协议检查](../tests/test_leases_pg.py) 和 [迁移保真/拒绝检查](../tests/test_leases_migration.py)。旧手动领取 fixture 显式激活后再验证原行为；过期测试显式查 PG lease，避免将活动预算冒充租约。历史 schema 使用[测试专用 SQL 构造器](../tests/historical_runs.py)，不让最新 ORM 伪装旧字段，也不增加生产兼容路径。原 0009 全状态迁移案例包含 running，因此保留到 0016 的原字段/快照/前缀/Outbox 断言，再验证 0017 拒绝且事务回滚；成功升级与结构无漂移由新迁移案例覆盖。未删弱原业务断言。

实际新离线用例 5 项、新 PG 协议 14 项、迁移 2 项均通过；执行/事件/答案 53 项、取消/Agent/Dispatcher 接线 46 项、SSE/读取/Outbox 18 项相关 PG 回归通过。218 项离线集成范围与其他包末结果在 7.6 按节点去重记录；复跑不累加。源码格式/静态检查与 Event 生成漂移通过，OpenAPI 语义相同，ModelSnapshot v1–v5/Checkpoint v1/公开 Event 未改，无新依赖或质量资产重算。提交拆分不触发重复回归。

## 7.5 实施与故障验证（2026-11-14）

故障验证落在同一正式 Repository/Executor/API 上。7.4 的 PG 用例控制激活、续租、活动截止、取消及终态保存顺序；本小节补充 [HITL 事务不确定性](../tests/test_hitl_pg.py)、[独立进程握手](../tests/lease_process.py) 与 [HTTP 崩溃/迟到保存](../tests/test_leases_http.py)，没有新增生产测试 route、内存事实镜像或自动重放入口。

| 故障/交错 | 实际裁决与验证 |
| --- | --- |
| 两个 claimant 同时领取 | 同一 Run 只一个 owner，另一方不执行；错误 owner 写入拒绝 |
| 健康任务超过最初短租期 | 心跳延长提交资格，活动 deadline 不变、模型仅调用一次，监控最终清理 |
| 心跳与取消竞争 | Thread→Run 锁和最终条件决定顺序；取消持久后不能再续租，唯一取消终态 |
| 活动到期但仍有提交租期 | 新 delta/工具观测拒绝，心跳不延长；允许原终态事务有界收尾 |
| 心跳故障，含已提交但回包丢失 | 停止 Provider，保存受控 EXECUTION_STORAGE_UNAVAILABLE；不按本地资格继续 |
| 激活已提交但回包丢失 | Provider 未启动；Run 保留已登记期限，失联后仅过期终结，不重领/退预算 |
| 暂停事务回滚/已提交但回包丢失 | 前者没有残留 Checkpoint/等待事件；后者 PG 保持等待，不能再次领取运行 |
| 接收事务回滚/已提交但 HTTP 报错 | 原凭据/幂等输入可重试；分别仍 waiting_input 或已 resume_pending，最终只有一次 accepted |
| 终态已提交但回包丢失 | 再次执行只读到不可领取，原 Message/唯一 completed 保持，没有第二次模型请求 |
| API 被硬退出 | 真实心跳停止后租约到期，由第二 API 明确失败；原前缀和消息保留，下一排队 Run 正常执行 |
| Provider 已完成、旧 API 迟到保存 | 第一次保存前握手阻塞；第二 API 过期终结后释放旧事务，旧结果拒写，不覆盖前缀/终态 |

竞争由 asyncio.Barrier 或独立进程 ready/finish/release 文件控制；等待到业务截止时仍查询真实 PG 时钟，不把固定 sleep 当作并发证明。测试子进程握手包含 nonce、实际 server PID 和随机端口，Windows venv launcher 与真实进程 PID 区分，硬退出和清理只作用于自有进程；未终止其他项目进程。

`uv run --env-file .env --no-sync python scripts/check_runtime.py tests/test_hitl_pg.py tests/test_leases_pg.py` 最终 31 passed（17 HITL、14 租约；7.4 已覆盖部分复用，不相加）。`uv run --env-file .env --no-sync python scripts/check_runtime.py tests/test_leases_http.py tests/test_cancellation_http.py` 为 6 passed，其中后者是 A 包曾收集跳过的 4 项旧 HTTP/Redis/独立进程检查，此次实际执行通过；它们是既有用例，不能计作新增。通知故障、跨实例取消、拒绝配合取消的受控 Provider 和进程退出沿用这些检查，无收费模型。

原恢复/取消/快照冻结/越权/过期/同 Message/ToolCall 的 14 项 HITL 断言全部保留，新增 pause_ack/accept_ack/accept_rollback 三项参数。7.4 的激活/心跳/终态回包丢失与此处的暂停/接收共同覆盖不确定提交；事务事实仍以 PG 为准，不要求 Python 返回值和事务回包永不丢失。

## 7.6 包末集成与集中验收（2026-11-14）

[真实链验收](../scripts/check_hitl.py) 与 [API 子进程](../scripts/hitl_process.py) 扩展到三个独立 OS 进程：正式上传/独立 Index Worker 构建并发布 → API 1 真实检索后暂停并退出 → API 2 认证接收补充、没有 Provider 因而保持 resume_pending，再退出 → API 3 重新加载本地模型、领取原 Run 并完成。PG、HTTP、CPU Embedding/Base、Milvus 及进程切换真实，回答/评判模型受控；实际调用分布为 `[answer,grade] / [] / [grade,answer]`，共 4 次，9 个事件、1 条引用，原 assistant ID/Document Version 保持。收费 0，1 个自有向量 scope 与临时库清理；报告只在所有断言和进程退出/清理完成后标 passed。

B 包以最终用例 node ID 去重：**391 项通过 = 26 新 + 365 既有；218 离线 + 173 真实 PG（含 HTTP/Redis/独立进程），0 跳过**。新用例是 leases.py 5、leases_pg.py 14、leases_migration.py 2、leases_http.py 2、hitl_pg.py 新模式 3。更新的旧 fixture/断言口径属于修改，不计为新增；多次执行同一节点不叠加。A 旧浏览器/前端结果不计入这 391。

实际通过的命令范围如下，均从 MiniMew 根目录运行。PG 命令使用专属临时库，不消费开发聊天或资料；因统一 owner/期限协议改变，覆盖所有手动 claim 的消费者及正式执行链，不扩展到上级 SuperMew：

```powershell
uv run --no-sync pytest -q tests/test_leases.py tests/test_execution.py tests/test_execution_retry.py tests/test_model_messages.py tests/test_agent.py tests/test_reasoning.py tests/test_reasoning_graph.py tests/test_answer_repairs.py tests/test_knowledge.py tests/test_evidence.py tests/test_cancellation.py tests/test_execution_diagnostics.py tests/test_dispatcher.py tests/test_hitl.py tests/test_hitl_policy.py tests/test_model_control.py tests/test_migrations.py
# 218 passed
uv run --env-file .env --no-sync python scripts/check_runtime.py tests/test_execution_pg.py tests/test_events_pg.py tests/test_answers_pg.py
# 53 passed
uv run --env-file .env --no-sync python scripts/check_runtime.py tests/test_cancellation_pg.py tests/test_agent_pg.py tests/test_runtime_pg.py
# 46 passed
uv run --env-file .env --no-sync python scripts/check_runtime.py tests/test_stream_reads_pg.py tests/test_sse_pg.py tests/test_outbox_pg.py
# 18 passed
uv run --env-file .env --no-sync python scripts/check_runtime.py tests/test_hitl_pg.py tests/test_leases_pg.py
# 31 passed
uv run --env-file .env --no-sync python scripts/check_runtime.py tests/test_leases_http.py tests/test_cancellation_http.py
# 6 passed
uv run --env-file .env --no-sync python scripts/check_runtime.py tests/test_answers_migration.py tests/test_document_migration.py tests/test_hitl_migration.py tests/test_tool_observation_migration.py
# 4 passed
uv run --env-file .env --no-sync python scripts/check_runtime.py tests/test_reasoning_pg.py tests/test_repair_pg.py tests/test_tool_observations_pg.py tests/test_leases_migration.py tests/test_cancellation_migration.py tests/test_indexing_migration.py
# 首次 14 passed / 1 failed；下述单项复验后，此范围 15 个不同节点有效通过
uv run --env-file .env --no-sync python scripts/check_runtime.py tests/test_cancellation_migration.py
# 1 passed，不与上一命令重复累计
uv run --env-file .env --no-sync python scripts/check_hitl.py
# passed=true，3 API processes / 4 controlled calls / 9 events / 1 citation
uv run --no-sync ruff check src tests migrations scripts
uv run --no-sync ruff format --check src tests migrations scripts
uv run --no-sync python scripts/generate_event_contract.py --check
uv run --env-file .env --no-sync alembic upgrade head
uv run --env-file .env --no-sync alembic check
```

开发期失败和复验原因如实保留：旧手动 claim 的 fixture 未激活活动预算，最初被新写入 guard 拒绝；将 fixture 接上正式 activate 协议后，原业务断言复验通过。部分过期案例原用 remaining 等租期，现改为 PG lease 查询，活动预算另测，短准备租期直接过期的案例不误做激活。组合回收测试曾受其他遗留过期 Run 计数影响，改在自有新库复验，未放宽断言。旧 schema fixture 使用最新 ORM 时读取了不存在的新列，改为历史 SQL 构造器。原 0009 全状态 fixture 含 running，被 0017 预检正确拒绝；先断言 0016 全字段保真，再增加拒绝升级/DDL 回滚检查；只复跑受影响单项，未把旧状态自动终结或改迁移以迁就测试。离线批量修改辅助脚本曾因 Windows 默认 GBK 读取 UTF-8 失败，显式 UTF-8 后修正；失败不计通过。

Event 真源/生成消费类型通过漂移检查；从正式路由构造 OpenAPI 并与已生成 JSON 作语义比较，完全一致，无手工修改生成物。B 无新增公开协议、ModelSnapshot/Checkpoint 版本、依赖或 RAG 评测资产变化。迁移、Worker/数据库检查入口的 head 校验、README 的排空/升级/重启命令和 `.env.example` 的 20/5 秒默认项已同步；不覆盖 `.env`。

本包没有修改前端源码、API/Event 形状、浏览器依赖或 UI 配置，因此复用 [A 包集中验收](#a-包集中验收2026-11-14) 的 154 前端单元、类型/构建及 2 项补充/刷新浏览器结果，不声称本轮重跑。执行资格与终态改变的部分由本轮真实后端 HTTP/PG/进程链覆盖。A 全量格式检查的 39 个未改文件告警仍作为既有未通过项记录，没有批量格式化或凑出全绿。

未执行：收费 DeepSeek 的歧义判别/澄清质量、全量 MiniMew/上级 SuperMew 门禁、运行中任意节点无损重放/写操作工具严格一次、长时间高并发/恢复 SLA 压测。前两项分别属于后续质量评测和未受影响范围，其余超出本章承诺。真实进程/短租期验证不能证明任意外部网络调用的执行次数或生产吞吐。设备初始化、模型缓存、历史数据保真和服务运行快照仅保存在 [本机设备记录](../devices/windows-desktop.md)，其他设备须重新核对。

## 结章文字总结

总结日期：2026-11-14，覆盖 7.1–7.6 的持久澄清、原 Run 续接、短租约资格与故障验证。学习进度、真实理解反馈和后续授权只见 [当前交接](../AGENT_HANDOFF.md#当前检查点)。

### 本章实现了什么

用户现在可以在知识问答遇到真实可澄清缺口时补充信息，而不是再发送原问题建立另一次问答。等待已经提交后，刷新页面或退出 API 仍能读到原提示；一次性凭据经认证轮换，补充幂等接收后回到原执行链。等待不占实例的活跃执行槽，但保持 Thread FIFO；原 Run、两条业务 Message、待完成 ToolCall、冻结快照和资料范围都不更换。

健康执行通过短租约续租，失联执行更快具备回收条件；计算期限保持独立，不能因为服务还活着就无限工作。活动阶段结束后的有限租期只容许保存终态。数据最终来自同一 PG 事务，SSE、浏览器状态或进程内返回值均不拥有完成权。

### 关键概念与取舍

**图状态与业务事务分工**：LangGraph 知道下一步从澄清节点继续，但不知道业务 Message/Run/幂等凭据怎么更新。Run 私有 BufferedSaver 先保存白名单 JSON，HitlStore 再把完整恢复信封和业务等待同事务提交；仅安装一个通用 saver 并不能保证这些事实原子。外层 ToolCall、模型内部消息和额度必须随图一并保存，否则恢复会重新选工具或退还次数。

**恢复边界必须可证明**：waiting_input 是完整暂停点，resume_pending 表示补充已接收且尚未调用模型，可跨进程继续。running 后的未知外部调用不在持续持久账本内，失联后只失败/取消，不能回退旧 Checkpoint。UUID owner_token 负责 PG 条件隔离；若未来加入写操作工具，目标系统的幂等/隔离仍需单独设计。

**三个期限解决三个问题**：HITL 的等待期限受原 scope 生命周期约束；本段活动 deadline 决定计算余额；短 lease 决定当前提交资格。心跳不增加计算时间，等待不消耗活动时间，恢复不换版本或退次数。每 Run 最多一次澄清、等待最多 900 秒、原 scope 55 分钟/旧索引保留 1 小时，是当前资源策略的配套边界，不是跨天续跑能力。

**模块与进程不同**：API、Dispatcher、RunExecutor、RAG 图、HITL 存储是同一 API 进程内的模块/协程；Indexing Worker 是单独进程。PostgreSQL 保存权威事实，Milvus 保存检索索引，Redis 负责提醒。每 Run 的心跳是私有协程，不是新增部署服务；运行多个 API 时仍由 PG 的锁/条件决定谁领取。

### 业务流程与失败场景

以“培训餐费能报多少”开始：Run 创建时冻结 v5 策略和消息对 → Dispatcher claim/activate → 外层模型选择 knowledge_search → 已发布资料 scope 内检索/重排/评判 → 图发现国内/海外歧义并 interrupt → 执行器停止监控，将图、原 ToolCall、Evidence、剩余预算交给 HitlStore → PG 原子提交 Checkpoint、waiting_input、assistant 等待和 hitl.required。

用户补充“国内”时，API 验证身份/凭据/幂等键 → 原子保存接收记录、hitl.accepted 与 resume_pending → Dispatcher 领取新 owner，写 run.resumed → 执行器校验原 scope/快照并只激活原余额 → 重建原图/外层消息，以 Command(resume=...) 继续原工具 → 将最终 Evidence 一次交给外层 → 回答引用校验 → 原 assistant、答案依据、message.completed 与 run.completed 同提交。只有最后这个提交才是业务完成。

暂停/接收/终结事务失败时，未提交部分回滚；此前已提交的正文/事件保留。响应丢失时读 PG/使用原幂等输入确认，不新建 Run。取消由同一锁顺序和终态事务裁决；原 owner 失权后不能追加 delta/工具观测/Checkpoint 或结果。通知不可用仍可从 PG 补放，断开 SSE/浏览器不等于取消任务。

### 验证与限制

A 包负责新增状态、序列化、原子暂停、权限、幂等与浏览器交互；B 包负责新资格协议、事务不确定性、取消/超时/失联隔离和三进程真实检索链。具体命令、26 新/365 旧的 B 去重结果、A 结果复用条件与未执行范围见 7.6，不将受控模型称作真实质量证明。当前没有新增通用逐节点持久执行引擎，也没有把失联 running 自动重新排队。

### 与下一章的衔接

第八章将围绕 Dataset、Observation/Judge、冻结评测配置与可比较报告，把此前“固定案例可运行”推进到质量闭环；可复用正式知识工具/Run 预算/错误语义和持久恢复边界。评测数据、指标、执行进程和质量阈值仍需先讨论并列明确工作包；本章总结不自动创建 Evaluation Worker、数据库表或第八章应用代码。

## 本章结束时的代码框架

范围日期：2026-11-14，以下按真实源码说明，省略未改变的通用认证/文档构建细节；未来评测执行器不列为已实现。

### 业务目录与模块职责

| 目录或模块 | 做什么、为何存在 | 上下游与数据归属 | 源码入口 |
| --- | --- | --- | --- |
| API / conversations | 认证资源入口，创建/查询/取消/获取恢复凭据/接收补充；不在 HTTP 中长跑模型 | 接收用户输入，调用正式 Repository/HitlStore；业务 Thread/Message 属 PG | [runs.py](../src/minimew/api/runs.py)、[conversations.py](../src/minimew/conversations.py) |
| execution/dispatcher | 周期查候选、限制实例并发、扫描过期任务、有界退出 | PG 决定领取，调度执行器；只保存本实例活跃任务句柄 | [dispatcher.py](../src/minimew/execution/dispatcher.py) |
| execution/repository | Thread→Run 短事务，claim/activate/heartbeat/取消/条件写入/唯一终态 | 读取冻结快照/原消息，写 PG Run/Message/Journal/Outbox/答案依据；Claim 是凭证 | [repository.py](../src/minimew/execution/repository.py) |
| execution/service | 外层模型/工具循环、Run 私有 budget/context、取消心跳监控与最终回答 | 调 Provider/Registry/知识工具；收到 PauseRequested 交 HitlStore，最终结果交 Repository | [service.py](../src/minimew/execution/service.py)、[tools.py](../src/minimew/execution/tools.py) |
| model_control / settings | 冻结业务策略与校验运行参数分开 | v5 HitlPolicy 随 Run 冻结；本实例 20/5 秒运行设置不改历史快照 | [model_control.py](../src/minimew/model_control.py)、[settings.py](../src/minimew/settings.py) |
| knowledge / rag | 已发布 scope、检索/重排、内部评判/改写/规划与澄清路由 | 以同 Run context 调 Milvus/模型；图状态只归本次知识工具，不拥有业务终态 | [knowledge.py](../src/minimew/knowledge.py)、[workflow.py](../src/minimew/rag/workflow.py)、[reasoning.py](../src/minimew/rag/reasoning.py) |
| hitl/checkpoint | 白名单 JSON、8 MiB 上限、Run 私有 BufferedSaver，保存最新 channel 与 pending writes | 与 LangGraph 异步接口协作；内存只是暂停提交前缓冲，正式文档存 PG | [checkpoint.py](../src/minimew/hitl/checkpoint.py) |
| hitl/store / policy / schemas | 原子暂停/接收/等待过期、一次性摘要凭据、输入结构和冻结澄清规则 | 调 Repository 锁与终态入口；RunCheckpoint 文档/接收记录属 PG，恢复 Token 明文不保存 | [store.py](../src/minimew/hitl/store.py)、[policy.py](../src/minimew/hitl/policy.py)、[schemas.py](../src/minimew/hitl/schemas.py) |
| models / migrations | 持久结构、状态/所有权/预算写入约束与历史升级 | 同一 PG 事实，无旁路内存镜像；0015/16/17 前向演进 | [models.py](../src/minimew/models.py)、[0015](../migrations/versions/0015_hitl_checkpoints.py)、[0016](../migrations/versions/0016_resume_receipt_nulls.py)、[0017](../migrations/versions/0017_active_execution_leases.py) |
| execution/journal / outbox / streaming | 有序事件读回、可靠提醒、SSE 投影；提醒丢失仍查 Journal | 持久事实 PG，Redis 只提醒，订阅各自拥有游标/等待任务 | [journal.py](../src/minimew/execution/journal.py)、[outbox.py](../src/minimew/execution/outbox.py)、[streaming.py](../src/minimew/execution/streaming.py) |
| frontend events / store / components | 按事件显示 waiting/resume，刷新重建、提交补充与错误说明 | projection 为投影；一次性 Token/原提交意图仅页面闭包，不写浏览器存储 | [reducer](../frontend/src/events/runEventReducer.ts)、[chat store](../frontend/src/stores/chat.ts)、[ChatPanel](../frontend/src/components/ChatPanel.vue) |
| 验收资产 | PG 屏障、真实进程握手与受控模型的真实检索链 | 专属临时库/自有向量 scope，测试 SQL 历史 fixture 不进生产执行链 | [leases_pg](../tests/test_leases_pg.py)、[leases_http](../tests/test_leases_http.py)、[hitl_pg](../tests/test_hitl_pg.py)、[check_hitl](../scripts/check_hitl.py) |

### 代表性流程走读

```text
浏览器 API / SSE
    ↓ HTTP                ↑ Event Journal 重放
API runs → HitlStore.accept → PG: resume_pending / accepted receipt
    ↓
Dispatcher → Repository.claim / activate → RunExecutor + 本 Run 心跳监控
                                           ↓ 恢复原 ToolCall
                                    knowledge_search
                                           ↓
                         RAG graph + BufferedSaver + Command(resume)
                                           ↓
                               Milvus / Grader / Evidence
                                           ↓
                    原工具完成 → 外层答案/引用校验 → Repository.finish
                                           ↓
                      PG: 原 Message + 答案依据 + 唯一 terminal Event
```

图中 API 内所有模块可在同一 OS 进程；用户补充请求结束后，Dispatcher 独立调度。第三进程续接证明状态来自 PG，前两个进程闭包/内存均不需要存活。索引 Worker 只负责上传构建和发布，不领取聊天 Run；新增心跳没有改变这种进程职责。

### 本章发生的结构整理

新增 MiniMew 自身 hitl 模块承载编码、策略、输入和存储职责，避免将图保存/权限事务/浏览器恢复协议全部堆入执行器。B 在原 execution 模块内扩展正式协议，没有再建恢复 Dispatcher/第二条聊天 route。测试历史 schema 构造器独立到 tests/historical_runs.py，辅助进程与真实资源验收脚本留在 tests/scripts；未重写已执行迁移、上级生产目录或依赖锁文件。后续路径变化须同步本表和章节链接。
