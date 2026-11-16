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
