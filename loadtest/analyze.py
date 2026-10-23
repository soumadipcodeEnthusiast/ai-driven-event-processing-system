"""
analyze.py — turn a JMeter run + Prometheus metrics into a PASS/FAIL report.

    python3 loadtest/analyze.py --jtl results/<run>/results.jtl --start <epoch s> --end <epoch s> \
        --rate 2000 --batch 100 [--prometheus http://localhost:9090] [--out summary.md]

Checks (thresholds are the SRS acceptance criteria):
  REQ-004  broker ack latency: p95 of a whole batch's acks <= 50 ms
  REQ-001  achieved producer throughput >= 95 % of the target rate
  REQ-001  ingestion consumer lag stays < 5 000 records (= 500 ms at 10 000 events/s)
  SLO      ingestion processing p95 < 500 ms
  E2E      every produced event ingested, scored by anomaly detection and archived
  API      p95 within --api-max-ms and < 1 % HTTP errors per endpoint (slow responses
           are reported separately: they count against p95, not as errors)

Pipeline counts use exact counter deltas (value at --end minus value at --start),
not increase(), which extrapolates to the window edges.

Exit status 1 when any check fails (so CI can gate on it). Stdlib only.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import urllib.parse
import urllib.request
from collections import defaultdict

PRODUCE_LABEL = "produce batch (acks=all)"


def pct(values: list[float], q: float) -> float:
    if not values:
        return math.nan
    s = sorted(values)
    k = (len(s) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def prom(base: str, query: str, at: float) -> float:
    url = f"{base}/api/v1/query?" + urllib.parse.urlencode({"query": query, "time": f"{at:.3f}"})
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            result = json.load(resp)["data"]["result"]
    except Exception as exc:  # Prometheus is optional for the JMeter-side checks
        print(f"warning: Prometheus query failed ({exc}): {query}", file=sys.stderr)
        return math.nan
    return float(result[0]["value"][1]) if result else math.nan


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jtl", required=True)
    ap.add_argument("--start", type=float, required=True, help="load start, unix seconds")
    ap.add_argument("--end", type=float, required=True, help="evaluation time (after drain)")
    ap.add_argument("--rate", type=float, required=True, help="target events/s")
    ap.add_argument("--batch", type=int, required=True)
    ap.add_argument("--duration", type=float, required=True, help="load duration, seconds")
    ap.add_argument("--api-max-ms", type=float, default=500.0)
    ap.add_argument("--prometheus", default="http://localhost:9090")
    ap.add_argument("--out")
    a = ap.parse_args()

    # (timestamp ms, elapsed ms, success, HTTP/sampler response code)
    samples: dict[str, list[tuple[int, int, bool, str]]] = defaultdict(list)
    with open(a.jtl, newline="") as fh:
        for row in csv.DictReader(fh):
            samples[row["label"]].append(
                (int(row["timeStamp"]), int(row["elapsed"]), row["success"] == "true",
                 row["responseCode"])
            )

    checks: list[tuple[str, str, str, bool]] = []  # (id, check, measured, ok)
    lines: list[str] = []

    # ── Producer side (JMeter) ───────────────────────────────────────────────
    prod = samples.get(PRODUCE_LABEL, [])
    ok_batches = [s for s in prod if s[2]]
    produced = len(ok_batches) * a.batch
    if prod:
        first = min(s[0] for s in prod)
        last = max(s[0] + s[1] for s in prod)
        achieved = produced / max((last - first) / 1000.0, 1e-9)
    else:
        achieved = 0.0
    ack = [float(s[1]) for s in ok_batches]
    p95_ack = pct(ack, 0.95)
    checks.append(("REQ-004", f"batch ack p95 ≤ 50 ms (acks=all, {a.batch} events/batch)",
                   f"{p95_ack:.1f} ms (p50 {pct(ack, 0.5):.1f}, p99 {pct(ack, 0.99):.1f})",
                   p95_ack <= 50))
    checks.append(("REQ-001", f"throughput ≥ 95 % of {a.rate:.0f} events/s",
                   f"{achieved:,.0f} events/s ({produced:,} events, {len(prod) - len(ok_batches)} failed batches)",
                   achieved >= 0.95 * a.rate and len(ok_batches) == len(prod)))

    # ── Pipeline side (Prometheus) ───────────────────────────────────────────
    window = f"{int(a.end - a.start)}s"
    lag = prom(a.prometheus,
               'max_over_time((max(kafka_consumer_fetch_manager_records_lag_max{service="ingestion-service"} >= 0))'
               f'[{window}:5s])', a.end)
    checks.append(("REQ-001", "ingestion consumer lag < 5 000 records (≈ 500 ms @ 10k/s)",
                   f"max {lag:,.0f} records", not math.isnan(lag) and lag < 5000))
    p95_proc = prom(a.prometheus,
                    f"histogram_quantile(0.95, sum by (le) (increase(ingestion_processing_seconds_bucket[{window}])))",
                    a.end)
    checks.append(("SLO", "ingestion processing p95 < 500 ms",
                   f"{p95_proc * 1000:.0f} ms", not math.isnan(p95_proc) and p95_proc < 0.5))

    def delta(query: str) -> float:
        before = prom(a.prometheus, query, a.start - 1)
        # A labelled counter only exists after its first increment (e.g. right
        # after a service restart), so "no series yet" at the start means 0.
        return prom(a.prometheus, query, a.end) - (0.0 if math.isnan(before) else before)

    ingested = delta("sum(ingestion_events_total)")
    scored = delta('sum(anomaly_events_scored_total{source="kafka"})')
    anomalies = delta("sum(anomalies_detected_total)")
    audited = delta("sum(anomaly_audit_published_total)")
    archived = delta('sum(archive_records_total{topic="raw-events"})')

    def ratio(n: float) -> float:
        return n / produced if produced else math.nan

    # Other traffic during the run (or at-least-once redelivery) can add a little.
    checks.append(("E2E", "all produced events ingested (±1 %)",
                   f"{ingested:,.0f} / {produced:,} ({ratio(ingested):.1%})", abs(ratio(ingested) - 1) <= 0.01))
    checks.append(("REQ-016", "all ingested events anomaly-scored (±1 %)",
                   f"{scored:,.0f} scored; {anomalies:,.0f} anomalies, {audited:,.0f} audited",
                   ingested > 0 and abs(scored / ingested - 1) <= 0.01))
    checks.append(("REQ-019", "every anomaly recorded on the audit topic",
                   f"{audited:,.0f} / {anomalies:,.0f}", anomalies > 0 and audited >= anomalies))
    checks.append(("REQ-028", "raw events archived to S3 (≥ 99 %)",
                   f"{archived:,.0f} ({ratio(archived):.1%})", ratio(archived) >= 0.99))

    # ── API side (JMeter) ────────────────────────────────────────────────────
    for label, rows in sorted(samples.items()):
        if label == PRODUCE_LABEL or not label.startswith(("POST", "GET")):
            continue
        el = [float(r[1]) for r in rows]
        http_err = sum(not r[3].startswith("2") for r in rows) / len(rows)
        slow = sum(e > a.api_max_ms for e in el)
        p95 = pct(el, 0.95)
        checks.append(("API", f"{label}: p95 ≤ {a.api_max_ms:.0f} ms, HTTP errors < 1 %",
                       f"p95 {p95:.0f} ms, p99 {pct(el, 0.99):.0f} ms, {len(rows)} calls, "
                       f"{http_err:.1%} errors, {slow} slower than {a.api_max_ms:.0f} ms",
                       p95 <= a.api_max_ms and http_err < 0.01))

    lines.append(f"# Load test result — target {a.rate:,.0f} events/s for {a.duration:.0f} s\n")
    lines.append("| Req | Check | Measured | Result |")
    lines.append("|---|---|---|---|")
    for rid, check, measured, ok in checks:
        lines.append(f"| {rid} | {check} | {measured} | {'PASS' if ok else '**FAIL**'} |")
    failed = [c for c in checks if not c[3]]
    lines.append(f"\n**{len(checks) - len(failed)}/{len(checks)} checks passed.**")
    report = "\n".join(lines)
    print(report)
    if a.out:
        with open(a.out, "w") as fh:
            fh.write(report + "\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
