# MiniMew离线评测模块

继承[MiniMew约定](../../../AGENTS.md)。本模块是独立练习的评测接口，不导入上级SuperMew业务包。

- `protocol.py`是Pydantic类型真源；`scoring.py`是纯评分/Report；`gate.py`拥有比较/政策/产物；`cli.py`只组织显式文件输入输出。模块导入/计分不加载模型、访问网络或连接数据库。
- `evals/rag/schema/`从`scripts/generate_evaluation_schema.py`生成，禁止手改。输入的JSON键、ID、内容指纹与Unicode字符区间必须严格核验。
- Dataset是人工标准，Observation是事实；材料覆盖和引用结构不能变成语义正确性。无gold/未评价为unavailable，故障切片单列，失败不能从质量分母消失。
- 改变计分语义须更新scorer_version及对应schema/样本对照，不把新旧规则报告视作同条件结果。baseline必须显式选择，不通过改gold或重写历史观察迎合实现。
- Provenance标签/内容指纹不是签名或真实执行证明；真实采集/Judge属于后续B包，不用手写live标签充当质量验收。
- 运行`uv run --no-sync pytest -q --confcutdir=tests/evaluation tests/evaluation`隔离旧在线App全局夹具，不为离线测试安装在线依赖。
- 定向Ruff、schema生成`--check`和CLI足够覆盖纯模块变化；按实际行为触发其他测试，避免无变化重复全仓验证。
