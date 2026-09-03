"""运行 Bash 入口并拦截 Docker CLI，验证隔离目标与失败后停止编排。"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/verify-rag.sh"


@pytest.fixture
def docker_stub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    if shutil.which("bash") is None:
        pytest.skip("需要容器中的 Bash；不能用扫描脚本文字替代行为测试")
    calls = tmp_path / "calls.jsonl"
    executable = tmp_path / "docker"
    executable.write_text(f"#!{sys.executable}\n" + '''import json, os, sys
from pathlib import Path
args = sys.argv[1:]
with Path(os.environ["RAG_TEST_CALLS"]).open("a", encoding="utf-8") as output:
    output.write(json.dumps(args) + "\\n")
if args[:2] == ["volume", "inspect"] and os.environ.get("RAG_TEST_NO_CACHE") == "1":
    sys.exit(1)
if "compose" in args and os.environ.get("RAG_TEST_FAIL_COMMAND") in args:
    sys.exit(17)
''', encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("RAG_TEST_CALLS", str(calls))
    return calls


def run_script(mode: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(SCRIPT), mode], capture_output=True, text=True, timeout=15)


def commands(path: Path) -> list[list[str]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.mark.parametrize("mode", ["unit", "integration"])
def test_modes_only_target_fixed_test_project_and_stop_before_business_services(docker_stub: Path, mode: str) -> None:
    result = run_script(mode)
    assert result.returncode == 0, result.stderr
    calls = commands(docker_stub)
    compose = [args for args in calls if args[0] == "compose"]
    for args in compose:
        assert args[args.index("--project-name") + 1] == "evalspark-rag-test"
        assert args[args.index("--env-file") + 1] == str(SCRIPT.parents[1] / "docker/rag-test.env")
        assert args[args.index("-f") + 1] == str(SCRIPT.parents[1] / "docker-compose.rag-test.yml")
        assert not {"down", "prune", "migrate", "backend", "frontend", "mysql"}.intersection(args)
    runs = [args for args in compose if "run" in args]
    assert all("--no-deps" in args and "--rm" in args for args in runs)
    if mode == "unit":
        assert not any("up" in args or "stop" in args for args in compose)
        assert len(runs) == 4 and runs[-1][-3:] == ["frontend-test", "pnpm", "build"]
    else:
        migration = next(index for index, args in enumerate(compose) if args[-2:] == ["--no-deps", "runner"])
        worker = next(index for index, args in enumerate(compose) if args[-3:] == ["up", "-d", "worker-test"])
        acceptance = next(index for index, args in enumerate(compose) if args[-1] == "acceptance-runner")
        assert migration < worker < acceptance


def test_build_failure_stops_before_tests_or_services(docker_stub: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_TEST_FAIL_COMMAND", "build")
    result = run_script("unit")
    assert result.returncode != 0
    assert not any("run" in args or "up" in args for args in commands(docker_stub))


def test_missing_cache_refuses_integration_without_creating_or_removing_volumes(docker_stub: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_TEST_NO_CACHE", "1")
    result = run_script("integration")
    assert result.returncode != 0 and "docker volume create evalspark_rag_model_cache" in result.stderr
    assert not any({"build", "run", "up", "create", "rm", "prune"}.intersection(args) for args in commands(docker_stub))


def test_unknown_mode_never_calls_docker(docker_stub: Path) -> None:
    assert run_script("deploy").returncode == 2
    assert not docker_stub.exists()
