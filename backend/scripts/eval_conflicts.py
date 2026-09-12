"""M2-2 Critic 冲突检测离线评估脚本（FR-15 / TR-9.1）。

用法（backend 目录下，需配置真实 LLM 密钥，脚本不进 CI）：

    uv run python scripts/eval_conflicts.py
    uv run python scripts/eval_conflicts.py --save tests/eval/conflict_result.json

评估集：``tests/eval/conflict_cases.jsonl``，每条样例为同一议题下的两条证据，
标注 is_conflict/type/severity，覆盖 factual/methodological/temporal/
perspective 四类型，并含同议题互补信息（非冲突）负例。

对照口径：
- semantic：LLM 语义检测（critic.detect_conflicts_semantic，温度 0、单批硬超时）；
- heuristic：M1 Jaccard + 可信度差启发式（critic.detect_conflicts）。

主门禁（TR-9.1）：语义检测冲突召回 ≥ 启发式召回；另报负例误报率、
四类型召回、类型准确率、降级次数与 token 消耗。

测试资产隔离（TR-9.2）：评估集为 jsonl、脚本在 scripts/ 下，tests/eval 无
test_*.py，``pytest`` 默认收集 0 项（可用
``uv run pytest tests/eval --collect-only -q`` 核对）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from app.orchestrator.nodes.critic import (
    build_candidate_pairs,
    detect_conflicts,
    detect_conflicts_semantic,
    generate_draft_claims,
)
from app.orchestrator.state import EvidenceDict
from app.provider.client import LLMClient

_DEFAULT_CASES = Path("tests/eval/conflict_cases.jsonl")
_CONFLICT_TYPES = ("factual", "methodological", "temporal", "perspective")

CaseResult = dict[str, Any]


def load_cases(path: Path) -> list[dict[str, Any]]:
    """读取 jsonl 评估集并校验必备字段。"""
    cases: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            if "is_conflict" not in item or "a" not in item or "b" not in item:
                raise ValueError(f"第 {line_no} 行缺少 is_conflict/a/b 字段")
            if item["is_conflict"] and item.get("type") not in _CONFLICT_TYPES:
                raise ValueError(f"第 {line_no} 行冲突样例 type 非法")
            cases.append(item)
    return cases


def _to_evidence(side: dict[str, Any], ev_id: str) -> EvidenceDict:
    """把评估样例一方转成节点使用的 EvidenceDict。"""
    return {
        "id": ev_id,
        "sub_question_id": "sq_eval",
        "url": f"https://eval.example/{ev_id}",
        "domain": "eval.example",
        "title": side["title"],
        "snippet": side["snippet"],
        "source_type": "news",
        "source_level": "secondary",
        "credibility": side.get("credibility", "B"),
        "fingerprint": f"fp-{ev_id}",
        "published_at": None,
        "fetched_at": "2026-09-01T00:00:00+00:00",
    }


async def run_eval(cases: list[dict[str, Any]]) -> list[CaseResult]:
    """对每条样例分别跑语义检测与启发式检测。"""
    client = LLMClient.from_registry()
    results: list[CaseResult] = []
    total = len(cases)
    for idx, case in enumerate(cases, start=1):
        material = [
            _to_evidence(case["a"], f"{case['id']}-a"),
            _to_evidence(case["b"], f"{case['id']}-b"),
        ]
        claims = generate_draft_claims(material)
        candidate_count = len(build_candidate_pairs(material))

        started = time.perf_counter()
        semantic_round = await detect_conflicts_semantic(material, llm=client)
        latency_ms = round((time.perf_counter() - started) * 1000)
        heuristic = detect_conflicts(claims, material)

        sem_first = semantic_round.conflicts[0] if semantic_round.conflicts else None
        heur_first = heuristic[0] if heuristic else None
        result: CaseResult = {
            "id": case["id"],
            "topic": case.get("topic", ""),
            "expected_conflict": bool(case["is_conflict"]),
            "expected_type": case.get("type"),
            "expected_severity": case.get("severity"),
            "candidate_prefilter": candidate_count,
            "semantic_conflict": sem_first is not None,
            "semantic_type": sem_first.get("type") if sem_first else None,
            "semantic_severity": sem_first.get("severity") if sem_first else None,
            "semantic_degraded": semantic_round.degraded,
            "semantic_tokens": semantic_round.tokens_used,
            "semantic_latency_ms": latency_ms,
            "heuristic_conflict": heur_first is not None,
            "heuristic_type": heur_first.get("type") if heur_first else None,
            "heuristic_severity": heur_first.get("severity") if heur_first else None,
        }
        results.append(result)
        print(
            f"[{idx}/{total}] {case['id']:4s} "
            f"expect={('conflict/' + str(case.get('type'))) if case['is_conflict'] else 'none':22s} "
            f"semantic={'Y' if result['semantic_conflict'] else 'N'} "
            f"heuristic={'Y' if result['heuristic_conflict'] else 'N'} "
            f"{'(degraded)' if semantic_round.degraded else ''}",
            flush=True,
        )
    return results


def _ratio(hits: int, total: int) -> float:
    return round(hits / total, 4) if total else 0.0


def compute_metrics(results: list[CaseResult]) -> dict[str, Any]:
    """汇总召回/误报/类型准确/降级/token 等指标。"""
    positives = [r for r in results if r["expected_conflict"]]
    negatives = [r for r in results if not r["expected_conflict"]]

    sem_hits = [r for r in positives if r["semantic_conflict"]]
    heur_hits = [r for r in positives if r["heuristic_conflict"]]
    sem_fp = [r for r in negatives if r["semantic_conflict"]]
    heur_fp = [r for r in negatives if r["heuristic_conflict"]]

    type_correct = [r for r in sem_hits if r["semantic_type"] == r["expected_type"]]
    severity_correct = [
        r for r in sem_hits if r["expected_severity"] and r["semantic_severity"] == r["expected_severity"]
    ]

    per_type: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "hit": 0})
    for r in positives:
        bucket = per_type[str(r["expected_type"])]
        bucket["total"] += 1
        if r["semantic_conflict"]:
            bucket["hit"] += 1

    latencies = sorted(int(r["semantic_latency_ms"]) for r in results)
    return {
        "total": len(results),
        "positive_total": len(positives),
        "negative_total": len(negatives),
        "semantic_recall": _ratio(len(sem_hits), len(positives)),
        "heuristic_recall": _ratio(len(heur_hits), len(positives)),
        "semantic_false_positive_rate": _ratio(len(sem_fp), len(negatives)),
        "heuristic_false_positive_rate": _ratio(len(heur_fp), len(negatives)),
        "semantic_type_accuracy": _ratio(len(type_correct), len(sem_hits)),
        "semantic_severity_accuracy": _ratio(len(severity_correct), len(sem_hits)),
        "semantic_recall_by_type": dict(sorted(per_type.items())),
        "degraded_cases": sum(1 for r in results if r["semantic_degraded"]),
        "semantic_tokens_total": sum(int(r["semantic_tokens"]) for r in results),
        "latency_avg_ms": round(sum(latencies) / len(latencies)) if latencies else 0,
        "latency_max_ms": latencies[-1] if latencies else 0,
        "false_positive_case_ids": [r["id"] for r in sem_fp],
        "missed_case_ids": [r["id"] for r in positives if not r["semantic_conflict"]],
    }


def report(metrics: dict[str, Any]) -> bool:
    """打印对照表并给出 TR-9.1 门禁结论。"""
    print("\n" + "=" * 60)
    print("Critic 冲突检测离线评估：语义检测 vs Jaccard 启发式")
    print("=" * 60)
    print(
        f"样本：{metrics['total']} 条（冲突 {metrics['positive_total']} / "
        f"非冲突 {metrics['negative_total']}）"
    )
    print(f"冲突召回：语义 {metrics['semantic_recall']:.2%} vs 启发式 {metrics['heuristic_recall']:.2%}")
    print(
        f"负例误报率：语义 {metrics['semantic_false_positive_rate']:.2%} "
        f"vs 启发式 {metrics['heuristic_false_positive_rate']:.2%}"
    )
    print(f"语义检测类型准确率：{metrics['semantic_type_accuracy']:.2%}")
    print(f"语义检测严重度准确率：{metrics['semantic_severity_accuracy']:.2%}")
    by_type = metrics["semantic_recall_by_type"]
    print("语义检测分类型召回：")
    for ctype in _CONFLICT_TYPES:
        stat = by_type.get(ctype, {"total": 0, "hit": 0})
        print(f"  {ctype:16s} {stat['hit']}/{stat['total']}")
    print(
        f"降级次数：{metrics['degraded_cases']}；"
        f"语义检测 token 总耗：{metrics['semantic_tokens_total']}；"
        f"延迟均值 {metrics['latency_avg_ms']}ms / 最大 {metrics['latency_max_ms']}ms"
    )
    if metrics["missed_case_ids"]:
        print(f"语义漏判样例：{metrics['missed_case_ids']}")
    if metrics["false_positive_case_ids"]:
        print(f"语义误报样例：{metrics['false_positive_case_ids']}")

    passed: bool = bool(
        metrics["semantic_recall"] >= metrics["heuristic_recall"]
    )
    print("\nTR-9.1 门禁（语义召回 ≥ 启发式召回）：" + ("通过 ✅" if passed else "未通过 ❌"))
    return passed


def main() -> int:
    parser = argparse.ArgumentParser(description="Critic 冲突检测离线评估")
    parser.add_argument("--cases", type=Path, default=_DEFAULT_CASES, help="评估集 jsonl 路径")
    parser.add_argument("--save", type=Path, default=None, help="结果 JSON 保存路径")
    args = parser.parse_args()

    cases = load_cases(args.cases)
    results = asyncio.run(run_eval(cases))
    metrics = compute_metrics(results)
    passed = report(metrics)

    if args.save is not None:
        args.save.write_text(
            json.dumps({"metrics": metrics, "results": results}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\n结果已保存：{args.save}")

    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
