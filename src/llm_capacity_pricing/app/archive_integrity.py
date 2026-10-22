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


def _path_escape_reasons(root: Path, relative: str) -> list[str]:
    """拒绝绝对路径、..、符号链接组件与解析后越界。"""
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        return [f"非法归档路径：{relative}"]
    current = root
    for part in candidate.parts:
        current = current / part
        if current.is_symlink():
            return [f"拒绝符号链接路径组件：{relative}"]
    try:
        if not (root / candidate).resolve().is_relative_to(root.resolve()):
            return [f"归档路径越界：{relative}"]
    except OSError:
        return [f"无法解析归档路径：{relative}"]
    return []


def verify_files(
    run_dir: str | Path,
    manifest: Mapping[str, Any],
    required: tuple[str, ...] | list[str],
) -> list[str]:
    """只校验本次实际要读取的文件：必须被登记、存在且哈希一致。

    这样某个候选的产物损坏不会连带否定同一运行内其它独立候选；
    共享文件（exp_config 等）仍按需逐条校验。
    """
    root = Path(run_dir)
    reasons: list[str] = []
    registered: dict[str, str] = {}
    for section in ("inputs", "outputs"):
        values = manifest.get(section) or {}
        if not isinstance(values, dict):
            reasons.append(f"manifest.{section} 不是路径→哈希映射")
            continue
        registered.update({str(key): str(value) for key, value in values.items()})
    if not registered:
        return reasons or ["manifest 未登记任何输入/输出哈希"]

    for relative in required:
        relative = str(relative)
        if relative not in registered:
            reasons.append(f"manifest 未登记实际使用的文件：{relative}")
            continue
        path = root / relative
        escape = _path_escape_reasons(root, relative)
        if escape:
            reasons.extend(escape)
            continue
        if not path.is_file():
            reasons.append(f"缺少归档文件：{relative}")
            continue
        actual = sha256_file(path)
        if actual != registered[relative]:
            reasons.append(
                f"哈希不一致：{relative}（期望 {registered[relative][:12]}…，"
                f"实际 {actual[:12]}…）"
            )
    return reasons


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
        escape = _path_escape_reasons(root, relative)
        if escape:
            reasons.extend(escape)
            continue
        if not path.is_file():
            reasons.append(f"缺少归档文件：{relative}")
            continue
        actual = sha256_file(path)
        if actual != expected:
            reasons.append(
                f"哈希不一致：{relative}（期望 {expected[:12]}…，实际 {actual[:12]}…）"
            )
    return reasons


_VERSION_ALIASES = {"current": "0.24.0"}


def _version(value: Any) -> Any:
    return _VERSION_ALIASES.get(str(value), value) if value is not None else None


def verify_predict_metadata(
    run_dir: str | Path, manifest: Mapping[str, Any], predict_relative: str
) -> list[str]:
    """校验 manifest 声明的身份与 predict 输入是否一致。

    SHA256 只能证明“文件没变”，不能证明“标签写对了”。这里检查
    spec/workpoint 与 predict YAML 的模型、硬件、后端/版本、负载与并行拓扑。
    只比较 manifest 中**已声明**的字段；缺失字段不强行补造。
    """
    import yaml

    root = Path(run_dir)
    predict_path = root / predict_relative
    if predict_path.is_symlink() or not predict_path.is_file():
        return [f"{predict_relative}: 输入文件不存在或为符号链接"]
    try:
        cfg = yaml.safe_load(predict_path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001
        return [f"{predict_relative}: YAML 解析失败：{exc}"]

    engine = cfg.get("engine") or {}
    traffic = cfg.get("traffic") or {}
    source = traffic.get("source") or {}
    load = traffic.get("load") or {}
    parallel = ((engine.get("workers") or {}).get("aggregated") or {}).get(
        "parallelism"
    ) or {}
    spec = manifest.get("spec") or {}
    workpoint = manifest.get("workpoint") or {}
    reasons: list[str] = []

    def compare(label: str, declared: Any, actual: Any) -> None:
        if declared is None:
            return
        if actual is None:
            reasons.append(f"{predict_relative}: {label} 声明为 {declared}，输入未给出")
        elif str(declared) != str(actual):
            reasons.append(
                f"{predict_relative}: {label} 声明为 {declared}，输入为 {actual}"
            )

    compare("spec.model", spec.get("model"), engine.get("model"))
    compare("spec.system", spec.get("system"), engine.get("hardware"))
    compare("spec.backend", spec.get("backend"), engine.get("backend"))
    compare(
        "spec.backend_version",
        _version(spec.get("backend_version")),
        _version(engine.get("backend_version")),
    )
    compare("spec.isl", spec.get("isl"), source.get("input_tokens"))
    compare("spec.osl", spec.get("osl"), source.get("output_tokens"))
    compare("spec.concurrency", spec.get("concurrency"), load.get("concurrency"))

    compare("workpoint.model", workpoint.get("model"), engine.get("model"))
    compare("workpoint.system", workpoint.get("system"), engine.get("hardware"))
    compare("workpoint.input_len", workpoint.get("input_len"), source.get("input_tokens"))
    compare("workpoint.output_len", workpoint.get("output_len"), source.get("output_tokens"))
    compare("workpoint.concurrency", workpoint.get("concurrency"), load.get("concurrency"))
    compare("workpoint.tp", workpoint.get("tp"), parallel.get("tensor"))
    compare("workpoint.pp", workpoint.get("pp"), parallel.get("pipeline"))
    compare("workpoint.dp", workpoint.get("dp"), parallel.get("replicas"))
    return reasons
