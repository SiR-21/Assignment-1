#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
import random
import shutil
import statistics as st
import subprocess
from collections import defaultdict


ALGORITHMS = ("PassPerItem", "UntilNoChange", "WhileNeeded")
TYPES = ("Integer", "String")
SIZES = (128, 512, 2048)
PATTERNS = ("random", "sorted", "reverse", "nearly_sorted", "few_distinct")
LABELS = {"random": "Random distinct", "sorted": "Sorted",
          "reverse": "Reverse", "nearly_sorted": "Nearly sorted",
          "few_distinct": "Few distinct (16 keys)"}
CELL_FIELDS = ("type", "size", "distribution", "algorithm")
# Two-sided 95% Student t critical values. The planned experiment uses df=9.
T95 = (None, 12.706205, 4.302653, 3.182446, 2.776445, 2.570582,
       2.446912, 2.364624, 2.306004, 2.262157, 2.228139, 2.200985,
       2.178813, 2.160369, 2.144787, 2.131450, 2.119905, 2.109816,
       2.100922, 2.093024, 2.085963, 2.079614, 2.073873, 2.068658,
       2.063899, 2.059539, 2.055529, 2.051831, 2.048407, 2.045230,
       2.042272)


def quantile(values, p):
    """Linear interpolation at index (N-1)*p, including both endpoints."""
    x = sorted(values)
    pos = (len(x) - 1) * p
    lo, hi = math.floor(pos), math.ceil(pos)
    return x[lo] + (pos - lo) * (x[hi] - x[lo])


def t95(df):
    if df < 1:
        raise ValueError("At least two JVM replications are needed for an interval")
    if df < len(T95):
        return T95[df]
    # Cornish-Fisher approximation, sufficiently accurate beyond the table.
    z = 1.959963984540054
    return z + (z**3 + z) / (4*df) + (5*z**5 + 16*z**3 + 3*z)/(96*df**2)


def summary(values):
    n = len(values)
    mean = st.mean(values)
    sd = st.stdev(values)
    half = t95(n - 1) * sd / math.sqrt(n)
    q1, q3 = quantile(values, .25), quantile(values, .75)
    return {"n_forks": n, "min_ns": min(values), "q1_ns": q1,
            "median_ns": st.median(values), "q3_ns": q3,
            "max_ns": max(values), "mean_ns": mean, "sample_sd_ns": sd,
            "iqr_ns": q3 - q1, "ci95_low_ns": mean - half,
            "ci95_high_ns": mean + half}


def ratio_summary(ratios):
    logs = [math.log(x) for x in ratios]
    mean = st.mean(logs)
    sd = st.stdev(logs)
    half = t95(len(logs) - 1) * sd / math.sqrt(len(logs))
    return {"n_forks": len(logs), "geometric_mean_speedup": math.exp(mean),
            "ci95_low": math.exp(mean - half),
            "ci95_high": math.exp(mean + half), "mean_log_ratio": mean,
            "sample_sd_log_ratio": sd,
            "min_fork_ratio": min(ratios), "max_fork_ratio": max(ratios)}


def cell_order(cell):
    typ, size, pattern, alg = cell
    return (TYPES.index(typ), PATTERNS.index(pattern), SIZES.index(size),
            ALGORITHMS.index(alg))


def cell_dict(cell):
    return dict(zip(CELL_FIELDS, cell))


def write_csv(path, rows, fields=None):
    if not rows:
        raise ValueError("No rows to write: " + str(path))
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields or list(rows[0]))
        w.writeheader()
        for row in rows:
            w.writerow({k: (format(v, ".12g") if isinstance(v, float) else v)
                        for k, v in row.items()})


def read_raw(directory, expected_forks, expected_blocks):
    paths = sorted(directory.glob("raw-fork-*.csv"))
    if len(paths) != expected_forks:
        raise ValueError(f"Expected {expected_forks} raw files, found {len(paths)}")
    rows = []
    seen = set()
    required = {"fork", "block", "order", "input_seed", *CELL_FIELDS,
                "batch", "elapsed_ns", "ns_per_sort", "gc_count", "gc_time_ms"}
    expected_cells = {(t, n, p, a) for t in TYPES for n in SIZES
                      for p in PATTERNS for a in ALGORITHMS}
    grouped = defaultdict(list)
    input_seeds = defaultdict(set)
    for path in paths:
        with path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            if set(reader.fieldnames or ()) != required:
                raise ValueError("Unexpected raw schema in " + str(path))
            for raw in reader:
                row = {k: (float(v) if k == "ns_per_sort" else int(v))
                       if k not in ("type", "distribution", "algorithm") else v
                       for k, v in raw.items()}
                cell = tuple(row[k] for k in CELL_FIELDS)
                key = (row["fork"], row["block"], cell)
                if key in seen or cell not in expected_cells:
                    raise ValueError("Duplicate or unexpected raw cell: " + str(key))
                seen.add(key)
                if not (1 <= row["fork"] <= expected_forks and
                        1 <= row["block"] <= expected_blocks):
                    raise ValueError("Unexpected fork or block")
                if not (row["batch"] > 0 and row["elapsed_ns"] > 0 and
                        math.isfinite(row["ns_per_sort"]) and row["ns_per_sort"] > 0):
                    raise ValueError("Nonpositive or invalid duration/batch")
                exact = row["elapsed_ns"] / row["batch"]
                if not math.isclose(exact, row["ns_per_sort"], rel_tol=1e-10, abs_tol=1e-6):
                    raise ValueError("Duration inconsistent with batch normalization")
                grouped[(row["fork"], row["block"])].append(row)
                input_seeds[(row["fork"], row["block"], row["size"],
                             row["distribution"])].add(row["input_seed"])
                rows.append(row)
    expected_rows = len(expected_cells) * expected_blocks * expected_forks
    if len(rows) != expected_rows:
        raise ValueError(f"Expected {expected_rows} raw rows, found {len(rows)}")
    for block, group in grouped.items():
        if (len(group) != len(expected_cells) or
                {r["order"] for r in group} != set(range(1, len(expected_cells)+1))):
            raise ValueError("Incomplete block or invalid randomized order: " + str(block))
    if any(len(seeds) != 1 for seeds in input_seeds.values()):
        raise ValueError("Inputs are not paired across algorithms and key types")
    return rows


def read_properties(path):
    result = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            result[key] = value
    return result


def analyze(rows, directory, results_dir, forks, blocks):
    by_fork = defaultdict(list)
    for row in rows:
        cell = tuple(row[k] for k in CELL_FIELDS)
        # Recalculate from integer raw time rather than rounded display values.
        by_fork[(cell, row["fork"])].append(row["elapsed_ns"] / row["batch"])
    fork_means = {key: st.mean(values) for key, values in by_fork.items()}
    cells = sorted({key[0] for key in fork_means}, key=cell_order)
    samples = {c: [fork_means[(c, f)] for f in range(1, forks+1)] for c in cells}
    stats = {c: summary(samples[c]) for c in cells}
    write_csv(results_dir / "fork-means.csv", [
        {**cell_dict(c), "fork": f, "n_blocks": len(by_fork[(c, f)]),
         "mean_ns": fork_means[(c, f)]} for c in cells for f in range(1, forks+1)])
    write_csv(results_dir / "stats.csv", [{**cell_dict(c), **stats[c]} for c in cells])

    workloads = [(t, n, p) for t in TYPES for p in PATTERNS for n in SIZES]
    speedups = []
    for competitor in ALGORITHMS[:-1]:
        by_workload = {}
        for workload in workloads:
            ratios = [fork_means[(workload+(competitor,), f)] /
                      fork_means[(workload+("WhileNeeded",), f)]
                      for f in range(1, forks+1)]
            by_workload[workload] = ratios
            speedups.append({"scope": "condition", "type": workload[0],
                             "size": workload[1], "distribution": workload[2],
                             "competitor": competitor, "baseline": "WhileNeeded",
                             **ratio_summary(ratios)})
        overall = [math.exp(st.mean(math.log(by_workload[w][f]) for w in workloads))
                   for f in range(forks)]
        speedups.append({"scope": "overall_equal_weight", "type": "ALL",
                         "size": "ALL", "distribution": "ALL",
                         "competitor": competitor, "baseline": "WhileNeeded",
                         **ratio_summary(overall)})
    write_csv(results_dir / "paired-speedups.csv", speedups)

    winners = []
    wins = {alg: 0 for alg in ALGORITHMS}
    ties = 0
    for workload in workloads:
        means = {a: stats[workload+(a,)]["mean_ns"] for a in ALGORITHMS}
        smallest = min(means.values())
        best = [a for a in ALGORITHMS if means[a] == smallest]
        ties += len(best) > 1
        if len(best) == 1:
            wins[best[0]] += 1
        winners.append({"type": workload[0], "size": workload[1],
                        "distribution": workload[2], "fastest_by_mean": best,
                        "mean_ns": means})

    def compare(t, n, p, faster, slower):
        fast = stats[(t, n, p, faster)]["mean_ns"]
        slow = stats[(t, n, p, slower)]["mean_ns"]
        return {"type": t, "size": n, "distribution": p, "faster": faster,
                "slower": slower, "observed_slower_over_faster": slow/fast,
                "prediction_holds": fast < slow}

    h1 = [compare(t, 2048, p, "WhileNeeded", other)
          for t in TYPES for p in ("random", "reverse") for other in ALGORITHMS[:-1]]
    h2 = [compare(t, n, p, early, "PassPerItem") for t in TYPES
          for n in SIZES for p in ("sorted", "nearly_sorted")
          for early in ("UntilNoChange", "WhileNeeded")]
    h3 = []
    for t in TYPES:
        for p in ("random", "reverse"):
            for alg in ALGORITHMS:
                means = [stats[(t, n, p, alg)]["mean_ns"] for n in SIZES]
                h3.append({"type": t, "distribution": p, "algorithm": alg,
                           "sizes": list(SIZES), "means_ns": means,
                           "prediction_holds": all(x < y for x, y in zip(means, means[1:]))})
    gc_rows = [{**{k: r[k] for k in ("fork", "block", "order", *CELL_FIELDS,
                                    "batch", "elapsed_ns", "gc_count", "gc_time_ms")}}
               for r in rows if r["gc_count"] > 0 or r["gc_time_ms"] > 0]
    logs = [read_properties(p) for p in sorted(directory.glob("run-fork-*.txt"))]
    durations = {"timed_batches_seconds": sum(r["elapsed_ns"] for r in rows)/1e9,
                 "total_sort_calls_measured": sum(r["batch"] for r in rows),
                 "min_batch_ns": min(r["elapsed_ns"] for r in rows),
                 "max_batch_ns": max(r["elapsed_ns"] for r in rows),
                 "batch_target_ns": 5000000,
                 "batches_below_target": sum(r["elapsed_ns"] < 5000000 for r in rows),
                 "min_batch_count": min(r["batch"] for r in rows),
                 "max_batch_count": max(r["batch"] for r in rows),
                 "batch_cap": 1024,
                 "batches_at_cap": sum(r["batch"] == 1024 for r in rows),
                 "fraction_batches_at_cap": sum(r["batch"] == 1024 for r in rows)/len(rows),
                 "fraction_batches_below_target": sum(r["elapsed_ns"] < 5000000 for r in rows)/len(rows)}
    if len(logs) == forks:
        durations.update({"sum_fork_wall_seconds": sum(int(p["wall_ns"]) for p in logs)/1e9,
                          "sum_warmup_seconds": sum(int(p["warmup_ns"]) for p in logs)/1e9,
                          "min_warmup_rounds": min(int(p["warmup_rounds"]) for p in logs),
                          "max_warmup_rounds": max(int(p["warmup_rounds"]) for p in logs)})
    max_sd = max(cells, key=lambda c: stats[c]["sample_sd_ns"])
    max_cv = max(cells, key=lambda c: stats[c]["sample_sd_ns"]/stats[c]["mean_ns"])
    fastest = min(cells, key=lambda c: stats[c]["mean_ns"])
    slowest = max(cells, key=lambda c: stats[c]["mean_ns"])
    findings = {
        "analysis_unit": "one fresh JVM; arithmetic mean of its block ns-per-sort values",
        "independent_replications_per_cell": forks, "blocks_per_fork_cell": blocks,
        "raw_rows": len(rows), "cells": len(cells), "workloads": len(workloads),
        "quartile_method": "linear interpolation at (N-1)*p",
        "interval_method": "two-sided 95% Student t; cell means on raw scale, paired speedups on log scale",
        "interval_df": forks-1, "t_critical": t95(forks-1),
        "interval_limitations": "Approximate marginal intervals assuming independent process-level observations and suitable sampling behavior; do not cover systematic bias or provide simultaneous/multiplicity-controlled claims.",
        "excluded_rows": 0, "wins_by_condition_mean": wins, "tied_workloads": ties,
        "condition_winners": winners,
        "overall_paired_speedups": [s for s in speedups if s["scope"] == "overall_equal_weight"],
        "hypotheses": {name: {"holds_count": sum(r["prediction_holds"] for r in values),
                              "comparison_count": len(values),
                              "all_hold": all(r["prediction_holds"] for r in values),
                              "comparisons": values}
                       for name, values in (("H1", h1), ("H2", h2), ("H3", h3))},
        "extremes": {"fastest_cell": {**cell_dict(fastest), **stats[fastest]},
                     "slowest_cell": {**cell_dict(slowest), **stats[slowest]},
                     "largest_sd_cell": {**cell_dict(max_sd), **stats[max_sd]},
                     "largest_cv_cell": {**cell_dict(max_cv), **stats[max_cv],
                                         "coefficient_of_variation": stats[max_cv]["sample_sd_ns"]/stats[max_cv]["mean_ns"]}},
        "durations": durations,
        "gc": {"rows_with_observed_collection": len(gc_rows),
               "observed_collection_count_sum": sum(r["gc_count"] for r in rows),
               "observed_collection_time_ms_sum": sum(r["gc_time_ms"] for r in rows),
               "rows": gc_rows,
               "note": "GC counters are diagnostics around timed regions; zero does not imply absence of other runtime or OS interference."}}
    (results_dir / "findings.json").write_text(json.dumps(findings, indent=2)+"\n", encoding="utf-8")
    return samples, findings


def figures(samples, out_dir, render_png):
    """Draw every process mean; each panel uses a labeled logarithmic y axis."""
    try:
        import reportlab
        from reportlab.pdfgen import canvas
        from reportlab.lib.colors import HexColor, white
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
    except ImportError as e:
        raise SystemExit("Plotting needs reportlab; omit --plots for statistics only") from e
    out_dir.mkdir(parents=True, exist_ok=True)
    # Vera ships with ReportLab. Embedded text avoids OS-specific font fallback.
    font_dir = Path(reportlab.__file__).parent / "fonts"
    pdfmetrics.registerFont(TTFont("StudySans", str(font_dir / "Vera.ttf")))
    pdfmetrics.registerFont(TTFont("StudySansBold", str(font_dir / "VeraBd.ttf")))
    colors = {"PassPerItem": HexColor("#b54445"),
              "UntilNoChange": HexColor("#2774ae"),
              "WhileNeeded": HexColor("#12836f")}
    page_w, page_h = 540, 540
    for typ in TYPES:
        path = out_dir / ("fork-distributions-" + typ.lower() + ".pdf")
        c = canvas.Canvas(str(path), pagesize=(page_w, page_h))
        c.setTitle(typ + " keys: distribution of JVM-level sort durations")
        c.setAuthor("Bubble-sort experiment")
        c.setFillColor(HexColor("#152c3a"))
        c.setFont("StudySansBold", 15)
        c.drawString(27, 512, typ + " keys | duration across fresh JVMs")
        c.setFont("StudySans", 8.5)
        c.drawString(27, 498, "Each dot is one JVM mean; 8 measured blocks per JVM. Lower is faster.")
        for panel, pattern in enumerate(PATTERNS):
            col, row = panel % 2, panel // 2
            left, bottom = 47 + col*263, 355 - row*151
            width, height = 211, 103
            values = [v/1000 for n in SIZES for a in ALGORITHMS
                      for v in samples[(typ, n, pattern, a)]]
            low, high = math.log10(min(values)), math.log10(max(values))
            spread = max(.35, high-low)
            low, high = low-.1*spread, high+.12*spread

            def yy(us):
                return bottom + (math.log10(us)-low)/(high-low)*height

            c.setFillColor(HexColor("#152c3a"))
            c.setFont("StudySansBold", 9.2)
            c.drawString(left, bottom+height+12, LABELS[pattern])
            c.setFont("StudySans", 6.6)
            c.drawString(left, bottom+height+3, "Duration per sort (microseconds, log scale)")
            ticks = [m*10**e for e in range(math.floor(low)-1, math.ceil(high)+1)
                     for m in (1, 2, 5) if low <= math.log10(m*10**e) <= high]
            if len(ticks) > 7:
                ticks = [10**e for e in range(math.floor(low), math.ceil(high)+1)
                         if low <= e <= high]
            for tick in ticks:
                y = yy(tick)
                c.setStrokeColor(HexColor("#e0e6e9")); c.setLineWidth(.4)
                c.line(left, y, left+width, y)
                c.setFont("StudySans", 6.8); c.setFillColor(HexColor("#465a66"))
                c.drawRightString(left-4, y-2.2, f"{tick:g}")
            c.setStrokeColor(HexColor("#8998a1")); c.setLineWidth(.6)
            c.line(left, bottom, left+width, bottom)
            c.line(left, bottom, left, bottom+height)
            for ni, n in enumerate(SIZES):
                center = left + width*(ni+.5)/3
                c.setFont("StudySans", 7.5); c.setFillColor(HexColor("#263f4d"))
                c.drawCentredString(center, bottom-12, f"n={n}")
                for ai, alg in enumerate(ALGORITHMS):
                    x = center+(ai-1)*18
                    vals = [v/1000 for v in samples[(typ, n, pattern, alg)]]
                    q1, q3 = quantile(vals, .25), quantile(vals, .75)
                    color = colors[alg]
                    c.setStrokeColor(color); c.setFillColor(white); c.setLineWidth(.8)
                    c.line(x, yy(min(vals)), x, yy(max(vals)))
                    for extreme in (min(vals), max(vals)):
                        c.line(x-3, yy(extreme), x+3, yy(extreme))
                    c.rect(x-5, yy(q1), 10, max(.25, yy(q3)-yy(q1)), fill=1, stroke=1)
                    c.setLineWidth(1.3)
                    c.line(x-5, yy(st.median(vals)), x+5, yy(st.median(vals)))
                    rng = random.Random(f"{typ}:{pattern}:{n}:{alg}")
                    c.setFillColor(color)
                    for v in vals:
                        c.circle(x+rng.uniform(-3.6, 3.6), yy(v), 1.35, fill=1, stroke=0)
        # Sixth panel documents the visual encoding, with no hidden cells.
        left, top = 310, 156
        c.setFillColor(HexColor("#152c3a")); c.setFont("StudySansBold", 10)
        c.drawString(left, top, "How to read these panels")
        for index, alg in enumerate(ALGORITHMS):
            y = top-19-index*17
            c.setFillColor(colors[alg]); c.rect(left, y-2, 10, 6, fill=1, stroke=0)
            c.setFillColor(HexColor("#263f4d")); c.setFont("StudySans", 8.8)
            c.drawString(left+17, y-2, alg)
        c.setFont("StudySans", 8)
        for i, line in enumerate(("Box: Q1 to Q3; line: median.",
                                  "Whiskers: minimum to maximum.",
                                  "Dots: all 10 process means.",
                                  "Y-axis ranges vary by panel.",
                                  "All observations retained.")):
            c.drawString(left, top-78-i*12, line)
        c.setFillColor(HexColor("#465a66")); c.setFont("StudySans", 7)
        c.drawString(27, 17, "Source: raw-fork-*.csv; aggregation and quartiles defined in analysis/analyze.py.")
        c.showPage(); c.save()
        if render_png:
            binary = shutil.which("pdftoppm")
            if not binary:
                raise SystemExit("PNG rendering needs pdftoppm; install Poppler or omit --png")
            subprocess.run([binary, "-png", "-r", "180", "-singlefile",
                            str(path), str(path.with_suffix(""))], check=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parents[1]
    p.add_argument("--data", type=Path, default=root / "data")
    p.add_argument("--results", type=Path, default=root / "results")
    p.add_argument("--expected-forks", type=int, default=10)
    p.add_argument("--expected-blocks", type=int, default=8)
    p.add_argument("--plots", action="store_true", help="Create vector PDF figures (requires reportlab)")
    p.add_argument("--png", action="store_true", help="Also render figures with pdftoppm; implies --plots")
    args = p.parse_args()
    if args.expected_forks < 2 or args.expected_blocks < 1:
        p.error("At least two forks and one block are required")
    args.results.mkdir(parents=True, exist_ok=True)
    rows = read_raw(args.data, args.expected_forks, args.expected_blocks)
    samples, findings = analyze(rows, args.data, args.results,
                                args.expected_forks, args.expected_blocks)
    if args.plots or args.png:
        figures(samples, args.results / "figures", args.png)
    print(f"Validated {len(rows)} raw rows; {len(samples)} cells; {args.expected_forks} JVM replicates per cell.")
    print("Condition wins by mean: " + json.dumps(findings["wins_by_condition_mean"]))
    for item in findings["overall_paired_speedups"]:
        print(f"{item['competitor']}/WhileNeeded equal-weight geometric speedup: "
              f"{item['geometric_mean_speedup']:.3g} "
              f"(95% CI {item['ci95_low']:.3g} to {item['ci95_high']:.3g})")


if __name__ == "__main__":
    main()
