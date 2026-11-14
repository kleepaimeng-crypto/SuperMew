# MiniMew RAG 策略

继承 [MiniMew 约定](../../../AGENTS.md)。本目录只拥有知识工具内部策略，不拥有 Run、发布状态、用户身份或 Evidence 编号。

- `pipeline.py` 是唯一入口；Dense 基线与 Hybrid 共享固定 published scope。能力由向量 Adapter 显式报告，网络/坏数据不能触发 Dense fallback。
- `protocol.py` 的候选策略由 Run 的 v2/v3 Model Snapshot 冻结；v3另冻结`reasoning.py`角色策略。不能在工具调用间读取当前配置重置预算。旧v1/v2快照保持摘要与原语义。
- RRF 仅在应用内融合一次；Dense、BM25、RRF、Base 原始分数分别记录，不视为可信概率。
- 补全只读固定 scope 的不可变目录；按原 chunk 身份精确取回并核验，单层同节、最多6成员/累计18候选，禁止递归变成全文。最终按组和预算选择，再由知识工具原子登记 Evidence。
- Base 固定 revision/CPU FP32/batch1，加载只读本地。下载只走 `python -m minimew.rag.reranker prepare`；窗口超限不截断，重排失败显式记录 code/attempts，期限耗尽不能降级继续。
- `workflow.py`只拥有knowledge_search内部StateGraph/Send策略，不拥有Run终态或持久Checkpoint。Grader评判实际有预算草稿；聚合后按原用户问题复评。Planner/Rewrite次数、12模型尝试/8检索和总期限来自同一Run预算，子任务并发2且不提交Evidence；最终由KnowledgeService一次提交。执行资格信号不能吞成普通工具/子任务失败。
- v4的`repairs`冻结有限格式纠正与`selection.py`代表材料策略；旧v1–v3摘要/行为保持。代表引用必须先按各草稿映射到version/build/chunk身份，再在最终字符预算后检查；丢失代表来源不能被Grader“足够”覆盖。纠正只收集私有无工具输出，仍占4外层/12总尝试和原期限，字符保留与原引用校验均须通过。
- B包定向用`tests/test_reasoning.py`与`test_reasoning_graph.py`；真实原Run/取消/私有输出用`check_runtime.py tests/test_reasoning_pg.py`。不隐式调用收费Provider、下载模型或启动其他项目服务。
- 第六章固定发布资料/全路线验收用`scripts/check_rag_suite.py`，案例与必要原文锚点在`examples/rag/chapter06-*`；辅助契约/覆盖/导出检查用`tests/test_rag_cases.py`。只有显式`--write-observation`更新共同观察资产，不含设备时延/身份。受控Grader不能作语义质量裁判；最终材料缺口必须照实保留，不通过改预期或预算掩盖。该观察不是第八章质量Gate。
- 定向离线入口为 `tests/test_rag.py`、`test_hybrid.py`、`test_rag_materials.py`；真实旧数据升级用 `check_runtime.py tests/test_hybrid_pg.py`，真实候选策略对照用 `scripts/check_rag.py`，正式发布/Run/引用用 `scripts/check_knowledge.py`。后两者显式使用本地模型/Milvus和自有临时资源，不能把受控回答验收称为收费模型质量证明。
