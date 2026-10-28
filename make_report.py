#!/usr/bin/env python
"""Write a swap-absorption correctness report using only the standard library."""
import argparse
import json
from collections import Counter
from pathlib import Path

from run_validation import CHECKS
from swap_absorption_checks import FEATURE_CHECKS


def passed(record):
    return record.get("passed") is True and not record.get("error")


def build_report(records, results_path):
    """Validate the complete case matrix and return ``(success, Markdown)``."""
    if not all(isinstance(r, dict) for r in records):
        raise ValueError("Every result must be a JSON object")
    environments = [r for r in records if r.get("record") == "environment"]
    if len(environments) != 1:
        raise ValueError("Expected exactly one environment record")
    env = environments[0]
    if env.get("schema_version") != 2:
        raise ValueError("Expected swap-absorption schema version 2; rerun run_validation.py")
    fixtures, seeds, levels = env["fixtures"], env["seeds"], env["optimization_levels"]
    if not (
        isinstance(fixtures, list) and all(isinstance(f, str) for f in fixtures)
        and len(fixtures) == len(set(fixtures)) == 16
        and isinstance(seeds, list) and seeds
        and all(type(s) is int and 0 <= s < 2**64 for s in seeds)
        and len(seeds) == len(set(seeds))
        and isinstance(levels, list) and levels
        and all(type(level) is int and level in (2, 3) for level in levels)
        and len(levels) == len(set(levels))
    ):
        raise ValueError("Invalid fixtures, seeds, or optimization levels")
    settings = dict(basis_gates=["rz", "sx", "x", "cx"], coupling_map="line(10)",
                    layout_method="trivial", routing_method="sabre", translation_method="translator",
                    approximation_degree=1.0, qubits_initially_zero=False)
    cases = [r for r in records if r.get("record") == "case"]
    checks = [r for r in records if r.get("record") == "self_check"]
    features = [r for r in records if r.get("record") == "feature_check"]
    coverage = [r for r in records if r.get("record") == "coverage"]
    expected = Counter((f, s, level) for f in fixtures for s in seeds for level in levels)
    complete = (
        all(env.get(key) == value for key, value in settings.items())
        and len(records) == 1 + len(cases) + len(checks) + len(features) + len(coverage)
        and Counter((r.get("fixture"), r.get("seed"), r.get("optimization_level"))
                    for r in cases) == expected
        and Counter((r.get("name"), r.get("optimization_level")) for r in checks)
        == Counter((name, level) for name in CHECKS for level in levels)
        and Counter(r.get("name") for r in features) == Counter(FEATURE_CHECKS)
        and Counter(r.get("optimization_level") for r in coverage) == Counter(levels)
    )
    metrics = ("absorption_runs", "absorption_moves", "absorption_swaps_seen", "depth",
               "two_qubit_gates")
    for rec in cases:
        if passed(rec):
            complete &= (
                all(type(rec.get(key)) is int and rec[key] >= 0 for key in metrics)
                and rec.get("absorption_runs", 0) > 0
                and sorted(rec.get("final_permutation", [])) == list(range(10))
            )
    for rec in coverage:
        selected = [r for r in cases if r.get("optimization_level") == rec.get("optimization_level")]
        moves = sum(r.get("absorption_moves", 0) for r in selected)
        complete &= (
            rec.get("absorption_moves") == moves and moves > 0
            and rec.get("absorption_runs") == sum(r.get("absorption_runs", 0) for r in selected)
            and rec.get("cases_with_moves") == sum(r.get("absorption_moves", 0) > 0 for r in selected)
        )
    failed = [r for r in cases + checks + features + coverage if not passed(r)]
    ok = complete and not failed
    lines = [
        "# SWAP absorption correctness", "",
        f"**{'PASS' if ok else 'FAIL'}** — {sum(map(passed, cases))}/{sum(expected.values())} "
        f"cases; {sum(map(passed, checks))}/{len(CHECKS) * len(levels)} negative self-checks; "
        f"{sum(map(passed, features))}/{len(FEATURE_CHECKS)} focused checks.", "",
        f"Revision: `{env['sha']}`. Seeds: `{seeds}`. Optimization levels: `{levels}`.",
        f"Run: {env['timestamp']}. Qiskit {env['qiskit_version']}; Python {env['python']}.",
        f"Loaded extension SHA-256: `{env['accelerate_sha256']}`.", "",
        "16 circuits, 10 qubits each; full preset pipeline, bidirectional line, trivial initial "
        "layout, SABRE routing, and the `rz`, `sx`, `x`, `cx` basis. Exact synthesis; arbitrary "
        "input states (`qubits_initially_zero=False`).",
        "Each output must match its original unitary (layout-aware, global phase kept, default "
        "tolerances), preserve width and layout metadata, satisfy connectivity and basis "
        "constraints, and execute the absorption pass.", "",
        "| Circuit | " + " | ".join(f"Level {level}" for level in levels) + " |",
        "|---|" + "---|" * len(levels),
    ]
    for fixture in fixtures:
        counts = [sum(passed(r) for r in cases if r.get("fixture") == fixture
                      and r.get("optimization_level") == level) for level in levels]
        lines.append(f"| {fixture} | " + " | ".join(f"{n}/{len(seeds)}" for n in counts) + " |")
    lines += ["", "Actual moves selected by the pass (required at every requested level):", "",
              "| Level | Cases with moves | Moves | Pass executions | Coverage |",
              "|---|---|---|---|---|"]
    for level in levels:
        matches = [r for r in coverage if r.get("optimization_level") == level]
        rec = matches[0] if len(matches) == 1 else {}
        lines.append(f"| {level} | {rec.get('cases_with_moves', '?')} | "
                     f"{rec.get('absorption_moves', '?')} | {rec.get('absorption_runs', '?')} | "
                     f"{'PASS' if passed(rec) else 'FAIL'} |")
    lines += ["", "Negative controls use a passing output with a nonidentity final permutation "
              "at each level. Ignoring its layout and appending an extra X must each break "
              "the unitary comparison.", ""]
    for level in levels:
        for name in CHECKS:
            matches = [r for r in checks if r.get("name") == name
                       and r.get("optimization_level") == level]
            rec = matches[0] if len(matches) == 1 else {}
            source = f"`{rec.get('fixture', '?')}`, seed {rec.get('seed', '?')}"
            lines.append(f"- Level {level}, `{name}`: {'PASS' if passed(rec) else 'FAIL'} ({source}).")
    lines += ["", "Focused checks:", ""]
    for name in FEATURE_CHECKS:
        matches = [r for r in features if r.get("name") == name]
        good = len(matches) == 1 and passed(matches[0])
        lines.append(f"- `{name}`: {'PASS' if good else 'FAIL'}.")
    if not complete:
        lines += ["", "FAIL: missing, duplicate, or unexpected results/settings, or missing move coverage."]
    for rec in failed:
        label = (f"{rec.get('fixture')} seed {rec.get('seed')}" if rec.get("record") == "case"
                 else rec.get("name", rec.get("record")))
        if "optimization_level" in rec:
            label += f" level {rec['optimization_level']}"
        lines += ["", f"FAIL: {label}: {rec.get('error', 'check failed')}"]
    lines += ["", "Scope: full preset correctness on these circuits and seeds, not a general proof. "
              "Gate counts and depths are diagnostics; an improvement is not required for every case. "
              f"Raw results and build path: `{results_path}`."]
    return ok, "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=Path("results/results.jsonl"))
    parser.add_argument("--out", type=Path, default=Path("report.md"))
    args = parser.parse_args()
    try:
        records = [json.loads(line) for line in args.results.read_text().splitlines() if line.strip()]
        ok, report = build_report(records, args.results)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        ok, report = False, f"# SWAP absorption correctness\n\n**FAIL** — invalid results: {exc}\n"
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report, encoding="utf-8")
    print(f"{'PASS' if ok else 'FAIL'}; wrote {args.out}")
    return int(not ok)


if __name__ == "__main__":
    raise SystemExit(main())
