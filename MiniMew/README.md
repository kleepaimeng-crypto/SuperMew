# MiniMew：从零复现 SuperMew

MiniMew 是以当前 SuperMew 为参考的分阶段学习与复现计划。目标是理解并逐步实现知识库 Agent 的执行、检索、恢复和评测机制。

目前只创建学习文档，尚无应用代码、运行环境或独立依赖，也尚未建立独立 Git 仓库。

## 导航

- [分阶段技术、实施步骤与理解题](LEARNING_PLAN.md)
- [学习互动与编码前确认规则](AGENTS.md)
- [Agent 跨设备交接与当前进度](AGENT_HANDOFF.md)
- [SuperMew 正式领域语言](../CONTEXT.md)
- [SuperMew 当前能力](../README.md)

## 九阶段路线

| 阶段 | 实现范围 | 验收标准 |
| --- | --- | --- |
| 1. 工程与领域骨架 | FastAPI、Vue、数据库迁移、配置、身份隔离、领域模型和 Contract | 空库可迁移；接口可鉴权；前后端共享契约 |
| 2. 持久化普通对话 | Thread、Message、Run、模型快照、Provider 调用、Event Journal、SSE | 能完成一轮对话；重复请求不会创建重复 Run；断线可重放 |
| 3. 最小聊天前端 | 登录、Thread 列表、输入框、流式正文、Run 状态、取消 | 刷新能恢复历史；重复 Event 不重复追加；不同 Run 不串消息 |
| 4. 文档入库与发布 | Document、不可变 Version、持久 Index Job、独立 Worker、解析与向量索引 | 上传后可查询进度；新版本构建失败时旧版本仍可检索 |
| 5. 基础知识问答 | 最小 Tool Registry、Guardrail、知识库 Tool、Dense 检索、Evidence、答案引用 | 问题能基于已发布文档回答；引用能定位具体文档版本 |
| 6. 完整 RAG 策略 | BM25、RRF、Auto-merging、Rerank、证据评判、一次重写、复杂问题拆解 | 固定案例可以验证各条路线；空结果和 Provider 故障明确区分 |
| 7. HITL 与故障恢复 | Checkpoint、暂停、同一 Run 恢复、取消竞态、lease/fencing、过期 owner 处理 | 人工回答后继续原执行；旧 owner 不能写入；终态只产生一次 |
| 8. RAG 评测闭环 | Dataset、fingerprint、Observation、Gate、baseline、Evaluation Job/Worker、工作台 | 同一数据与配置可比较结果；评测复用正式 RAG；失败案例可追踪 |
| 9. 扩展能力与交付 | Skill、模型与能力控制面完整 UI、Web Research、只读 SQL、Sandbox、生产部署 | 权限拒绝发生在执行前；API、Indexing Worker、Evaluation Worker 可独立运行；真实基础设施检查通过 |

## 学习方式

每个小节按以下顺序进行：

```text
解释问题与技术职责
→ 用具体场景提问
→ 用户解释与提问
→ 补足理解缺口
→ 确认本次实现范围并获准开始
→ 小步编码与验证
→ 讲解代码并复盘
```

不会一次生成九阶段代码。文档中的技术与步骤是待逐阶段讨论的方案，不表示依赖已安装或实现已完成。

## 当前进度

阶段、已通过的理解检查、待答问题与编码授权统一记录在 [Agent 交接文档](AGENT_HANDOFF.md#当前检查点)，继续学习前先读取。换设备流程也在该文档中。

## 参考范围与不变量

MiniMew 后续使用独立入口和配置，不接入 SuperMew 的生产执行链。其核心对象沿用 Thread、Message、Run、Event、Document Version 和 Model Snapshot 等正式术语。

阶段可以逐步扩展能力，但不得在早期引入之后需要推翻的事实来源：

- PostgreSQL 保存持久事实，SSE 是 Event Journal 的投影。
- 一个 Run 拥有一个用户 Message 和一个 assistant Message。
- Run 创建时固定模型快照，恢复不重选当前模型。
- 文档只检索已发布版本，失败 candidate 不替换 current。
- 外部服务故障与健康空结果必须区分。
- Tool 执行前完成权限与 Guardrail 检查，Secret 不进入公开状态。

当前修改只涉及本目录学习文档，不变更上级 Contract、迁移、评测资产或应用行为。
