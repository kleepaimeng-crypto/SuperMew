# MiniMew评测模块

继承[MiniMew约定](../../../AGENTS.md)。本模块是独立练习的评测接口，不导入上级SuperMew业务包。

- `protocol.py`是Pydantic类型真源；`scoring.py`是纯评分/Report；`gate.py`拥有比较/政策/产物；`cli.py`只组织显式文件输入输出。上述离线路径及result_protocol/runtime_result/versioned导入和计分不加载模型、访问网络或连接数据库。runtime_protocol定义Job输入；store/corpus/manage/worker是显式运行路径，Worker只在启动后加载模型和访问基础设施。
- `evals/rag/schema/`从`scripts/generate_evaluation_schema.py`生成，禁止手改。输入的JSON键、ID、内容指纹与Unicode字符区间必须严格核验。
- Dataset是人工标准，Observation是事实；材料覆盖和引用结构不能变成语义正确性。无gold/未评价为unavailable，故障切片单列，失败不能从质量分母消失。
- 改变计分语义须更新scorer_version及对应schema/样本对照，不把新旧规则报告视作同条件结果。baseline必须显式选择，不通过改gold或重写历史观察迎合实现。
- Provenance标签/内容指纹不是签名或真实执行证明；B包使用真实本地检索、受控生成与受控Judge，标controlled；不能把受控评分或手写live标签充当真实语义质量验收。
- 运行`uv run --no-sync pytest -q --confcutdir=tests/evaluation tests/evaluation`隔离旧在线App全局夹具，不为离线测试安装在线依赖。
- 定向Ruff、schema生成`--check`和CLI足够覆盖纯模块变化；按实际行为触发其他测试，避免无变化重复全仓验证。

- Case必须使用CaseIdentity与独立EvidenceDirectory；共享KnowledgeCalculation、ModelCaller、grounded_answer，不造Thread/Run、不复制RAG图。gold只在答卷持久后进入JudgeInput。
- store拥有Job/Case资格与阶段事务；暂停和补充接收分别保存，恢复同一Case和检查点；过期generating/judging按未知结果失败，不盲目重放。
- corpus只复制明确映射并已发布的不可变版本，使用服务端生成的专属Milvus集合隔离BM25统计；清理只针对其记录，引用中的Job存在时拒绝清理。
- 真实本地/跨进程验收必须显式执行scripts/check_evaluation.py，离线单测不得隐式依赖本机模型、Secret或下载。
- `workbench.py`定义工作台公开投影/比较输入；历史、Corpus目录、Case详情只由同一EvaluationStore按账号读取，比较调用同一compare_v2。页面不输出私有Checkpoint/owner/gold，也不另算一套Gate。工作台状态/轮询不得宣布Worker终态。
