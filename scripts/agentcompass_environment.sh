agentcompass_is_benchmark() {
    case "$1" in
        swebench_pro|skillsbench) return 0 ;;
        *) return 1 ;;
    esac
}

resolve_agentcompass_source() {
    local project_root="$1"
    local raw_path="${2:-}"
    local source_abs

    if [[ -z "$raw_path" ]]; then
        raw_path="../AgentCompass"
    fi
    if [[ "$raw_path" == /* || "$raw_path" =~ ^[A-Za-z]:[\\/] ]]; then
        source_abs="$(cd -- "$raw_path" && pwd)"
    else
        source_abs="$(cd -- "$project_root/$raw_path" && pwd)"
    fi
    if [[ ! -f "$source_abs/pyproject.toml" ]]; then
        echo "AgentCompass source is invalid: $raw_path" >&2
        return 66
    fi
    printf '%s\n' "$source_abs"
}

ensure_agentcompass_environment() {
    local project_root="$1"
    local raw_source="${2:-}"
    local source_abs
    local venv_dir
    local python_version="${DYNSTEER_AGENTCOMPASS_PYTHON:-3.12}"
    local python_bin=""
    local stamp_dir
    local stamp_file
    local expected_stamp
    local current_stamp=""
    local runtime_dependencies="polars==0.20.31 networkx>=3.2 tqdm>=4.66.0 scipy==1.13.1"

    if ! command -v uv >/dev/null 2>&1; then
        echo "uv is required to prepare the AgentCompass environment." >&2
        exit 127
    fi
    if ! source_abs="$(resolve_agentcompass_source "$project_root" "$raw_source")"; then
        exit 66
    fi

    venv_dir="${DYNSTEER_AGENTCOMPASS_VENV:-$project_root/.venv-agentcompass}"
    stamp_dir="$venv_dir/.dynsteer"
    stamp_file="$stamp_dir/source"
    expected_stamp="$source_abs|$runtime_dependencies"
    for python_bin in "$venv_dir/bin/python" "$venv_dir/Scripts/python.exe" "$venv_dir/Scripts/python"; do
        if [[ -x "$python_bin" ]]; then
            break
        fi
        python_bin=""
    done

    if [[ -z "$python_bin" ]]; then
        uv venv "$venv_dir" --python "$python_version"
        for python_bin in "$venv_dir/bin/python" "$venv_dir/Scripts/python.exe" "$venv_dir/Scripts/python"; do
            if [[ -x "$python_bin" ]]; then
                break
            fi
        done
    elif ! "$python_bin" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' >/dev/null 2>&1; then
        echo "Rebuilding AgentCompass environment with Python $python_version: $venv_dir"
        uv venv "$venv_dir" --python "$python_version" --clear
    fi
    if [[ -z "$python_bin" ]]; then
        echo "AgentCompass Python executable not found: $venv_dir" >&2
        exit 127
    fi

    if [[ -f "$stamp_file" ]]; then
        read -r current_stamp < "$stamp_file" || true
    fi
    if [[ "${DYNSTEER_AGENTCOMPASS_FORCE_INSTALL:-0}" == "1" || "$current_stamp" != "$expected_stamp" ]]; then
        echo "Preparing AgentCompass environment: $venv_dir"
        echo "Using AgentCompass source: $source_abs"
        uv pip install --python "$python_bin" -e "$source_abs"
        uv pip install --python "$python_bin" --no-deps -e "$project_root"
        # shellcheck disable=SC2086
        uv pip install --python "$python_bin" $runtime_dependencies
        mkdir -p "$stamp_dir"
        printf '%s\n' "$expected_stamp" > "$stamp_file"
    fi

    export UV_CACHE_DIR="${UV_CACHE_DIR:-$project_root/.uv-cache}"
    export UV_PROJECT_ENVIRONMENT="$venv_dir"
    export DYNSTEER_PYTHON="$python_bin"
    export DYNSTEER_SKIP_UV_SYNC=1
    export DYNSTEER_BENCHMARK_SOURCE_ROOT="$source_abs"
}
