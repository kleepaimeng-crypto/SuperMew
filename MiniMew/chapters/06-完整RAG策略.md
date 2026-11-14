# 第6章：完整RAG策略

学习进度、真实反馈和编码授权以[交接记录](../AGENT_HANDOFF.md#当前检查点)为准。本章首先记录计划与架构讨论，未实现内容不写成当前能力。

## 第六章结束后的必做补齐：异常反馈（2026-11-13，待执行）

用户明确要求：“等第6章结束后，补充完整的异常反馈，确保错误可以记录在日志以及前端显示。”本项在第六章结束后安排专项实现；本次仅保存待办与验收要求，不提前修改应用代码，不将其记为已完成。

背景：实际文档问答曾因空标题元数据触发`KNOWLEDGE_RESULT_INVALID`。失败状态、错误码和Journal已保存，但后端捕获typed异常后未输出足够诊断日志，前端仅展示“未完成/回答未完成”，用户无法理解原因。持久失败状态正确不等于异常反馈完整。

补齐范围：

1. **后端日志**：工具、Provider、检索/编码、证据校验、存储及执行异常在实际处理边界输出适当级别的结构化日志。包含可关联的Run ID、阶段、稳定错误码，以及适用的工具调用ID和受控原因；未预期异常提供必要堆栈。避免同一失败逐层重复打印，不把API Key、Token、DSN密码、整份文档或模型私有推理写入日志。
2. **前端反馈**：将持久错误码映射为可理解的中文原因，保留错误码/Run标识供排查；工具失败与整次Run失败均有明确提示。区分任务失败、资料健康为空、取消，以及订阅/资料读取故障；读取失败不能把已成功Run改成失败。对可重试情况给出与实际能力匹配的操作提示，不假装所有错误都能重试解决。
3. **实时与历史一致**：SSE收到失败事件后能显示原因，刷新或重新打开历史后仍能恢复。继续使用原Run/Event/API事实来源，不新增前端自行判定的终态。HTTP 201/200仅表示创建、读取或订阅成功，不代替业务Run结果。
4. **验收**：用确定性故障注入覆盖代表性工具、Provider、证据格式及存储/读取失败，检查日志关联字段、前端错误说明、历史恢复、未知错误码的受控提示与Secret不泄露；按受影响范围运行测试，不重复未变化的全章门禁。

交付时同步错误契约与生成消费者（如需变更）、前端映射、必要测试、README和交接记录，清楚说明实际覆盖及剩余限制。本项是异常可观测性与用户反馈补齐，原空标题兼容问题仍作为功能缺陷单独追踪，不能只改善提示就声称原因已修复。

## 开章计划（2026-11-13）

### 本章目标与范围

在第五章已经贯通的Agent/knowledge_search/Evidence/引用链上，提高编号和词项命中、材料完整性、证据排序与覆盖判断，并为不足或复杂问题选择有界路线。最终答案仍由原RunExecutor及其模型回合生成，业务Message、执行资格、取消、Journal/SSE和引用读取保持唯一正式路径。

当前实际策略为query编码→固定published scope的Dense检索→全局top‑3→预算内Evidence→原模型混合回答。新策略放在知识工具内部，不创建第二个聊天入口，不把每次检索/子问题变成新Run。文档入库与原子发布继续由独立Worker拥有。

### 现有连接点与本章缺口

| 现有文件 | 已有能力 | 本章连接点 |
| --- | --- | --- |
| [knowledge.py](../src/minimew/knowledge.py) | 身份绑定、Run内scope、query编码、Dense查询、结果预算与Evidence登记 | 保留工具边界，将策略委派给一个正式RAG入口，不在Tool里继续堆全部路线 |
| [documents/retrieval.py](../src/minimew/documents/retrieval.py) | 从PG固定已发布版本、校验scope归属/期限、按manifest搜索 | Dense/词项检索必须共享同一scope与版本过滤，避免融合进candidate或他人材料 |
| [vector_store/milvus.py](../src/minimew/vector_store/milvus.py) | 当前512维FLOAT_VECTOR、FLAT/COSINE及带身份payload | 尚无正式BM25/analyzer/sparse索引通路；需评估schema、build fingerprint及重建发布，不是开关即生效 |
| [documents/chunking.py](../src/minimew/documents/chunking.py) | Section父关系、标题路径、原文span与叶chunk | 有父关系元数据，不等于已有父块检索与Auto-merging；补全文本必须有可信来源及预算 |
| [evidence.py](../src/minimew/evidence.py)、[answers.py](../src/minimew/answers.py) | 单chunk可引用材料、混合答案校验与持久快照读取 | 合并材料的身份/位置/组成关系需明确，不能把整段父文伪装成原子chunk；保持历史引用可读 |
| [model_control.py](../src/minimew/model_control.py) | 当前ModelSnapshot只冻结answer角色 | Fast/Grader/Rerank角色未接入；明确复用冻结answer或扩展角色快照，再调用模型，不能读全局当前配置 |
| [execution/service.py](../src/minimew/execution/service.py) | 外层最多4次模型请求（含重试）、6次Tool，共用期限 | 新增内部评判/规划/重写调用必须有显式总预算，不能每次Tool重新获得一份额度 |

### 沿用技术、首次引入与职责

沿用Python/Pydantic、SQLAlchemy/PG、Redis、Milvus/PyMilvus、原Provider/LangChain消息协议、本地轻量BGE、不可变版本/构建和原Evidence/引用界面。

- BM25是词项相关性算法，拟使用Milvus原生稀疏检索，优先不增加Elasticsearch。中文分词、编号是否保留、索引字段与本机能力需要验证；本轮仅查文档，未连接验证或重建集合。
- RRF按多个召回列表中的名次融合，解决Dense分数与BM25分数不能直接相加的问题，不调用回答模型，也不自动证明相关性。
- Auto-merging利用同一版本的层次关系组织更完整材料，Rerank再对问题与候选重新评分。实现须分别控制上下文长度/候选数，保留来源；算法不要求安装完整LlamaIndex。
- Grader是评判职责，拟用模型及严格Pydantic结构化结果描述相关性、覆盖、冲突/歧义与下一路线；不是任意Python库名，也不等于确定性事实核验。
- LangGraph StateGraph负责RAG内部节点、条件边、有限回路和子问题聚合。当前MiniMew依赖清单尚未声明LangGraph，编码前核对版本与异步/Provider边界；不直接照搬SuperMew同步图的bridge或擅自安装。
- Step-back与HyDE是两种查询改写策略；每条路线只选择一种，最多一次改写与一次复评，不叠加成无限循环。具体先实现哪种在6.5讨论后确定。

### 子项安排

| 小节 | 目标与实施顺序 | 技术职责与取舍 | 验收场景 |
| --- | --- | --- | --- |
| 6.1 单一RAG入口与对照案例 | 先定义输入/候选/结果、Run私有上下文/trace、预算与固定对照，再接现有Dense | 只迁移原Tool内部策略，不新增聊天/检索旁路；记录身份、路线、分数和耗时，不记录私有推理或整段全文到trace | 相同问题/版本可复现当前Dense基线；工具参数仍不接受用户身份/filter；正式答案/引用不退化 |
| 6.2 Dense＋BM25＋RRF | 在相同scope召回，再按名次融合去重 | Milvus能力/中文analyzer/稀疏schema与冻结build核对；候选池大于最终Evidence池，不把所有命中都塞给模型 | 语义问法及精确编号各有固定样例；无跨版本/用户混入；明示能力不兼容与连接故障区别 |
| 6.3 上下文整合与Rerank | 从融合候选组织可引用完整片段，再做更细排序/预算选择 | 父子关系、版本原文、span/hash/组成chunk关系；Rerank模型与部署位置另定。先明确材料身份及历史契约兼容，避免错误复用child chunk_id | 条件与例外可一起供回答使用；最终引用定位实际提供的文本；对照只融合/合并/重排的效果与成本 |
| 6.4 证据评判与结果语义 | 在检索后判断材料是否支持当前问题 | Grader结构化判断覆盖/歧义/冲突与不足；新增模型角色配置需冻结，所有实际模型请求计入本Run共享预算 | 区分健康空、非空但不足、歧义与Provider故障；混合补充不伪装资料规定 |
| 6.5 一次改写与LangGraph路线 | 不足时改写一次→重新检索→复评一次→返回结果 | 在同一个RAG入口编排状态/条件边；Step-back或HyDE二选一；到达次数/期限后停止，不每次Tool刷新预算 | 原问法失配可有界改善；再次不足可明确返回，不发生无界模型/检索放大 |
| 6.6 复杂问题拆解与确定性聚合 | 复杂问题规划一次，形成有限子问题并发检索/评判，合并覆盖信息 | 拟2–4个子问题、并发/子查询/上下文额度另定；共享同一scope，按问题顺序去重聚合，部分失败保留覆盖缺口 | 跨章节问题可分别找到依据；部分覆盖与全部Provider失败不同；仍同一个Run/assistant Message |
| 6.7 对照验收与结章 | 复用正式入口比较路线，保存总结与真实代码框架 | 看支持性/覆盖/引用、p50/p95、真实模型调用数/费用与降级trace；受控协议验收与在线质量分开 | 固定案例可定位每条路线的收益/退化；不是策略越复杂越好；不提前搭第八章评测平台 |

### 开发工作包建议

- A：6.1–6.3，单一入口与召回质量包。集中确定索引升级、候选协议、合并身份/Rerank选择，讲解可按小节，开发一次授权后连续实施。
- B：6.4–6.6，证据判断与有界策略包。集中确定Grader/Planner/Rewrite模型快照、总预算和LangGraph执行边界后实施。
- C：6.7，集中对照与结章。复用已覆盖最终状态的有效结果，只补尚缺的跨模块/真实质量检查，不因切分提交重新跑全套。

以上为建议批次，不代表已经授权；若A的实际耦合/模型依赖需调整，先写具体变化，不用编号强行切割接口。小节完成按最新约定提交推送，合并提交需相应授权。

### 数据流与关键边界

```text
原RunExecutor（执行资格、期限、取消、外层Agent回合）
  → Registry / Guardrail → knowledge_search（身份与能力入口）
    → 唯一RAG Pipeline（本章规划）
      → 固定scope → Dense/BM25 → RRF → 有界合并 → 可选Rerank
      → Grader → 足够 / 不足后一次改写复查 / 复杂问题子查询聚合
    ← 选定的可引用材料、覆盖结果与公开trace摘要
  → 原Evidence登记与结果消息 → 原回答模型 → 原终结事务与引用界面
```

图中RAG内部路线为规划。LangGraph管理算法流程，Run仍是持久业务执行身份；图不自行宣布Run终态，也不新增另一个最终答案循环。Graph state属于本Run，Checkpoint持久恢复/HITL仍在第七章。

1. 先确定唯一正式输入/输出与权威数据，不在Tool/HTTP/测试各复制一套策略。模型选择工具是外层决策，如何检索与评判是工具内部策略，两者不混用。
2. 所有召回/改写/子查询继承同一用户与发布scope，合并/融合不能跨版本；并行不代表绕过API现有单线程CPU/RPC准入，性能以测量为准。
3. 来源清单、Evidence引用和trace不同：trace只记录公开程序步骤/候选身份/评分/覆盖/降级/耗时，不记录Secret、endpoint、整段原文或私有推理。
4. Hybrid只在Adapter明示能力不兼容时允许同入口Dense降级；超时/连接/坏结果仍是故障。Rerank允许的排序回退须有明确code/attempts/fallback，不能静默当作正常成功。
5. 目前4次模型请求是外层预算；不能以此声称已限制尚不存在的内部Grader/Planner调用。新增调用的计数、重试所有者、总deadline及跨多次Tool共享预算须在包内明确，不自动放大4/6或收费授权。
6. 控制面改动只影响之后Run。若增加角色/索引/合并Evidence形状，先明确快照/Contract版本/必要迁移与旧材料读取，再实现；不得为兼容另建隐蔽双读路径。

### 首轮架构走读

“报错E1024怎么处理”这类问题既依赖精确编号，也依赖上下文；“差旅申请、餐费标准、回来多久提交”则要求多处依据。单次Dense top‑3即使返回相似文本，也未必同时覆盖编号、条件/例外和多个子问题。

因此本章逐层处理四件事：召回扩大候选范围，融合/重排组织候选，合并补足上下文，评判决定是否足够以及是否改写/拆解。最后提供给回答模型的仍是有预算、可定位的Evidence，而不是无限增加的长prompt。Rerank回答“哪些候选更相关”，Grader回答“这些材料是否足以支持这个问题”，后者仍是有误差的模型判断，需要对照案例校验。

本轮已查阅[Milvus BM25](https://milvus.io/docs/bm25-function.md)、[RRF](https://milvus.io/docs/rrf-ranker.md)、[LangGraph StateGraph](https://reference.langchain.com/python/langgraph/graph/state/StateGraph)与[Auto-merging说明](https://developers.llamaindex.ai/python/framework/integrations/retrievers/auto_merging_retriever/)等官方资料。文档能力不等于本机已验收，未安装LangGraph/Rerank模型、联网下载权重、修改集合或运行模型调用。本次仅新增计划与交接，未写应用/配置/迁移/测试代码，无业务测试或Git提交推送。

### 6.1 单一入口、数据协议与职责讨论（2026-11-13）

本轮用户“继续”，沿上一轮的开章规划继续架构讲解；以下是拟议设计，不是已实现的模块或已授权的代码包。没有要求用户复述或作简单边界题，也不据此预填理解结论。

#### 从当前代码看拆分理由

当前[KnowledgeService.search](../src/minimew/knowledge.py)在同一方法中完成身份校验、冻结并复用发布scope、query编码、Dense检索、来源投影校验、输出预算和Evidence登记。第五章只有一条检索路线，这个组织方式足够直接；若继续在这里堆入BM25/RRF/合并/Rerank/Grader/改写/拆解，权限入口和算法策略会相互牵连。

拟保留knowledge_search作为唯一知识工具入口，提取内部RAG Pipeline承接算法编排。Pipeline是进程内服务模块，类似Spring Service间的调用；不是新HTTP服务、独立进程或另一个业务Run。LangGraph若引入，也在这个内部边界内编排。

```text
RunExecutor：Run资格、取消、绝对期限、外层Agent循环
  → Registry / Guardrail
  → knowledge_search：校验可信身份，取得并复用本Run的发布scope
      → RAG Pipeline：在固定scope和预算内召回、融合、选材、评判
      ← 最终材料 + 覆盖结果 + 程序trace
    → 输出预算/来源校验 → EvidenceLedger登记E1/E2 → Tool结果
  → 原回答模型 → 原答案校验与终结事务
```

scope仍由工具的Run内会话持有，Pipeline只使用同一scope；不能让模型填user_id、版本过滤或自行扩大文档范围。底层Adapter仍校验精确manifest/身份，不能因上层已经鉴权而跳过。

#### 输入输出分成模型参数与服务端上下文

以下名称只用于解释职责，最终类型与文件组织在工作包设计中确定，不宣称已经存在。

| 拟议对象 | 内容与所有者 | 为什么单独传递 |
| --- | --- | --- |
| RagRequest | 本次query；来自已严格校验的工具参数 | 表达用户要检索什么，不携带客户端自选权限 |
| RagContext | 服务端身份、冻结scope、原Run绝对期限、共享预算、已冻结配置和本次trace收集器 | 表达程序允许在哪里、用什么资源检索；多次工具调用不能重置Run总预算 |
| Candidate | 文档/版本/片段/位置/原文等来源身份；以及各阶段名次与评分 | 候选会被筛掉，尚不分配答案引用ID；不能把Dense、BM25、Rerank分数当同一种数值 |
| RagResult | 最终选定材料、覆盖/不足等结果、受控trace | 给工具入口统一出口；运行故障仍走typed error，不伪装为健康无结果 |

当前KnowledgeResult只有ok/no_documents/no_matches和limited；上表并未修改这一正式协议。未来“部分覆盖/歧义”等结果怎样交给外层模型、评分怎样从单一score升级，必须显式确定协议与消费者，不能悄悄换字段含义。当前ModelSnapshot只有answer角色，RagContext中的未来Grader/Planner配置也不能假定已经可用。

候选应保留按阶段命名的dense_score、bm25_rank、rrf_score、rerank_score等信息（具体字段待定），融合依赖名次，重排有自己的评分语义。同一个来源被两路命中应合并为一个候选；合并若产生父级新材料，必须提供真实的来源身份和范围，不能把父级全文挂在原子块ID下。

#### 一次请求怎样贯通

以“餐费上限多少，回来后几天内提交”为例：knowledge_search先取得本Run固定的已发布版本范围，再交给Pipeline。6.1首先接通现有Dense路径作为可对照基线；待后续实现时，Pipeline内部才可能取多路候选、融合、补上下文、重排和判断覆盖。候选20条、最终选2条只是示意，不是已确定top-k或当前能力。

Pipeline最终选出餐费和提交期限两份材料后，工具入口再次按实际输出与累计Evidence预算选定可返回材料，再交给本Run的EvidenceLedger登记为E1/E2。被丢弃的候选不占用引用编号；同一Run已有相同来源时复用已有编号。因此检索策略无须了解最终答案里的引用位置，EvidenceLedger也不负责判断向量相关性。

Tool结果携带原文及E1/E2返回外层模型；外层模型按已选混合回答规则生成正文，parse_answer检查结构与引用，原终结事务保存正文和引用材料。Pipeline既不写另一个assistant Message，也不自行宣布Run成功。覆盖判断是后续Grader的职责，不能把现有引用格式校验当作支持性验证。

#### 预算与trace是流程协议的一部分

现有4次模型请求（含重试）与6次工具调用约束外层Agent循环。Grader、改写和Planner引入后，一次knowledge_search内部也可能发起模型请求，因此必须在原Run上下文内明确共享计数，区分外层模型次数、内部策略模型次数、检索/子查询次数、最终材料预算，同时服从同一个绝对期限。具体限额待选型后定；本轮不增加请求次数、费用或模型角色。

trace记录程序发生了什么，例如本次Dense召回数、融合前后候选身份、保留材料、各阶段耗时、覆盖缺口、明确降级code与计数。它不等于模型私有思维链，也不等于回答引用目录；不把整段原文、Secret或endpoint复制进去。Pipeline返回typed trace并不意味着每个节点已经写入Journal或前端可见事件；存储与公开投影方案需在实现包中明确，当前tool.started/completed/failed契约没有随本讨论改变。

#### 现有源码对应与本轮边界

- [knowledge.py](../src/minimew/knowledge.py)：拟拆分的知识入口；当前仍包含Dense编排与Evidence登记。
- [documents/retrieval.py](../src/minimew/documents/retrieval.py)：发布scope及现有唯一Dense读取，不另建绕过发布约束的检索路径。
- [execution/tools.py](../src/minimew/execution/tools.py)：Registry、Guardrail、Run私有ToolContext/会话；拟议RagContext应由此类可信执行上下文派生。
- [evidence.py](../src/minimew/evidence.py)：累计来源目录与答案结构/引用校验，继续接收最终返回材料。
- [execution/service.py](../src/minimew/execution/service.py)：外层Agent循环和原Run终结职责继续保留。

下一步讨论6.2的Dense/BM25候选如何进入同一协议，以及RRF为什么用名次融合；同时说明现有索引需要怎样升级，之后再收敛A包的选型、完整范围和验收。A/B/C仍为建议批次，本轮没有第六章编码授权。

本轮只更新章节设计与交接，检查文档差异和本节源码链接；未修改应用源码、依赖、Contract、迁移、评测资产或运行配置，未启动模型/数据库验收，未运行业务测试，也不计新增通过用例。讨论文档不触发commit/push。

### 6.2 Dense、BM25、RRF与索引职责讨论（2026-11-13）

用户继续讲解，本轮仍是设计讨论；没有开始第六章应用编码、选定全部参数或授权修改真实索引。以下路线为拟议设计，当前生产式读取仍是第五章Dense路径。

#### 两路召回各自提供什么

Dense将query编码为向量，与文档chunk向量比较语义相似度。例如问题“出差吃饭最多报多少”，资料写“餐费补助上限”，词面不同也可能召回。BM25依据分词后词项的出现情况、稀有程度及文本长度等计算相关性，适合补充错误码、产品型号和制度名称等词项线索；并非精确字符串匹配，E1024/E1025是否能区分取决于分析器实际输出，不能只凭算法名称承诺。

Milvus原生BM25由服务端文本分析/稀疏检索完成，不需要新增本地Embedding神经模型或调用回答Provider。拟保留现有BGE Dense，同时让同一逻辑chunk具有BM25可索引文本；新增的是索引字段、分析配置与检索协议，不默认增加Elasticsearch进程。

```text
同一问题 + 本Run同一已发布scope
  ├─ BGE query编码 → Dense候选排名
  └─ 文本分析/BM25 → 词项候选排名
           ↓
  按完整来源身份去重 + RRF融合
           ↓
  有界候选池 → 后续合并/Rerank → 预算内Evidence
```

两路必须在检索时就使用同一精确manifest范围；不能先全库取top-k再在应用层过滤，否则其他用户/旧版可能挤掉当前范围应有候选，且返回路径扩大。合并键应绑定用户/文档/版本/build/chunk等可信来源身份，不靠正文相同或孤立的chunk编号去重。候选临时标签与业务Run、引用E编号各不相同。

#### RRF怎样使用两份排名

Dense余弦分数和BM25词项分数尺度不同，不能直接相加比较。RRF对每一路中实际出现的候选按名次加分：`rrf(d) = Σ 1 / (k_rrf + rank_i(d))`；名次从1开始，未入该路列表贡献0。k_rrf是平滑常数，与每路取多少条的top-k不同。以下k_rrf=60仅作示意，不是本项目已调优参数。

| 临时候选标签（不是Run或引用ID） | Dense名次 | BM25名次 | 示意RRF分数 |
| --- | --- | --- | --- |
| A | 1 | 2 | 1/61 + 1/62 ≈ 0.03252 |
| B | 2 | 未入选 | 1/62 ≈ 0.01613 |
| C | 3 | 1 | 1/63 + 1/61 ≈ 0.03227 |
| D | 未入选 | 3 | 1/63 ≈ 0.01587 |

示意融合结果为A、C、B、D：两路共同靠前的候选受到支持，单路独有候选也可以保留。它不会读懂原文或证明材料正确，RRF分数也不是正确概率；两路都没召回的片段无法由融合创造。准确编号的单路候选仍可能被排后，需要通过候选池大小和固定案例验证，不能假定融合总比单路好。同分时必须使用稳定来源键确定顺序，便于对照与定位。

先各取足够候选、再融合、最后按预算选材料；不能两路各取很少并直接视作完整材料。每路召回数、融合保留数、最终Evidence数量/字符预算是不同参数，具体值留A包确定，不把示意数值写成默认能力。

#### 入库侧与查询侧一起接通

当前[MilvusBackend](../src/minimew/vector_store/milvus.py)只创建身份字段、JSON payload和512维FLOAT_VECTOR，索引为FLAT/COSINE。payload内存在原文不代表已经建立全文索引；现有schema校验也会拒绝不匹配结构。

拟议新索引的同一条逻辑chunk记录需同时提供：可分析的VARCHAR检索文本、原生BM25输出的SPARSE_FLOAT_VECTOR、已有Dense向量及同一来源身份。检索文本建议用标题路径加source_text，字段名与长度待定；不直接复用带模型专用前缀/格式的embedding_text。送给回答模型的来源原文仍由原source_text/定位信息拥有。

入库由独立Indexing Worker执行文本准备、Dense编码、写入/验证候选索引和发布；API查询只读已发布产物。BM25分析器、检索文本构造和索引schema应纳入可识别的构建配置/指纹，manifest验证需覆盖新增检索产物。现有Dense记录不会因新增一段查询代码自动成为Hybrid记录；旧数据的重建/迁移、切换时机、失败保留和旧scope生命周期需在A包确定，不能原地修改已冻结manifest，也不宣称现有版本/任务模型已经支持任意重建。

#### 中文分析器与复现边界

BM25文档入库与查询采用相同分析逻辑。这里的分词目标是可检索词项；BGE tokenizer的384 token预算用于控制模型编码长度，两者属于不同配置，不共用同一种token含义。A包需实际检查中文短句、E1024、E1025、连字符型号、字母数字混写和标题是否按预期产生词项，再决定分析器，不能仅凭“支持中文”通过选型。

固定scope保证候选属于同一发布范围，不等于冻结整个BM25统计语料。Milvus BM25使用语料统计，真实对照还需记录索引/语料状态；不能承诺共享集合中新增数据后旧scope一定得到完全相同的词项分数。具体统计范围及候选构建数据对其影响需在实际部署验证。

当前[search_published](../src/minimew/documents/retrieval.py)逐manifest调用Dense并按相同余弦指标取全局top‑3；接入BM25时应明确在同一逻辑scope形成每路排名，再做融合，不能将多个文档的局部第一名都当作全局第一名。共享集合、过滤与分数可比性需由正式Adapter验证。

#### 技术选择、验收与下一步

拟复用Milvus原生BM25和现有BGE，不需要仅为本节增加GPU或回答模型调用；RRF是确定性排名计算，可在Milvus或应用内完成，但正式融合位置只选择一个。倾向让Pipeline掌握两路排名/候选身份以便trace与离线测试，最终调用API和实现位置需兼容实际部署能力后确定，不并行维护两份排名结果。

固定案例应包括语义改述、准确编号/近似编号、标题词项、Dense独有/BM25独有/共同命中、去重/稳定同分、同一发布scope与输出预算，比较召回收益及退化。能力明确不兼容时才允许显式Dense降级；检索连接/超时故障仍是typed error，不能因一条路径出错悄悄改成单路成功。

本轮核对[Milvus BM25](https://milvus.io/docs/bm25-function.md)、[RRF](https://milvus.io/docs/rrf-ranker.md)、[Analyzer](https://milvus.io/docs/analyzer-overview.md)官方文档，以上原生功能是文档能力，不代表本机服务/集合已经验收。未连接或修改Milvus，未安装依赖/下载模型/发起收费调用；仅更新本章与handoff并检查文档差异、本地链接，不运行应用测试或commit/push。

下一步讲6.3：为什么融合后仍需要补齐父级上下文与Rerank，它们怎样改变候选排序、材料长度和引用身份；再集中收敛A包范围与选型。当前无第六章编码授权，也未记录用户已经掌握所有概念。

### 6.3 Auto-merging、Rerank与引用身份讨论（2026-11-13）

用户“继续”承接6.2，本轮仍只讲设计与源码对应，不开始A包开发，也不认定用户已完成全部理解或选择。新模块、阈值、模型和输出形状均为拟议。

#### 为什么融合后还要补上下文

Dense/BM25/RRF提供可能相关的片段及排名，无法保证单个片段包含适用对象、数额、例外和期限。例如一节内的三个片段分别说明适用人员、每日150元上限、已提供工作餐时扣减；问题“公司提供午餐，还能报150吗”若只召回数额，会缺失例外。

Auto-merging利用构建时的父子关系：同一父节点下足够多子片段被召回时，把这些子片段对应的候选提升为父级上下文，从已冻结原文读取更完整的连续内容。它不调用模型重写制度，不是把任意相似段落拼接，也不是扩大overlap。命中2/3后取整节只是示意；命中率分母应来自可信构建目录中的完整子节点集合，不能以本次已命中集合为分母，重复的Dense/BM25命中也不能重复计数。

LlamaIndex的AutoMergingRetriever使用显式父子节点并可递归合并。本项目倾向先做单层、有长度/材料预算的合并，不因算法名称直接安装整个LlamaIndex或创建其第二套存储目录。若仅增加相邻块而没有父子阈值，就应称邻块扩展，不混写成已完成Auto-merging。

#### 当前章节结构提供了什么，还缺什么

[chunking.py](../src/minimew/documents/chunking.py)有Section(section_id,parent_section_id,title_path,span)，Chunk含section_id、原文span、source_text及hash。[parsing.py](../src/minimew/documents/parsing.py)的章节span覆盖该节及下级章节，根节点可覆盖全文。因此“有parent_section_id”不等于已经存在适合直接合并的分层检索节点；父章节可能过大，子章节与直属chunk关系也需明确。

[artifacts.py](../src/minimew/documents/artifacts.py)保存冻结ChunkBuild及manifest，目前read_artifact验证后只返回manifest；[storage.py](../src/minimew/documents/storage.py)能按版本和预期hash读取不可变原文。拟议合并需正式取得同一scope对应的受验证层级与来源范围，不能扫描Milvus命中行来反推完整父子目录，也不能读取Document.current的新版本替换Run已冻结的版本。源文件读取和模型/RPC一样需要遵守当前异步执行与资源准入边界。

A包需确定合并父节点范围、直接子节点计数、阈值、一次最多提升层级、长度/模型输入限制、重叠去重与保留未合并片段的规则。过大父节点保持子候选并记录未合并原因，不一路提升到全文；增加材料也不保证语义充分，后续Grader仍有独立职责。

#### Rerank与Dense/RRF的区别

Dense把问题与片段分别编码，片段向量可在入库时预计算，用于从大集合快速召回。常见Cross-Encoder重排模型在查询时联合读取一对“问题＋候选原文”并输出相关性分数，再对有限候选重排。RRF只利用名次，不读取原文；Rerank读取文本、需要模型计算，但不生成最终答案。它通常比独立编码后比向量更昂贵，因此只对召回池评分，而不扫描全库。

示意问题为“公司已提供午餐还能全额报销吗”：候选“每天150元”和“已提供餐食的相应扣减条款”都可能被召回，重排模型可能把后者放前。这个例子解释目的，不是已测质量或必然正确的承诺；模型仍可能错排，分数含义依模型而异，不能都解释为0到1的正确概率。

重排不是改用BGE-small再算一次同一余弦相似度，也不要求改动原Dense向量维度。通常需要另外选择重排模型/Provider、tokenizer、输入窗口、运行位置与版本配置；现有BGE-small和answer-only快照不能假定兼任。可本地CPU/GPU或外部服务，但本轮没有选定或下载任何模型，没有性能或硬件承诺。后续若新增角色/调用，必须按Run冻结配置、共享期限与资源预算，并明确单一重试所有者。

#### 顺序、预算与真实引用

拟议A包路线为Dense/BM25→RRF去重→有界父级合并→可选Rerank→材料预算/去重→Evidence登记。先合并再重排使评分看到更多适用条件，但会增加输入长度且可能稀释相关性；先重排子片段再合并能省计算，却可能提前丢掉触发合并的同级片段。先采用可对照的明确顺序，再根据案例决定是否调整，不能在两个位置重复调用却不计成本。

现有BGE分块384 token预算只约束其编码输入，不是父级材料、Rerank输入或回答模型的通用窗口。合并后的材料无需为了返回原文而重新生成Dense向量，但必须分别满足重排的query+passage输入窗口、单条Evidence最多10000字符、单次知识工具序列化输出11000字符和Run累计Evidence预算18条/24000字符等既有约束。预算含元数据，不能把字符值直接当token或承诺恰好容纳同样长的正文；合并超限不静默截掉例外条款。

当前[EvidenceSource](../src/minimew/evidence.py)要求chunk_id、section_id、精确span、text和content_sha256；[KnowledgeService.project](../src/minimew/knowledge.py)又验证片段属于manifest。父级全文不能冒用一个命中子chunk_id，因为原身份/范围/hash都不再描述实际材料。

需要在A包明确两种方案之一：保持现有片段引用、将补充上下文解析成受验证的原片段并保留各自ID；或正式引入可验证父级来源身份，扩展材料协议及答案读取/前端，并保持既有答案快照可读。前者对现有引用链影响较小，但多条Evidence可能更占预算，内部父级候选与实际输出材料也必须一致；后者能整节引用，改动协议和迁移范围更大。当前没有悄悄选定一种，也不把拼接文本或不连续内容伪装成连续原文。外部看到的材料必须包含其所声称引用的实际来源。

#### 技术职责与验收边界

| 步骤 | 读取什么 | 产生什么 | 是否需要新模型 |
| --- | --- | --- | --- |
| RRF | 各路候选身份与名次 | 融合排名 | 否 |
| Auto-merging | 命中分布、可信父子目录、固定版本原文 | 更完整且有界的来源候选 | 否 |
| Cross-Encoder Rerank | 问题与有限候选文本 | 新相关性分数/排名 | 是，具体模型待选 |
| 后续Grader | 问题与最终可用材料 | 覆盖/不足等结构化判断 | 规划中，6.4讨论 |

验收应对照有例外的条款、未达阈值/重复命中、超大父节、多层标题、重叠片段、同版来源和引用范围、重排输入超长、稳定排序及质量/耗时变化。Rerank故障若采用保留原融合顺序的有界回退，需明确code、attempts、fallback；运行开关主动关闭与故障回退应区分，不把故障伪装为模型已重排。对照必须使用实际最后交给回答模型的材料，而不能仅凭中间父级候选判充分。

本轮核对[LlamaIndex Auto-merging](https://developers.llamaindex.ai/python/framework/integrations/retrievers/auto_merging_retriever/)、[Sentence Transformers Retrieve & Re-rank](https://www.sbert.net/examples/sentence_transformer/applications/retrieve_rerank/README.html)和[Cross-Encoder用法/分数语义](https://www.sbert.net/docs/cross_encoder/usage/usage.html)官方资料；未引入这些库或运行模型。仅更新章节与交接，检查文档差异、本地链接；未改应用/Contract/迁移/索引，不运行业务测试或commit/push。

6.1–6.3架构职责已作首轮讲解，不等于实现或用户已确认。下一步集中收敛A包：索引升级/分词、父级来源与引用方案、Rerank运行方式/开关、候选和材料预算、实际验收及提交边界，再取得该包具体开始反馈；不因“继续”自动开始编码，也不重复简单判断题。

### A包收敛与双设备重排选型（2026-11-13，模型待用户后续选择）

用户继续讨论后，已明确选择“保留原片段引用”；Rerank问题回复为“根据我的双设备各自推荐本地模型，同时介绍外部模型。等我了解后下次回复”。因此本轮优先完成选型说明，不下载模型、不开始编码、不催促再确认或将推荐写成已选。此前A/B/C建议与第六章代码授权边界不变。

#### 已确定的引用方向与A包职责

保留原片段引用：按可信父节关系组织补全，补回的原chunk仍具有独立身份、原文范围/hash和E编号，不把父节全文挂到一个子chunk_id。内部父级分组仅是候选组织；交给重排/后续Grader/回答模型的材料须明确对应实际保留下来的原片段。父组放不进预算时不能宣称已经完整提供该节；保持原片段路线并记录限制。整节单一Evidence身份的扩展不在本次已选方向内。

拟议A包覆盖6.1–6.3的一条端到端链：knowledge_search入口→唯一内部Pipeline→同scope Dense/BM25召回→单一RRF融合→有界结构补全→可选重排→最终原片段Evidence；保持原Run/权限/取消/期限/答案读取和终结事务。模型选择未定，尚不能把A包标为可直接执行或获准开发。

实现位置拟按职责在现有代码基础上整理：knowledge.py保留入口与Evidence登记；rag内部承接候选协议/编排/融合/合并/重排；documents与vector_store拥有构建、manifest校验和发布读取；knowledge_runtime拥有所选本地资源的加载/准入/关闭。具体文件名不预创建。A包不提前实现6.4–6.6的Grader/改写/拆解、LangGraph复杂路由、HITL或评测工作台。

源码核对发现IndexJob.version_id存在唯一约束，BuildConfiguration.index_layout当前固定dense-cosine-v1，版本与KB构建配置有冻结身份。Hybrid升级必须明确旧产物的转换/新构建/核验/切换方式，不能重复插入同版本任务或原地改变冻结配置；不删除用户文档/答案，不并行维护隐藏的正式查询路径。此处仍是A包设计要求，不是迁移脚本或已决定的全部升级步骤。

#### 双设备事实与推荐依据

本轮只读系统查询确认当前笔记本：i7-11800H、系统可见约15.8GiB（通常标称16GB）内存、RTX3060 Laptop，nvidia-smi显示6144MiB显存。另一台R7-9700X、24GB内存、RTX5070来自用户描述，本轮未访问该设备/核实显存。性能、空闲资源与模型推理均未测试。

| 设备/用途 | 首选候选 | 推荐运行方向 | 取舍 |
| --- | --- | --- | --- |
| 当前笔记本，先学通重排链 | BAAI/bge-reranker-base | 小批GPU FP16；若保持现有CPU环境，则显式CPU FP32小候选池 | 278M、中英文；较小，方便与基础链对照。较旧模型，不能承诺质量或速度优于其他候选 |
| RTX5070台式机，作为常用本地方案 | BAAI/bge-reranker-v2-m3 | GPU FP16、小批量/受控长度起步 | 568M、多语言，可处理更长输入；推理实际占用高于权重，需要实测 |
| 台式机的第二轮对照候选 | Qwen/Qwen3-Reranker-0.6B | GPU部署、限制输入和候选数 | 0.6B、100+语言、标称32k上下文、支持任务指令；评分实现需遵循模型协议，不预设比BGE更好或更快 |

这是基于模型规模、语言、输入能力与设备的工程建议，不是本机benchmark。笔记本3060也可以尝试v2-m3短文本小批量，不将其写成硬件不支持；先选base是更小起点，CPU是否可交互使用必须测量。台式机首推v2-m3是延续BGE接口/部署方式的简单选择，不是最新排行榜结论；Qwen0.6B作为对照，不要求两者同时常驻。

BAAI官方给base/v2-m3分别列278M/568M及约1.11GB/2.27GB模型大小；按每参数2字节粗算，FP16纯权重约0.56GB/1.14GB，Qwen0.6B约1.2GB。这是权重数量级，不含激活、tokenizer、框架/工作区、并发和其他进程，更不能当作运行显存上限。GPU小批量容纳的判断是推断，必须以实际所选模型长度、精度和占用验证。

项目当前pyproject的torch来源为pytorch-cpu；显卡和驱动被识别不代表当前虚拟环境已能GPU推理。若后续选择GPU，要明确兼容5070/3060的PyTorch/CUDA安装与独立环境锁定方案并实际验收，不复制另一台设备的venv，不在导入时联网加载权重。CPU/GPU选择是显式配置，失败不能偷偷改设备。GPU可以先只承担Rerank，不必同时更换现有Embedding。

#### 输入窗口与已有分块的关系

base以短输入（通常512 token级）的question+passage对为起点；必须用其实际tokenizer/config验证，不能拿BGE-small的384 token预算证明能放下。v2-m3官方配置有8194位置槽，对应约8192文本token窗口；Qwen标称32k。更长窗口不等于应把每条候选拉满，初始仍用受控短材料和小批量测量。

保留原片段引用后，重排可按受验证原片段或明确有界的组进行；具体顺序/评分单位要随所选模型收敛。如果组超出窗口，不能无声截去后半部例外条款后宣称完成了整组相关性评分；应采用可复现的片段策略或记录未重排原因。模型的长窗口也不放大现有Tool输出与累计Evidence预算。

#### 外部API：托管方式与模型身份分开

外部Rerank依然接收query和已召回的有限候选文本，返回索引/相关性排序，不需要把整个知识库上传给它，也不替换现有DeepSeek最终回答Provider。本地与远端均应经同一个Reranker接口，由Run冻结模型配置；远端返回的索引必须映射回本Run输入候选，不能接受其改写引用身份或自造来源。

1. 阿里云百炼qwen3-rerank：托管文本重排服务，适合想避免本地权重/显存维护且接受候选文本传输的场景。官方说明与开源Qwen3重排系列性能对齐，不代表后台就是本地0.6B同一revision。2026-11-13核对的北京/新加坡页面文本输入标价为0.5元/百万token，地区、实际计量和活动以控制台/账单为准；不是每百万次查询。示例若服务记账输入为1万token，单次约0.005元，仅说明计费数量级，不估算本项目固定每问费用。
2. 硅基流动/v1/rerank：官方接口示例含BAAI/bge-reranker-v2-m3，指令参数支持Qwen3-Reranker-0.6B/4B/8B等。适合以托管开放模型对照本机部署；同名不保证revision、精度、分块或结果完全相同。价格和模型可用性以实际控制台为准，本轮未核定免费资格、不承诺免费。部分服务会自行分割长候选取最高子块分，不能将其评分等同整组覆盖。

本地费用主要是模型准备、内存/显存、计算和维护；外部API省去本机推理占用，但增加网络时延、用量费用，并向供应方发送本次query和候选原文。结果是否更好取决于模型和案例，不由“外部API”这个部署方式决定。即使已有DeepSeek答题API Key，也不能假设它能用于另一家重排服务；密钥只放本机配置，不发聊天、不进Event/材料。

建议路线供下次选择：笔记本base、台式机v2-m3，保留Qwen0.6B作为后续对照；外部API先了解，不自动接入或收费。若希望双设备结果更便于比较，也可两台先用同一个v2-m3、分别控制设备/批量，再测差异；换重排模型不会改变Milvus的Dense向量维度，单独换Embedding模型才需要重新考虑编码空间和索引构建。

#### 来源、验证与下次起点

本轮只查官方来源：[BGE base](https://bge-model.com/bge/bge_reranker.html)、[BGE v2](https://bge-model.com/bge/bge_reranker_v2.html)、[v2-m3配置](https://huggingface.co/BAAI/bge-reranker-v2-m3/raw/main/config.json)、[Qwen0.6B模型卡](https://huggingface.co/Qwen/Qwen3-Reranker-0.6B)、[百炼模型与价格](https://docs.bailian.console.aliyun.com/en/model-studio/qwen3-rerank)、[硅基流动重排接口](https://docs.siliconflow.cn/docs/api/rerank-post)。硬件查询只读，未调用Provider/加载模型；未下载或修改应用/环境/索引/Contract/迁移，未执行业务测试。章节/handoff差异和本地链接检查单独记录，不冒充模型验收，无commit/push。

下一轮等待用户对本地/外部模型的选择或疑问，保留已选原片段引用；不重问引用方案或出简单理解题。选型后再给最终A包范围、必要迁移/集成/性能检查及提交拆分，并取得具体“开始”反馈。当前无第六章应用编码或模型下载/付费调用授权。

### A包模型选择确认（2026-11-13）

用户明确：“暂时选择本机笔记本BGE Reranker Base的方案，其他方案等后续我有明确提示再执行。”本节为最新选择，优先于上一节尚待模型选择的历史状态。

- 当前重排模型确定为本机笔记本使用`BAAI/bge-reranker-base`，继续采用已经选定的原片段引用；不重复询问这两项。
- RTX5070设备的v2-m3、Qwen3重排模型以及百炼/硅基流动等外部API仅保留为说明材料。用户明确提示前，不为这些方案下载权重、安装依赖、创建专用实现/服务、配置账户或发起调用；普通“继续”不激活这些备选路线，也不因后续在台式机开发就自动切换模型。
- 模型选择与执行设备/精度配置分开：笔记本CPU/GPU、批量、输入窗口及必要环境变更，在最终A包范围中明确后验证；不能由模型选定推导为GPU环境已经就绪或测速通过。
- 本次仅确认选型并保存文档，不代表已经开始第六章A包编码、模型下载或环境安装。下一步按已选base和原片段引用收敛A包完整实施/索引升级/验收范围，再取得该包明确开始反馈；包内不反复索取同一范围授权。

本轮只更新章节、学习计划与handoff并检查文档差异/本地链接，无应用/依赖/Contract/迁移/索引改动，无模型下载、业务测试或commit/push。

### A包最终实施范围建议（2026-11-13，待明确开始）

用户在选定笔记本Base后“继续”，本轮明确可交付范围，仍不视作开始编码。沿用已选`BAAI/bge-reranker-base`和原片段引用；不再进行选型问答。以下具体方案供用户以“开始A包”等明确反馈授权，若有疑问则在同一方案上补讲，不重启课程。

#### 交付目标和实施顺序

一条正式知识请求贯通：原RunExecutor/Registry/Guardrail→knowledge_search→唯一内部RAG Pipeline→同scope Dense+BM25→应用内单一RRF→有界父节补全→本机Base重排→按组和输出预算选原片段→原Evidence/答案持久化与引用界面。A包覆盖6.1–6.3；6.4–6.6 Grader、改写、拆解、复杂LangGraph图，以及第7/8章功能不进入本包。

| 顺序 | 具体交付 | 实际连接点与职责 |
| --- | --- | --- |
| 1：6.1边界与基线 | 提取候选/阶段评分/材料组/结构化trace协议与唯一Pipeline；接通现有Dense作对照 | knowledge.py保留身份、scope和最终Evidence；rag内部拥有算法，execution仍拥有Run，不新增聊天route |
| 2：6.2构建与升级 | Milvus原生BM25字段/分析器、检索文本、构建指纹/manifest核验；显式旧数据升级与发布流程 | documents的catalog/Worker/artifact以及vector_store，PG迁移只做数据库元数据，不启动模型或访问Milvus |
| 3：6.2召回与融合 | 同一精确scope各路候选排名、来源去重、稳定RRF及显式能力结果 | Pipeline掌握完整阶段分数与trace；Milvus只召回，不再同时启用服务端第二份RRF |
| 4：6.3补全与重排 | 单层同节补全、完整材料组、本地Base adapter/资源生命周期与有界评分 | 冻结产物提供完整目录/来源；runtime拥有共享只读权重与准入，Run持有预算/结果 |
| 5：集成交付 | 固定对照案例、真实隔离PG/Milvus/本地模型链、迁移与历史答案核验、耗时/资源记录 | 更新受影响契约/生成物/文档；按实际变更测试，不自动扩展全仓或重做第五章 |

建议新增的rag内部文件按协议、编排、融合/合并和重排实际职责组织，不创建空壳服务；尽量复用现有documents/vector_store正式实现。保留同一个知识Tool和底层检索Interface，Dense基线是同接口内显式策略，不是另一条问答路径。

#### 入库侧与旧数据的升级要求

检索文本采用标题路径+原文；分析器先通过中文词句、E1024/E1025及连字符型号的实际分词检查，再冻结配置。BGE-small Embedding与既有分块预算保持，索引布局升级影响build fingerprint和产物身份；检索范围始终从PG发布目录取得，不以Milvus里“存在一行”判断发布。

旧Version/manifest不原地改写，同一Version不额外创建第二个IndexJob。优先复用现有“同内容、不同构建配置生成新候选Version+Job”的目录机制：保存原内容，按新构建形成候选，Worker构建并核验，发布事务再切换current；失败不切换。KB后续默认构建与各Version已冻结构建分开处理。源版本/历史AnswerRecord和引用快照保留，真实用户内容不删除。

旧布局到Hybrid的读写/发布切换、正在运行的scope保留与回收须落实为显式升级流程；对旧产物只能报告真实Dense能力，不能假称已完成Hybrid，也不能因超时/坏schema隐式走旧库。若实际切换无法在现有发布机制内满足不变量，应先说明具体范围变化，不增加隐藏双读/双写来绕过。先隔离库验证流程，再按明确范围执行真实开发库升级；未在本轮触碰数据库或Milvus。

#### 合并、评分单位和预算建议

先用简单、可复现的初始值开展对照，不把其当作质量最优参数：Dense和BM25各取10，按相同scope形成各路全局列表；RRF平滑常数60，融合保留最多12个原片段候选。若直接父节的不同子片段命中数至少2且超过其可信子片段总数一半，尝试单层补全。完整补全组最多6个原片段，所有候选累计最多18个；超过界限保留原候选，记录未补全原因，不提升到全文。

组内来源均来自同一版本/build；补回内容通过原片段身份/hash/范围验证。每个片段仍独立编号，组只承担选材依赖，不能变成伪造父级Evidence。Base先对原片段query+passage对评分；补全组的初始排序依据取组内最高重排分（仅表示最强相关片段，不表示全组已覆盖问题），同分沿用融合优先级和稳定来源键。按组选择时保留其全部成员，不能只留下数额、删去一起补回的例外；重复来源去重，多组重叠时以确定规则处理。

最终最多6个原片段且服从现有单次知识结果11000字符、单条Evidence10000字符、Run累计18条/24000字符等更紧限制；先核算真实序列化开销再登记E编号。组放不下则不声称完整提供该父节，保留可验证原候选并记录limited。未来Grader必须检查实际输出材料，不检查一个后来未发出的中间父全文。初始数量可根据固定案例/时延调整，相关配置在Run创建时冻结，不能在多次Tool调用间重置预算或静默更改。

#### 当前笔记本模型与运行方式

本包建议先沿用现有CPU版PyTorch，以本地Base、FP32、batch=1进行基线，避免本包增加CUDA环境改造；GPU/其他模型/远端API均不自动实施。这个选择是拟议A包配置，不代表CPU速度已满足交互需求。若实测不可接受，如实给出测量及可选后续调整，不能静默下载更大/其他模型、改GPU依赖或付费调用。

获准开始后只准备Base权重与tokenizer，核对官方revision后固定；常规启动/测试只读本地缓存，不隐式联网。用实际reranker tokenizer检查完整问题+标题/原文+特殊token是否满足窗口，不以Embedding的384 token代替。超窗不静默截去条款；初版可显式放弃本次重排、保留融合/分组次序并记录稳定原因、attempts和fallback。关闭重排的对照路线与故障/超窗回退区分记录，不冒充真实重排成功。

模型和连接由应用生命周期管理，不在模块导入加载；同步推理放到受控工作线程/队列中，遵守原Run期限与取消，线程未实际完成前不提前释放资源槽。现有外层4模型请求/6工具调用不放大；Base评分单独计候选对数/批次/耗时，不当作DeepSeek回答回合。公开Journal继续已有工具观测，不为每个内部阶段新增SSE事件；结构化trace用于程序诊断与对照，完整持久工作台留后续范围。工具结果的评分含义与生成消费者显式同步，不能把RRF分数悄悄冒充旧Dense余弦。

#### 验收、提交与授权边界

- 定向离线用例覆盖唯一入口、各路排名/RRF/去重、同分、父子关系/完整组预算、Base协议/窗口/回退与Run隔离。离线测试用受控adapter，不联网下载。
- 受影响PG集成/迁移覆盖新构建、旧数据升级、发布失败保留current、同Run固定scope/配置、历史答案/引用快照可读；不无理由重跑全部历史迁移。
- 获准后使用隔离资源运行真实Milvus原生BM25、现有Embedding和选定Base，验证端到端来源/排序，记录模型冷启动/热查询分开耗时、候选数与进程内存等；实际测量与估算分别报告。固定案例比较Dense、Hybrid、补全及重排，质量未提升也如实记录。
- 仅在契约、状态或UI消费确实改变时运行对应生成/类型/消费者检查；不自动做前端重设计或全浏览器回归。不调用收费回答/重排API；真实检索/重排与受控最终回答链的验收分开描述。
- 包内按依赖连续实现，按6.1/6.2/6.3可审阅边界组织提交并遵守现有完成后推送personal/main约定；切分提交不触发重复全量验收。若实际耦合需要合并提交，按已有规则说明并取得相应合并范围反馈，不自行把所有存量变更一并带入。

用户明确开始此范围后再实施，包内不按小节重复索取开始确认；出现影响范围/模型选择/基础设施切换的实质变化才重新沟通。当前仍只有设计与已选模型，没有A包代码、安装或索引操作授权。

本轮只更新章节/交接与计划入口，检查文档差异、本地链接和格式；不运行应用测试、不加载/下载模型、不改变运行环境、数据库或索引，不commit/push。后续从本节具体范围取得开始反馈，不再反复开章或重讲已选模型。


### 6.1 实施与验收（2026-11-13）

用户明确“开始 A 包”，按6.1–6.3范围连续实现。本节已提取 `rag/protocol.py` 和 `rag/pipeline.py`，从KnowledgeService委派唯一Dense策略入口；Run固定scope、严格投影、预算与Evidence登记保持，trace归属每个KnowledgeSession，不记录正文。Runtime仍管理资源，未新增执行进程。Hybrid与重排将在同包后续接入。

实际命令：`uv run --no-sync pytest -q tests/test_knowledge_runtime.py tests/test_knowledge_cases.py tests/test_rag.py tests/test_knowledge.py --tb=short`，41通过（2新增、39既有，0跳过）；Ruff检查/格式通过。首次定向28项中21失败源于自动清理测试引用的PROFILE导出，修正显式导出后28通过，随后扩大至41；复跑不相加。未改变事务、模型调用、公开契约或数据库结构，本小节不重跑PG/浏览器/真实模型。没有新增依赖、迁移或生成物。


### 6.2 实施与验收（2026-11-13）

Hybrid布局为`hybrid-jieba-bm25-v1`，正文检索文本复用冻结embedding_text（标题+原文），同一集合同时保存Dense与Milvus原生BM25。分析器按[Milvus官方全文检索](https://milvus.io/docs/full-text-search.md)接线并真实检查：jieba+lowercase保留中文、E1024/E1025和AB-123；removepunct会删除连字符型号，已在创建业务数据前修正本轮自有空探测集合。BM25统计为集合级，并非每用户独立IDF；权限过滤仍严格限定同一个发布scope。应用内仅一份RRF，按各路全局排名融合和来源去重，工具返回score_kind/stage_scores明确分数含义。

新知识库默认Hybrid，既有知识库保持冻结配置；`documents.upgrade`默认计划、显式--apply更新该用户KB目标并经原上传事务创建同内容新构建候选，原版本/清单/源文件不改。每份重建有确定幂等键和current/pending前置条件，正在上传时拒绝覆盖；批量可部分提交，重跑跳过同内容已排队Hybrid任务，当前指针只有Worker核验发布后变化。旧Dense策略是同接口的显式能力结果，网络和坏schema不静默降级。

Run的新v2模型快照冻结RagPolicy，旧v1摘要保持；数据仍在原JSON字段，既有不可变触发器继续保护，未改变ORM/DDL所以无需新Alembic版本。构建布局枚举由正式OpenAPI生成TS，不手改消费者；Event v1与历史答案格式保持。

实际验证：定向离线82通过（4新增Hybrid用例）；`check_runtime.py tests/test_hybrid_pg.py tests/test_indexing_pg.py tests/test_knowledge_pg.py tests/test_runs_pg.py`为42通过（2新增）；`check_knowledge.py`真实CPU/Milvus/原Run/Journal/Evidence与受控回答通过（原生Hybrid，6事件，收费0），临时库与自有scope清理。前序145离线运行与最终82有重叠，不相加；旧PG夹具显式选择Dense v1，原有断言不变，新Hybrid测试单独启用。Ruff和前端typecheck通过；未重跑全仓、收费模型或浏览器，未改变页面交互。6.3将在同包接续。


### 6.3 与 A 包集中交付（2026-11-13）

用户已明确授权A包，6.1/6.2/6.3按依赖连续交付。6.3新增同Section下原chunk的单层材料补全：至少命中2个且严格超过该节可信chunk总数一半，最多6成员/累计18候选；不递归提升成父全文，不自动补相邻或上层章节的条件。目录来自同一次固定scope的不可变产物；只精确读取缺少的chunk，逐项核验原manifest，不把父全文冒充子chunk。完整组保留原片段引用，若最终6片段/知识结果/Evidence累计预算放不下整组，则回到原命中并标记limited，组内试算不提前提交E编号。畸形来源不会被当作预算不足而跳过。

Base固定`BAAI/bge-reranker-base@2cfc18c9415c912f9d8155881c133215df768a70`，只用本地safetensors、CPU FP32/batch1、512完整配对窗口，启动资源失败明确拒绝知识模式。对问题+每个原片段标题/正文评分，组排序取成员最高分并稳定打破同分；该分不代表整组覆盖或答案可信概率。超窗/评分失败保留融合/组顺序并记录稳定code、attempts、耗时和fallback，禁用对照与故障回退区分；总期限耗尽不能回退继续。模型与连接随runtime生命周期管理，共用受控单线程槽，取消等待不会提前释放实际计算槽；原4次模型请求/6工具上限未放大。新Run固定策略，旧v1 Run继续明确Dense语义。

实际验证记录（有重叠，不将复验累计成用例数）：

- `pytest -q tests/test_rag.py tests/test_hybrid.py tests/test_rag_materials.py tests/test_knowledge.py tests/test_knowledge_runtime.py tests/test_knowledge_cases.py tests/test_evidence.py tests/test_agent.py tests/test_tools.py tests/test_cancellation.py tests/test_model_control.py tests/test_document_chunking.py tests/test_documents.py tests/test_vector_store.py --tb=short`：251通过、0跳过。新增材料用例9项；随后能力声明与策略一致性、trace调整定向42/39项通过，新增启动Rerank错误参数后runtime7项通过，覆盖各自最终状态；复跑不相加。
- `check_runtime.py tests/test_hybrid_pg.py tests/test_knowledge_pg.py tests/test_answers_pg.py tests/test_tool_observations_pg.py tests/test_cancellation_pg.py`：46通过1失败。失败为全局过期恢复多扫到前序其他模块遗留Run，非同Run重复终结；随后在全新临时库执行整个`tests/test_cancellation_pg.py`，25通过。保持原断言、终态和超时不变，临时库均清理。6.2的42项PG记录保留，不冒充6.3新增通过。
- `check_knowledge.py`：最终真实CPU/Milvus/Hybrid/Base→正式工具→原Run/Journal→持久答案/引用通过，回答Provider受控、收费0，6个Journal事件和自有向量范围清理。它不是DeepSeek真实工具选择/回答质量证明。
- `check_rag.py`：自有候选策略对照最终通过，验证E1024、AB-123和E1025健康无命中、Dense/Hybrid/补全/Base四路线、补回原例外和来源身份。每路线3个样本，只作本机smoke耗时，不报告可靠p95或广泛质量提升；设备测量另存设备记录。首轮虽完成策略断言但清理报错，修正finally中连接释放后复跑发现Windows句柄签名错误；修正ctypes后完整复跑及补充编号用例通过，未改业务断言/阈值。自有临时库与候选均清理。
- 相关Ruff、格式与文档差异检查通过；`reranker status`确认固定缓存，未重复下载。6.2生成的OpenAPI/API TS在本节未改变，沿用其类型检查结果；Event v1保持。不改变前端状态/交互，不运行全量浏览器或上级SuperMew门禁，不收费调用。

开发目录升级已先隔离验收，再对本机一份已发布Dense文档执行只读计划及显式apply，Worker核验发布同内容新Hybrid版本。旧Version/源文保留，原Thread/Run/Message/Answer/Event行数与内容哈希一致；设备独立部署状态不在共同handoff重复维护。没有ORM/DDL改变，无需新迁移（仍0014）；快照与构建JSON通过原不可变保护，依赖/锁不变。

### A 包实际代码框架（2026-11-13，覆盖6.1–6.3）

| 源码 | 责任与协作 |
| --- | --- |
| [rag/protocol.py](../src/minimew/rag/protocol.py)、[model_control.py](../src/minimew/model_control.py) | 类型化策略、候选/材料组/trace；新Run冻结v2策略，旧v1摘要不变 |
| [rag/pipeline.py](../src/minimew/rag/pipeline.py)、[fusion.py](../src/minimew/rag/fusion.py) | 唯一Dense/Hybrid编排、各路排名与应用内RRF，不接管Run或发布 |
| [rag/merging.py](../src/minimew/rag/merging.py)、[reranker.py](../src/minimew/rag/reranker.py) | 单层同节完整材料、原片段Base评分/窗口与稳定回退；prepare是唯一显式下载命令 |
| [documents/profiles.py](../src/minimew/documents/profiles.py)、[upgrade.py](../src/minimew/documents/upgrade.py) | 新Hybrid布局身份、默认只读的旧KB升级；真实构建仍交给原Version/Job/Worker |
| [documents/retrieval.py](../src/minimew/documents/retrieval.py)、[artifacts.py](../src/minimew/documents/artifacts.py) | 固定scope和可信目录，核对准确目标集合/身份；旧scope保留原生命周期 |
| [vector_store](../src/minimew/vector_store) | 原生BM25与Dense读写、schema/analyzer/索引校验、精确manifest与受限fetch，显式能力报告 |
| [knowledge.py](../src/minimew/knowledge.py)、[knowledge_runtime.py](../src/minimew/knowledge_runtime.py) | 工具身份/scope和原子Evidence登记；runtime管理本地权重、连接与线程准入，trace只记计数/原因不记正文 |
| [check_rag.py](../scripts/check_rag.py)、[check_knowledge.py](../scripts/check_knowledge.py) | 自有候选对照与正式发布/Run链验收，区分真实检索/重排和受控回答 |

代表性请求：原RunExecutor从冻结快照绑定RagPolicy→knowledge_search绑定原用户与published scope→Pipeline编码/同scope两路召回/RRF→可信目录精确补全→runtime内Base评分→知识工具按完整组和累计预算试算、登记原chunk的E编号→原模型回合与答案持久事务/既有引用界面。查询不创建新Run，Worker仍是独立进程。6.4–6.6的Grader/LangGraph/改写/拆解未开始，整个第六章未结章；已知回答格式缺陷与章末异常专项仍待实施。


### 6.4 实施记录（2026-11-14）

用户明确“开始 B 包”，据此开始上方6.4–6.6具体范围；历史“待开始”是当时状态。6.4完成共享基础与材料评判，随后在同一授权内继续6.5/6.6。

正式连接点仍是`RunExecutor → ToolRegistry/Guardrail → KnowledgeService → RagPipeline`。新增[RunModelCaller](../src/minimew/execution/reasoning.py)只负责内部角色调用，复用本Run冻结Provider配置、资格检查、绝对期限与预算，不创建业务Message或独立执行器。[reasoning契约](../src/minimew/rag/reasoning.py)严格限定足够、不足、歧义、冲突、支持引用和缺口；支持引用只能来自送入Grader的实际草稿，不能根据被预算删去的候选评判。

[KnowledgeService.prepare](../src/minimew/knowledge.py)先在试算Evidence目录内生成最多8000字符材料草稿，Grader后连assessment整体核对11000字符，再一次提交Evidence；旧v1/v2继续原11000材料和原样JSON。草稿评分/失败期间不污染正式ledger。v3在Run预留事务冻结；显式`MINIMEW_REASONING_ENABLED=false`只让之后新Run选v2，不回写旧Run。12次总额度包含外层、内部及实际重试；内部先占额度再await，最多占到11，为最终回答留一次。外层最多4、工具6、检索8；内部调用并发2、至多一次重试，SDK重试仍关闭。1024/512/256 token上限均不超过原Profile上限，当前60秒Profile的总期限不变。

执行资格信号移至[control.py](../src/minimew/execution/control.py)，原repository/service导入仍可用。工具边界显式传播取消、owner丢失及存储故障，不能将它们包成一般工具失败；内部正文分片由caller私有消费，不写公开Journal。最终回答仍用原解析与原终结事务，新增提示要求说明材料不足/歧义/冲突，不放宽既有引用断言。

验证：共享基础/旧知识工具定向38项通过；12项新reasoning测试包含严格引用、旧摘要、保留最终额度、检索8次、重试尝试计数、并发2、取消释放及三个资格信号穿透（均保留断言）。B包集中回归与真实链结果在6.6记录，不能将复跑数相加。模型JSON异常不冒充无资料；真实DeepSeek费用与语义质量未测。新增测试路径为`tests/test_reasoning.py`。OpenAPI从正式路由导出并经前端生成器规范化后无差异；无需改公开Event、DDL或评测资产。



### 6.5 实施记录（2026-11-14）

[workflow.py](../src/minimew/rag/workflow.py)使用真正的`StateGraph`，按`assess → 条件边 → rewrite → assess → END`编排工具内部策略。足够、歧义、冲突直接返回结构化状态；不足才允许一次Step-back。`rewrite_used`属于Run共享预算，多次knowledge_search不能重置次数；仅内部检索式更新，最终评判目标取自原Run用户问题。数字/字母/型号锚点进行程序校验；中文实体、地点及自然语言限制依靠提示和原问题复评，不声称已有完整NER或语义等价证明。

改写候选与原候选按身份去重，新增候选优先进入有限选材，再用同一8000字符草稿规则复评；中间步骤不登记Evidence。所有查询复用第一次工具调用冻结的published scope和用户身份，仍走唯一`RagPipeline`，不新增聊天route。图不配置checkpointer，关闭自动LangSmith追踪；进程退出仍使用原Run过期/失败机制，节点恢复留第七章。

新增LangGraph1.2.22（锁文件含其官方checkpoint/prebuilt/sdk依赖，但未配置持久checkpoint）；通过`uv add`和`uv sync --dev --extra embedding --extra vector --locked`更新独立manifest/锁。`uv lock --check`解析108包，`uv pip check`106个已安装包兼容。直接图测试3项通过：足够、不足后单次改写、禁止丢弃E1024编号；真实Run/取消等集中验证见6.6。三项是本节中间实现复验，不计作包末新增三项。未下载新模型或调用收费Provider。

### 6.6 实施与B包集中验收（2026-11-14）

#### 整体位置与数据流

原Run只拥有一个用户Message、一个assistant Message和一条持久执行链；本包新增的是知识工具内部的决策层：

```text
RunExecutor（冻结配置、总期限、取消/owner、Journal、终结事务）
  → ToolRegistry / Guardrail
  → KnowledgeService（第一次固定published scope、最终Evidence提交）
    → RagPipeline（Dense/Hybrid、RRF、有限补全、Base）
    → 有预算材料草稿 → Grader
      ├─ 足够/歧义/冲突 → 返回该状态与材料
      └─ 不足 → Planner一次 → 2–4子问题（并发2）
           → 各自Pipeline/草稿/Grader → 按计划顺序去重选材
           → 按原用户问题复评
           → 仍不足且额度允许 → 全Run一次Step-back补查/复评
    → 最终草稿与assessment整体预算检查 → 一次Evidence提交
  → 原模型回答 → 原引用解析 → 原持久终结事务
```

图节点和Send分支不是独立进程或业务Run。状态、信号量、Planner/Rewrite标志和预算由当前Run持有；子任务只读scope/试算Evidence，不并发修改ledger。无持久checkpointer，不能从节点恢复；这仍是第七章边界。Graph API使用LangGraph1.2.22，最终依赖由独立uv.lock锁定。

#### 聚合、覆盖与失败处理

Planner只返回2–4个非空、去重、长度受限的检索问题；每个检索在派发前占共享8次额度，每次模型真实尝试在调用前占12次额度。超过额度明确停止，不保证60秒内走完所有路线。子问题按计划索引排序，候选按身份去重，保持完整材料组及总选材预算；完成先后不改变最终顺序。聚合Grader同时看到各方面的支持/缺口状态及实际最终草稿，再评判原用户问题；子问题曾足够不能代替聚合后的覆盖结论。

受控Provider/Embedding/Vector子任务失败保留稳定码；部分成功继续汇总，最终assessment明确不足及失败方面；所有子任务失败则工具失败，不能变成无知识。取消、执行资格丢失、存储故障和总预算耗尽不当作普通子任务失败吞掉。一次Planner/Rewrite标志跨本Run多次工具调用共享，重试也计模型总额度。中文实体语义保持依靠提示与原问题复评，程序只严格校验数字/字母/型号锚点，不声称形式化语义保证。

新增源码：[workflow.py](../src/minimew/rag/workflow.py)负责节点/条件边/Send和聚合，[reasoning.py](../src/minimew/rag/reasoning.py)负责严格角色结果，[RunModelCaller](../src/minimew/execution/reasoning.py)负责私有调用；[KnowledgeService](../src/minimew/knowledge.py)仍拥有范围和Evidence，[RunExecutor](../src/minimew/execution/service.py)仍拥有生命周期。[test_reasoning_graph.py](../tests/test_reasoning_graph.py)覆盖共享标志、乱序汇总和部分/全部失败；[test_reasoning_pg.py](../tests/test_reasoning_pg.py)覆盖真实PG中的原问题、原快照、原Message、私有输出与取消终态。Grader与最终回答复用原冻结DeepSeek配置，关闭思考；没有新增模型缓存或收费调用。

#### 实际执行与结果

- 离线集中：`uv run --no-sync pytest -q tests/test_reasoning.py tests/test_reasoning_graph.py tests/test_agent.py tests/test_knowledge.py tests/test_rag_materials.py tests/test_model_control.py tests/test_deepseek.py tests/test_execution_retry.py tests/test_evidence.py tests/test_model_messages.py`当时169通过；SDK畸形响应注入产生1条预期Pydantic序列化警告。
- 补充8项reasoning测试后12项通过；最终caller校验/失败码改变后，定向`tests/test_reasoning.py tests/test_reasoning_graph.py tests/test_tools.py`44通过（18重复、26个既有工具测试本包首次执行）。6.6再补跨调用共享、乱序去重及材料组seed去重3项，最终图9项全部通过；末次图/材料组定向18项通过（9图+9既有材料组，重复不叠加）。最终有效离线覆盖206个不同用例（21新增、185既有），不是把复跑总数相加。
- 真实PG：`uv run --env-file .env --no-sync python scripts/check_runtime.py tests/test_reasoning_pg.py tests/test_agent_pg.py tests/test_knowledge_pg.py tests/test_runs_pg.py tests/test_answers_pg.py`46通过（4新增、42既有）；caller最终校验修改后，仅重复4项B包PG，全部通过。覆盖v3评分、改写、分解、等待内部模型时取消，以及已有scope隔离、持久答案/快照与原Run行为。自有临时库均清理，不写开发历史。
- 6.4/6.5为审阅边界分别执行38项共享基础/旧知识工具、3项图路线，均通过；它们是中间代码检查，计数包含在最终有效覆盖中，不计新增。合计252个不同pytest用例（25新增、227既有），所选0 skipped。
- `uv run --env-file .env --no-sync python scripts/check_knowledge.py`真实CPU/Milvus/独立Worker发布/正式API与Run/引用链多次通过；末次覆盖最终代码，2次受控外层模型+1次内部Grader=3次总请求，6条公开事件、1条持久引用，内部模型正文未出端。临时库、自有scope精确清理；收费模型0。这是接线证明，不是真实DeepSeek质量评测。
- 定向Ruff check/format通过；`uv lock --check`、`uv pip check`通过。正式路由OpenAPI导出、`npm run contracts`后无生成差异，`npm run typecheck`通过；Event真源未变；首次字节检查因Windows检出CRLF失败，从真源生成LF后漂移检查通过，并为两份Event生成物增加Git的LF检出约束（生成物本身无Git语义差异）。无新DDL/迁移、HTTP/Event版本、页面行为或RAG评测资产变更。

本包先因本机Docker未启动出现PG/真实链ConnectionRefused，不能计通过；修复环境后重跑取得上述有效结果。本机修复与部署只记录设备文件。新乱序测试首次将底层retriever入参误当原查询，1失败/7通过；按真实接口改为通过受控编码标记识别查询，保持顺序/去重断言后8通过。最终审查又新增seed回退回归并真实复现重复片段：成员去重后未过滤旧seeds，预算回退可重新选入重复命中；修复为同步过滤seeds，图与材料组18项通过。未放宽旧业务断言。

未运行：整个MiniMew/上级全量回归、全量浏览器、收费DeepSeek质量、完整RAG评测平台、GPU/M3、Checkpoint恢复。原因分别为影响范围局限工具内部/共享预算，前端行为未改，以及后续章节/未授权收费边界。A包的全候选本机benchmark未重复，因为索引、编码、补全及Base源码未变。第六章尚未结章，6.7与既有未引用导语缺陷、章后完整异常反馈仍待后续范围，不标记已修复。

### C包：6.7具体范围（2026-11-14，待明确开始）

B包完成后用户回复“继续”。本轮核对现有实现与A/B运行报告，继续整理6.7工作包及整体架构说明；未将该反馈扩展为尚未列明范围的应用编码授权。B包最终提交c4b0e78已推送个人仓库main。下列范围获得明确开始反馈后连续实施，不逐案例重新确认。

#### 在整体架构中的位置

A包的Pipeline负责从固定published scope中寻找、补全和排序可定位材料；B包的Workflow负责根据实际材料决定直接返回、单次改写或有限拆解；RunExecutor仍负责期限、取消、持久Message/Event和终态。C包是在这些正式入口外增加显式验收入口和可重现案例，不增加运行时聊天route或第二套检索实现。

检索模型和Grader回答不同问题：前者的材料是否包含必要规则，可以用固定原文/span与人工写明的预期条件核对；后者的覆盖判断是否可靠，需要真实模型输出与案例预期对照。受控Grader/Planner只证明编排接线，不作为语义质量裁判，也不把它的预设输出计为策略收益。

#### 一次授权后的实施顺序与验收

| 顺序 | 具体工作 | 可审阅结果 |
| --- | --- | --- |
| C.1 固定案例 | 在MiniMew内保存小型案例资产：编号/型号精确匹配、语义问法、供餐例外、跨章节条件、不足/歧义/冲突；写明预期原文依据、必要限制、可支持部分及不能推出的结论 | 用例预期来自固定资料与人工明确规则；案例版本/内容指纹可核对，不照抄本次模型输出当答案 |
| C.2 正式入口对照 | 扩展现有显式验收脚本，复用RagPipeline比较Dense、Hybrid、补全、Base；用KnowledgeService/原Run验证B包直接评分、一次改写、有限拆解到最终Evidence/答案引用 | 相同资料与问题下记录最后实际提供的材料、必要条件覆盖/遗漏、引用定位、稳定错误/降级；不另写业务算法或直接改开发库 |
| C.3 成本与失败 | 记录本地检索阶段耗时、真实执行的模型尝试/重试与检索数、总期限及有限路由；覆盖尚缺的预算后覆盖和受控部分/全部失败协作 | 冷加载与热运行分开；样本量和统计方法明确，小样本不宣称可靠p95；受控模型耗时不冒充真实DeepSeek时延，收费明确为0 |
| C.4 集中结章 | 汇总A/B最终状态已覆盖的有效检查，只补新增脚本/案例和此前未覆盖的跨模块链路；保存第六章总结、真实模块框架与代表性请求走读 | 给出各策略收益/退化及证据边界、实际运行命令与结果；6.7必要检查通过后按持续授权单独commit/push到已核对的个人main |

具体输入是版本化固定资料、问题和明确的期望依据；输出是可重现案例、机器可读对照报告及中文结章说明。运行报告包含scope/资料指纹、模型revision、冻结策略、样本数、材料标识、调用数与阶段耗时；无Secret、私有推理或整份文档日志。设备耗时/内存、容器状态和启动故障继续只写设备文档，共同总结保存可移植结论与复现入口。

已有`check_rag.py`只比较A包四条候选路线、每路线3次样本；已有`check_knowledge.py`在最终B代码下验证了正式发布→原Run→持久引用的充分材料路线。C包补的是固定案例和路线之间尚缺的协作/覆盖报告，条件未变的历史结果标明原记录后复用，不机械重跑B包252项或全仓测试。脚本/案例新增或修正行为先跑定向离线检查；真实检查仅用显式临时PG、自有scope、本地缓存模型并精确清理，不消费开发库任务。

默认继续真实本地Embedding/Base/Milvus与受控回答/Grader/Planner，不调用收费DeepSeek、不新增模型/依赖或下载，不建立第八章Dataset/Job/Gate平台。报告明确“真实模型语义质量及线上成本尚未测”，不能因编排验收通过声称所有复杂问题都能正确回答。若以后要做真实DeepSeek小规模质量对照，先单独列调用/费用上限和具体案例，再获对应授权。

不纳入本包：第七章Checkpoint/HITL、持久暂停恢复、新模型/GPU、完整模型选择UI、已知答案未引用导语修复及完整异常反馈专项。第六章结束后必须接续安排既定异常反馈专项；完成C包不等于这些问题已修复。当前只保存具体方案，结章总结和验收结果仍留空，待用户明确“开始C包”（或同义开始反馈）。

### 6.7 实施与C包验收（2026-11-14）

用户明确“开始 C 包”，授权上方C.1–C.4范围连续实施；实际完成固定案例、正式发布资料对照、受控Run路线、可移植观察资产及本章总结/框架。本次没有修改生产RAG、执行器或引用校验行为。

#### 案例、入口与观测口径

[chapter06-policy.md](../examples/rag/chapter06-policy.md)为固定示例资料，[chapter06-cases.json](../examples/rag/chapter06-cases.json)含11个带预期依据/不能推出结论的案例；输入按UTF-8/LF规范化，案例与源文本共同指纹为`48a768f6148590086560d8f9ab4665abcf5d5f53a1b78accf60be49f9dcdbda4`。预期锚点唯一定位原文，覆盖依据实际[start,end)区间并集核对，拒绝原文/hash伪造；不把相邻片段之间的空洞或重复片段计为覆盖。覆盖只表示必要原文被提供，不等于答案正确或Grader准确；无已知答案的案例记coverage=null，不能奖励为100%。

[check_rag_suite.py](../scripts/check_rag_suite.py)创建隔离临时PG与自有上传，通过正式API→独立Worker发布同一资料，再复用RagPipeline与KnowledgeService.prepare比较7案例×4路线，统一按v3材料预算选择；另通过原API预留Run、原RunExecutor/Registry/KnowledgeService/Workflow/终结事务验收全部11案例。只替换Provider为显式预置路由/失败，无真实DeepSeek调用。完成标准包含精确向量清理和临时库删除；模型资源加载失败时清理不再依赖重载模型。

完整本机报告在忽略的`.artifacts/rag-suite-check.json`；显式`--write-observation`生成[共同观察资产](../examples/rag/chapter06-observation.json)，白名单保留版本指纹、模型revision/策略、原文跨度/覆盖缺口、稳定错误及尝试数，不含设备时延、运行身份、Secret或原始模型消息。它是此次观察，不是自动质量Gate或第八章baseline系统。BM25仍受共享集合统计影响，不能保证其他时点/设备分数完全相同。

#### 实际收益、无收益与退化

| 固定案例 | Dense最终材料 | Hybrid / 补全 / Base最终材料 | B包受控路线的观察 |
| --- | --- | --- | --- |
| E1024＋AB-123 | 必要锚点1/2 | 三路线均2/2 | 直接评分、3次模型尝试、1次检索 |
| 餐费额度与非自动发放 | 2/2 | 均2/2 | 3次模型尝试；此次没有证据说明复杂策略优于Dense |
| 公司/接待方供餐例外 | 2/2 | 均2/2 | 一次改写，5次模型尝试、2次检索；未增加本例锚点覆盖 |
| 餐费＋住宿＋提交期限 | 2/3 | 三路线均3/3 | 拆解后最终只保留2/3；7次模型尝试、3次检索，出现退化 |
| 培训歧义、同时有效通知冲突 | 必要锚点均齐 | 三路线均齐 | ambiguous/conflicting是预置模型输出，不能证明真实模型能识别这些情况 |
| 火星补贴未知 | 有近邻材料，coverage=null | 仍有近邻，coverage=null | 预置不足→一次改写→仍不足；不能从非空结果推出金额 |

跨章节退化被保留为观察：B的新增候选优先、确定性组选择和final_k/材料预算会改变实际可见材料；本例直接Base路线齐全，拆解聚合后遗漏提交期限。报告标记`sufficient_with_missing_anchors=true`，即受控“足够”与人工必要锚点缺口不一致。没有为让结果好看而改查询、放大预算、减少预期锚点或把预置Grader当裁判；生产策略未在C包悄然改动。今后可另列“聚合时保护已覆盖方面”的改进范围，并用同一案例对照；不能在本章宣称所有复杂问题已可靠回答。

部分子Grader失败的正式Run成功返回明确insufficient及PROVIDER_ERROR缺口，9次模型尝试、4次检索；两个子Grader全失败时Run以TOOL_EXECUTION_FAILED失败，5次模型尝试；单次可重试Grader失败后4次尝试完成，重试计数1；入口/子Grader重试耗尽内部额度时11次尝试后以AGENT_BUDGET_EXCEEDED失败，未越过12总额度或污染Evidence。11场景共58次受控模型尝试、24次真实Pipeline查询，收费0；9成功/2预期失败，均只创建原Run的两个业务Message，不写内部正文分片。

#### 实际命令、失败及复验

- 新增`tests/test_rag_cases.py`最终17项通过，涵盖案例/观察指纹、无效预期/路由、跨度并集/缺口/伪造、未知答案不计满分、小样本统计、真实尝试计数、入口/聚合Grader只看预算后幸存材料、导出白名单。首次13项收集因脚本目录不在pytest路径失败，按现有脚本测试入口修正后13通过；补2项后15通过；白名单测试首次夹具缺policy，1失败/15通过，补全实际契约后16通过；最后加入观察指纹检查17通过。保留原业务断言，复跑不计新增。
- `uv run --env-file .env --no-sync python scripts/check_rag_suite.py`首次因本机Base加载MemoryError失败，已清理自有scope/临时库，不能计通过。释放本次开发服务模型内存后，默认每路线3样本的28项材料观察+11正式Run场景通过；为覆盖最终草稿一致性/可移植导出，再次默认3样本通过。
- 最终清理/报告收尾完善后，`uv run --env-file .env --no-sync python scripts/check_rag_suite.py --samples 1 --write-observation`再次通过28材料观察+11场景、1自有scope精确清理及临时库删除。最终代码使用1样本，因此selection_stable=null，不拿单样本证明排序稳定；原3样本结果留在本机记录，不相加冒充更多案例。模型语义质量/真实DeepSeek时延未测；本地p50/最大值仅保存设备文件，不报告可靠p95。
- `uv run --no-sync ruff check scripts/rag_cases.py scripts/check_rag_suite.py tests/test_rag_cases.py`与相同文件format检查通过；纯文档差异/本地链接核对通过。A/B最终生产代码、依赖、配置、数据库结构、协议消费者未变化，按实际影响复用对应验收记录，不重跑B的252项或上级全量，不重复浏览器/模型下载。17个新pytest与真实CLI观察分开报告，不相加称作pytest数量。

无新增依赖、迁移、公开HTTP/Event Contract、前端状态或正式评测平台；仅新增案例、验收脚本/测试、观察资产及文档。设备独立的资源故障、服务暂停/恢复和测量在devices记录。本章基础链已结章，真实DeepSeek质量、聚合覆盖改进、未引用导语缺陷及章后完整异常反馈保留为明确边界；第七章未开始。

## 结章文字总结（2026-11-14）

覆盖6.1–6.7。本章在既有持久Run与已发布文档之上完成了唯一RAG策略入口：Dense作为基线，显式Hybrid构建使Milvus原生BM25与向量在同scope内召回；RRF融合名次，单层有限补全保护上下文，固定Base重排候选；最后按实际材料草稿评分，必要时一次Step-back或一次有限Planner/Send，聚合后仍评判原用户问题。策略、内部输出上限和全Run额度随v3快照冻结；旧v1/v2摘要和原语义不变。

主要取舍是把“找什么材料”和“一次执行是否完成”分开：Pipeline/Workflow只交付候选、覆盖状态与可定位原文；KnowledgeService拥有用户scope和最终Evidence提交；RunExecutor/Repository拥有执行资格、持久Message/Event及终态。向量分数、RRF和Base分数不是答案可信概率；Grader也可能判断错，预设模型接线不能替代真实质量对照。完成图或断开SSE都不能提前宣布Run完成。

一条实际请求先由API事务预留Run及两个Message并冻结模型，Dispatcher/Executor领取资格；模型提出knowledge_search后Registry/Guardrail校验，KnowledgeService取得固定published scope；Pipeline检索并组织材料，Workflow用共享60秒Profile期限、12模型尝试/4外层/6工具/8检索额度编排；只有最终预算通过的材料一次进入Evidence，随后原模型回答、引用解析、原持久终结事务保存结果，Journal经SSE投影到前端。子问题是内部任务，不是多个业务对话；内部模型文本不写公开正文。

文档候选仍通过独立Worker构建/核验/原子发布，读current不会看到失败candidate。Hybrid仅能力不兼容可显式Dense降级，Base错误/超窗不截断、保留顺序并记录code/attempts；Provider故障不当作健康无知识。部分子任务失败有可用材料但明确缺口，全部失败、取消、owner/存储故障与预算耗尽遵循各自边界。没有配置持久checkpointer，进程退出不能恢复到图中某个节点。

实际验证按A/B/C各节记录保留，不合并重叠用例数；C确认Hybrid在固定编号/跨章节案例扩大必要材料覆盖，也确认补全/重排未在所有例子继续提升，以及拆解可能降低最终覆盖。模型费用本次为0，未做真实DeepSeek质量或生产时延评估。公共Contract/数据库仍沿用既有版本；本章未建立第八章评测平台。用户已通过自然讨论选择了模型/预算和工作包，不预填“完全掌握”。

下一步先落实已经约定的完整异常反馈专项，并另行明确未引用导语缺陷和聚合覆盖改进的范围；第七章将在原Run/Checkpoint归属上扩展持久暂停、恢复和HITL，不能用新发一条用户Message模拟恢复。实际反馈、待办与授权以交接记录为准。

## 本章结束时的代码框架（2026-11-14）

| 模块 | 职责及上下游关系 | 数据/状态归属 | 源码 |
| --- | --- | --- | --- |
| 发布目录与升级 | 既有Worker发布；升级复用Version/Job/清单/current路径，为Pipeline提供固定范围 | PG保存current/产物；Milvus保存候选向量 | [retrieval.py](../src/minimew/documents/retrieval.py)、[upgrade.py](../src/minimew/documents/upgrade.py) |
| 候选协议/冻结策略 | 定义候选身份、组、阶段分数、RAG策略与快照版本 | 策略冻结进Run；trace仅本次调用 | [protocol.py](../src/minimew/rag/protocol.py)、[model_control.py](../src/minimew/model_control.py) |
| 唯一候选Pipeline | 编码→同scope召回→RRF→有限补全→可选Base→按组选择 | 不拥有Evidence或Run终态 | [pipeline.py](../src/minimew/rag/pipeline.py)、[fusion.py](../src/minimew/rag/fusion.py)、[merging.py](../src/minimew/rag/merging.py)、[reranker.py](../src/minimew/rag/reranker.py) |
| 知识工具 | Registry handler固定用户/版本scope；投影原文/span/hash，生成预算草稿并最终一次提交Evidence | KnowledgeSession与EvidenceLedger每Run私有 | [knowledge.py](../src/minimew/knowledge.py)、[evidence.py](../src/minimew/evidence.py) |
| Workflow/角色契约 | StateGraph条件边、Send子任务、评分/单次改写/有限拆解与聚合 | 图状态局限本次工具；共享计数在Run预算；无持久Checkpoint | [workflow.py](../src/minimew/rag/workflow.py)、[reasoning.py](../src/minimew/rag/reasoning.py) |
| Run模型调用/执行 | 内部角色复用Provider，受限重试/私有输出；外层Executor管理资格/取消、额度和模型工具循环 | RunModelCaller/RunBudget本Run持有，PG事务拥有持久事实 | [reasoning.py](../src/minimew/execution/reasoning.py)、[service.py](../src/minimew/execution/service.py)、[repository.py](../src/minimew/execution/repository.py) |
| 重资源生命周期 | API加载只读本地编码/Base与向量连接，同步操作单线程准入，退出排空后关闭 | 模型可跨Run共享，query/材料/预算不共享；Worker另进程 | [knowledge_runtime.py](../src/minimew/knowledge_runtime.py) |
| 显式验收与案例 | 复用上述真实入口，仅在验收进程注入预设Provider，核对原文和协议、保存观察 | 固定资料进Git；设备耗时与临时报告不混入共同进度 | [check_rag_suite.py](../scripts/check_rag_suite.py)、[rag_cases.py](../scripts/rag_cases.py)、[cases](../examples/rag/chapter06-cases.json) |

代表性“餐费、住宿和提交期限”走读：API预留/冻结→Executor外层模型→Registry授权→knowledge.py固定scope→pipeline.py提供实际材料→workflow.py初评/plan/Send→各子任务同一Pipeline→按计划索引去重选材→原问题复评→knowledge.py一次Evidence提交→Executor调用最终回答→evidence.py解析引用→Repository写原Message/终态/Event。C的此例在聚合选材时遗漏期限，故不能从“每个子任务有材料”直接推导最终覆盖完整；查看观察资产的跨度/缺口可定位这一点。

本章将原知识工具内候选检索职责提取到rag模块，保留正式工具与原Run入口，没有为策略新增第二条聊天路径。C只新增显式验收资产，无生产目录重构。后续持久Checkpoint/HITL与评测Job/Gate仍属第七/八章，不在本框架中伪装成已实现模块。
