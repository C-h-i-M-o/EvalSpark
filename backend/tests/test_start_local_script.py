from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "start-local.sh"


def test_start_local_validates_configuration_before_compose_up() -> None:
    script = SCRIPT_PATH.read_text(encoding="utf-8")

    assert "CHANGE_ME" in script
    assert "docker compose config --quiet" in script
    assert "docker compose up --build" in script
    assert script.index("docker compose config --quiet") < script.index("docker compose up --build")


def test_start_local_requires_only_docker_on_the_host() -> None:
    script = SCRIPT_PATH.read_text(encoding="utf-8")

    assert "python" not in script.lower()
    assert "pnpm" not in script.lower()
    assert "lsof" not in script.lower()
    assert "curl" not in script.lower()
    assert ".venv" not in script
    assert "find_available_port" not in script


def test_start_local_never_removes_docker_volumes() -> None:
    script = SCRIPT_PATH.read_text(encoding="utf-8")

    assert "down -v" not in script
    assert "docker volume rm" not in script


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
