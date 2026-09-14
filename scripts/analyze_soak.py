#!/usr/bin/env python3
"""Read a soak JSONL log and print a pass/fail verdict.

Checks the four things a soak run exists to answer, none of which a
whole-run average would show:

  RSS slope     least-squares KB/hour, bucket 0 excluded (import and first-touch
                allocation land there and would tilt the fit)
  Tail drift    mean blit p95 over the first third vs the last third — the
                thing BENCH.md's 4.40 vs 3.20ms p95 disagreement makes worth
                watching
  Errors        counts by type and errno; recovered errors are acceptable if
                written up, since that rate feeds the HUD daemon's error policy
  Slack floor   the single worst frame of the run, which a mean would bury

Usage:
    python scripts/analyze_soak.py runs/soak-20260913-2350.jsonl
    python scripts/analyze_soak.py runs/*.jsonl --exit-code 0
"""

import argparse
import json
import sys

MB = 1024
RSS_SLOPE_LIMIT_KB_H = 1024.0  # ~1 MB/hour
TAIL_DRIFT_LIMIT = 0.15  # 15%


def load(path):
    buckets = []
    with open(path) as fh:
        for line_no, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                buckets.append(json.loads(line))
            except json.JSONDecodeError as exc:
                # A truncated final line means the run was killed mid-write;
                # that is worth saying out loud rather than silently dropping.
                print(f"  ! {path}:{line_no}: unparseable line ({exc})")
    return buckets


def lstsq_slope(xs, ys):
    """Least-squares slope of ys against xs (units: y per x)."""
    n = len(xs)
    if n < 2:
        return 0.0
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    denom = sum((x - mean_x) ** 2 for x in xs)
    if denom == 0:
        return 0.0
    return sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denom


def mean(values):
    return sum(values) / len(values) if values else 0.0


def thirds(values):
    """Split into (first third, last third); needs at least 3 elements."""
    n = len(values)
    if n < 3:
        return values, values
    k = n // 3
    return values[:k], values[-k:]


def analyze(buckets):
    """Return (results dict, list of (name, passed, detail))."""
    frames = sum(b.get("frames", 0) for b in buckets)
    late = sum(b.get("late", 0) for b in buckets)
    dropped = sum(b.get("dropped", 0) for b in buckets)
    errors = sum(b.get("errors", 0) for b in buckets)

    error_types = {}
    for b in buckets:
        for key, count in b.get("error_types", {}).items():
            error_types[key] = error_types.get(key, 0) + count

    # RSS slope, skipping bucket 0 (warm-up).
    rss_buckets = [b for b in buckets if "rss_kb" in b][1:]
    rss_t = [b["t"] for b in rss_buckets]
    rss_v = [b["rss_kb"] for b in rss_buckets]
    slope_kb_h = lstsq_slope(rss_t, rss_v) * 3600.0
    monotonic = len(rss_v) >= 3 and all(
        b >= a for a, b in zip(rss_v, rss_v[1:])
    ) and rss_v[-1] > rss_v[0]

    p95s = [b["blit_ms"]["p95"] for b in buckets if "blit_ms" in b]
    first, last = thirds(p95s)
    first_p95, last_p95 = mean(first), mean(last)
    drift = (last_p95 - first_p95) / first_p95 if first_p95 else 0.0

    slack_mins = [b["slack_ms"]["min"] for b in buckets if "slack_ms" in b]
    slack_floor = min(slack_mins) if slack_mins else 0.0

    results = {
        "buckets": len(buckets),
        "frames": frames,
        "late": late,
        "dropped": dropped,
        "errors": errors,
        "error_types": error_types,
        "rss_first": rss_v[0] if rss_v else 0,
        "rss_last": rss_v[-1] if rss_v else 0,
        "rss_slope_kb_h": slope_kb_h,
        "rss_monotonic": monotonic,
        "p95_first_third": first_p95,
        "p95_last_third": last_p95,
        "tail_drift": drift,
        "slack_floor_ms": slack_floor,
    }

    checks = [
        (
            "RSS slope under 1 MB/h, no monotonic climb",
            slope_kb_h < RSS_SLOPE_LIMIT_KB_H and not monotonic,
            f"{slope_kb_h:+.1f} KB/h, {results['rss_first']} -> {results['rss_last']} KB"
            + (", MONOTONIC" if monotonic else ""),
        ),
        (
            "blit p95 last third within 15% of first",
            abs(drift) <= TAIL_DRIFT_LIMIT,
            f"{first_p95:.2f} -> {last_p95:.2f} ms ({drift * 100:+.1f}%)",
        ),
        (
            "no errors",
            errors == 0,
            f"{errors} recovered" + (f" {error_types}" if error_types else ""),
        ),
        (
            "late/dropped at or near zero",
            late == 0 and dropped == 0,
            f"{late} late, {dropped} dropped of {frames} frames",
        ),
    ]
    return results, checks


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="+", help="soak JSONL log(s)")
    parser.add_argument(
        "--exit-code",
        type=int,
        default=None,
        help="the run's exit code, to fold criterion 5 into the verdict",
    )
    args = parser.parse_args(argv)

    ok = True
    for path in args.paths:
        buckets = load(path)
        print(f"\n=== {path} ===")
        if not buckets:
            print("  no buckets — run produced nothing")
            ok = False
            continue

        results, checks = analyze(buckets)
        span_min = (buckets[-1]["t"] + 60.0) / 60.0
        print(
            f"  {results['buckets']} buckets / ~{span_min:.0f} min, "
            f"{results['frames']} frames"
        )
        print(
            f"  blit p95 {results['p95_first_third']:.2f} -> "
            f"{results['p95_last_third']:.2f} ms | "
            f"slack floor {results['slack_floor_ms']:.1f} ms | "
            f"RSS {results['rss_first']} -> {results['rss_last']} KB"
        )
        print()
        for name, passed, detail in checks:
            print(f"  [{'PASS' if passed else 'FAIL'}] {name}: {detail}")
            ok = ok and passed

        if args.exit_code is not None:
            passed = args.exit_code == 0
            print(f"  [{'PASS' if passed else 'FAIL'}] clean exit: code {args.exit_code}")
            ok = ok and passed
        else:
            print("  [ -- ] clean exit: pass --exit-code to include this check")

    print(f"\n{'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
