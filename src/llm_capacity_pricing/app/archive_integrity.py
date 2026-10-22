"""归档完整性校验：manifest 里登记的每个输入/输出都必须存在且哈希一致。"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_manifest_hashes(
    run_dir: str | Path,
    manifest: Mapping[str, Any],
    *,
    required: tuple[str, ...] | list[str] = (),
) -> list[str]:
    """返回完整性问题的列表；空列表表示全部通过。

    ``manifest["inputs"]`` 与 ``manifest["outputs"]`` 的键是**相对 run_dir**
    的路径，值是 sha256。没有登记任何文件、文件缺失或哈希不符都视为不可用。
    ``required`` 是本次真正要读取的文件；即使它们没被登记，也必须报错，
    不能只靠“其它文件有哈希”就放行。
    """
    root = Path(run_dir)
    reasons: list[str] = []
    registered: dict[str, str] = {}
    for section in ("inputs", "outputs"):
        values = manifest.get(section) or {}
        if not isinstance(values, dict):
            reasons.append(f"manifest.{section} 不是路径→哈希映射")
            continue
        if not values:
            reasons.append(f"manifest.{section} 未登记任何文件")
            continue
        registered.update({str(key): str(value) for key, value in values.items()})
    for relative in required:
        if str(relative) not in registered:
            reasons.append(f"manifest 未登记实际使用的文件：{relative}")
    if not registered:
        return reasons or ["manifest 未登记任何输入/输出哈希"]

    for relative, expected in sorted(registered.items()):
        path = root / relative
        if not path.is_file():
            reasons.append(f"缺少归档文件：{relative}")
            continue
        actual = sha256_file(path)
        if actual != expected:
            reasons.append(
                f"哈希不一致：{relative}（期望 {expected[:12]}…，实际 {actual[:12]}…）"
            )
    return reasons
