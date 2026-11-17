"""显式文件输入的离线评测CLI，退出码0通过、1未达标、2输入无效。"""

import argparse
import json
import sys
from pathlib import Path

from minimew.evaluation.gate import EvaluationArtifact, GatePolicy, evaluate
from minimew.evaluation.protocol import (
    Dataset,
    EvaluationInputError,
    canonical,
    load,
    validate_inputs,
)
from minimew.evaluation.runtime_result import ArtifactV2, ObservationV2, compare_v2, score_v2
from minimew.evaluation.scoring import score
from minimew.evaluation.versioned import load_artifact, load_corpus, load_observations


def render(artifact: EvaluationArtifact) -> str:
    report, gate = artifact.report, artifact.gate
    summary = report.summary
    lines = [
        f"评测来源：{report.provenance.mode}（来源标签不是质量或真实性证明）",
        f"Gate：{gate.decision}",
        f"Case：应评{summary.expected}，观察{summary.observed}，完成{summary.completed}，"
        f"错误{summary.errors}，等待{summary.waiting}，取消{summary.cancelled}，缺失{summary.missing}",
        "质量切片指标：",
    ]
    for name in type(report.quality.metrics).model_fields:
        metric = getattr(report.quality.metrics, name)
        value = "未评价" if metric.value is None else f"{metric.value:.6f}"
        lines.append(
            f"  {name}：{value}；应评{metric.eligible}/已评{metric.evaluated}"
            f"/错误{metric.errors}/不可用{metric.unavailable}"
        )
    lines.append("逐题：")
    for case in report.cases:
        lines.append(
            f"  {case.case_id} [{case.kind}]：{case.outcome}，结果符合={case.outcome_match}，"
            f"材料={case.covered_count}/{case.gold_count}"
        )
    lines.append("逐条规则：")
    for rule in gate.rules:
        lines.append(f"  [{rule.status}] {rule.name}：期望{rule.expected}，实际{rule.actual}")
    for difference in gate.differences:
        lines.append(f"实验差异：{difference.field}，允许={difference.permitted}")
    for delta in gate.deltas:
        lines.append(
            f"质量指标差值：{delta.metric} {delta.baseline} → {delta.candidate}，差值{delta.change}"
        )
    lines.append("材料覆盖/引用结构不等于答案语义正确；本CLI不调用Evaluator。")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate", "score", "compare"))
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--judge-minimum", type=int, choices=(0, 1, 2), default=2)
    parser.add_argument("--baseline", type=Path, help="显式选择此前JSON评测产物作为参照")
    parser.add_argument("--format", choices=("json", "text"), default="json")
    parser.add_argument("--output", type=Path, help="显式写出；不得覆盖任何输入文件")
    args = parser.parse_args(argv)
    try:
        if args.command == "compare" and args.baseline is None:
            raise EvaluationInputError("BASELINE_REQUIRED")
        inputs = (args.dataset, args.corpus, args.observations, args.policy, args.baseline)
        if args.output is not None and any(
            p is not None and args.output.resolve() == p.resolve() for p in inputs
        ):
            raise EvaluationInputError("OUTPUT_OVERWRITES_INPUT")
        dataset = load(args.dataset, Dataset)
        corpus = load_corpus(args.corpus)
        observations = load_observations(args.observations)
        v2 = isinstance(observations, ObservationV2)
        validate_inputs(dataset, corpus, observations.structural if v2 else observations)
        if args.command == "validate":
            output = canonical({"status": "valid", "cases": len(dataset.cases)}) + "\n"
            code = 0
        else:
            policy = (
                load(args.policy, GatePolicy)
                if args.policy
                else GatePolicy(allowed_modes=("controlled",))
                if v2
                else GatePolicy()
            )
            baseline = load_artifact(args.baseline) if args.baseline else None
            if baseline is not None and isinstance(baseline, ArtifactV2) != v2:
                raise EvaluationInputError("BASELINE_SCHEMA_MISMATCH")
            if v2:
                artifact = score_v2(
                    dataset, corpus, observations, minimum=args.judge_minimum, policy=policy
                )
                if baseline is not None:
                    artifact = compare_v2(artifact, baseline, policy)
                text = (
                    render(artifact.structural) + f"受控Judge（0–2）：{artifact.decision}；"
                    f"总模型尝试：{artifact.total_model_attempts}\n"
                    "受控输出只验证流程，不证明模型语义质量。\n"
                )
                for result in artifact.evaluations:
                    value = (
                        f"正确性={result.judge.correctness}，完整性={result.judge.completeness}，"
                        f"依据性={result.judge.grounding}"
                        if result.judge
                        else f"未评价（{result.judge_error or 'JUDGE_UNAVAILABLE'}）"
                    )
                    text += f"  Judge {result.case_id}：{value}；已续接={result.resumed}\n"
                for delta in artifact.judge_deltas:
                    text += (
                        f"  Judge差值 {delta.metric}：{delta.baseline} → {delta.candidate}，"
                        f"差值{delta.change}\n"
                    )
                code = 0 if artifact.decision == "pass" else 1
            else:
                report = score(dataset, corpus, observations)
                artifact = EvaluationArtifact(
                    report=report,
                    gate=evaluate(report, policy, baseline.report if baseline else None),
                )
                text = render(artifact)
                code = 0 if artifact.gate.decision == "pass" else 1
            output = canonical(artifact) + "\n" if args.format == "json" else text
        if args.output:
            args.output.write_bytes(output.encode("utf-8"))
        else:
            # 避免Windows管道默认编码改变报告的Unicode字符。
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stdout.write(output)
        return code
    except EvaluationInputError as error:
        print(
            json.dumps({"status": "invalid", "code": error.code, "details": error.details}),
            file=sys.stderr,
        )
        return 2
    except OSError:
        print('{"status":"invalid","code":"REPORT_WRITE_FAILED"}', file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
