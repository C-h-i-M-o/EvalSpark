from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "start-local.sh"


def test_start_local_runs_alembic_migrations_before_backend() -> None:
    script = SCRIPT_PATH.read_text(encoding="utf-8")

    assert "run_migrations" in script
    assert ".venv/bin/alembic upgrade head" in script
    assert "run_migrations\n  start_backend" in script


def test_start_local_selects_available_ports_and_passes_backend_target_to_vite() -> None:
    script = SCRIPT_PATH.read_text(encoding="utf-8")

    assert "find_available_port" in script
    assert 'BACKEND_PORT="$(find_available_port "${BACKEND_PORT}")"' in script
    assert 'FRONTEND_PORT="$(find_available_port "${FRONTEND_PORT}")"' in script
    assert 'VITE_BACKEND_TARGET="http://${BACKEND_HOST}:${BACKEND_PORT}"' in script
    assert 'warn "端口 ${port} 已被占用，尝试端口 $((port + 1))。" >&2' in script
    assert "--strictPort" in script


def test_start_local_does_not_download_embedding_model() -> None:
    script = SCRIPT_PATH.read_text(encoding="utf-8")

    assert "sentence_transformers" not in script
    assert "prepare_embedding_model" not in script
    assert "SentenceTransformer" not in script
    assert "prepare_react_frontend\n  BACKEND_PORT" in script


def test_start_local_installs_locked_dependencies_and_checks_services() -> None:
    script = SCRIPT_PATH.read_text(encoding="utf-8")

    assert 'pip install -e ".[dev]"' in script
    assert "pnpm install --frozen-lockfile" in script
    assert 'REACT_FRONTEND_DIR="${PROJECT_ROOT}/frontend"' in script
    assert 'require_command lsof "请先安装 lsof。"' in script
    assert 'require_command curl "请先安装 curl。"' in script
    assert "docker compose version" in script
    assert 'wait_for_url "后端服务"' in script
    assert 'wait_for_url "React 前端服务"' in script


def test_comment_migration_supports_latest_init_schema() -> None:
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "20260606_add_user_comments.py"
    )
    migration = migration_path.read_text(encoding="utf-8")

    assert 'if "user_comments" not in tables:' in migration
    assert 'if "comment" in _existing_columns("user_feedback"):' in migration
