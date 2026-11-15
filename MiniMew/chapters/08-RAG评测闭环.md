# 第 8 章：RAG 评测闭环

当前学习进度、用户真实反馈和应用编码授权只见 [交接记录](../AGENT_HANDOFF.md#当前检查点)。本章是 2026-11-15 保存的开章方案，拟议模块和验收尚未实现；第七章已交付的恢复能力见[上一章总结](07-HITL与故障恢复.md#结章文字总结)。

## 开章计划

计划日期：2026-11-15。

### 本章目标与范围

让“问答能运行”进一步成为“可以说明在哪些问题上正确、哪里退化、结论来自哪次执行”。在相同人工期望与明确可比的运行条件下，保存每个 Case 的观察事实、评分、错误和报告，避免只看一次回答或几条通过数字决定策略优劣。

第六章已有 11 个固定案例、真实检索对照和受控推理观察；第七章已有持久暂停、恢复与资格隔离。本章沿用这些正式执行能力，逐步新增评测契约、纯评分器、持久 Evaluation Job/Case、独立 Evaluation Worker 和最小评测工作台。Evaluation Job 不等于聊天 Run，不以另发用户问题、伪造 Thread/Run 或复制检索图实现评测。

本章以 MiniMew 自身代码为实现边界。上级 [领域语言](../../CONTEXT.md)、[ADR-0014](../../docs/adr/0014-rag-evaluation-contract.md)、[ADR-0024](../../docs/adr/0024-model-control-and-rag-evaluation-runtime.md) 只作设计参考，不导入上级业务包，也不照搬其完整管理平台。

### 本章学习节奏与工作包

遵循[功能链分包约定](../AGENTS.md#开发批次与教学小节分离2026-11-13-新增)。教学围绕“标准是什么→事实怎么采→谁来评分→报告如何决策”，工程接线集中推进，不逐函数讲语法或提简单判断题。

- **A 包，8.1–8.3：离线评测契约与评分闭环。** Dataset/Observation/Provenance/Report/GatePolicy 真源、确定性指标、baseline 可比性、离线 CLI、静态夹具及测试。模型、数据库、Milvus、Worker、浏览器均不是本包的运行前置。静态评分通过只证明评测系统按规则计算，不代表真实问答质量。
- **B 包，8.4–8.5：持久真实执行与独立评分。** 先明确共享计算接口和执行上下文，再做 Evaluation Job/Case 事务、独立 Worker、隔离索引、冻结配置与有界 Evaluator Judge。沿用既有模型与 CPU 缓存作为候选，不默认下载新模型或运行收费评测；真实 Provider 范围与费用上限须在该包开始前另行明确。
- **C 包，8.6：最小工作台与集中验收。** 数据集导入/校验、Job 发起/进度/取消、Case 错误与报告查看、baseline 比较入口，以及章节总结和实际源码框架。界面沿用现有账号作用域状态，不扩成全量模型管理中心。

以上是拟议分包，分别取得明确开始反馈后才创建对应应用、测试或资产文件。包内按依赖连续实现；小节提交/推送和测试触发仍遵守 AGENTS，不因切分提交重复验收。

### 子项安排

| 子项 | 目标 | 依赖和实施顺序 | 技术职责 | 必要验收 |
| --- | --- | --- | --- | --- |
| 8.1 | 版本化 Dataset/Case、人工期望与内容 fingerprint | 首先固定评价单位和分母 | 沿用 Pydantic、规范化 JSON、SHA-256；不把受控模型输出当人工答案 | ID 唯一；未知字段、缺失/矛盾期望拒绝；内容变化必变 fingerprint；时间/本机路径不进入内容身份 |
| 8.2 | Observation、来源与确定性评分 | 依赖 8.1，先事实后聚合 | 纯 Python 指标；事实投影与评分拆开 | 缺 Case/重复 Case/无效数值拒绝；无 gold 指标显式 unavailable；Provider 故障不变为空知识 |
| 8.3 | baseline、Gate、离线 JSON/中文报告 | 依赖 8.1–8.2，完成 A 链 | 纯评分服务与 CLI；按真源生成 schema | 同输入可重建同报告；不兼容 fingerprint/provenance 拒绝门禁；关键失败和缺失结果不能被均分掩盖 |
| 8.4 | Job/Case、冻结执行快照、独立 Worker 与正式 RAG Adapter | 先确定共享计算接口，再迁移/调度 | 沿用 SQLAlchemy/Alembic、PG lease/owner、正式检索图与 Provider | HTTP 不长跑模型；Case 状态/进度/终态原子；旧 owner 拒写；不重复未知外部调用或覆盖已提交 Case |
| 8.5 | Evaluator 角色、结构化 Judge 与人工校准 | 依赖 8.4；离线先验证 Judge 契约 | 沿用既有 Provider；独立冻结评估角色、提示版本与评分量表 | Judge 超时/格式错误显式记录；不拿在线 Grader 自评替代质量证据；人工抽样复核 |
| 8.6 | 工作台、历史报告、取消与端到端验收/结章 | 依赖 B 的持久 API | 沿用 Vue/Pinia/类型契约、定向 HTTP/浏览器检查 | 刷新可读进度；账号切换清理状态；取消/失败与质量分数分开；保存最终总结与代码职责 |

### 已有资产能复用到哪里

| 已有入口 | 已经解决的职责 | 本章需要补的部分 |
| --- | --- | --- |
| [chapter06-cases.json](../examples/rag/chapter06-cases.json) / [rag_cases.py](../scripts/rag_cases.py) | 固定问题、原文锚点、受控查询/规划/故障模式与字面覆盖检查 | 人工期望独立于编排脚本；质量案例和故障注入案例分开统计；新增评测 schema，不改写历史案例/观察 |
| [check_rag_suite.py](../scripts/check_rag_suite.py) | 真实 CPU/Milvus 检索、受控模型编排与白名单观察导出 | 正式 Evaluation Adapter/Job、稳定来源身份、结构化评分和可比 Gate；历史受控结果不能改名为 live 质量结果 |
| [knowledge.py](../src/minimew/knowledge.py) / [rag/workflow.py](../src/minimew/rag/workflow.py) | 正式知识工具、原 scope/Evidence、检索/评判/改写/澄清 | 评测观察正式接口，不在 Adapter 复制图算法 |
| [execution/reasoning.py](../src/minimew/execution/reasoning.py) / [execution/tools.py](../src/minimew/execution/tools.py) | 当前 Run 绑定的模型内部调用、ToolContext、预算与权限 | B 包需明确共有计算上下文的抽取；现有接口显式持有 Run/Thread 身份，不能直接把 Job ID 塞进 Run 字段冒充复用 |
| [model_control.py](../src/minimew/model_control.py) | 当前 Answer Profile 与 Run v1–v5 冻结目录 | Evaluator 角色和 Job 执行快照尚未实现；B 包讨论如何冻结，不回写历史 Run |
| [execution/repository.py](../src/minimew/execution/repository.py) / [documents/jobs.py](../src/minimew/documents/jobs.py) | 已有聊天/索引域的锁顺序、资格、取消与事务模式 | 复用设计，不让这两个 Repository 同时拥有 Evaluation Job；后者有自己的正式持久状态 |

### 整体位置与数据归属

```text
人工标注 Dataset + 固定语料/预期
                ↓
          校验 / fingerprint
                ↓
         Observation 输入接口
         ↙                  ↖
静态受控观察夹具       Evaluation Job → 独立 Worker（B 包）
                            ↓
                  正式 RAG/回答/澄清计算接口
                            ↓
                  事实投影 + Evaluator Judge
         ↘                  ↙
           同一个纯评分器
                ↓
      指标 / 切片 / baseline 比较 / Gate
                ↓
         Report → CLI / 工作台
```

Dataset 保存人工标准；Observation 保存某次执行的事实；Judge 是对事实的一个评分来源；Report 汇总事实与指标；baseline 是经审查选定、由评分器生成的参考报告；Gate 是“哪些条件允许接受结果”的政策。这些对象分别版本化，不让 UI、脚本输出或当前模型配置成为第二套标准。

在线 **Grader** 处在图内部，决定材料足够、不足、歧义或拆解；评测 **Evaluator** 在候选答案产生后，结合独立期望与受控证据判断正确性、完整性和依据。两者可以使用同一候选 Model Profile，但角色、输入、提示/量表和预算分开，不能因此声称评分无偏或模型彼此独立。初期不需要训练模型或引入评测 SaaS；模型角色选择在 B 包明确。

### 评分与可比性的首版建议

1. **先固定评价单位。** 以 Case 为单位，区分普通质量、关键质量和运行故障注入切片。第六章的 mode/queries 是受控编排输入，不能直接拿来要求真实模型必须走同一路线；期望应描述允许的结果、必须覆盖的事实、禁止断言和必要的补充信息。
2. **指标必须有可审查 gold。** 首版优先原文锚点/区间覆盖、结果符合率、HITL 最终完成情况、故障/缺失率和调用次数。跨度覆盖只能证明材料被提供，不能证明回答理解正确。排名指标须先有明确 gold relevance 和排序单位；没有标注时 MRR/nDCG 等为 unavailable，不用临时猜测凑出分数。
3. **分母不静默变化。** 报告保留 eligible/evaluated/missing/error 数量；没有 gold 的 Case 不计入该检索指标，却仍保留运行/结果观察。执行失败不伪装 NO_KNOWLEDGE，也不能通过丢掉失败 Case 提高平均分。HITL 需要补充后命中标注最终结果，停在等待不算完成。
4. **来源分层。** A 包静态夹具标记 contract_smoke；B 的真实本地检索加受控回答仍明确 controlled；真实生成/Judge 才按实际路径记录 live 来源。静态或受控报告不能通过要求真实语义质量的 Gate。观察中不持久化材料全文、endpoint、Secret、原始 Provider payload、异常原文或私有推理；Judge 临时所需原文受控传入，公开输出只留数值、简短理由和稳定证据身份。
5. **可比性先校验后算差值。** Dataset 内容身份之外，固定 corpus 内容、评测 schema/评分与 Judge 提示版本、源码/依赖身份、Embedding/Reranker revision、索引 manifest 和冻结模型/策略。设备绝对路径、随机资源 ID、时钟不进可移植 fingerprint；真实执行资源的私有定位与内容身份分开。首版 Gate 严格校验已声明可比维度；刻意换策略/模型的实验列明差异，不默认成为同一基线的发布结论。
6. **检索 scope 不等于 BM25 统计隔离。** 现有第六章脚本明确共享 collection 的 BM25 统计仍受其他语料影响。B 包真实评测须明确专属 collection/索引及语料绑定和自有资源清理，不能只冻结 namespace 就宣称结果可复现；开发知识库不作为默认评测沙箱。
7. **Gate 与执行成功分开。** 正常完成的评测 Job 可以得到不达标报告；Job failed 表示未能完成评测。关键案例、缺失观察、来源不符与分母下降需要显式规则，不能被总体均分抵消。阈值在 A 包根据标注与量纲明确，不为让当前实现通过倒推标准。
8. **Judge 不代替人工标准。** B 包先定义严格结构化量表与失败语义，再有界调用；独立记录 Judge 预算/失败，不退还或放大生成侧原有额度。模型给出的正确/有依据分数仍需人工抽查校准，字符重合不能冒充语义正确率。

### 代表性请求与失败场景

以原固定问题“出差餐费、住宿上限和提交期限分别是什么”走读：Dataset 的人工期望保留 150 元、350 元、返程后 5 个工作日及必要限制；候选实现可以直接检索或拆解，但最后必须依据正确材料覆盖所有方面。Worker 调正式图得到材料和候选答案，Adapter 记录最终材料身份/覆盖/错误，Evaluator 另行评价答案。若中间检索找到三方面、最终合并只留下两方面，报告必须在最终覆盖/完整性中体现缺口，不能被在线 Grader 的 sufficient 抹掉。

A 包用静态 Observation 模拟上述不同事实，只检验评分器和 Gate 算得是否正确；不运行检索。Dataset 变化后旧 Observation 拒绝，重复/缺失 Case 拒绝或按明确错误事实处理，不能默默补零。B 包 Provider/PG/向量故障保持 typed error；Judge 失败保持 Judge error，不转成模型答错或健康无知识。取消和 owner 失效以各自持久 Job/Case 事务裁决；未知结果的外部调用不盲目重放，具体恢复边界在 B 包开始前落定。

### A 包的具体实施与验收范围

拟新增 MiniMew 自身 evaluation 业务模块和正式离线 CLI，资产与 schema 使用独立版本化目录；这是编码范围提案，本轮不创建这些文件。实施顺序为 8.1 契约/人工期望 → 8.2 纯评分与严格输入校验 → 8.3 报告、可比性和 Gate/CLI 接线，连续完成后集中验证。

沿用 Pydantic 与标准库即可，不计划新增生产依赖。schema 从 Pydantic 真源生成；静态 Observation 是明确标记的测试夹具，baseline 必须由同一评分入口生成，不手调结果。第六章案例、观察和第七章代码保持原状，来源不同不静默双读混用。

必要检查：严格契约和稳定 fingerprint、Case 对齐、指标边界/分母、unavailable、故障与健康空结果区分、provenance/baseline mismatch、关键回归/缺失 Gate、JSON/Markdown 确定性、schema 漂移、CLI 成功/拒绝退出码与报告脱敏。纯离线执行不得隐式加载 CPU 模型、访问 PG/Milvus、下载资源或要求真实 Secret；若发现必须改在线协议或数据库，先说明并划回 B 包讨论。

A 包不新增数据库表、API、Worker、模型角色调用或 UI，不运行真实质量评测和收费调用。必要测试范围限于新增纯模块、CLI/生成器和直接消费者；不会因章节编号触发整个 MiniMew 或上级全仓门禁。

### 计划调整

2026-11-15：将 LEARNING_PLAN 的五项建议细化为 8.1–8.6：单列 baseline/Gate 便于 A 包离线端到端交付；真实 Job/Worker 与 Evaluator 作为 B 包，工作台/结章为 C 包。增加当前源码暴露的 Run 身份耦合与共享 BM25 统计边界，避免把 Adapter 或 namespace 误当作完整隔离。该计划没有改变在线行为，也不构成任何工作包的编码开始反馈。

## 结章文字总结（完成后填写）

尚未实施。结束时按真实结果填写：交付能力、核心取舍、完整数据流、失败/复验记录、离线与真实质量证据、限制和第九章衔接；不提前填写模型质量通过或用户理解通过。

## 本章结束时的代码框架（完成后填写）

本轮只列已有 seam，未创建 Evaluation 应用模块。每包完成后按实际源码补职责、状态归属与代表性请求；章末集中保存最终目录/模块协作和结构整理记录，不将上方拟议模块画成已实现。
