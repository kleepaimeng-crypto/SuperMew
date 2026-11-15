# Windows 台式设备环境记录

记录日期：2026-11-13。适用设备：本轮完成 MiniMew 初始化的 Windows 台式开发设备。

本文只保存该设备的工具版本、配置落实情况、容器、模型缓存、初始化问题与实际验证记录。学习进度、模型选型决定、工作包授权和跨设备通用规则统一见 [AGENT_HANDOFF.md](../AGENT_HANDOFF.md)；正式运行命令与项目配置定义见 [README.md](../README.md)。不记录密钥、Token 或原始凭据。

下面是带日期的历史快照：进程状态、端口、数据库计数与通过结果只代表核对当时，重启、更新代码或使用应用后需重新检查；不能据此推断其他设备已准备就绪。

## 初始化前核对（2026-11-11）

- 当前工作副本为个人仓库的 `main`，本地提交 `aedab82`；`origin` 指向个人仓库。第四章 Worker 与第五章计划文件均已纳入 Git；当时未 fetch/pull，不据此判断远端是否还有更新。已有 IDE 修改保留。
- 已现场确认 Python 3.12.10、uv 0.11.31、Node 24.18.0、npm 11.16.0、Docker Engine 29.6.2 和 Compose 5.3.1 可用，Docker 服务正在运行。
- 本机尚无 MiniMew 的 `.env`、`.venv`、`frontend/node_modules`、默认 `data/models` 与 `data/documents`；未发现本项目容器及 Compose 项目卷，默认 API/前端/PG/Redis/Milvus 端口均未监听。缓存存在旧 Milvus 2.5.14，不能替代本项目锁定的 3.0.0 镜像。
- 当日仅完成环境核对，尚未安装依赖、创建配置、迁移数据库、下载模型或启动服务；后续实际初始化结果见下一节。

## 初始化与验证（2026-11-13）

- 本机按用户拷贝的 `.env` 初始化；用户确认旧 PostgreSQL 没有聊天/文档，因此新建独立数据卷，无需迁移旧数据。
- 初始化核对的代码基线为本地提交 `6474cea`。保留原有 IDE 修改，不操作电脑上的其他项目容器。
- `uv sync --dev --extra embedding --extra vector --locked` 完成，101 个已安装包经 `uv pip check` 检查兼容；`npm ci` 安装 122 包，审计 0 漏洞；`npm run build`（包含类型检查）通过。未升级 manifest/lock，也未安装 GPU 推理环境。
- 按 Compose 固定 digest 创建独立项目容器和新数据卷：PostgreSQL 18.4（锁定 postgres:18）、Redis 7.4.12、Milvus 3.0.0，分别监听本机 55432/56379/59530，全部健康。旧容器和旧卷保持不动。首次 Redis 启动因 Windows CRLF 失败，转换现有入口脚本为 LF 后恢复；新增 `.gitattributes` 的 `docker/*.sh text eol=lf` 防止再次检出 CRLF，脚本业务内容无变化。
- 沿用原有 `.env` 全部条目，仅追加样例中新引入的缺失设置，显式开启 `MINIMEW_KNOWLEDGE_ENABLED=true`。数据库迁移到 `0014 (head)`，`alembic check` 无结构漂移；通过正式 CLI 创建 deepseek-v4-flash 回答 Profile v1、关闭思考，不调用收费模型。核对用户/Thread/Run/Document/IndexJob 均为 0，Profile 为 1。
- Embedding 固定 `BAAI/bge-small-zh-v1.5@7999e1d3359715c523056ef9478215996d62a620` 已下载到默认 `data/models`；正式 CLI 真实 CPU 编码/512 维与固定相关排序检查通过。正式向量 CLI 的写入、manifest 核验、检索、隔离及自有候选清理检查通过，`cleanup=complete`。
- `scripts/check_database.py` 实际验证 0014、预期表集合、事务提交/回滚/Session 清理及真实 ready 200；Redis 使用本机配置认证 PING 成功。已后台启动 API、独立文档 Worker 和 Vite；HTTP live/ready/前端均为 200，前端代理未认证 Thread 请求为 401，Worker 心跳 running 且持续更新。进程信息与本次辅助脚本/日志位于忽略的 `.artifacts/bootstrap/`。
- Reranker 沿用已选 `BAAI/bge-reranker-base`，官方模型元数据固定 revision `2cfc18c9415c912f9d8155881c133215df768a70`；已准备 safetensors/配置/Tokenizer 到默认 `data/models`，不下载备选模型或变更应用协议。权重共 1,112,206,140 字节，下载在约 604 MB 停滞后仅终止自有下载进程、从同一官方固定 revision 分段续传；最终 SHA-256 为 `ced967c45fd1902eb92716c9ceeca7c95a936770ea9db611f5a841b926e33fbd`，与官方 LFS 元数据一致。仅从本地 safetensors 加载、禁用远程代码，CPU FP32 固定相关/无关文本检查通过，原始分数分别约 7.575/-10.195；这只是本机推理 smoke，不是质量评测或第六章正式 RAG 接入。
- 本次没有新增业务测试；上述检查为本机初始化新执行，历史用例数不计入。本次未运行全量回归、浏览器交互或收费模型质量检查，范围为基础设施/模型准备与启动；Contract/ORM/迁移真源/评测资产未改。


## A包部署与本机测量（2026-11-13）

既定BGE Small与Base现已由正式API知识Runtime加载；本地API/Worker在无活跃Run/Job时重启，开发库一份文档按同内容新版本从Dense升级到Hybrid，历史对话/答案/Event哈希核对不变。此处只记录本机部署结果，共同实现进度与授权仍见交接。

候选对照脚本最终成功运行：冷加载 6.656 秒、独立检查进程工作集约 1.97 GiB（含编码/重排模型及推理缓存）。每条路线3次样本，非可靠p95或质量评测：

| 路线 | 样本中位数（秒） | 样本最大值（秒） | 输出片段 |
| --- | --- | --- | --- |
| dense | 0.016 | 0.031 | 3 |
| hybrid | 0.031 | 0.032 | 2 |
| completion | 0.031 | 0.032 | 3 |
| reranked | 0.344 | 0.359 | 3 |

该次受控候选样例验证完整补回例外、原生BM25编号/连字符型号及本地Base评分，不代表收费回答质量或跨设备性能。固定脚本为 `scripts/check_rag.py`；正式发布/原Run链另由 `scripts/check_knowledge.py` 验证，辅助日志/进程ID仍在 `.artifacts/bootstrap/`，不可当作其他设备的前提。

## B包本机环境与部署快照（2026-11-14）

本次Docker Desktop 4.83.0初始未能提供引擎：日志分别报告`Docker/run/dockerInference`、`docker-secrets-engine/engine.sock`残留AF_UNIX对象不可访问（ReparsePoint）。仅停止Docker Desktop/backend，将两个本机运行时目录移至同层`.b-backup-20261114`/`-2`备份后重启；没有重置、prune、卸载或删除数据库/向量卷。第一次仅移开一个目录后暴露另一个残留，最终同时移开两个目录恢复。备份留待后续系统重启后按需处理，本次不删除；不要把本次修复推导为其他设备也需要操作。

恢复后仅启动`minimew-postgres-1`、`minimew-redis-1`、`minimew-milvus-1`，均healthy；Docker engine为29.6.2，其他项目旧容器保持退出。PG开发库head0014，启动应用前只读核对Run无pending/running、index_jobs均succeeded；未迁移或回写开发历史。uv环境已按独立锁安装LangGraph1.2.22，108包锁/106已装包兼容，原模型缓存与CPU选择沿用。

已后台启动当前源码API 8001、Vite 5174及独立Worker，启动日志确认模型从本地加载、无下载；API live/ready与前端均HTTP 200。新Run推理策略设置true（默认），未编辑`.env`或收费调用。具体自有启动器PID、日志保存于忽略的`.artifacts/bootstrap/b-services.json`与`b-*`文件，不将PID作为跨设备启动依据。ready只证明PG，不冒充全链质量验收。

本机首次PG与真实知识链因引擎关闭而ConnectionRefused，之后隔离PG46项通过、最终B用例4项复验通过；真实CPU/Milvus/Worker发布/正式Run与引用受控链最终成功，2外层+1内部模型请求、6Journal事件、1引用，收费0，自有scope/临时库清理。共享进度与授权仅见交接，本节不复制其状态。Event生成检查首次因Windows检出CRLF字节不同失败；从真源重新生成LF后检查通过，语义/Git diff无变化。

## C包本机测量与服务恢复（2026-11-14）

首次固定案例真实CLI在Base权重加载时抛出MemoryError（正式Runtime对外为KNOWLEDGE_RETRIEVAL_FAILED）；独立只读本地加载诊断也复现MemoryError，不是缺权重，不下载/换模型。只读检查开发库无pending/running Run、无未完成IndexJob后，暂停本次启动的MiniMew API/Worker释放其模型副本，前端、数据库及其他应用保持。此后模型加载与对照成功；不据此保证其他设备资源或认定内存唯一根因。

默认每案例/路线3样本的两次检查均成功，最终CLI收尾逻辑完善后用`--samples 1 --write-observation`复验通过；同为28个路线观察与11个正式Run受控场景，不累加成不同案例数量。原3样本报告在忽略的`.artifacts/bootstrap/c-suite-three-samples.json`，最终完整报告`.artifacts/rag-suite-check.json`。下表为前者7个问题各自样本中位数的范围，不是总体p50或可靠p95：

| 路线 | 各问题3样本中位数范围（秒） |
| --- | --- |
| Dense | 约0.031–0.047 |
| Hybrid | 约0.047–0.078 |
| 补全 | 约0.047–0.078 |
| Base重排 | 约0.875–1.172 |

该次冷加载约11.344秒。这是单机、小语料、受控负载的描述性记录；受控模型不代表真实DeepSeek时延/费用。最终1样本的selection_stable为null，不拿它证明排序稳定。临时PG与自有1个scope均清理，开发库head0014/既有Run与Job状态计数保持，未修改.env或消费开发任务。

完成后重新后台启动API 8001与独立Worker，本地模型加载完成；启动器PID与日志位于忽略的`.artifacts/bootstrap/c-services.json`和`c-api.*`/`c-worker.*`，前端仍5174。共同案例/覆盖结论、源码和授权只见交接与第六章，设备文档不另维护学习进度。

## 异常反馈专项本机快照（2026-11-14）

定向后端/PG与浏览器均使用自有临时库、受控Provider，无收费调用或本地模型重排矩阵。真实API/PG浏览器后端日志已实际出现`execution_diagnostic`，Run编号与页面对应，包含安全文件/函数/行号；自有后端/库按原CLI退出清理。辅助运行记录保存于忽略的`.artifacts/bootstrap/errors-*.log`。

本机默认Playwright headless-shell v1248缺失，首次1440/390两个场景未能启动；显式安装仅headless-shell下载在0%未推进，已停止自有安装进程，未改生产浏览器。使用已安装`C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe`，显式设置`MINIMEW_TEST_BROWSER_CHANNEL=msedge`后两场景通过；真实聊天API/PG场景也通过。该变量仅供测试选择channel，未写.env，未设置时仍沿用Playwright原默认。390px截图无横向溢出；可重看忽略的`.artifacts/errors-mobile.png`（为视觉复核重复390用例收集）和`.artifacts/chat-live.png`。

源码稳定后只读核对开发库无pending/running Run、IndexJob均succeeded，head0014不变；仅重启自有API加载最终日志代码，Worker与Vite保留。最终启动器在`.artifacts/bootstrap/errors-services.json`，日志`errors-api-final.*`；ready与前端访问正常。设备记录不复制学习授权或测试进度，共同结果以交接与章节为准。

## 格式与覆盖修复本机快照（2026-11-14）

真实本地对照使用既有CPU/模型缓存/Milvus。为避免此前已观察到的多份Base副本内存分配失败，确认开发库无进行中的Run/Job后暂停自有API/Worker进行验收；其他应用及基础设施保持。完整矩阵28观察/11场景通过，格式故障真实知识链在正文字符保护完善后再次通过（4受控请求、6事件、1引用），自有scope与临时库清理，收费0。原3/3对照结果与本机完整报告保存在忽略的`.artifacts/rag-suite-check.json`/`.artifacts/knowledge-check.json`，运行日志见`repairs-*.log`。

API/Worker已恢复并加载最终源码，启动器PID在忽略的`.artifacts/bootstrap/repairs-services.json`，日志`repairs-api-final.*`/`repairs-worker-final.*`；Vite 5174保留。只读事务核对未来新Run冻结schema_version=4，repair_limit=1、aggregation=representatives-v1；未编辑.env或开发历史，开发库head仍0014、已有Run/IndexJob状态计数保持。默认行为来源于提交的应用设置，非设备专用覆写；其他设备仍须现场核对。

## 第七章 A 包设备验证快照（2026-11-14）

- 沿用本机 PostgreSQL/Milvus/Redis 和既有 CPU Embedding/Base 缓存，没有下载新模型、安装新依赖或修改 `.env`。正式模型/策略选择仍见共同交接。
- 开发库由 0014→0015→0016；每次升级前停止本项目 API/Worker，升级后 alembic check 无漂移，既有表行数/内容指纹保持；新增 run_checkpoints 为空。最终仍为 2 个 failed/3 个 succeeded Run、2 个 succeeded Index Job，没有创建开发库验收聊天或迁移旧资料。Worker 重启后的心跳属于正常运行写入。
- 两独立 API 进程的临时 PG/真实 CPU/Milvus 续接链通过，4 次受控模型调用/9 事件/1 引用，收费 0；1 个自有向量 scope 精确删除，临时库清理。Windows 验收脚本早期阻塞在 NumPy DLL 首次导入，stdin 控制线程后移至应用资源初始化后解决；本地 HTTP 显式 trust_env=False，模型设置离线读取。没有修改系统代理或重下权重。
- 390/1280 两项补充/刷新浏览器场景通过，使用既有 Edge channel；未下载浏览器。截图保存在本机 frontend/test-results，未提交。
- 最终 API 8001、前端 5174 返回 200，API live/ready 均 200；正式 Worker 已重新启动，最近 10 秒内有 running 心跳。只读冻结探针确认之后的新 Run 为 v5、等待 900 秒；没有为探针创建 Run。启动元数据/日志在忽略目录 `.artifacts/bootstrap/chapter07-*`。
- 全量前端格式检查仍提示 39 个未改文件告警，本包定向文件格式、类型和构建通过；没有扩大全量格式化。

## 第七章 B 包设备验证快照（2026-11-14）

- 沿用本机既有 PG/Redis/Milvus 与 CPU Embedding/Base 缓存，没有安装新依赖、下载权重或编辑 `.env`。三 API 进程验收设置 HF/Transformers 离线，只读已有缓存；受控模型不联网计费。
- 升级前只读确认开发库没有 pending/running/resume_pending，已有 Run 为 failed 2 / succeeded 4，Index Job succeeded 2；与 A 较早快照的3个成功Run不同，以本次现场事实为准。停止本项目 API/Worker 后保存全部业务表行数/内容指纹，0016→0017 与 alembic check 通过；排除新 deadline 列后原数据全部一致，新列历史值均 NULL。没有迁移旧设备数据或创建开发库验收 Run。
- 真实 CPU/Milvus/PG 的三个独立 API 进程按暂停、仅接收、续接运行，4次受控调用、9事件、1引用，收费0；临时库及1自有scope清理。完整报告 `.artifacts/hitl-check.json`，各进程日志 `.artifacts/hitl-pause.log`、`hitl-accept.log`、`hitl-resume.log`。测试进程硬退出使用 nonce 和子进程真实 PID 握手，未关闭其他项目。
- API 8001 与独立索引 Worker 已恢复，API live/ready 与前端5174均200，最近10秒存在running Worker心跳。只读探针确认 head0017、新Run快照v5/等待900秒、lease20/heartbeat5，开发Run/Job计数保持。探针不创建Run；启动器/日志/指纹位于忽略目录 `.artifacts/bootstrap/chapter07b-*`。
- Python辅助脚本显式UTF-8，避免Windows默认GBK解码中文文件失败；不改变系统编码。前端/浏览器未变，本轮未重复运行UI或下载浏览器，既有39处全量格式告警仍保留。

共同进度、实际反馈、恢复边界与后续授权只见交接和第七章，本文不维护另一套学习状态；这些本机快照不代表其他设备已运行。
