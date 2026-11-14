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

## 结章文字总结（完成后填写）

待本章结束后填写实际实现、概念取舍、完整流程、验证与限制，以及与评测章节的衔接。当前没有本章应用变更或运行验收，不预填完成结论。

## 本章结束时的代码框架（完成后填写）

待实现后按真实文件梳理职责、调用关系、数据归属和代表性恢复流程。当前源码导航仅用于定位待扩展的正式 seam：

- [执行器](../src/minimew/execution/service.py)、[Repository](../src/minimew/execution/repository.py)、[Dispatcher](../src/minimew/execution/dispatcher.py)。
- [RAG 图](../src/minimew/rag/workflow.py)、[评判与冻结策略](../src/minimew/rag/reasoning.py)。
- [Run API](../src/minimew/api/runs.py)、[数据库模型](../src/minimew/models.py)、[Event 真源](../contracts/run_event_v1.json)。

这些入口目前不具备本章拟议的持久 HITL 链；最终框架与任何结构整理在实际完成后记录。
