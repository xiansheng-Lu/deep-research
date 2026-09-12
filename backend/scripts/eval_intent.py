"""M2-1 意图路由离线评估脚本（LLD §15.2 门禁：研究路径召回 ≥ 85%）。

用法（backend 目录下，需配置真实 LLM 密钥）：

    uv run python scripts/eval_intent.py
    uv run python scripts/eval_intent.py --save tests/eval/intent_result.json

口径说明（对齐 PRD 模块 G 保守策略：不确定默认走研究）：
- research_path_recall（主门禁）：research 样例被判 research 或 uncertain 的比例，
  两者都会进入研究流水线，即"不漏掉深度研究需求"的产品召回；
- strict_research_recall：research 样例被精确判为 research 的比例（参考指标）；
- chat_path_precision：chat 样例被判 chat 的比例（闲聊不得误入研究流水线）；
- uncertain_conservative_rate：uncertain 样例被判 research/uncertain 的比例。

脚本不进 CI（依赖真实模型），结果作为 M2-1 验收证据存档。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import cast

from app.agents.intent_router import classify_intent
from app.provider.client import LLMClient

# LLD §15.2 M2-1 验收硬门禁
RESEARCH_RECALL_GATE = 0.85
# PRD 模块 G：判别反馈 ≤2s（仅观测，不作为硬门禁）
LATENCY_TARGET_S = 2.0

_DEFAULT_CASES = Path("tests/eval/intent_cases.jsonl")

# 单条评估结果
CaseResult = dict[str, object]


def load_cases(path: Path) -> list[dict[str, str]]:
    cases: list[dict[str, str]] = []
    with path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            if "text" not in item or "expected" not in item:
                raise ValueError(f"第 {line_no} 行缺少 text/expected 字段")
            cases.append(item)
    return cases


async def run_eval(cases: list[dict[str, str]]) -> list[CaseResult]:
    client = LLMClient.from_registry()
    results: list[dict[str, object]] = []
    total = len(cases)
    for idx, case in enumerate(cases, start=1):
        started = time.perf_counter()
        decision = await classify_intent(case["text"], llm=client)
        latency_ms = round((time.perf_counter() - started) * 1000)
        results.append(
            {
                "text": case["text"],
                "expected": case["expected"],
                "bucket": case.get("bucket", ""),
                "actual": decision.intent,
                "source": decision.source,
                "degraded": decision.degraded,
                "confidence": decision.confidence,
                "latency_ms": latency_ms,
                "reason": decision.reason,
            }
        )
        print(f"[{idx}/{total}] {case['expected']:9s} -> {decision.intent:9s} {latency_ms}ms", flush=True)
    return results


def compute_metrics(results: list[CaseResult]) -> dict[str, object]:
    research_cases = [r for r in results if r["expected"] == "research"]
    chat_cases = [r for r in results if r["expected"] == "chat"]
    uncertain_cases = [r for r in results if r["expected"] == "uncertain"]

    def ratio(items: list[CaseResult], pred: Callable[[CaseResult], bool]) -> float:
        return sum(1 for r in items if pred(r)) / len(items) if items else 0.0

    research_path_recall = ratio(research_cases, lambda r: r["actual"] in ("research", "uncertain"))
    strict_research_recall = ratio(research_cases, lambda r: r["actual"] == "research")
    chat_path_precision = ratio(chat_cases, lambda r: r["actual"] == "chat")
    uncertain_conservative = ratio(uncertain_cases, lambda r: r["actual"] in ("research", "uncertain"))

    latencies = sorted(cast(int, r["latency_ms"]) for r in results)
    p90_idx = min(len(latencies) - 1, int(len(latencies) * 0.9))
    metrics: dict[str, object] = {
        "total": len(results),
        "research_path_recall": round(research_path_recall, 4),
        "strict_research_recall": round(strict_research_recall, 4),
        "chat_path_precision": round(chat_path_precision, 4),
        "uncertain_conservative_rate": round(uncertain_conservative, 4),
        "latency_avg_ms": round(sum(latencies) / len(latencies)),
        "latency_p90_ms": latencies[p90_idx],
        "latency_max_ms": latencies[-1],
        "over_2s_cases": sum(1 for ms in latencies if ms > LATENCY_TARGET_S * 1000),
        "fallback_cases": sum(1 for r in results if r["source"] == "fallback"),
    }

    # 分桶明细
    by_bucket: dict[str, dict[str, int]] = defaultdict(dict)
    for r in results:
        counts = by_bucket.setdefault(str(r["bucket"]), {})
        key = str(r["actual"])
        counts[key] = counts.get(key, 0) + 1
    metrics["buckets"] = dict(sorted(by_bucket.items()))
    return metrics


def report(results: list[CaseResult], metrics: dict[str, object]) -> bool:
    print("\n" + "=" * 60)
    print("意图路由离线评估结果")
    print("=" * 60)
    research_recall = cast(float, metrics["research_path_recall"])
    strict_recall = cast(float, metrics["strict_research_recall"])
    chat_precision = cast(float, metrics["chat_path_precision"])
    uncertain_rate = cast(float, metrics["uncertain_conservative_rate"])
    buckets = cast(dict[str, dict[str, int]], metrics["buckets"])

    print(f"样本总数：{metrics['total']}（research 30 / chat 23 / uncertain 5）")
    print(f"研究路径召回（主门禁 ≥{RESEARCH_RECALL_GATE:.0%}）：{research_recall:.2%}")
    print(f"严格 research 召回（参考）：{strict_recall:.2%}")
    print(f"闲聊路径精确率：{chat_precision:.2%}")
    print(f"uncertain 保守符合率：{uncertain_rate:.2%}")
    print(
        f"延迟：均值 {metrics['latency_avg_ms']}ms / P90 {metrics['latency_p90_ms']}ms / "
        f"最大 {metrics['latency_max_ms']}ms，超 {LATENCY_TARGET_S:.0f}s {metrics['over_2s_cases']} 例"
    )
    print(f"保守降级次数：{metrics['fallback_cases']}")
    print("\n分桶明细：")
    for bucket, counts in buckets.items():
        print(f"  {bucket:20s} {counts}")

    errors = [
        r for r in results
        if (r["expected"] == "research" and r["actual"] not in ("research", "uncertain"))
        or (r["expected"] == "chat" and r["actual"] != "chat")
        or (r["expected"] == "uncertain" and r["actual"] not in ("research", "uncertain"))
    ]
    if errors:
        print(f"\n错例（{len(errors)} 条）：")
        for r in errors:
            print(f"  [{r['bucket']}] 期望 {r['expected']} 实得 {r['actual']}：{r['text']}")
    else:
        print("\n无错例。")

    passed = research_recall >= RESEARCH_RECALL_GATE
    print("\n门禁结论：" + ("通过 ✅" if passed else "未通过 ❌（研究路径召回低于 85%，需调整 prompt/few-shot）"))
    return passed


def main() -> int:
    parser = argparse.ArgumentParser(description="意图路由离线评估")
    parser.add_argument("--cases", type=Path, default=_DEFAULT_CASES, help="评估集 jsonl 路径")
    parser.add_argument("--save", type=Path, default=None, help="结果 JSON 保存路径")
    args = parser.parse_args()

    cases = load_cases(args.cases)
    results = asyncio.run(run_eval(cases))
    metrics = compute_metrics(results)
    passed = report(results, metrics)

    if args.save is not None:
        args.save.write_text(
            json.dumps({"metrics": metrics, "results": results}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\n结果已保存：{args.save}")

    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
