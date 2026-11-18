# 开发与使用体验 Bug 记录

记录实际开发、部署和使用中发现的问题。每项使用稳定编号，保留首次现象与失败证据；后续补充根因、修复方法、影响范围和实际验证。状态区分待定位、已定位、修复中、已修复待部署、已验证。未经执行的检查不能记为通过；代码修复与本机数据修复分别记录。不得保存密钥、原始凭据或整份用户文档。

新增记录包含：发现日期/环境、现象与影响、复现条件、错误码及关联任务、根因、修复方法、历史数据处理、验证结果、提交和剩余限制。其他已知问题仅在有具体证据时追加，不把功能规划当Bug。

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
