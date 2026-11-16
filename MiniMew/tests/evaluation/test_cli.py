"""独立进程CLI与离线依赖边界。"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "evals/rag/fixtures"
BASE = [
    "--dataset",
    str(FIXTURES / "dataset.json"),
    "--corpus",
    str(FIXTURES / "corpus.json"),
    "--observations",
    str(FIXTURES / "observation.json"),
]


def cli(command, *args):
    return subprocess.run(
        [sys.executable, "-m", "minimew.evaluation.cli", command, *BASE, *map(str, args)],
        cwd=ROOT,
        capture_output=True,
        encoding="utf-8",
        timeout=15,
    )


def test_validate_score_and_baseline_compare_are_deterministic(tmp_path):
    assert json.loads(cli("validate").stdout) == {"status": "valid", "cases": 4}
    baseline = tmp_path / "reference.json"
    assert cli("score", "--output", baseline).returncode == 0
    first = cli("compare", "--baseline", baseline)
    second = cli("compare", "--baseline", baseline)
    assert first.returncode == second.returncode == 0
    assert first.stdout == second.stdout
    value = json.loads(first.stdout)
    assert value["gate"]["decision"] == "pass"
    assert all(d["change"] == 0 for d in value["gate"]["deltas"])
    assert json.loads(baseline.read_text(encoding="utf-8"))["gate"]["baseline_fingerprint"] is None


def test_text_report_distinguishes_fault_and_unavailable_semantics():
    result = cli("score", "--format", "text")
    assert result.returncode == 0
    assert "错误1" in result.stdout and "semantic_correctness：未评价" in result.stdout
    assert "timeout [fault]" in result.stdout and "不调用Evaluator" in result.stdout


def test_gate_failure_uses_exit_one_with_full_report(tmp_path):
    policy = tmp_path / "policy.json"
    policy.write_text('{"allowed_modes":["live"]}', encoding="utf-8")
    result = cli("score", "--policy", policy)
    assert result.returncode == 1
    assert json.loads(result.stdout)["gate"]["decision"] == "fail"


@pytest.mark.parametrize("mode", ["missing", "duplicate_key", "secret"])
def test_invalid_input_uses_exit_two_without_raw_input(mode, tmp_path):
    path = tmp_path / "bad.json"
    value = json.loads((FIXTURES / "observation.json").read_text(encoding="utf-8"))
    if mode == "missing":
        value["cases"].pop()
    elif mode == "secret":
        value["api_key"] = "never-print-this"
    path.write_text(
        '{"x":1,"x":2}' if mode == "duplicate_key" else json.dumps(value), encoding="utf-8"
    )
    # 最后的显式参数替换默认观察文件。
    result = cli("score", "--observations", path)
    assert result.returncode == 2 and not result.stdout
    assert "never-print-this" not in result.stderr
    diagnostic = json.loads(result.stderr)
    if mode == "missing":
        assert diagnostic["details"] == {"expected": 4, "observed": 3, "missing": 1}


def test_output_cannot_overwrite_baseline_or_inputs(tmp_path):
    baseline = tmp_path / "baseline.json"
    cli("score", "--output", baseline)
    original = baseline.read_bytes()
    result = cli("compare", "--baseline", baseline, "--output", baseline)
    assert result.returncode == 2 and baseline.read_bytes() == original
    assert "OUTPUT_OVERWRITES_INPUT" in result.stderr


def test_compare_requires_explicit_baseline():
    result = cli("compare")
    assert result.returncode == 2 and "BASELINE_REQUIRED" in result.stderr


def test_schema_generation_drift_is_clean():
    result = subprocess.run(
        [sys.executable, "scripts/generate_evaluation_schema.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        encoding="utf-8",
        timeout=15,
    )
    assert result.returncode == 0, result.stderr


def test_cli_runs_with_network_and_online_model_database_imports_forbidden():
    program = """
import importlib.abc, socket, sys
class OfflineOnly(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        online = {'torch','langgraph','sqlalchemy','pymilvus','openai','redis'}
        if fullname.split('.')[0] in online or fullname == 'minimew.app':
            raise AssertionError('Online import: ' + fullname)
def forbidden(*args, **kwargs):
    raise AssertionError('Network access')
sys.meta_path.insert(0, OfflineOnly())
socket.create_connection = forbidden
socket.socket = forbidden
from minimew.evaluation.cli import main
raise SystemExit(main(sys.argv[1:]))
"""
    result = subprocess.run(
        [sys.executable, "-c", program, "score", *BASE],
        cwd=ROOT,
        capture_output=True,
        encoding="utf-8",
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["gate"]["decision"] == "pass"
