#!/usr/bin/env python
"""P0-2 反向场景断言：未核验的实验模板导入后不得自动获得生产级可信度。

流程（对给定的数据库与 metadata 模板）：

1. 用**未经核验**的模板导入一份真实格式的结果文件 -> 可信等级必须是 ``unknown``；
2. 哈希不符时 ``attest-run`` 必须失败；
3. 用真实哈希核验后 -> 升级为 ``measured_internal``，且质量状态可追溯核验人与时间。

用法：

    python scripts/check_attest_flow.py --db <db> --metadata <某个 metadata.yaml> [--result <结果文件>]

退出码 0 = 全部符合预期，非零 = 存在可绕过核验的路径（回归）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import yaml  # noqa: E402

from llm_capacity_pricing.services import (  # noqa: E402
    ServiceError,
    attest_benchmark_run,
    data_quality_report,
    import_performance_run,
    open_db,
)

FAILURES: list[str] = []
CHECKED = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global CHECKED
    CHECKED += 1
    if ok:
        print(f"[ok]   {label}")
    else:
        FAILURES.append(label)
        print(f"[FAIL] {label} {detail}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--metadata", required=True)
    parser.add_argument(
        "--result", default="data/fixtures/perf_curve/perf_curve_c8.json"
    )
    args = parser.parse_args()

    template = yaml.safe_load(Path(args.metadata).read_text(encoding="utf-8"))
    run_id = template["record_id"]
    check(
        "模板默认声明未核验",
        template.get("verification_status") != "verified",
        f"verification_status={template.get('verification_status')!r}",
    )

    import_performance_run(args.result, args.metadata, args.db, kind="vllm")

    db, repo = open_db(args.db)
    try:
        with db.session() as session:
            run = repo.get_benchmark_run(session, run_id)
    finally:
        db.dispose()
    check("导入后可信等级不是测量类", run.data_trust == "unknown", run.data_trust)
    check(
        "质量标记记录了需要核验",
        any(f.startswith("source_verification_required:") for f in run.quality_flags),
        str(run.quality_flags),
    )

    report = data_quality_report(args.db)
    item = next(
        (
            i
            for i in report["items"]
            if i["domain"] == "benchmark_run" and i["record_id"] == run_id
        ),
        None,
    )
    check("数据质量报告不认可其为生产级", item is not None and not item["production_grade"])

    try:
        attest_benchmark_run(
            args.db, run_id=run_id, confirmed_by="check_attest_flow",
            raw_sha256="0" * 64,
        )
        check("哈希不符时核验必须失败", False, "居然通过了")
    except ServiceError as exc:
        check("哈希不符时核验必须失败", "哈希与导入时记录的不一致" in str(exc), str(exc))

    info = attest_benchmark_run(
        args.db, run_id=run_id, confirmed_by="check_attest_flow",
        raw_sha256=run.raw_sha256, note="核验原始输出文件",
    )
    check("哈希一致时核验通过", info["data_trust"] == "measured_internal", str(info))
    check("核验后是生产级", bool(info["production_grade"]), str(info["quality_status"]))
    check(
        "核验可追溯（核验人与时间）",
        any(f.startswith("verified_by:") for f in info["quality_flags"])
        and any(f.startswith("verified_at:") for f in info["quality_flags"]),
        str(info["quality_flags"]),
    )
    check(
        "核验后不再提示需要核验",
        not any(
            f.startswith("source_verification_required") for f in info["quality_flags"]
        ),
    )

    print()
    if FAILURES:
        print(f"核验流程检查失败 {len(FAILURES)}/{CHECKED}：{FAILURES}")
        return 1
    print(f"核验流程检查通过 {CHECKED}/{CHECKED}：未核验导入 -> unknown；显式核验后才升级。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
