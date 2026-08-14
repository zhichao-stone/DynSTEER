detect_compose() {
    if docker compose version >/dev/null 2>&1; then
        echo "docker compose"
        return 0
    fi
    if command -v docker-compose >/dev/null 2>&1; then
        echo "docker-compose"
        return 0
    fi
    return 1
}

absolute_host_path() {
    local base_dir="$1"
    local input_path="$2"
    if [[ "$input_path" == /* || "$input_path" =~ ^[A-Za-z]:[\\/] ]]; then
        cd -- "$input_path" && pwd
    else
        cd -- "$base_dir/$input_path" && pwd
    fi
}

docker_mount_path() {
    local host_path="$1"
    if command -v cygpath >/dev/null 2>&1; then
        cygpath -w "$host_path"
        return 0
    fi
    printf '%s\n' "$host_path"
}

experiment_bootstrap_lines() {
    local project_root="$1"
    local experiment_config="$2"
    local container_project_root="${3:-}"

    local python_cmd=""
    if command -v python >/dev/null 2>&1; then
        python_cmd="python"
    elif command -v python3 >/dev/null 2>&1; then
        python_cmd="python3"
    else
        echo "Python is required to resolve experiment bootstrap information." >&2
        return 127
    fi

    local python_project_root="$project_root"
    local python_experiment_config="$experiment_config"
    if command -v cygpath >/dev/null 2>&1; then
        python_project_root="$(cygpath -m "$project_root")"
        if [[ -e "$experiment_config" ]]; then
            python_experiment_config="$(cygpath -m "$experiment_config")"
        fi
    fi

    MSYS2_ARG_CONV_EXCL="*" "$python_cmd" - "$python_project_root" "$python_experiment_config" "$container_project_root" <<'PY'
import json
import posixpath
import sys
from pathlib import Path

project_root = Path(sys.argv[1]).resolve()
raw_config_path = Path(sys.argv[2])
if raw_config_path.is_absolute():
    config_path = raw_config_path
else:
    cwd_candidate = raw_config_path.resolve()
    config_path = cwd_candidate if cwd_candidate.exists() else (project_root / raw_config_path).resolve()
container_project_root = str(sys.argv[3]).strip() if len(sys.argv) > 3 else ""

config = json.loads(config_path.read_text(encoding="utf-8"))
benchmarks = config.get("benchmarks", [])
if not isinstance(benchmarks, list) or not benchmarks:
    raise SystemExit("experiment config 必须包含非空 benchmarks 数组")


def resolve_data_root(spec: dict[str, object], benchmark: str) -> Path:
    raw_value = spec.get("data_root")
    if raw_value is None:
        raw_value = f"data/{benchmark}"
    path = Path(str(raw_value))
    if path.is_absolute():
        return path
    candidate = (config_path.parent / path).resolve()
    if candidate.exists():
        return candidate
    return (project_root / path).resolve()


seen: set[str] = set()
for spec in benchmarks:
    if not isinstance(spec, dict):
        continue
    benchmark = str(spec.get("benchmark") or "").strip()
    if not benchmark or benchmark in seen:
        continue
    data_root = resolve_data_root(spec, benchmark)
    manifest_path = data_root / "benchmark.json"
    if not manifest_path.exists():
        raise SystemExit(f"benchmark.json not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    source_root_raw = manifest.get("source_root")
    source_root = "-"
    container_source_root = "-"
    if isinstance(source_root_raw, str) and source_root_raw.strip():
        raw_source_root = source_root_raw.strip()
        source_root = raw_source_root
        if container_project_root:
            container_source_root = posixpath.normpath(posixpath.join(container_project_root, raw_source_root))
    max_workers = manifest.get("max_workers")
    max_workers_text = str(max_workers) if isinstance(max_workers, int) and not isinstance(max_workers, bool) else "-"
    print("\t".join([benchmark, str(data_root), source_root, container_source_root, max_workers_text]))
    seen.add(benchmark)
PY
}

experiment_uses_agentcompass() {
    local project_root="$1"
    local experiment_config="$2"
    local container_project_root="${3:-}"
    local line
    while IFS=$'\t' read -r line _; do
        case "$line" in
            swebench_pro|skillsbench)
                return 0
                ;;
        esac
    done < <(experiment_bootstrap_lines "$project_root" "$experiment_config" "$container_project_root")
    return 1
}

experiment_uses_toolsandbox() {
    local project_root="$1"
    local experiment_config="$2"
    local container_project_root="${3:-}"
    local line
    while IFS=$'\t' read -r line _; do
        if [[ "$line" == "toolsandbox" ]]; then
            return 0
        fi
    done < <(experiment_bootstrap_lines "$project_root" "$experiment_config" "$container_project_root")
    return 1
}
