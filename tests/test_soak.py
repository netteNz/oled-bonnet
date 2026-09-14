import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

from oled_hud.soak import BlitTimer, SoakRecorder, percentile, rss_kb

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def load_analyzer():
    """scripts/ isn't a package, so load the analyzer by path."""
    spec = importlib.util.spec_from_file_location(
        "analyze_soak", SCRIPTS / "analyze_soak.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


analyze_soak = load_analyzer()


def read_lines(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


# -- percentile ---------------------------------------------------------------


@pytest.mark.parametrize("q", [0.0, 0.5, 0.95, 0.99, 1.0])
def test_percentile_matches_numpy(q):
    values = sorted(float(v) for v in np.random.default_rng(0).normal(3, 0.5, 500))
    assert percentile(values, q) == pytest.approx(np.percentile(values, q * 100))


def test_percentile_edge_cases():
    assert percentile([], 0.95) == 0.0
    assert percentile([4.2], 0.95) == 4.2


# -- rss ----------------------------------------------------------------------


def test_rss_kb_is_plausible_and_not_a_high_water_mark():
    value = rss_kb()
    assert 1_000 < value < 10_000_000  # a few MB, not pages and not bytes
    assert isinstance(value, int)


# -- recorder -----------------------------------------------------------------


def test_record_frame_does_no_io(tmp_path):
    """The core constraint: nothing reaches the filesystem in the frame path."""
    path = tmp_path / "soak.jsonl"
    rec = SoakRecorder(path, bucket_s=60.0)
    rec.maybe_flush(0.0)  # establish the time origin

    for i in range(5000):
        rec.record_frame(blit_ms=3.0, slack_ms=13.0, late=False, dropped=0)
        rec.maybe_flush(i * 0.001)  # 5 seconds of frames, no bucket roll

    assert path.read_text() == ""
    rec.close()
    assert len(read_lines(path)) == 1


def test_bucket_rolls_over_and_discards_raw_samples(tmp_path):
    path = tmp_path / "soak.jsonl"
    rec = SoakRecorder(path, bucket_s=60.0)
    rec.maybe_flush(0.0)

    for i in range(3600):
        rec.record_frame(blit_ms=3.0, slack_ms=13.0, late=False, dropped=0)
    assert rec.maybe_flush(60.0) is True

    lines = read_lines(path)
    assert len(lines) == 1
    assert lines[0]["t"] == 0.0
    assert lines[0]["frames"] == 3600
    # Raw samples must not survive the flush, or the recorder is the leak.
    assert rec._blit == []
    assert rec._slack == []


def test_bucket_contents_match_the_documented_shape(tmp_path):
    path = tmp_path / "soak.jsonl"
    rec = SoakRecorder(path, bucket_s=10.0)
    rec.maybe_flush(0.0)

    for ms in (2.0, 3.0, 4.0, 100.0):
        rec.record_frame(blit_ms=ms, slack_ms=13.0, late=False, dropped=0)
    rec.record_frame(blit_ms=3.0, slack_ms=-1.0, late=True, dropped=2)
    rec.maybe_flush(10.0)

    row = read_lines(path)[0]
    assert set(row) >= {
        "t", "frames", "late", "dropped", "errors", "blit_ms", "slack_ms", "rss_kb"
    }
    assert row["frames"] == 5
    assert row["late"] == 1
    assert row["dropped"] == 2
    assert row["blit_ms"]["max"] == 100.0
    assert row["slack_ms"]["min"] == -1.0
    assert row["rss_kb"] > 0


def test_errors_are_recorded_by_type_and_errno(tmp_path):
    path = tmp_path / "soak.jsonl"
    rec = SoakRecorder(path, bucket_s=10.0)
    rec.maybe_flush(0.0)

    rec.record_error(OSError(121, "Remote I/O error"))
    rec.record_error(OSError(121, "Remote I/O error"))
    rec.record_error(OSError(5, "Input/output error"))
    rec.record_error(ValueError("no errno"))
    rec.maybe_flush(10.0)

    row = read_lines(path)[0]
    assert row["errors"] == 4
    assert row["error_types"] == {"OSError:121": 2, "OSError:5": 1, "ValueError": 1}


def test_close_flushes_the_final_partial_bucket(tmp_path):
    path = tmp_path / "soak.jsonl"
    rec = SoakRecorder(path, bucket_s=60.0)
    rec.maybe_flush(0.0)
    for _ in range(10):
        rec.record_frame(blit_ms=3.0, slack_ms=13.0, late=False, dropped=0)
    rec.maybe_flush(5.0)
    assert read_lines(path) == []

    rec.close()
    lines = read_lines(path)
    assert len(lines) == 1
    assert lines[0]["frames"] == 10


def test_close_on_an_empty_run_writes_nothing(tmp_path):
    path = tmp_path / "soak.jsonl"
    rec = SoakRecorder(path, bucket_s=60.0)
    rec.close()
    assert read_lines(path) == []
    rec.close()  # idempotent


def test_a_long_stall_emits_one_bucket_per_missed_minute(tmp_path):
    path = tmp_path / "soak.jsonl"
    rec = SoakRecorder(path, bucket_s=60.0)
    rec.maybe_flush(0.0)
    rec.record_frame(blit_ms=3.0, slack_ms=13.0, late=False, dropped=0)
    rec.maybe_flush(185.0)  # three buckets' worth of wall clock passed

    lines = read_lines(path)
    assert [row["t"] for row in lines] == [0.0, 60.0, 120.0]
    assert lines[0]["frames"] == 1
    assert lines[1]["frames"] == 0  # stall is visible, not smeared


def test_context_manager_closes(tmp_path):
    path = tmp_path / "soak.jsonl"
    with SoakRecorder(path, bucket_s=60.0) as rec:
        rec.maybe_flush(0.0)
        rec.record_frame(blit_ms=1.0, slack_ms=1.0, late=False, dropped=0)
    assert len(read_lines(path)) == 1


def test_rejects_a_non_positive_bucket(tmp_path):
    with pytest.raises(ValueError):
        SoakRecorder(tmp_path / "x.jsonl", bucket_s=0)


def test_creates_parent_directories(tmp_path):
    rec = SoakRecorder(tmp_path / "runs" / "nested" / "soak.jsonl")
    rec.close()
    assert (tmp_path / "runs" / "nested").is_dir()


# -- BlitTimer ----------------------------------------------------------------


class Recorder:
    def __init__(self):
        self.errors = []

    def record_error(self, exc):
        self.errors.append(exc)


def test_blit_timer_measures_and_resets_on_success():
    timer = BlitTimer(Recorder())
    with timer:
        pass
    assert timer.blit_ms >= 0.0
    assert timer.consecutive == 0
    assert timer.errors == 0


def test_transient_oserror_is_counted_and_swallowed():
    rec = Recorder()
    timer = BlitTimer(rec, max_consecutive=50)
    for _ in range(10):
        with timer:
            raise OSError(121, "Remote I/O error")
    assert timer.errors == 10
    assert timer.consecutive == 10
    assert len(rec.errors) == 10


def test_a_success_resets_the_consecutive_streak():
    timer = BlitTimer(Recorder(), max_consecutive=3)
    with timer:
        raise OSError(121, "boom")
    with timer:
        raise OSError(121, "boom")
    assert timer.consecutive == 2
    with timer:
        pass
    assert timer.consecutive == 0
    assert timer.errors == 2  # still counted for the run total


def test_a_wedged_bus_propagates():
    timer = BlitTimer(Recorder(), max_consecutive=3)
    with timer:
        raise OSError(121, "boom")
    with timer:
        raise OSError(121, "boom")
    with pytest.raises(OSError):
        with timer:
            raise OSError(121, "boom")


def test_non_oserror_is_never_swallowed():
    timer = BlitTimer(Recorder())
    with pytest.raises(ValueError):
        with timer:
            raise ValueError("a bug, not a bus problem")
    assert timer.errors == 0


def test_blit_timer_works_without_a_recorder():
    timer = BlitTimer(None)
    with timer:
        raise OSError(121, "boom")
    assert timer.errors == 1


# -- analyzer -----------------------------------------------------------------


def make_bucket(t, rss_kb=41216, p95=3.2, frames=3600, late=0, dropped=0, errors=0):
    return {
        "t": t,
        "frames": frames,
        "late": late,
        "dropped": dropped,
        "errors": errors,
        "blit_ms": {"p50": 2.9, "p95": p95, "p99": 4.4, "max": 6.1},
        "slack_ms": {"mean": 13.2, "min": 11.0},
        "rss_kb": rss_kb,
    }


def test_lstsq_slope():
    assert analyze_soak.lstsq_slope([0, 1, 2, 3], [0, 2, 4, 6]) == pytest.approx(2.0)
    assert analyze_soak.lstsq_slope([0, 1, 2], [5, 5, 5]) == pytest.approx(0.0)
    assert analyze_soak.lstsq_slope([1], [5]) == 0.0


def test_healthy_run_passes_every_check():
    buckets = [make_bucket(t * 60.0) for t in range(30)]
    _, checks = analyze_soak.analyze(buckets)
    assert all(passed for _, passed, _ in checks)


def test_a_leak_is_caught():
    # 2 MB/hour climb, steadily upward all the way through the tail.
    buckets = [make_bucket(t * 60.0, rss_kb=41216 + t * 34) for t in range(30)]
    results, checks = analyze_soak.analyze(buckets)
    assert results["rss_slope_kb_h"] > 1024
    assert results["rss_tail_slope_kb_h"] > 1024
    assert results["rss_monotonic"] is True
    assert checks[0][1] is False


def test_warm_up_step_in_bucket_zero_is_ignored():
    """A single early allocation must not read as a leak."""
    buckets = [make_bucket(0.0, rss_kb=20000)] + [
        make_bucket(t * 60.0, rss_kb=41216) for t in range(1, 30)
    ]
    results, checks = analyze_soak.analyze(buckets)
    assert results["rss_slope_kb_h"] == pytest.approx(0.0)
    assert checks[0][1] is True


def test_a_plateaued_staircase_is_not_a_leak():
    """Real soak shape: a few discrete steps early on (allocator/arena
    warm-up), then flat. Monotonic over the whole run, but the tail has
    converged — must pass, not fail, since nothing is still climbing."""
    steps = [35604] * 12 + [35628] * 2 + [35640] * 8 + [35644] * 7  # 29 buckets
    buckets = [make_bucket(t * 60.0, rss_kb=v) for t, v in enumerate(steps)]
    results, checks = analyze_soak.analyze(buckets)
    assert results["rss_monotonic"] is True  # never ticks down
    assert results["rss_slope_kb_h"] < 1024
    assert results["rss_tail_slope_kb_h"] < 1024  # flat by the end
    assert checks[0][1] is True


def test_a_climb_that_starts_late_is_still_caught():
    """A leak that only kicks in partway through must still fail on tail
    slope even though the overall-run slope is diluted by a flat first half."""
    flat = [make_bucket(t * 60.0, rss_kb=40000) for t in range(15)]
    climbing = [make_bucket((15 + t) * 60.0, rss_kb=40000 + t * 50) for t in range(15)]
    results, checks = analyze_soak.analyze(flat + climbing)
    assert results["rss_tail_slope_kb_h"] > 1024
    assert checks[0][1] is False


def test_tail_drift_is_caught():
    buckets = [make_bucket(t * 60.0, p95=3.2) for t in range(20)]
    buckets += [make_bucket((20 + t) * 60.0, p95=4.4) for t in range(10)]
    results, checks = analyze_soak.analyze(buckets)
    assert results["tail_drift"] > 0.15
    assert checks[1][1] is False


def test_errors_and_drops_are_caught():
    buckets = [make_bucket(t * 60.0) for t in range(10)]
    buckets[3]["errors"] = 2
    buckets[3]["error_types"] = {"OSError:121": 2}
    buckets[5]["late"] = 7
    results, checks = analyze_soak.analyze(buckets)
    assert results["errors"] == 2
    assert results["error_types"] == {"OSError:121": 2}
    assert checks[2][1] is False
    assert checks[3][1] is False


def test_analyzer_cli_on_a_real_file(tmp_path, capsys):
    path = tmp_path / "soak.jsonl"
    path.write_text("\n".join(json.dumps(make_bucket(t * 60.0)) for t in range(10)))
    assert analyze_soak.main([str(path), "--exit-code", "0"]) == 0
    assert "PASS" in capsys.readouterr().out


def test_analyzer_cli_fails_on_an_empty_log(tmp_path, capsys):
    path = tmp_path / "empty.jsonl"
    path.write_text("")
    assert analyze_soak.main([str(path)]) == 1
    assert "no buckets" in capsys.readouterr().out


def test_analyzer_reports_a_truncated_final_line(tmp_path, capsys):
    path = tmp_path / "soak.jsonl"
    good = "\n".join(json.dumps(make_bucket(t * 60.0)) for t in range(5))
    path.write_text(good + '\n{"t": 300.0, "frames": 36')
    analyze_soak.main([str(path)])
    assert "unparseable" in capsys.readouterr().out
