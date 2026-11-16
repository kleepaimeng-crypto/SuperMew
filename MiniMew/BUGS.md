# 开发与使用体验 Bug 记录

记录实际开发、部署和使用中发现的问题。每项使用稳定编号，保留首次现象与失败证据；后续补充根因、修复方法、影响范围和实际验证。状态区分待定位、已定位、修复中、已修复待部署、已验证。未经执行的检查不能记为通过；代码修复与本机数据修复分别记录。不得保存密钥、原始凭据或整份用户文档。

新增记录包含：发现日期/环境、现象与影响、复现条件、错误码及关联任务、根因、修复方法、历史数据处理、验证结果、提交和剩余限制。其他已知问题仅在有具体证据时追加，不把功能规划当Bug。

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
