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
材料覆盖不能推出答案正确，语义指标尚未评价。Gate与CLI在同一A包的8.3继续交付。
完整设计见[第八章](../../chapters/08-RAG评测闭环.md)。
