import hashlib
import json
from collections.abc import Mapping

from dynsteer.model import JsonObject


def artifact_manifest(files: object) -> list[JsonObject]:
    """对已白名单化的 artifact 内容生成稳定摘要清单。"""
    if not isinstance(files, Mapping):
        raise TypeError("SkillsBench files 必须是 path -> value 对象")
    manifest: list[JsonObject] = []
    for raw_path, value in sorted(files.items(), key=lambda item: str(item[0])):
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError("SkillsBench artifact path 必须是非空字符串")
        if isinstance(value, str):
            kind = "text"
            payload = value.encode("utf-8")
        elif isinstance(value, bytes):
            kind = "bytes"
            payload = value
        else:
            try:
                payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
                kind = "json"
            except (TypeError, ValueError):
                kind = "unknown"
                payload = repr(value).encode("utf-8")
        manifest.append({
            "path": raw_path,
            "kind": kind,
            "size_bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        })
    return manifest
