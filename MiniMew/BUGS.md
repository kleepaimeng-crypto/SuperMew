# 开发与使用体验 Bug 记录

记录实际开发、部署和使用中发现的问题。每项使用稳定编号，保留首次现象与失败证据；后续补充根因、修复方法、影响范围和实际验证。状态区分待定位、已定位、修复中、已修复待部署、已验证。未经执行的检查不能记为通过；代码修复与本机数据修复分别记录。不得保存密钥、原始凭据或整份用户文档。

新增记录包含：发现日期/环境、现象与影响、复现条件、错误码及关联任务、根因、修复方法、历史数据处理、验证结果、提交和剩余限制。其他已知问题仅在有具体证据时追加，不把功能规划当Bug。

## BUG-012：云上 HTTP 部署后点击「上传并构建」无反应且服务端无请求日志

- 发现：2026-11-26，云上公网 IP HTTP 部署（`http://47.116.122.1`）。状态：**代码已修复，待前端验证与云上重新构建部署**。
- 现象与影响：在文档页选择 `.md` 文件后点击「上传并构建」，界面没有任何反应，`uploading` 与错误提示均未变化，服务端 `minimew-api` 无任何请求日志。同因还会影响所有在非安全上下文生成客户端幂等键的写操作（发消息、恢复 HITL、新建会话、创建评测）。
- 根因：浏览器仅在安全上下文（`https://`、`localhost` 或 `127.0.0.1`）暴露 `crypto.randomUUID()`。在公网 IP 的 `http://` 页面中 `crypto.randomUUID` 为 `undefined`，[文档上传](frontend/src/stores/documents.ts)、[聊天发送与恢复](frontend/src/stores/chat.ts)、[新建会话](frontend/src/stores/newChat.ts)与[评测创建](frontend/src/stores/evaluations.ts)直接调用 `crypto.randomUUID()` 抛出 `TypeError`，导致请求根本未发出。
- 修复：新增 [uuid.ts](frontend/src/uuid.ts) 的 `randomUuid()`，优先调用 `crypto.randomUUID()`，不可用时回退到 `crypto.getRandomValues()` 生成 RFC 4122 UUID v4，并替换上述 4 个 Store 中的直接调用；新增单元测试 [uuid.spec.ts](frontend/src/uuid.spec.ts)。
- 历史数据与部署：无需数据库迁移或历史数据处理；云上需拉取修复并重新执行前端构建（`npm run build`）后刷新页面生效。

## BUG-009：百炼编码的上传回执无法在网页解析

- 发现：2026-11-20，Windows 本机，数据库 `0025`。项目库切换到百炼 `text-embedding-v4` 后上传 Markdown，网页显示「上传结果尚未确认」，数据库已有文档、版本、提交记录和 Index Job。
- 根因已定位：[前端回执和文档版本校验](frontend/src/documents.ts)仅接受本地 BGE 模型 ID 和 40 位十六进制 revision，拒绝了百炼预设 `bailian-text-embedding-v4` 的 `bailian-v4-contract-v1`。修复时应覆盖百炼回执及版本读取，并保留现有校验和上传幂等行为。
- 同次构建失败证据：Index Job `993bf6a1-298e-4f1a-8883-be55b8839b70` 停在 `embedding`，完成 0/257，实际原因码 `EMBEDDING_PROVIDER_UNAVAILABLE`。现有适配器把传输异常与非 401/403 的 HTTP 错误归到该码，具体原因仍待用同一端点、同一凭据和短测试文本定位。空库切换成功只表明设置已经生效。
- 经用户要求，用项目已保存的编码凭据和同一北京 DashScope 地址发起一次合成短文本测试。百炼返回 HTTP `400`、`Model.AccessDenied`，已定位到模型调用权限。需在 `minimew` 业务空间开通 `text-embedding-v4` 推理权限，并检查 Key 的模型访问范围。授权生效后复测，再为已有文档提交新版本。原提交标识仍对应原任务。后端业务代码和网页回执校验尚未修改。
- 用户确认已授权后，使用同一已保存凭据和端点复测一条合成短文本，HTTP `200`，通过 MiniMew 的响应校验并得到一条 512 维向量。原文档构建与前端回执校验尚未复验。
- 修复：前端版本解析支持百炼预设，校验模型名、维度和 revision。上传回执和版本详情共用此校验。定向 Vitest 10 项通过，前端类型检查通过。历史上传仍沿用原提交标识和任务。代码已修复，待本机页面验收。

## BUG-011：百炼单次请求失败会中断整个文档的向量编码

- 发现：2026-11-20，本机 `structure-v3` 成功分出 359 个片段。同一文档两次构建分别在 16/359 和 320/359 停止，Job `f7df40c0-3ede-4ccb-8dd4-b90d82b023bc` 和 `bff474ba-9eb5-49f5-b5e1-83edce7f2652` 都在编码阶段记录 `EMBEDDING_PROVIDER_UNAVAILABLE`。
- 定位：按各自冻结配置重建输入，使用同一端点和项目已保存的凭据，分别复测原失败批次第 17–20 和 321–324 个片段。两次均返回 HTTP `200`，各 4 个向量通过校验。原始失败发生在哪一种 HTTP 或连接异常仍未知。当前适配器将多种错误合并为同一内部码，Worker 在本次编码请求错误后直接终止整个 Job。
- 连接探测：百炼域名当前返回 2 个公网 IPv6 和 4 个公网 IPv4。生产传输连续 12 次无 Key HEAD 请求均收到 HTTP `400`，对各地址保留原 Host 和 TLS SNI 分别探测 3 次也全部收到 HTTP 响应。该探测验证当时的 DNS、连接与 TLS，不覆盖带鉴权的连续推理。未找到能还原原任务具体 HTTP 状态的日志。
- 恢复：用户反馈未修改配置，重新提交构建后成功。数据库确认 MySQL 文档 v3 在 17:34 完成 359/359，Job `1e31b2a6-1917-41c2-81bc-4072d3b7908a` 为 `succeeded`，版本 `published` 且为当前版本。故障现场已恢复，先前失败的具体触发原因仍未知。
- 拟修复：在编码适配器保留脱敏的状态码和错误类别，在原期限内仅对连接异常、限流及可重试的服务端错误短暂退避并重试当前批次。永久错误保留明确诊断，所有片段成功后仍经原有索引核验与发布。准备以受控 HTTP 响应验收，尚未改应用代码或重跑完整文档构建。

## BUG-010：超长 Markdown 代码块阻止索引构建

- 发现：2026-11-20，Windows 本机，Index Job `fe9d81d8-dc8b-4d3b-93d6-e44785cecbdd` 在本地解析/分块阶段报 `DOCUMENT_BLOCK_TOO_LARGE`。原始文件有四个围栏代码块超过 384 token，任务未进入 Embedding。
- 修复：`structure-v3` 优先沿空行和完整代码行拆分长代码块，单行超限时沿 Token 对应的原文位置拆分。代码片段使用无重叠的原文连续范围，标题计入预算。上传、Embedding 索引重建、分块预览和 Worker 支持该版本，旧版本和已领取任务保留冻结配置。
- 验证：原始文件经 Worker 相同的模型配置、分词器和分块入口生成 290 个分块，最多 384 token。分块相关 pytest 38 项通过，临时 PostgreSQL 的上传至发布及表格引用回归 1 项通过。真实原文编码与 Milvus 发布待部署后验证。
- 启用：配套重启 API 和 Index Worker。已有失败任务保留历史，给文档上传未经手改的原始文件作为新版本即可走新规则。无需数据库迁移。

## BUG-008：数据库升到 0023 后文档 Worker 仍要求升级

- 发现：2026-11-20，Windows 本机。数据库已为 `0023 (head)`，启动文档 Worker 仍退出并提示 `Run MiniMew alembic upgrade head before starting the worker`。
- 根因与修复：[Worker 启动入口](src/minimew/documents/worker.py)写死了 `0022`。现在比较 Alembic 迁移链的 head 与数据库实际版本，仍拒绝不匹配的数据库，并在退出信息里显示两边的版本。已有 `0023` 数据库可直接重新启动 Worker，无需再次迁移。
- 验证：用真实本机 PostgreSQL 调用正式启动入口，`0023` 通过检查并到达模型初始化。模拟数据库为 `0022` 时，Worker 在初始化模型前退出并显示实际与预期版本。`pytest -q tests/test_migrations.py` 两项通过，修改文件通过 Ruff lint 与格式检查。启动探测在模型初始化前结束，未加载模型、连接 Milvus 或领取任务。

## BUG-004：普通 Markdown 长表格使文档构建失败

- 发现：2026-11-18，Windows 本机；状态：**代码已修复、定向验证通过，待本机 API/Worker 重启与网页验收**。
- 现象：上传 `Java基础面试题.md` 后，v1 构建失败，界面显示“文档内容无法处理，可能包含超过分块预算的代码或表格”。文件 149266 字节，低于 5 MiB 上传上限；未发布，因此不可检索。失败 Job 的公开错误为 `INVALID_SOURCE`；本地使用同一 BGE Small tokenizer 解析原文件，发现第 197–206 行表格为 624 token、第 2043–2054 行表格为 587 token，均超过冻结的 384-token 分块预算。
- 根因：`structure-v1` 将整张管道表格作为不可拆分块；即使每行本身可安全入库，整表超过预算仍抛出 `DOCUMENT_BLOCK_TOO_LARGE`。该错误由 Worker 映射为公开的 `INVALID_SOURCE`。本次未读取服务端私有 Job 日志，内部错误码以同配置本地复现为依据。
- 修复：新增 `structure-v2`，超预算的普通 Markdown 管道表格按完整数据行打包；后续片段在编码文本及知识工具结果中带原表头，`source_text` 始终为原文连续切片且引用坐标不变。标题和特殊 token 仍计入 384 预算；单行加表头仍超限时明确失败，不截断。代码块和嵌套原子结构沿原规则。新上传推进知识库的构建配置至 v2，已有 Version/Job 保持冻结的 v1 构建身份。
- 历史数据：原失败 v1 任务不改写或伪装成功；修复部署后用新上传请求创建新版，核验发布后方可检索。旧已发布版本与引用保留。
- 验证：离线定向 101 项通过（其中 4 项新增表格/知识结果检查）；真实临时 PostgreSQL 4 项通过（其中 1 项新增上传、Worker 发布与原文范围检查），临时库已清理。原文件用本地正式 BGE Small tokenizer 只读预览得到 333 个片段、2 个表格续接片段，最大 384 token；`scripts/check_chunking.py` 的样例、长 CRLF 和长表格均通过真实 CPU 512 维编码。变更文件 Ruff 检查/格式通过，`git diff --check` 通过。为覆盖换行规则的最终代码状态，离线 101 项重复执行一次、临时 PG 长表格用例重复执行一次；两次本地预览和 CPU 检查亦为复验，不计入新增用例。已执行用例 0 跳过。未运行全量浏览器、收费模型和真实 Milvus；本次改动不涉及其协议或检索策略，尚未重启当前运行服务。
- 提交与剩余限制：本次 Bug 修复未触发小章节提交/推送规则，未提交或推送。仅处理普通管道表格，单个超预算行和超长代码块仍失败；旧失败任务须在服务更新后用新请求上传，不自动复活。无新 DDL、Event Contract、依赖或评测资产变更。

## BUG-003：未检索的回答沿用历史引用并声称已检索

- 发现：2026-11-17；2026-11-18用户明确授权实施前五项修复。状态：**代码已修复、定向验证通过，待API重启与真实模型体验验收**。
- 现象：同一Thread中的新Run没有知识工具事件、没有本Run AnswerRecord，却输出“本次检索”与`[E1]`等标记；新Thread的表现可能不同。代表Run为`fc041382-0d94-476f-aa0e-b689f74ced2d`，旧记录保持不变。
- 根因：首轮缺少应用系统规则；历史助手正文及局部引用原样进入模型上下文；未检索的文本分支直接返回，没有来源校验。不能将该实例归因于向量召回失败，也不据此证明模型内部原因。
- 修复：首轮/工具结果后统一组装系统指令与当前Run状态，历史模型输入转换旧标题并去掉旧引用；未检索使用普通回答，已检索按实际资料采用混合协议；所有最终答案均经对应来源校验，非法无检索引用沿既有错误链路入日志与Run终态。原自动模式、预算、Guardrail、终态事务与SSE事实来源不变。
- 验证：离线执行链110通过；另120项相关模块通过；历史措辞调整及新增SDK用例后定向43通过（42重复）；临时PG 21通过且已清理；前端30通过。最后收窄普通Markdown链接误判后定向19通过（18重复、1新增）。详见[完整命令、职责与限制](chapters/05-自动检索与来源修复.md)。
- 边界：不改写历史回答、不提高模型调用额度、不新增独立路由器；流式中已提交的违规文字保留为失败/未完成正文。没有实时部署或真实模型准确率验收，不宣称能识别任意自然语言的虚构执行陈述。本修复无Contract/DDL/依赖变化。

## BUG-001：空 Markdown 标题导致知识问答失败

- 发现：2026-11-16，Windows笔记本；状态：**已验证（代码修复与本机索引发布）**。
- 现象：提问“rag有什么知识点”后未生成答案，知识工具和Run均以`KNOWLEDGE_RESULT_INVALID`失败。
- 关联任务：`a958c192-5c15-4475-b83a-4b5efaf6dd5a`。用户原Markdown的空ATX标题（如仅`#`/`##`）被解析为`title_path: [""]`。
- 证据：现有产物414个片段中413个含空标题；源文件hash验证通过，当前解析器重新解析223个章节，其中222个含空标题。取冻结片段调用正式来源投影可复现`material_projection/source_contract_invalid/ValidationError`；原文hash与span正确。
- 根因：入库解析允许空标题进入来源元数据，Evidence契约要求标题路径的每个元素非空。两端约束不一致；空标题是合法Markdown，不能据此判定原文损坏。仅重传或切换Hybrid不会修复同样的解析结果。
- 修复方案：新解析版本保留结构/坐标，空标题不追加到标题路径；构建指纹区分新旧规则，通过显式升级生成新Version/Job，成功核验后发布。旧来源、历史答案和引用不原地修改，来源校验不放宽。
- 修复实现：`parse_source`增加明确的`markdown-txt-v2`规则，保留无名节点层级，标题路径继承有名父节点且不追加空元素；旧v1规则与指纹保留。正式Worker按版本冻结parser解析，并声明两种parser的可领取指纹。新知识库/预览使用v2；现有知识库使用正式升级命令的`--parser markdown-txt-v2`，可加`--document`精确重建指定用户的一份文档。升级继续经过原Version/Job/Worker发布事务，没有放宽Evidence校验、直接修改Milvus记录或改写历史版本。
- 本机数据：排空后短暂停止本项目API/Worker，保存历史摘要再升级。发现两份同源上传副本，各由414片段的v1产物重建为409片段v2产物，空标题元数据均为零，核验后发布。两份原文件/旧版本保留，未合并或删除重复文档；`runs/messages/answer_materials/run_events`行数与摘要前后一致，原失败Run仍保留失败事实。新版API与Worker恢复，live/ready均200。
- 验证：112个不同pytest用例有效通过（72离线、40临时真实PG；10新增、102既有，0跳过），其中空标题/继承层级/CRLF/原文坐标/严格来源拒绝8项新增、版本升级与指定文档隔离2项新增。39项PG首轮因新指纹未加入Worker白名单及新增测试调用夹具方式错误失败15项；修正后39通过，再因新增指定文档功能复验4项，其中3项重复不累计。未删改旧断言。
- 静态与契约：9个受影响Python文件Ruff及格式通过；从真源重新导出OpenAPI与TS类型，前端typecheck通过。无数据库DDL、Event schema或评测资产变化。
- 真实链：用本地BGE Small编码原问题、真实Milvus读取当前发布范围，3个命中均通过KnowledgeService来源投影。检查不调用付费Provider、不创建新业务Run，不把来源校验通过当作最终答案质量证明。未跑全仓/完整浏览器/付费回答回归；后端与前端正式接口可供用户重新提问。未知未来问题继续按独立编号追加，不把本记录当所有KNOWLEDGE_RESULT_INVALID都已解决。
- 复现命令：离线`pytest -q tests/test_empty_headings.py tests/test_document_chunking.py tests/test_hybrid.py tests/test_knowledge.py`；真实PG使用`python scripts/check_runtime.py tests/test_hybrid_pg.py tests/test_documents_pg.py tests/test_indexing_pg.py`，指定文档新增后定向复验`tests/test_hybrid_pg.py`。完整命令通过项目`uv run --env-file .env --no-sync`执行。
- 提交：见Git中`fix(minimew): 修复 BUG-001 空标题解析与索引重建`提交。设备与本机检查明细见[笔记本记录](devices/windows-laptop.md)。其他模型/外部API不在本次修复范围。

## BUG-002：答案格式纠正前模型预算耗尽

- 发现：2026-11-16，Windows笔记本；状态：**已修复，2026-11-17完成定向验证**。
- 现象：BUG-001修复后重新提问“rag有什么知识点”，两次knowledge_search均完成，最终无正文，错误码`AGENT_BUDGET_EXCEEDED`。任务`6fd01df0-e040-4804-94ff-3630c4032a3d`，持久Journal为两组tool.started/tool.completed，随后message.completed(failed)/run.failed，与BUG-001的tool.failed不同。
- 确认依据：服务端诊断堆栈进入`_grounded_answer`，在解析答案捕获可修正的ValueError后、调用格式纠正前的`budget.require_model()`抛出ToolFailure（当时service.py第485/73行）。即资料检索及结果转换已通过，答案校验未通过且纠正调用没有预算；不能将本次错误解释为同一空标题问题或工具调用次数必然耗尽。
- 根因边界：当前内部调用只预留一个外层最终回答机会，没有完整预留一次格式纠正；外层4次/总12次上限与格式修复存在竞争。同时预算拒绝覆盖了原始答案格式诊断，日志将其归类为tool_failed，未附外层/内部/检索计数。现有记录不足以断言本Run具体耗尽哪项模型上限，也不能确定缺标题还是某段缺引用；不靠猜测补结论。
- 伴随状态：检索显式记录`HYBRID_LAYOUT_UNAVAILABLE`，沿已声明Dense能力执行；这是现有文档布局信息，不是本次异常的直接抛出点。它与答案预算问题分别处理，不自动改索引或放大权限/调用预算。
- 拟议修复：在原固定总预算与期限内提前规划工具/内部推理/最终回答及一次纠正的额度，预算边界前停止增加工作；拒绝时保留原格式原因、具体预算类别和计数。仍须严格验证引用/正文，不靠删除校验、无限重试、自动补编引用或提高总上限掩盖问题。具体冻结策略与新旧Run语义、定向用例范围需在实施前明确。
- 首轮诊断（2026-11-16）：只读Run/Journal与既有服务日志、核对实际抛出位置，当时尚未修改预算或实施修复。此前BUG-001验证只覆盖真实来源转换；BUG-002的后续修复与验证见下，不以早期只读记录代替当前状态。

### BUG-002修复与验证（2026-11-17）

- 新Run冻结`RepairPolicy.finalization="reserved-v1"`；旧快照中该字段为缺省None，按原策略执行，catalog hash递归排除None，旧v1–v5快照及历史失败任务不回写。不是修改全局变量让已创建Run中途切换策略。
- 总上限保持外层4次、总12次、工具6次、检索8次和原deadline。内部模型调用为最终回答及一次格式纠正预留2次；预留边界使用独立信号，Grader只能返回“充分性未完成验证/insufficient”，保留已有材料，不能把停止计算说成健康空知识或已经支持全部答案。Provider故障、权限、期限和取消继续走原失败路径。
- 外层发现剩余额度不足以再开展工具探索时，保留工具规格和私有缓冲语义，以`force_answer`请求显式收尾，DeepSeek发送`tool_choice=none`，不再执行模型违反该约束返回的工具请求。既有材料/不足信息交给原回答路径；格式纠正仍最多一次，严格校验正文字符和来源，不能改金额/删事实/编引用。
- RunBudget的预留状态随原Checkpoint字典保存并恢复；预算拒绝日志记录具体outer/total/internal/retrieval/tool类别及五项计数，格式校验原因先独立记录，纠正前预算拒绝还保留该原因作为cause_code。只记录受控标签/数值，不记录模型草稿、原文或Secret。没有新增公开Event或数据库字段。
- 实际验证：193个不同pytest通过（165离线、28真实临时PG；14新增、179既有，0跳过）。包括2轮工具+8内部调用+回答/纠正正好12次的执行器场景；真实RAG图/PG原Run在探索到边界后回答并纠正；改金额拒绝、强制收尾后调用工具拒绝、旧快照摘要、取消/暂停恢复、公开正文不泄露。另验证旧预算拒绝仍保留原格式原因与计数，日志不含私有草稿。HTTP内存Mock使用真实DeepSeek SDK，确认发送none；不是付费模型质量验证。
- 新增测试最初暴露了tools/tool标签断言、SDK夹具返回值和Registry方法名的测试接线错误，修正测试后11项通过；未放宽旧断言。1条既有畸形SDK输入Pydantic warning仍保留。其他复跑不重复累计。
- 检查入口：`pytest -q tests/test_finalization_budget.py tests/test_answer_repairs.py tests/test_reasoning.py tests/test_reasoning_graph.py tests/test_agent_sdk.py tests/test_execution_diagnostics.py tests/test_hitl_policy.py tests/test_hitl.py`，以及Provider/Agent/重试/ModelControl的101项定向回归；真实PG经`check_runtime.py`执行`test_repair_pg.py test_hitl_pg.py test_reasoning_pg.py test_diagnostics_pg.py`和新增`test_finalization_pg.py`。常规由`uv run --env-file .env --no-sync`运行。
- 10个受影响Python文件Ruff/格式通过，OpenAPI/TS重新导出无协议diff，前端typecheck通过；无新依赖、DDL、评测资产或前端行为变化，未运行全仓/全浏览器或收费Provider。官方工具收尾协议核对见[DeepSeek文档](https://api-docs.deepseek.com/api/create-chat-completion/)。
- 本机部署/历史保持结果见[笔记本记录](devices/windows-laptop.md)。旧失败Run保留，用新Run验证新策略；预留不保证资料必然充分、Provider永不故障或一次格式纠正必然成功，硬上限和最终契约继续生效。
- 部署核对：排空后仅重启本项目API，索引Worker与数据索引保持；live/ready均200。新Run冻结策略检查为reserved-v1，外层4/总12、thinking=disabled；runs/messages/answer_materials/run_events前后摘要一致，原两次失败Run未改写。
- 提交：见Git中`fix(minimew): 修复 BUG-002 回答与纠正预算预留`提交。

## BUG-005：项目对话仍需明确说“核对知识库”才检索

- 发现：2026-11-18；状态：**代码修复并完成定向/真实模型工具选择验证，待API重启**。用户明确授权修复。
- 现象：项目中询问“Java异常处理有哪些？”直接回答，追加“核对下知识库”才调用工具。
- 根因：项目隔离仅作用于工具执行后的知识库选择；首轮没有项目模式和目录提示，还沿用普通概念可直接回答的通用规则。
- 修复：从已校验用户归属的Thread读取项目名称与已发布、未下线文档的有界目录，通过内部ProviderRequest传递到首轮、工具反馈、恢复及格式修复；项目知识问题优先检索，普通对话、闲聊/纯改写保留自动判断。目录不当Evidence、权限不由模型决定，不新增调用次数。
- 验证：首批隔离源码124个不同离线、29个不同临时PG用例通过；新增8离线/2PG。9.3独立提交后组合复验34离线、4PG通过（含重复，不简单相加）。真实DeepSeek两次小样本：项目Java问题选择knowledge_search，纯改写不调用工具。测试接线和夹具导出修正、复验及限制见[修复记录](chapters/05-项目对话检索优先修复.md)。
- 边界：仍为auto，没有增加硬路由；小样本不证明所有问题均命中。无新DDL/Contract/依赖、不重写历史或索引、未重启用户API。


## BUG-006：执行过程默认折叠且当前步骤一闪而过

- 发现：2026-11-18，用户截图/体验反馈，明确要求运行时逐行显示过程，最终回答后收起。
- 根因：完整步骤位于默认关闭的details中，正文前只有随当前步骤更新的短暂提示；过程放在答案之后，ChatPanel滚动只跟随正文，步骤增长未接入。并非Journal未保存过程。
- 修复：过程移至答案前，组件按Run状态默认展开/成功收起；保留全部步骤并增加中文动作描述，pending有过程时暂缓临时正文；新增过程沿原底部跟随策略滚动，用户向上阅读与历史重开不强制跳底。失败/取消/等待补充保留可见过程，历史可重新展开。
- 验证：12个不同受控浏览器用例通过（10+4两轮，2重复），79个Vitest通过，类型检查/构建通过；无后端/协议/迁移变化，不调用收费模型。详情见[9.3体验修复](chapters/09-扩展能力与交付.md#93执行过程展示体验修复2026-11-18bug-006)。
- 部署：只涉及前端；未重启用户服务，未执行数据库迁移。其他未提交修复不纳入本次提交。

## BUG-007：汇总评判响应无效且被包装为泛化工具错误

- 发现：2026-11-18；2026-11-19用户授权“建议并修复”。状态：**代码修复和定向验收完成，待服务重启**。
- 原任务：`2755d684-7a74-48cc-b67e-db0d093354c2`。正常检索与4个子问题处理后，汇总评判事件报PROVIDER_INVALID_RESPONSE，外层改报TOOL_EXECUTION_FAILED。不是“材料不足直接导致失败”，也不是正常HITL触发；旧响应具体字段未留存，不能补猜。
- 修复：新工作冻结JSON/一次有界重评策略；校验JSON、字段及引用一致性，复用原输入与预算。ProviderFailure保留类型/错误码并写原tool.failed和终态；新增受控滚动JSON日志，前端按真实失败阶段显示原因。HITL、权限、取消、owner及终态事务不变。
- 验证：新角色故障/修复测试、旧快照/预算、工具/HITL及真实PG终态链通过；纯评测82、前端49、浏览器2、真实DeepSeek JSON模式合成材料1次通过。初始失败与定向复验、命令及范围见[修复记录](chapters/06-内部评判响应与错误透传修复.md)。日志不保存凭据、原文或无效响应。
- 交付边界：无DDL/新ErrorCode/依赖或baseline变更；可选策略字段已同步生成OpenAPI/TS/评测schema。原失败Run不改写、不自动重试；尚未重启用户API/Worker，真实用户文档最终答案未重新验收。
