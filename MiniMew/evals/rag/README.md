# MiniMew离线评测

本目录仅属于MiniMew，不修改上级SuperMew评测资产。

`fixtures/`是人工编写的四个`contract_smoke`观察：数额、供餐例外、无资料与预期故障。
`corpus.json`保存显式输入资料，`dataset.json`保存独立人工标准，`observation.json`保存静态事实。
这些样本不是模型生成质量或真实性证明，不改写第六章历史观察。

`src/minimew/evaluation/protocol.py`是离线Pydantic类型真源，`schema/`为生成物：

```powershell
uv run --no-sync python scripts/generate_evaluation_schema.py
uv run --no-sync python scripts/generate_evaluation_schema.py --check
uv run --no-sync pytest -q --confcutdir=tests/evaluation tests/evaluation
```

来源坐标是Unicode字符零基半开区间，依赖固定资料hash，不以临时chunk UUID跨设备匹配。
输入严格拒绝未知字段、重复ID/JSON键、非法状态、丢失Case和伪造来源。
引用目录对应关系属于评分检查；材料本身的内容身份无效则拒绝输入。

测试专用命令隔离旧的在线App全局夹具，因此不要求LangGraph/模型/数据库可用；既有测试夹具不变。
`evaluation/scoring.py`提供纯评分，Report核对逐Case与聚合一致；分开记录全体/quality/fault切片，
材料覆盖不能推出答案正确，语义指标尚未评价。

## 运行离线链

在MiniMew目录运行，无需`.env`、在线依赖、模型或数据库：

```powershell
uv run --no-sync python -m minimew.evaluation.cli validate --dataset evals/rag/fixtures/dataset.json --corpus evals/rag/fixtures/corpus.json --observations evals/rag/fixtures/observation.json
uv run --no-sync python -m minimew.evaluation.cli score --dataset evals/rag/fixtures/dataset.json --corpus evals/rag/fixtures/corpus.json --observations evals/rag/fixtures/observation.json --format text
uv run --no-sync python -m minimew.evaluation.cli compare --dataset evals/rag/fixtures/dataset.json --corpus evals/rag/fixtures/corpus.json --observations evals/rag/fixtures/observation.json --policy evals/rag/fixtures/gate-policy.json --baseline evals/rag/fixtures/baseline.json --format text
```

默认JSON输出是同一个`EvaluationArtifact`（Report＋Gate）。使用`--output report.json`可明确导出，
后续用`--baseline report.json`显式选作参考。CLI拒绝覆盖任一输入/基线文件，比较本身不更新baseline。
退出码：0=校验或Gate通过；1=已形成报告但不达标/证据不足/不可比较；2=无效输入或输出错误。
诊断只提供稳定code和受控计数，不输出原输入/凭据。

本目录的baseline由上述四条人工静态观察经评分器生成并经逐项测试核对，仅作为计分流程参照，
不是模型质量baseline、签名或真实执行证明。Gate要求live来源或语义评分时，这份观察不能通过。
Report中的模式标签来自离线输入，真实可信采集将在B包实现。

首版Gate检查正常案例完整性/错误、故障预期、预算、关键Case、必需指标和阈值；逐条展示原因。
baseline固定Dataset/语料/评分规则/来源模式，`permitted_changes`显式声明策略、实现等实验变量，
未声明差异不给差值。初始政策只针对`contract_smoke`，语义正确性仍为未评价。
`required_claims/forbidden_claims`是未来人工/Evaluator的标准，当前不以字串匹配计算语义分。

schema只描述静态类型；跨文件的内容hash/身份/区间/完整性及聚合关系由严格加载器与纯评分边界校验。
新增真实事实采集须复用同一Observation/Scorer接口，不复制RAG图或用Job ID冒充聊天Run。

完整设计见[第八章](../../chapters/08-RAG评测闭环.md)。

## B包持久受控执行v2

B包沿用A包Dataset人工标准，新增CorpusV2、JobRequest、ObservationV2、ArtifactV2与独立Judge量表。
旧六份v1 schema、fixtures和baseline保持原义；新schema用`*-v2.json`单独生成。
CLI按显式schema_version分派，拒绝跨版本baseline；v2默认只接受controlled来源。
结构材料覆盖/引用仍在structural内，Judge的正确性、完整性、依据性单独为0–2分，默认每项至少2分。
无Judge为insufficient，评分故障不变成0分或空知识；比较保存完整baseline指纹、各项前后均值与差值。
受控输出只证明接口、状态和评分流程，不证明真实模型语义质量；无真实token usage，不用字符数冒充。

执行前先在MiniMew目录应用迁移（现有API/Index Worker需要同步代码和schema版本）：

```powershell
uv run --env-file .env --no-sync alembic upgrade head
```

为Dataset准备`mapping.json`，格式为`{"policy":"真实已发布DocumentVersion UUID"}`，key对应Dataset source_id。
只接受本用户当前已发布的版本，原文hash/长度须与Dataset一致；准备会复制已核验的向量/文本到独立Milvus集合，
隔离BM25全局统计，不上传新用户文档、不改current version。专属scope保留7天，Job期限不得超过它。

```powershell
uv run --env-file .env --no-sync python -m minimew.evaluation.manage prepare --user <用户UUID> --dataset dataset.json --mapping mapping.json
uv run --env-file .env --no-sync python -m minimew.evaluation.worker
```

prepare返回corpus_id。按`schema/job-request-v2.json`提交JobRequest至`POST /v1/evaluation-jobs`：
使用正常Access Token鉴权及`Idempotency-Key`请求头；body包含corpus_id、Dataset和逐Case受控Scenario。
Scenario的roles为grade/plan/rewrite协议响应，answer/repair_answer为受控生成，judge为受控量表结果；
预设澄清必须同时给expected_prompt与supplement，实际图触发并精确匹配后才接收。
Job冻结配置与源码/依赖指纹。Worker环境不同则明确失败，须在相同环境续接或重新创建实验。

- `GET /v1/evaluation-jobs/{id}`：持久Job和Case阶段。
- `POST /v1/evaluation-jobs/{id}/cancel`：持久请求取消，等待Worker写入终态。
- `GET /v1/evaluation-jobs/{id}/report`：完成后的ArtifactV2，运行中为null。

Worker共用正式知识计算、LangGraph、模型额度、Evidence与答案校验；使用真实Case身份，不伪造Run。
先提交答卷，再构造含人工标准的JudgeInput。每Case最多12次生成/策略请求，Judge最多2尝试；
Job默认112次联合模型尝试上限，Case默认120秒有效执行预算、Job默认1800秒绝对期限。
未知generating/judging结果不重放；已保存waiting/accepted/generated阶段可接续。
当前Worker单Job内串行；多个Worker通过PG领取隔离资格。API请求本身不执行评测。

报告接口返回的report对象可另存为v2 baseline；离线CLI也接受v2观察并使用相同评分入口：

```powershell
uv run --no-sync python -m minimew.evaluation.cli score --dataset dataset.json --corpus corpus-v2.json --observations observation-v2.json --output report-v2.json
uv run --no-sync python -m minimew.evaluation.cli compare --dataset dataset.json --corpus corpus-v2.json --observations observation-v2.json --baseline report-v2.json --format text
```

`manage cleanup --user <UUID> --corpus <UUID>`仅清理所属独立集合且验证删除；任何Job仍引用时拒绝，
包括已完成Job。本包不提供自动归档/删除历史实验；不使用清空用户文档或共享collection作为清理方式。
prepare中断的ready=false产物保留在evaluation_corpora中，可按user_id定位并精确清理。

验收命令（模型须已经本地缓存、Milvus/PG须已启动；无下载、无收费）：

```powershell
uv run --env-file .env --no-sync python scripts/check_runtime.py tests/test_evaluation_runtime_pg.py tests/test_evaluation_migration.py
uv run --env-file .env --no-sync python scripts/check_evaluation.py
```

真实验收使用独立临时数据库、文档与评测集合，依次启动三个Worker进程，在waiting/accepted边界保存后退出，
验证同Case/同检查点续接、原文档版本不变及精确清理。`.artifacts/evaluation-check.json`保存最近一次实测摘要。
开发数据库不会由验收脚本升级；应用部署/重启需另按当前运行状态进行。
