# MiniMew 事件契约

继承 [MiniMew 学习约定](../AGENTS.md)。本目录属于独立练习实现，不修改上级 SuperMew 的同名契约或生成物。

- `run_event_v1.json` 是 MiniMew Run Event envelope、类型、状态、稳定错误码及最小 payload 的唯一真源。
- 在 MiniMew 目录运行 `uv run --no-sync python scripts/generate_event_contract.py` 生成消费者，再运行相同命令加 `--check` 验证漂移。生成器使用已锁定的开发工具 Ruff，不访问数据库或网络。
- 输出为 `src/minimew/generated/run_event_v1.py` 与 `frontend/src/generated/run-event-v1.ts`。禁止手工编辑或仅格式化生成物；格式也从生成器修正，统一生成 LF 换行。
- 生成器遇到不支持的 JSON Schema 结构必须明确拒绝，并随结构变化更新生成器和 `tests/test_execution.py`；不能忽略约束来生成宽松消费者。
- 事件不得包含 Secret、模型地址、原始异常或私有推理。公开错误使用受控 code；消息正文仅进入声明的 payload。
- sequence 是单个 Run 内的日志游标，与 Thread 的 Message sequence 不同。终态事件和 `message.completed` 由同一持久结果事务产生；SSE 只读取 Journal。
- 新 Event type 在同一真源中扩展并同步 producer、消费者和测试；破坏既有语义时新增 schema version，不保留第二套隐式事件形状。
