import json
import hashlib
import gzip
import os
import time
import sys
from pathlib import Path
import re
from collections.abc import Iterable, Mapping
from contextlib import contextmanager
from dataclasses import fields, is_dataclass
from enum import Enum
from typing import Any, BinaryIO, Iterator
import docker
from dynsteer.model import Actor, JsonObject, JsonValue, MISSING, Dimension

try:
    import fcntl
except ImportError:
    import msvcrt


_ACTOR_ALIASES = {
    "SYSTEM": Actor.SYSTEM,
    "USER": Actor.USER,
    "AGENT": Actor.AGENT,
    "EXECUTION_ENVIRONMENT": Actor.ENVIRONMENT,
    "ENVIRONMENT": Actor.ENVIRONMENT,
    "EVALUATOR": Actor.EVALUATOR,
}


@contextmanager
def exclusive_file_lock(path: Path) -> Iterator[None]:
    """\u4ee5\u8de8\u8fdb\u7a0b\u6587\u4ef6\u9501\u4e32\u884c\u5316\u5171\u4eab\u4ea7\u7269\u5199\u5165\u3002"""
    if path is None or not str(path).strip():
        raise ValueError("lock path \u4e0d\u80fd\u4e3a\u7a7a")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    with path.open("r+b") as stream:
        _lock_stream(stream)
        try:
            yield
        finally:
            _unlock_stream(stream)


def _lock_stream(stream: BinaryIO) -> None:
    while True:
        try:
            if "msvcrt" in globals():
                msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            else:
                fcntl.lockf(stream, fcntl.LOCK_EX)
            return
        except OSError:
            time.sleep(0.2)


def _unlock_stream(stream: BinaryIO) -> None:
    stream.seek(0)
    if "msvcrt" in globals():
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        fcntl.lockf(stream, fcntl.LOCK_UN)


def docker_event_text(event: object) -> str:
    """将 Docker API 的流式事件转换成稳定的单行或多行文本。"""
    if event is None:
        return ""
    if not isinstance(event, dict):
        return _clean_docker_text(str(event))
    if "error" in event or isinstance(event.get("errorDetail"), dict):
        detail = event.get("errorDetail")
        message = detail.get("message") if isinstance(detail, dict) else None
        return _clean_docker_text(str(message or event.get("error") or "unknown docker error"))
    if "stream" in event:
        lines = [_clean_docker_text(str(line)) for line in str(event["stream"]).splitlines()]
        return "\n".join(line for line in lines if line)
    if "status" in event:
        status = _clean_docker_text(str(event.get("status") or ""))
        progress = _clean_docker_text(str(event.get("progress") or ""))
        layer = _clean_docker_text(str(event.get("id") or ""))
        text = progress or status
        if layer and status and not text.startswith(layer):
            text = f"{layer}: {text}"
        return text
    if "aux" in event:
        aux = event.get("aux")
        if isinstance(aux, dict) and aux.get("ID"):
            return _clean_docker_text(f"image {aux['ID']}")
    return _clean_docker_text(json.dumps(event, ensure_ascii=False, separators=(",", ":")))


class DockerProgressPrinter:
    """把 Docker 流式事件压缩输出到 stderr，避免干扰预检 JSON。"""

    def __init__(self, title: str, *, single_line: bool = False) -> None:
        if title is None or not title.strip():
            raise ValueError("Docker 进度标题不能为空")
        self.title = title.strip()
        self.single_line = single_line
        self._line_length = 0

    def start(self, message: str) -> None:
        self._write_line(message)

    def event(self, event: object) -> None:
        for text in docker_event_text(event).splitlines():
            if not text:
                continue
            if self.single_line and sys.stderr.isatty():
                self._replace_line(text)
            elif _is_docker_milestone(text):
                self._write_line(text)

    def finish(self, message: str) -> None:
        self._finish_dynamic_line()
        self._write_line(message)

    def fail(self, message: str) -> None:
        self._finish_dynamic_line()
        self._write_line(f"ERROR: {message}")

    def _write_line(self, message: str) -> None:
        self._finish_dynamic_line()
        print(f"[docker] {self.title}: {message}", file=sys.stderr, flush=True)

    def _replace_line(self, text: str) -> None:
        text = _shorten_docker_text(text)
        padding = " " * max(self._line_length - len(text), 0)
        print(f"\r{text}{padding}", file=sys.stderr, end="", flush=True)
        self._line_length = len(text)

    def _finish_dynamic_line(self) -> None:
        if self._line_length:
            print(file=sys.stderr, flush=True)
            self._line_length = 0


def _clean_docker_text(value: str) -> str:
    return re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", value).strip()


def _shorten_docker_text(value: str) -> str:
    return value if len(value) <= 160 else f"{value[:157]}..."


def _is_docker_milestone(text: str) -> bool:
    lowered = text.lower()
    return lowered.startswith("step ") or any(
        marker in lowered
        for marker in (
            "pulling from",
            "pull complete",
            "digest:",
            "status:",
            "successfully built",
            "successfully tagged",
            "writing image",
            "naming to",
            "exporting layers",
            "done",
            "error",
        )
    )


def record_raw_response(
    raw: str,
    output_file: Path | None,
    key: str,
    records: dict[str, JsonObject],
) -> None:
    """增量记录实际 LLM 原始响应及其 SHA-256。"""
    if output_file is None:
        return
    records[key] = {
        "response": raw,
        "sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
    }
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def normalize_actor(value: object, field_name: str = "actor", required: bool = False) -> Actor | None:
    if value is None:
        if required:
            raise ValueError(f"缺少枚举字段: {field_name}")
        return None
    key = str(value).strip()
    actor = _ACTOR_ALIASES.get(key.upper())
    if actor is not None:
        return actor
    try:
        return Actor(key.lower())
    except ValueError as exc:
        raise ValueError(f"{field_name} 角色非法: {value}") from exc


def parse_int_value(
    value: object,
    label: str,
    *,
    default: int | None = None,
    min_value: int | None = None,
    error_type: type[Exception] = ValueError,
) -> int | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    try:
        parsed = int(value.strip()) if isinstance(value, str) else value
    except ValueError as exc:
        raise error_type(f"{label} 必须是整数") from exc
    if isinstance(parsed, bool) or not isinstance(parsed, int):
        raise error_type(f"{label} 必须是整数")
    if min_value is not None and parsed < min_value:
        raise error_type(f"{label} 必须大于等于 {min_value}")
    return parsed


def parse_float_value(
    value: object,
    label: str,
    *,
    default: float | None = None,
    min_value: float | None = None,
    error_type: type[Exception] = ValueError,
) -> float | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    try:
        parsed = float(value.strip()) if isinstance(value, str) else value
    except ValueError as exc:
        raise error_type(f"{label} 必须是数字") from exc
    if isinstance(parsed, bool) or not isinstance(parsed, (int, float)):
        raise error_type(f"{label} 必须是数字")
    parsed_float = float(parsed)
    if min_value is not None and parsed_float < min_value:
        raise error_type(f"{label} 必须大于等于 {min_value}")
    return parsed_float


def validated_target_dimensions(dimensions: Iterable[Dimension] | None) -> list[Dimension]:
    if dimensions is None:
        return list(Dimension)
    result = list(dict.fromkeys(dimensions))
    if not result:
        raise ValueError("target dimensions 不能为空")
    return result


def clamp(value: float, lower: float=0.0, upper: float=1.0) -> float:
    """将数值裁剪到 [lower, upper] 闭区间。"""
    return min(max(value, lower), upper)


def as_number(value: object, default: float | None=None) -> float | None:
    """读取 JSON 数字值，bool 或非数字返回默认值。"""
    return float(value) if isinstance(value, int | float) and (not isinstance(value, bool)) else default


def clamped_number(value: object, default: float=0.0, lower: float=0.0, upper: float=1.0) -> float:
    """读取 JSON 数字值并裁剪到指定范围。"""
    return clamp(as_number(value, default), lower, upper)


def read_json_file(path: Path, label: str, expected_type: type) -> Any:
    """读取 JSON 文件并校验顶层类型。

    入参：
        path: JSON 文件路径。
        label: 错误信息中展示的配置名称。
        expected_type: 顶层 JSON 期望类型。
    输出：
        已解析且通过类型校验的 JSON 值。
    """
    if path is None:
        raise ValueError(f"{label} 路径不能为空")
    if not path.exists():
        raise ValueError(f"{label} 不存在: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} 不是合法 JSON: {path}") from exc
    if not isinstance(data, expected_type):
        expected = "JSON 对象" if expected_type is dict else "JSON 数组"
        raise ValueError(f"{label} 必须是 {expected}")
    return data


def read_token(current: Any, token: str) -> Any:
    """读取轻量 JSON selector 中的单个 token。"""
    value = current
    rest = token
    while rest:
        if "[" in rest:
            name, tail = rest.split("[", 1)
            if name:
                if not isinstance(value, dict) or name not in value:
                    return MISSING
                value = value[name]
            index_text, after = tail.split("]", 1)
            if not isinstance(value, list):
                return MISSING
            try:
                index = int(index_text)
            except ValueError:
                return MISSING
            if index < 0 or index >= len(value):
                return MISSING
            value = value[index]
            rest = after.lstrip(".")
            if rest and "[" not in rest:
                if not isinstance(value, dict) or rest not in value:
                    return MISSING
                value = value[rest]
                rest = ""
        else:
            if not isinstance(value, dict) or rest not in value:
                return MISSING
            value = value[rest]
            rest = ""
    return value


def json_subsumes(actual: JsonValue, expected: JsonValue) -> bool:
    """判断 actual JSON 是否包含 expected JSON 的结构和值。"""
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return False
        for key, expected_value in expected.items():
            if key not in actual or not json_subsumes(actual[key], expected_value):
                return False
        return True
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) < len(expected):
            return False
        return all((json_subsumes(actual[index], value) for index, value in enumerate(expected)))
    return actual == expected


def normalize_str_from_source(source: Mapping[str, str], key: str) -> str | None:
    """从配置来源读取非空字符串并去除首尾空白。"""
    return optional_str(source.get(key))


def required_str(data: Mapping[str, Any], key: str, label: str="配置") -> str:
    """从 JSON 映射读取必填非空字符串。"""
    value = optional_str(data.get(key))
    if value is None:
        raise ValueError(f"{label} 必须提供非空字符串字段 {key}")
    return value


def optional_str(value: object, default: str | None=None) -> str | None:
    """读取可选非空字符串。"""
    return value.strip() if isinstance(value, str) and value.strip() else default


FORBIDDEN_EXECUTION_FIELDS = frozenset({
    "docker", "use_docker", "execution_mode", "environment",
    "sandbox", "backend", "agent" + "compass",
})


_CLIENT_CONFIG_FIELDS = {
    "api_key",
    "base_url",
    "max_retries",
    "timeout_seconds", "retry_base_seconds", "retry_max_seconds",
}
_CLIENT_CONFIG_TEXT_FIELDS = {"api_key", "base_url"}
_CLIENT_CONFIG_INT_FIELDS = {"max_retries"}
_CLIENT_CONFIG_FLOAT_FIELDS = {"timeout_seconds", "retry_base_seconds", "retry_max_seconds"}


def normalize_client_config(value: object, label: str="client_config") -> JsonObject:
    """校验并归一化 client_config 对象。

    入参：
        value: 待归一化的 JSON 对象。
        label: 错误提示中的字段来源名称。
    输出：
        仅包含允许字段且已清洗空字符串的 client_config。
    """
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} 必须是 JSON 对象")
    config: JsonObject = {}
    for raw_key, raw_value in value.items():
        if not isinstance(raw_key, str) or not raw_key.strip():
            raise ValueError(f"{label} 字段名必须是非空字符串")
        key = raw_key.strip()
        if key not in _CLIENT_CONFIG_FIELDS:
            raise ValueError(f"{label} 不支持的字段: {key}")
        normalized_value = _normalize_client_config_value(key, raw_value, label)
        if normalized_value is not None:
            config[key] = normalized_value
    if config and ("api_key" not in config or "base_url" not in config):
        raise ValueError(f"{label} 必须同时提供非空 api_key 与 base_url")
    return config


def safe_name(value: str) -> str:
    """生成可安全用于镜像、容器与文件名的短名称。"""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("name 不能为空")
    return "".join(char if char.isalnum() or char in "._-" else "-" for char in value.lower())[:100]


def docker_image_archive_path(base_dir: Path, image: str, label: str | None = None) -> Path:
    """按镜像引用和业务标签生成稳定的压缩备份路径。"""
    if not str(base_dir).strip():
        raise ValueError("base_dir 不能为空")
    if not str(image).strip():
        raise ValueError("image 不能为空")
    digest = hashlib.sha256(image.encode("utf-8")).hexdigest()[:12]
    return base_dir / f"{safe_name(label or image)}-{digest}.tar.gz"


def load_image_archive(client: docker.DockerClient, image: str, archive: Path) -> None:
    """从镜像压缩包载入并校验目标 tag。"""
    if not archive.is_file():
        raise FileNotFoundError(archive)
    with archive.open("rb") as archive_stream:
        for event in client.api.load_image(archive_stream, quiet=True):
            if isinstance(event, dict):
                error = event.get("error")
                if not error and isinstance(event.get("errorDetail"), dict):
                    error = event["errorDetail"].get("message")
                if error:
                    raise RuntimeError(f"镜像压缩包载入失败: {error}")
    try:
        client.images.get(image)
    except docker.errors.ImageNotFound as exc:
        raise RuntimeError(f"镜像压缩包缺少目标 tag {image}: {archive}") from exc


def save_image_archive(client: docker.DockerClient, image: str, archive: Path) -> None:
    """流式压缩保存镜像，先写临时文件保证原子替换。"""
    archive.parent.mkdir(parents=True, exist_ok=True)
    temporary = archive.with_name(f".{archive.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("wb") as raw_stream:
            with gzip.GzipFile(fileobj=raw_stream, mode="wb", compresslevel=1) as compressed_stream:
                for chunk in client.images.get(image).save(named=True):
                    compressed_stream.write(chunk)
        temporary.replace(archive)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def assert_execution_neutral(value: object, label: str="配置") -> None:
    """递归拒绝会显式选择执行环境的配置字段。"""
    if isinstance(value, dict):
        for key, item in value.items():
            name = str(key)
            if name in FORBIDDEN_EXECUTION_FIELDS:
                raise ValueError(f"{label} 包含禁用执行环境字段: {name}")
            assert_execution_neutral(item, f"{label}.{name}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            assert_execution_neutral(item, f"{label}[{index}]")


def compact_text(value: object, limit: int=160) -> str:
    """压缩任意值为单行短文本。"""
    if value is None:
        raise ValueError("摘要文本不能为空")
    if limit < 0:
        raise ValueError("摘要长度不能为负数")
    text = " ".join(str(value).split())
    if len(text) <= limit:
        return text
    return text[:max(limit - 3, 0)] + "..."


def compact_json_text(value: object, limit: int=160) -> str:
    """将 JSON 安全值压缩为单行短文本。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return compact_text(value, limit)
    return compact_text(json.dumps(json_safe(value), ensure_ascii=False), limit)


def first_text(values: list[str], limit: int=160) -> str | None:
    """返回列表中的首条短文本。"""
    return compact_text(values[0], limit) if values else None


def string_list(value: object) -> list[str]:
    """将 list 值转换为字符串列表，非 list 返回空列表。"""
    return [str(item) for item in value] if isinstance(value, list) else []


def clean_evidence_items(values: list[str], limit: int | None=None) -> list[str]:
    """清洗 evidence 文本，去除重复裸 step 引用。

    入参：
        values: 原始 evidence 字符串列表。
        limit: 可选最大返回条数。
    输出：
        保序去重后的 evidence；当存在 `step N: ...` 具体证据时，删除裸 `step N`。
    """
    if limit is not None and limit < 0:
        raise ValueError("evidence limit 不能为负数")
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        deduped.append(text)
    detailed_steps = _detailed_step_indexes(deduped)
    cleaned = [item for item in deduped if not _is_redundant_bare_step_reference(item, detailed_steps)]
    return cleaned[:limit] if limit is not None else cleaned


def get_object(data: JsonObject, key: str, type: object=None, default: object=None, required: bool=True) -> object:
    """从 JSON 对象读取字段，并按需校验存在性和类型。"""
    if data is None:
        raise ValueError("待读取字段的 JSON 对象不能为空")
    if key not in data:
        if required:
            raise ValueError(f"缺少必要字段: {key}")
        return default
    value = data[key]
    if required and type is not None and (not isinstance(value, type)):
        raise ValueError(f"字段 {key} 必须非空，且是 {type} 类型")
    return type(value)


def enum_value(enum_class: type[Enum], value: object, field_name: str) -> Enum:
    """将 JSON 枚举值转换为指定 Enum 成员。"""
    if enum_class is None or field_name is None:
        raise ValueError("enum_class 和 field_name 不能为空")
    if value is None:
        raise ValueError(f"缺少枚举字段: {field_name}")
    try:
        return enum_class(value)
    except ValueError as exc:
        raise ValueError(f"字段 {field_name} 的枚举值非法: {value}") from exc


def enum_name(value: object) -> str:
    """读取枚举或类枚举对象的稳定大写名称。"""
    if value is None:
        return ""
    name = getattr(value, "name", None)
    if isinstance(name, str):
        return name.upper()
    raw = str(value)
    if "." in raw:
        raw = raw.rsplit(".", 1)[-1]
    return raw.upper()


def unknown_fields(data: JsonObject, known: set[str]) -> JsonObject:
    """返回 JSON 对象中不属于 known 集合的字段。"""
    return {key: value for key, value in data.items() if key not in known}


def json_safe(value: object) -> JsonValue:
    """将常见 Python 对象转换为 JSON 安全值。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return _safe_scalar(value)
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value) and (not isinstance(value, type)):
        return {field.name: json_safe(getattr(value, field.name)) for field in fields(value) if field.metadata.get("json_safe", True)}
    enum_raw_value = getattr(value, "value", None)
    if isinstance(enum_raw_value, (str, int, float, bool)) or (enum_raw_value is None and isinstance(value, Enum)):
        return enum_raw_value
    if isinstance(value, dict):
        return {raw if isinstance((raw := getattr(key, "value", None)), str) else str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    return str(value)


def _safe_scalar(value: object) -> object:
    if isinstance(value, str) and value.startswith("sk-"):
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
        return f"sha256:{digest}"
    return value


def canonical_json(value: object) -> str:
    """将对象转换为稳定、紧凑的 JSON 文本。"""
    return json.dumps(
        json_safe(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def stable_json_digest(value: object) -> str:
    """返回对象稳定 JSON 表示的 SHA-256 摘要。"""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _detailed_step_indexes(values: list[str]) -> set[int]:
    """提取形如 `step N: ...` 的具体 step 证据编号。"""
    indexes: set[int] = set()
    for value in values:
        match = re.match("^\\s*step\\s+(\\d+)\\s*:", value, flags=re.IGNORECASE)
        if match is not None:
            indexes.add(int(match.group(1)))
    return indexes


def _is_redundant_bare_step_reference(value: str, detailed_steps: set[int]) -> bool:
    """判断裸 step 或 step 区间是否已被具体证据覆盖。"""
    single = re.match("^\\s*step\\s+(\\d+)\\s*$", value, flags=re.IGNORECASE)
    if single is not None:
        return int(single.group(1)) in detailed_steps
    step_range = re.match("^\\s*step\\s+(\\d+)\\s*[-~]\\s*(\\d+)\\s*$", value, flags=re.IGNORECASE)
    if step_range is None:
        return False
    start = int(step_range.group(1))
    end = int(step_range.group(2))
    if start > end:
        start, end = (end, start)
    return any((index in detailed_steps for index in range(start, end + 1)))


def _normalize_client_config_value(key: str, value: object, label: str) -> str | int | float | None:
    """归一化单个 client_config 字段值。"""
    if value is None:
        return None
    if key in _CLIENT_CONFIG_TEXT_FIELDS:
        if not isinstance(value, str):
            raise ValueError(f"{label}.{key} 必须是字符串")
        value = value.strip()
        return value or None
    if key in _CLIENT_CONFIG_INT_FIELDS:
        return parse_int_value(value, f"{label}.{key}", default=None, min_value=1)
    if key in _CLIENT_CONFIG_FLOAT_FIELDS:
        normalized_value = parse_float_value(value, f"{label}.{key}", default=None, min_value=0.0)
        if key == "timeout_seconds" and normalized_value is not None and normalized_value <= 0:
            raise ValueError(f"{label}.timeout_seconds 必须大于 0")
        return normalized_value
    raise ValueError(f"{label} 不支持的字段: {key}")
