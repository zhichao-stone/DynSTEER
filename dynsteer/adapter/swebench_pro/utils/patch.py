import hashlib
import re

from dynsteer.model import JsonObject

_RENAME_PATTERN = re.compile(r"^rename (?:from|to) (.+)$")


def patch_summary(value: object) -> JsonObject:
    """生成 patch 的大小、哈希和变更路径摘要，不执行 patch。"""
    if value is None or value == "":
        return {"present": False, "unified_diff": False, "size_bytes": 0, "sha256": None, "changed_paths": []}
    if not isinstance(value, str):
        raise TypeError("patch 必须是字符串或 None")
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    payload = normalized.encode("utf-8")
    paths: list[str] = []
    unified_diff = False
    for line in normalized.splitlines():
        path: str | None = None
        if line.startswith("+++ "):
            unified_diff = True
            path = line[4:].split("\t", 1)[0].strip()
            path = path.removeprefix("b/")
        else:
            match = _RENAME_PATTERN.match(line)
            if match:
                unified_diff = True
                path = match.group(1).strip()
        if path and path != "/dev/null" and path not in paths:
            paths.append(path)
    return {
        "present": True,
        "unified_diff": unified_diff,
        "size_bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "changed_paths": paths,
    }
