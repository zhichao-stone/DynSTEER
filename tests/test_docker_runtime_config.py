from pathlib import Path


def test_docker_compose_uses_host_user_and_project_uv_paths() -> None:
    """验证 Docker 运行时使用宿主机用户，并将 uv 目录放在项目目录内。"""
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")

    assert 'user: "${DYNSTEER_HOST_UID:-1000}:${DYNSTEER_HOST_GID:-100}"' in compose
    assert "HOME: /workspace/DynSTEER" in compose
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
    assert 'cp -R "$bootstrap_dir"/. "$venv_dir"/' in script
    assert 'cp -a "$bootstrap_dir"/. "$venv_dir"/' not in script


def test_project_uv_cache_is_ignored_by_git_and_docker() -> None:
    """验证项目内 uv cache 不会进入 git 或 Docker 构建上下文。"""
    gitignore = Path(".gitignore").read_text(encoding="utf-8")
    dockerignore = Path(".dockerignore").read_text(encoding="utf-8")

    assert ".uv-cache" in gitignore
    assert ".uv-cache/" in dockerignore


def test_dockerfile_uses_bootstrap_venv_without_runtime_opt_venv() -> None:
    """验证镜像只保留 bootstrap venv，运行期默认使用项目目录 .venv。"""
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")

    assert "UV_PROJECT_ENVIRONMENT=/workspace/DynSTEER/.venv" in dockerfile
    assert 'PATH="/workspace/DynSTEER/.venv/bin:${PATH}"' in dockerfile
    assert "UV_PROJECT_ENVIRONMENT=/opt/bootstrap-venv uv sync" in dockerfile
    assert "mkdir -p /opt/venv" not in dockerfile
    assert "cp -a /opt/bootstrap-venv/. /opt/venv/" not in dockerfile
