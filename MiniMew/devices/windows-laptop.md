# Windows 笔记本环境记录

本文件记录当前笔记本的本机准备与实际检查结果。学习进度与工作包授权以[交接记录](../AGENT_HANDOFF.md#当前检查点)为准；另一设备的历史验收见[Windows 台式设备记录](windows-desktop.md)，不能互相替代。

## BGE Reranker Base 准备（2026-11-16）

用户明确要求“安装这个模型”，范围为当前笔记本的 `BAAI/bge-reranker-base`，不扩展到其他模型、外部 API 或 GPU 环境改造。

- 硬件：i7-11800H、约15.8GiB系统可见内存、RTX3060 Laptop 6GiB显存。下载前D盘约125GiB空闲、系统约2GiB空闲内存；这是检查时状态，不是固定可用容量。
- 沿用现有CPU环境：torch `2.15.0+cpu`、transformers `5.22.2`、huggingface-hub `2.4.0`；未重新安装依赖或修改锁文件。
- 通过正式prepare入口从官方Hugging Face源下载固定revision `2cfc18c9415c912f9d8155881c133215df768a70`，仅准备该模型必需文件。
- 缓存位置：`data/models/models--BAAI--bge-reranker-base/snapshots/2cfc18c9415c912f9d8155881c133215df768a70/`。六个必需文件齐全，含约1060.7MiB的model.safetensors及tokenizer/配置。缓存受Git忽略，不随提交同步。

实际执行并成功退出：

```powershell
uv run --env-file .env --no-sync python -m minimew.rag.reranker prepare
```

随后设置当前检查子进程的 `HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1`，执行：

```powershell
uv run --env-file .env --no-sync python -m minimew.rag.reranker check
```

结果为 `files_present=true`、`encoding_verified=true`、`device=cpu`、`dtype=float32`。对“出差餐费每日上限是多少？”，餐费条款得分 `7.533872127532959`，密码重置文本得分 `-10.194923400878906`，相关文本排前。这是一次真实本地加载/评分smoke，不是质量评测、延迟benchmark或API/Milvus整链验收。

下载提示Windows缓存不能使用symlink、匿名访问有速率限制，本次仍成功完成；未修改管理员权限、开发者模式或账户凭据。验证进程正常结束，不保留额外常驻模型副本。

本次安装未修改应用源码、`.env`、Contract、迁移或数据库/向量索引，未重启API/Worker，未执行收费调用或完整业务回归。其他正在进行的开发及学习授权以交接记录为准；本次只新增设备环境记录，不创建应用代码提交或推送。

## 知识结果校验失败定位（2026-11-16，仅诊断）

截图任务`a958c192-5c15-4475-b83a-4b5efaf6dd5a`已持久failed，错误为`KNOWLEDGE_RESULT_INVALID`；Journal记录tool.failed与run.failed。此时本机数据库已到0017，不能继续引用此前0014的检查状态。

只读核对当前发布的Dense产物：414个chunk中413个所属title_path含空字符串。取一个冻结chunk调用当前KnowledgeService.project复现相同错误，诊断为material_projection/source_contract_invalid/ValidationError，样例原文hash和span正确。未加载模型或调用回答Provider。

从固定版本的源文件按预期hash读取并用当前parser重新解析：源文件完整性通过，223个section中222个带空标题路径，原Markdown中统计到222行空ATX标题（如只有`#`或`##`）。当前解析器仍接受空标题并写入title_path，而EvidenceSource拒绝空标题元素。因此仅重建或升级Hybrid不能保证解决同一问题，需要先统一解析产物与来源契约的规则；不将这类合法Markdown结构误报为原文损坏。

拟议修复范围为解析时保留层级/原文定位但不产生空标题元素、更新构建身份、验证空标题及有名父标题下的行为，再显式创建新候选版本重建/发布，保留旧来源/答案。该范围仅建议，未获本次具体修复开始反馈；本轮未改应用、原文件、发布数据、向量索引或错误校验规则，也未重试收费模型调用。

## BUG-001修复部署（2026-11-16）

用户随后明确要求建立Bug记录并修复本问题，代码与验证详见[BUG-001](../BUGS.md#bug-001空-markdown-标题导致知识问答失败)。本节取代上节“仅诊断”的当前状态，保留原诊断时间线。

确认无进行中Run/IndexJob后暂停本项目API/Worker，保存历史数据摘要，按v2解析规则分别重建两份同内容的上传副本；保留Dense布局，两份均发布409个片段、空标题元数据为零。原文件与旧版本保留，历史Run/Message/AnswerMaterials/Journal行数/hash不变。没有合并或删除重复文档。

本机辅助检查文件只在忽略的`.artifacts/bug001-*`：升级意图、历史摘要、真实检索结果及新API/Worker日志。原问题经真实本地Embedding/Milvus得到3个来源投影有效命中，不调用DeepSeek。新版API在8001的live/ready均200，索引Worker已恢复常驻；启动使用现有CPU模型，未修改GPU环境或`.env`。前端5174进程保持。

## BUG-002预算预留部署（2026-11-17）

修复与验证见[BUG-002](../BUGS.md#bug-002答案格式纠正前模型预算耗尽)。确认生产库无进行中Run后，仅重启本项目8001 API加载新冻结策略；索引Worker、数据库结构、索引、模型与`.env`未改，前端进程保持。

真实当前配置在Run预留事务中的只读快照检查为`repairs.finalization=reserved-v1`，outer_limit=4/model_limit=12/thinking=disabled。API的live/ready均200；runs/messages/answer_materials/run_events行数与摘要保持，原`a958c192...`及`6fd01df0...`失败记录仍为failed。辅助摘要和启动日志在忽略的`.artifacts/bug002-*`。本次验证包含真实PG/图的受控Provider全链与真实SDK内存HTTP，不含付费DeepSeek在线质量测量，也不对旧Run重发请求。
