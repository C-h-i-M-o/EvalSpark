import json
import shutil
import subprocess
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _compose_config() -> dict[str, Any]:
    if shutil.which("docker") is None:
        import pytest

        pytest.skip("当前测试环境没有 Docker CLI，跳过 Compose 集成契约测试")

    result = subprocess.run(
        ["docker", "compose", "config", "--format", "json"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stderr
    config: dict[str, Any] = json.loads(result.stdout)
    safe_environment_keys = {"VITE_BACKEND_TARGET", "CHOKIDAR_USEPOLLING"}
    for service in config.get("services", {}).values():
        environment = service.get("environment")
        if environment:
            service["environment"] = {
                key: value if key in safe_environment_keys else "<已脱敏>"
                for key, value in environment.items()
            }
    return config


def _read_env_example() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key] = value
    return values


def test_compose_defines_the_full_development_stack() -> None:
    config = _compose_config()

    assert set(config["services"]) == {"mysql", "migrate", "backend", "frontend"}
    assert set(config["volumes"]) == {"mysql_data", "frontend_node_modules"}


def test_compose_keeps_mysql_internal_and_orders_service_startup() -> None:
    services = _compose_config()["services"]

    assert "ports" not in services["mysql"]
    assert services["migrate"]["depends_on"]["mysql"]["condition"] == "service_healthy"
    assert services["backend"]["depends_on"]["migrate"]["condition"] == "service_completed_successfully"
    assert services["frontend"]["depends_on"]["backend"]["condition"] == "service_healthy"


def test_compose_enables_hot_reload_without_host_dependencies() -> None:
    services = _compose_config()["services"]

    assert services["backend"]["command"] == [
        "python",
        "-m",
        "uvicorn",
        "app.main:app",
        "--reload",
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
    ]
    assert services["frontend"]["environment"]["VITE_BACKEND_TARGET"] == "http://backend:8000"
    assert services["frontend"]["environment"]["CHOKIDAR_USEPOLLING"] == "true"

    frontend_targets = {mount["target"] for mount in services["frontend"]["volumes"]}
    assert frontend_targets == {"/app", "/app/node_modules"}


def test_development_dockerfiles_pin_runtime_lines_and_locked_installs() -> None:
    backend_path = PROJECT_ROOT / "backend" / "Dockerfile.dev"
    frontend_path = PROJECT_ROOT / "frontend" / "Dockerfile.dev"

    assert backend_path.is_file()
    assert frontend_path.is_file()

    backend = backend_path.read_text(encoding="utf-8")
    frontend = frontend_path.read_text(encoding="utf-8")
    assert "FROM python:3.12-slim" in backend
    assert "PIP_ROOT_USER_ACTION=ignore" in backend
    assert 'python -m pip install --no-cache-dir -e ".[dev]"' in backend
    assert "FROM node:22-bookworm-slim" in frontend
    assert "corepack prepare pnpm@10.15.1 --activate" in frontend
    assert "pnpm install --frozen-lockfile" in frontend


def test_environment_example_uses_compose_database_host_and_fixed_ports() -> None:
    values = _read_env_example()

    assert values["MYSQL_HOST"] == "mysql"
    assert values["MYSQL_USER"] == "multichateval"
    assert values["DATABASE_URL"].startswith("mysql+aiomysql://multichateval:")
    assert "@mysql:3306/" in values["DATABASE_URL"]
    assert values["BACKEND_PORT"] == "8000"
    assert values["FRONTEND_PORT"] == "5174"
    assert values["JWT_SECRET_KEY"] == "CHANGE_ME"


def test_mysql_initial_schema_marks_the_matching_alembic_baseline() -> None:
    init_sql = (
        PROJECT_ROOT / "docker" / "mysql" / "init" / "001_schema.sql"
    ).read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS alembic_version" in init_sql
    assert "'20260612_01'" in init_sql
