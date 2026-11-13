# Product

<!-- impeccable:product-schema 1 -->

本记录仅覆盖 MiniMew。用户于 2026-11-04 确认：学习复现优先，沿用现有定位。
学习进度、理解检查与应用编码授权统一见 [AGENT_HANDOFF.md](AGENT_HANDOFF.md#当前检查点)；本文件保存长期产品上下文。

## Platform

web

## Users

主要使用者是项目学习者本人：有 Java/Spring 开发经验，具备 Python/FastAPI 基础，希望通过独立实现理解 SuperMew。

学习重点是 Agent、RAG、持久化执行、故障恢复和评测。前端学习目前聚焦模块职责、状态归属、数据流与前后端衔接。

## Product Purpose

从可独立运行的最小系统开始，分阶段理解并实现知识库 Agent 的执行、检索、恢复与评测机制。

每一步的成果应能够解释清楚业务职责、状态保存位置、模块协作和失败行为，并通过与改动相关的实际验证。九阶段路线见 [LEARNING_PLAN.md](LEARNING_PLAN.md)。

## Positioning

MiniMew 以 SuperMew 的正式实现和领域模型为参照，在独立的运行环境中按已讨论的小步复现。学习时逐步引入能力，同时保留持久事实、资源归属、执行身份和契约的一致性边界。

## Operating Context

- 当前开发环境为 Windows/PowerShell，使用 Docker 的 Linux containers 提供独立基础设施。
- 后端位于 [src/minimew](src/minimew)，使用 Python 3.12、FastAPI、SQLAlchemy、PostgreSQL、Alembic 和 Redis，由 uv 管理独立依赖与环境。
- 浏览器应用位于 [frontend](frontend)，使用 Vue 3、TypeScript、Pinia、Vue Router 和 Vite。前端通过开发代理访问独立后端。
- 当前本地入口为前端 `127.0.0.1:5174`、API `127.0.0.1:8001`；安装和启动步骤以 [README.md](README.md) 为准。
- 按 2026-11-09 约定，工程底座简述目标和边界，合并工作包一次授权后连续实施、集中验收；Agent/RAG 等智能化重点讨论原理、取舍与失败案例。具体规则见 [AGENTS.md](AGENTS.md)。
- 换设备或换 Agent 时，通过已保存的学习文档恢复上下文；章节计划和总结保存在 [chapters](chapters)。

## Capabilities and Constraints

### 已有实现的产品边界

以下结合2026-11-13第五章交付与既有源码，具体阶段完成情况与验收记录仍以交接文档为准。

- 浏览器页面包含注册、登录、账户信息、续期与退出，以及个人 Thread 列表、创建和选择。
- 后端具有用户归属校验、Thread/Run/Message 持久化、幂等接收提问、模型配置与冻结快照、执行事件分页和 SSE 订阅入口。
- 执行模块已有流式正文、Event Journal、事务 Outbox、Redis 通知、DeepSeek Adapter 和后台调度实现。模型调用需要独立配置与凭据；已有模型选择为 DeepSeek，关闭思考。
- Thread 页面已接入消息历史、幂等提交、流式回答、执行状态、原游标重连、按 Run 停止、刷新恢复与身份/对话隔离；沿用既有页面样式，消息按纯文本展示。实际验收区分模拟接口、真实 API/PG 与受控模型，不能据此声称真实模型质量或生产部署通过。
- 文档入库已有Markdown/TXT上传、新版本、个人归属、不可变构建快照、持久Job/产物清单、独立CPU Worker、解析/分块/轻量编码、Milvus精确核验、原子发布与退休向量清理。文档工作区支持目录、新文档/新版上传、原Job进度、刷新恢复和失败新版保留旧current的展示；读取只选择用户已发布范围。
- 文档浏览器闭环已在隔离PG、配套源目录、自有Milvus范围和独立真实轻量CPU Worker中验证发布、坏新版失败、刷新及账户隔离；故障/迟到响应另用受控测试，不当作生产容量或真实断电证据。当前支持bge-small-zh-v1.5 CPU；5.3已接入显式启用的已发布知识Tool和Run内固定范围，默认关闭，开启后API加载独立模型资源。5.4–5.6已有Run私有Evidence与版本快照、区分文档依据/模型补充的答案、可点击引用/原文详情及Journal驱动的工具过程，刷新及新版/账户隔离已在真实CPU/Milvus/浏览器链验证。回答Provider受控，不能据此声称真实模型答案质量；BGE-M3/GPU完整切换、复杂RAG、HITL、评测和生产交付仍在后续路线。

### 必须保留的约束

- MiniMew 使用独立入口、依赖、配置和数据边界；不接入上级 SuperMew 的生产执行链。
- Thread 是用户拥有的持续对话容器；Run 是一次业务执行身份；Message 是持久消息；Event 是该次执行的有序事实。沿用 [正式领域语言](../CONTEXT.md)，避免混用。
- PostgreSQL 保存业务持久事实；SSE 投影 Journal，Redis 提供通知。浏览器断开或通知丢失不能自行改变 Run 的权威状态。
- 一个 Run 归属一条用户 Message 和一条 assistant Message，创建时固定无 Secret 的模型快照。同一提交意图的重试复用幂等键。
- Access Token 只保存在浏览器内存，Refresh Token 通过 HttpOnly Cookie 传输；模型密钥和原始凭据不进入公开状态。
- 后续文档检索仅使用已发布版本，失败候选不能替换当前版本；外部服务故障与健康检索空结果必须区分；工具执行前完成权限检查。这些是后续实现应遵守的产品约束。

### 尚未确定

面向外部用户的目标群体、商业化安排、生产部署目标和服务承诺尚未确定，后续有明确需求时再补充。

## Brand Commitments

- 产品名称沿用 MiniMew，学习文档和当前界面以中文为主。
- Thread、Run、Message、Event、Document Version 和 Model Snapshot 保留正式术语，并在需要时解释业务含义。
- 对外说明区分已实现能力、路线计划与实际验证结果。

## Evidence on Hand

- [README.md](README.md)：运行方式、当前接口与历史验证说明。
- [AGENT_HANDOFF.md](AGENT_HANDOFF.md)：实际学习反馈、授权边界和验证记录。
- [LEARNING_PLAN.md](LEARNING_PLAN.md) 与 [第 2 章计划](chapters/02-持久化普通对话.md)：路线、技术职责和验收场景。
- [前端路由](frontend/src/router.ts)、[页面](frontend/src/views)、[应用入口](src/minimew/app.py) 与 [执行模块](src/minimew/execution)：能力描述的实现依据。
- [contracts](contracts)、[tests](tests)、[前端测试](frontend/tests) 和 [验收脚本](scripts)：契约和可执行验证资产。历史结果需结合对应记录阅读，不能代替新环境的实际验证。

## Product Principles

1. 让学习者能解释职责、状态与失败行为，再按明确授权的小步实现。
2. 在同一正式业务链上逐步增加能力，保留关键领域边界。
3. 从持久事实呈现执行状态，让断线、重复请求与恢复行为可追踪。
4. 用源码、真实数据和实际验证支撑能力说明，明确区分计划与完成。
5. 保持独立运行边界，并用仓库内文档支持跨设备继续学习。
