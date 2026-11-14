"""第六章固定案例与原文覆盖核对；不是LLM Judge或生产检索实现。"""

import hashlib
import json
import math
import statistics
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ROOT = Path(__file__).resolve().parents[1]


class Case(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    id: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    question: str = Field(min_length=1, max_length=1024)
    mode: Literal[
        "direct",
        "rewrite",
        "plan",
        "insufficient",
        "ambiguous",
        "conflicting",
        "partial",
        "failure",
        "retry",
        "budget",
    ]
    required: tuple[str, ...]
    unavailable: tuple[str, ...]
    queries: tuple[str, ...]
    rewrite: str | None
    note: str = Field(min_length=1)

    @model_validator(mode="after")
    def route_inputs(self):
        planned = self.mode in {"plan", "partial", "failure", "budget"}
        if planned != (2 <= len(self.queries) <= 4) or len(set(self.queries)) != len(self.queries):
            raise ValueError("Invalid controlled plan")
        if self.mode in {"rewrite", "insufficient", "partial"} and not self.rewrite:
            raise ValueError("Controlled rewrite required")
        if any(not s.strip() for s in (*self.required, *self.unavailable, *self.queries)):
            raise ValueError("Blank case value")
        if len(set(self.required)) != len(self.required):
            raise ValueError("Duplicate expected aspect")
        return self


class Cases(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    version: Literal[1]
    source: Literal["chapter06-policy.md"]
    cases: tuple[Case, ...] = Field(min_length=1)


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def load_cases():
    directory = ROOT / "examples/rag"
    suite = Cases.model_validate_json(
        (directory / "chapter06-cases.json").read_text(encoding="utf-8")
    )
    source = (directory / suite.source).read_text(encoding="utf-8")  # canonical UTF-8/LF
    if len({c.id for c in suite.cases}) != len(suite.cases):
        raise ValueError("Duplicate case ID")
    for case in suite.cases:
        if any(source.count(s) != 1 for s in case.required):
            raise ValueError(f"Case anchors must identify one original span: {case.id}")
        if any(s in source for s in case.unavailable):
            raise ValueError("Unavailable claim occurs in corpus")
    return suite, source, fingerprint({"cases": suite.model_dump(mode="json"), "source": source})


def coverage(case, source, matches):
    intervals = []
    for match in matches:
        start, end = match["start"], match["end"]
        text = match["text"]
        if (
            not 0 <= start < end <= len(source)
            or source[start:end] != text
            or hashlib.sha256(text.encode()).hexdigest() != match["content_sha256"]
        ):
            raise ValueError("Material is not the original source span")
        intervals.append((start, end))
    supported, missing = [], []
    for anchor in case.required:
        start = source.index(anchor)
        cursor, end = start, start + len(anchor)
        for left, right in sorted(intervals):
            if left <= cursor:
                cursor = max(cursor, right)
        (supported if cursor >= end else missing).append(anchor)
    return {
        "covered": supported,
        "missing": missing,
        "coverage": len(supported) / len(case.required) if case.required else None,
        "unsupported_claims": list(case.unavailable),
        "note": "Literal source-span coverage only; not answer correctness or Grader accuracy",
    }


def timings(samples):
    if not samples or any(not math.isfinite(s) or s < 0 for s in samples):
        raise ValueError("Invalid timing samples")
    return {
        "samples": len(samples),
        "seconds": samples,
        "sample_median_seconds": statistics.median(samples),
        "sample_max_seconds": max(samples),
        "p95": None,
        "note": "Small warm sample; no reliable p95 or production latency estimate",
    }


class ScriptedModel:
    """仅供显式验收注入：预置路由/失败，不进行真实模型语义判断。"""

    def __init__(self, case):
        self.case = case
        self.attempts = []
        self.counts = Counter()
        self.drafts = []
        self.tool_result = None
        self.retry_failures = 0

    async def stream(self, request):
        from minimew.execution.messages import ModelTurn, ToolCall
        from minimew.execution.provider import ProviderCompleted, ProviderFailure

        purpose, case = request.purpose, self.case
        attempt = {"purpose": purpose, "error": None}
        self.attempts.append(attempt)
        if purpose == "answer":
            if request.messages[-1].role != "tool":
                turn = ModelTurn(
                    "tool_calls",
                    "",
                    (
                        ToolCall(
                            "case_search", "knowledge_search", json.dumps({"query": case.question})
                        ),
                    ),
                )
            else:
                self.tool_result = json.loads(request.messages[-1].content)
                paragraphs = []
                for match in self.tool_result["matches"]:
                    quote = " ".join(match["text"][:120].split())
                    paragraphs.append(f"资料摘录：{quote}[{match['citation_id']}]")
                text = "## 文档依据\n" + "\n\n".join(paragraphs) if paragraphs else ""
                grade = self.tool_result.get("assessment", {})
                text += (
                    "\n\n## 模型补充（未由本次资料验证）\n"
                    f"受控评判状态：{grade.get('status')}；缺口："
                    + "；".join(grade.get("missing", []))
                    + "。本次只验证编排与引用协议，摘录不代表完整答案。"
                )
                turn = ModelTurn("text", text.strip())
            yield ProviderCompleted(turn)
            return

        data = json.loads(request.messages[-1].content)
        key = (purpose, data["question"])
        self.counts[key] += 1
        number = self.counts[key]
        original = data["question"] == case.question
        if purpose == "grade":
            self.drafts.append(data)
            retry = (case.mode == "budget" and number == 1) or (
                case.mode == "retry" and original and number == 1
            )
            failure = (case.mode == "failure" and not original) or (
                case.mode == "partial" and data["question"] == case.queries[1]
            )
            if retry or failure:
                attempt["error"] = "PROVIDER_ERROR"
                self.retry_failures += int(retry)
                raise ProviderFailure(retryable=retry)
            initial = original and number == (2 if case.mode == "budget" else 1)
            planned = case.mode in {"plan", "partial", "failure", "budget"}
            status = "sufficient"
            if case.mode in {"ambiguous", "conflicting"}:
                status = case.mode
            elif case.mode == "insufficient" or (initial and (planned or case.mode == "rewrite")):
                status = "insufficient"
            output = {
                "status": status,
                "supported": []
                if status == "insufficient"
                else [m["citation_id"] for m in data["materials"]],
                "missing": [] if status == "sufficient" else ["预置的资料缺口或适用范围待澄清"],
                "decompose": initial and planned,
            }
        elif purpose == "plan":
            output = {"questions": list(case.queries)}
        elif purpose == "rewrite":
            output = {"query": case.rewrite or case.question}
        else:
            raise AssertionError("Unexpected role")
        yield ProviderCompleted(ModelTurn("text", json.dumps(output, ensure_ascii=False)))


def portable_observation(report):
    """明确白名单导出：设备时延、身份、地址、Secret及模型原始消息不入共同资产。"""
    retrieval = []
    for entry in report["retrieval"]:
        retrieval.append(
            {k: entry[k] for k in ("case", "route", "coverage", "limited", "selection_stable")}
            | {
                "fallback": entry["trace"]["fallback"],
                "materials": [
                    {k: m[k] for k in ("start", "end", "content_sha256")}
                    for m in entry["materials"]
                ],
            }
        )
    return {
        "generated_by": "scripts/check_rag_suite.py --write-observation",
        "suite_fingerprint": report["suite_fingerprint"],
        "source_sha256": report["source_sha256"],
        "embedding": report["embedding"],
        "reranker": report["reranker"],
        "policies": {o["route"]: o["policy"] for o in report["retrieval"]},
        "retrieval_mode": "real_local_cpu_milvus",
        "reasoning_mode": "controlled_fixture",
        "scope_note": report["scope_note"],
        "model_quality_evaluated": False,
        "retrieval": retrieval,
        "runs": [
            {
                k: v
                for k, v in entry.items()
                if k
                in {
                    "case",
                    "mode",
                    "terminal",
                    "error",
                    "model_attempts",
                    "roles",
                    "retry_failures",
                    "retrieval_queries",
                    "tool_calls",
                    "assessment",
                    "coverage",
                    "limited",
                    "initial_draft_coverage",
                    "last_draft_coverage",
                    "persisted_citations",
                    "sufficient_with_missing_anchors",
                    "journal_types",
                }
            }
            for entry in report["runs"]
        ],
    }
