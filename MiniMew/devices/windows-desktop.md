# Windows 台式设备环境记录

本文仅保存该设备带日期的运行快照，不记录 Secret。共同学习进度与授权见 [交接](../AGENT_HANDOFF.md)，正式命令见 [README](../README.md)。历史快照不代表当前或其他设备已就绪。

## 第七章 B 包设备验证快照（2026-11-14）

- 沿用本机既有 PG/Redis/Milvus 与 CPU Embedding/Base 缓存，没有安装新依赖、下载权重或编辑 `.env`。三 API 进程验收设置 HF/Transformers 离线，只读已有缓存；受控模型不联网计费。
- 升级前只读确认开发库没有 pending/running/resume_pending，已有 Run 为 failed 2 / succeeded 4，Index Job succeeded 2；与 A 较早快照的3个成功Run不同，以本次现场事实为准。停止本项目 API/Worker 后保存全部业务表行数/内容指纹，0016→0017 与 alembic check 通过；排除新 deadline 列后原数据全部一致，新列历史值均 NULL。没有迁移旧设备数据或创建开发库验收 Run。
- 真实 CPU/Milvus/PG 的三个独立 API 进程按暂停、仅接收、续接运行，4次受控调用、9事件、1引用，收费0；临时库及1自有scope清理。完整报告 `.artifacts/hitl-check.json`，各进程日志 `.artifacts/hitl-pause.log`、`hitl-accept.log`、`hitl-resume.log`。测试进程硬退出使用 nonce 和子进程真实 PID 握手，未关闭其他项目。
- API 8001 与独立索引 Worker 已恢复，API live/ready 与前端5174均200，最近10秒存在running Worker心跳。只读探针确认 head0017、新Run快照v5/等待900秒、lease20/heartbeat5，开发Run/Job计数保持。探针不创建Run；启动器/日志/指纹位于忽略目录 `.artifacts/bootstrap/chapter07b-*`。
- Python辅助脚本显式UTF-8，避免Windows默认GBK解码中文文件失败；不改变系统编码。前端/浏览器未变，本轮未重复运行UI或下载浏览器，既有39处全量格式告警仍保留。

共同进度、实际反馈、恢复边界与后续授权只见交接和第七章，本文不维护另一套学习状态；这些本机快照不代表其他设备已运行。
