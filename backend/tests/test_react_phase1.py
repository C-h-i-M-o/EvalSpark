import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REACT_DIR = PROJECT_ROOT / "frontend"
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "start-local.sh"


def test_react_frontend_package_defines_phase1_commands_and_dependencies() -> None:
    package_json = json.loads((REACT_DIR / "package.json").read_text(encoding="utf-8"))

    assert package_json["type"] == "module"
    assert package_json["scripts"]["dev"] == "vite"
    assert package_json["scripts"]["build"] == "tsc -b && vite build"
    assert package_json["scripts"]["preview"] == "vite preview"
    assert package_json["scripts"]["test"] == "vitest run"
    assert package_json["dependencies"]["@vitejs/plugin-react"]
    assert package_json["dependencies"]["react"]
    assert package_json["dependencies"]["react-dom"]
    assert package_json["dependencies"]["react-router-dom"]
    assert "any" not in (REACT_DIR / "src" / "api" / "client.ts").read_text(encoding="utf-8")


def test_react_frontend_configures_backend_proxy_and_typescript_strict_mode() -> None:
    vite_config = (REACT_DIR / "vite.config.ts").read_text(encoding="utf-8")
    ts_config = json.loads((REACT_DIR / "tsconfig.app.json").read_text(encoding="utf-8"))

    assert 'process.env.VITE_BACKEND_TARGET || "http://localhost:8000"' in vite_config
    assert 'process.env.VITE_DEV_PORT || "5174"' in vite_config
    assert '"/api"' in vite_config
    assert "changeOrigin: true" in vite_config
    assert ts_config["compilerOptions"]["strict"] is True
    assert ts_config["compilerOptions"]["noImplicitAny"] is True


def test_react_frontend_exposes_phase1_health_and_auth_recovery() -> None:
    app_source = (REACT_DIR / "src" / "App.tsx").read_text(encoding="utf-8")
    api_source = (REACT_DIR / "src" / "api" / "client.ts").read_text(encoding="utf-8")
    auth_source = (REACT_DIR / "src" / "features" / "auth" / "auth.ts").read_text(encoding="utf-8")
    auth_context_source = (REACT_DIR / "src" / "features" / "auth" / "AuthContext.tsx").read_text(encoding="utf-8")
    route_guard_source = (REACT_DIR / "src" / "routes" / "RouteGuards.tsx").read_text(encoding="utf-8")

    assert "getHealthStatus" in api_source
    assert 'fetchJson<HealthStatus>("/api/health")' in api_source
    assert "getCurrentUser" in api_source
    assert 'fetchJson<UserProfile>("/api/auth/me")' in api_source
    assert 'postJson<UserProfile>("/api/auth/login", credentials)' in api_source
    assert 'postJson<UserProfile>("/api/auth/register", credentials)' in api_source
    assert 'postJson<void>("/api/auth/logout")' in api_source
    assert "credentials: \"include\"" in api_source
    assert "isUnauthorizedError" in auth_source
    assert "AuthProvider" in app_source
    assert "ProtectedRoute" in app_source
    assert "PublicOnlyRoute" in app_source
    assert "getCurrentUser" in auth_context_source
    assert "正在恢复登录态" in route_guard_source


def test_start_local_script_uses_react_frontend_without_touching_vue_frontend() -> None:
    script = SCRIPT_PATH.read_text(encoding="utf-8")

    assert "docker compose up --build" in script
    assert "frontend" in (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "vue-frontend" not in script
