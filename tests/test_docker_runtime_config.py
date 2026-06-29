import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


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
    assert ".dynsteer-runtime" in gitignore
    assert ".dynsteer-runtime/" in dockerignore


def test_dockerfile_uses_bootstrap_venv_without_runtime_opt_venv() -> None:
    """验证镜像只保留 bootstrap venv，运行期默认使用项目目录 .venv。"""
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")

    assert "UV_PROJECT_ENVIRONMENT=/workspace/DynSTEER/.venv" in dockerfile
    assert 'PATH="/workspace/DynSTEER/.venv/bin:${PATH}"' in dockerfile
    assert "UV_PROJECT_ENVIRONMENT=/opt/bootstrap-venv uv sync" in dockerfile
    assert "mkdir -p /opt/venv" not in dockerfile
    assert "cp -a /opt/bootstrap-venv/. /opt/venv/" not in dockerfile


def test_start_script_prepares_runtime_data_root_without_mutating_manifest(tmp_path: Path) -> None:
    """验证 Docker 内 source_root 覆写只写入运行期副本，不修改原始 benchmark.json。"""
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is not available")
    if "system32" in bash.lower():
        pytest.skip("Windows WSL bash cannot access pytest Windows temp paths directly")
    project_root = tmp_path / "DynSTEER"
    data_root = project_root / "data" / "toolsandbox"
    data_root.mkdir(parents=True)
    original_manifest = {
        "benchmark": "toolsandbox",
        "source_root": "../ToolSandbox",
        "tool_backend": "DEFAULT",
        "language": "en",
    }
    (data_root / "benchmark.json").write_text(
        json.dumps(original_manifest, ensure_ascii=False, indent=4),
        encoding="utf-8",
    )
    (data_root / "run_config.json").write_text("[]", encoding="utf-8")
    start_script = tmp_path / "start-functions.sh"
    script_without_main = "\n".join(
        line for line in Path("scripts/start.sh").read_text(encoding="utf-8").splitlines() if line != 'main "$@"'
    )
    start_script.write_text(
        script_without_main,
        encoding="utf-8",
    )
    command = textwrap.dedent(
        f"""
        set -euo pipefail
        source "{start_script.as_posix()}"
        export DYNSTEER_IN_DOCKER=1
        prepare_runtime_data_root "{project_root.as_posix()}" toolsandbox "{data_root.as_posix()}" /workspace/benchmark-sources/toolsandbox
        """
    )

    result = subprocess.run([bash, "-lc", command], capture_output=True, text=True, check=True)

    effective_data_root = Path(result.stdout.strip())
    runtime_manifest = json.loads((effective_data_root / "benchmark.json").read_text(encoding="utf-8"))
    source_manifest = json.loads((data_root / "benchmark.json").read_text(encoding="utf-8"))
    assert effective_data_root == project_root / ".dynsteer-runtime" / "data" / "toolsandbox"
    assert runtime_manifest["source_root"] == "/workspace/benchmark-sources/toolsandbox"
    assert source_manifest == original_manifest
    assert (effective_data_root / "run_config.json").exists()
