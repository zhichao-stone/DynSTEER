from pathlib import Path


def test_docker_compose_uses_host_user_and_project_uv_paths() -> None:
    """验证 Docker 运行时使用宿主机用户，并将 uv 目录放在项目目录内。"""
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")

    assert 'user: "${DYNSTEER_HOST_UID:-0}:${DYNSTEER_HOST_GID:-0}"' in compose
    assert "UV_PROJECT_ENVIRONMENT: /workspace/DynSTEER/.venv" in compose
    assert "UV_CACHE_DIR: /workspace/DynSTEER/.uv-cache" in compose
    assert "dynsteer-venv:/opt/venv" not in compose
    assert "uv-cache:/root/.cache/uv" not in compose


def test_start_script_exports_host_user_for_compose() -> None:
    """验证宿主机侧启动脚本会把当前 UID/GID 传给 Docker Compose。"""
    script = Path("scripts/start.sh").read_text(encoding="utf-8")

    assert 'DYNSTEER_HOST_UID="${DYNSTEER_HOST_UID:-$(id -u)}"' in script
    assert 'DYNSTEER_HOST_GID="${DYNSTEER_HOST_GID:-$(id -g)}"' in script
    assert "DYNSTEER_HOST_UID" in script
    assert "DYNSTEER_HOST_GID" in script
    assert "UV_PROJECT_ENVIRONMENT:-.venv" in script
    assert "UV_CACHE_DIR:-.uv-cache" in script
