# MiniMew RAG 策略

继承 [MiniMew 约定](../../../AGENTS.md)。本目录只拥有知识工具内部策略，不拥有 Run、发布状态、用户身份或 Evidence 编号。

- `pipeline.py` 是唯一入口；Dense 基线与 Hybrid 共享固定 published scope。能力由向量 Adapter 显式报告，网络/坏数据不能触发 Dense fallback。
- `protocol.py` 的策略由新 Run 的 v2 Model Snapshot 冻结；不能在工具调用间读取当前配置重置预算。v1 快照保持旧摘要与 Dense 语义。
- RRF 仅在应用内融合一次；Dense、BM25、RRF、Base 原始分数分别记录，不视为可信概率。
- 补全只读固定 scope 的不可变目录；按原 chunk 身份精确取回并核验，单层同节、最多6成员/累计18候选，禁止递归变成全文。最终按组和预算选择，再由知识工具原子登记 Evidence。
- Base 固定 revision/CPU FP32/batch1，加载只读本地。下载只走 `python -m minimew.rag.reranker prepare`；窗口超限不截断，重排失败显式记录 code/attempts，期限耗尽不能降级继续。
- 定向离线入口为 `tests/test_rag.py`、`test_hybrid.py`、`test_rag_materials.py`；真实旧数据升级用 `check_runtime.py tests/test_hybrid_pg.py`，真实候选策略对照用 `scripts/check_rag.py`，正式发布/Run/引用用 `scripts/check_knowledge.py`。后两者显式使用本地模型/Milvus和自有临时资源，不能把受控回答验收称为收费模型质量证明。
