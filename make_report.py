#!/usr/bin/env python
"""Write a swap-absorption correctness report using only the standard library."""
import argparse
import json
import re
from collections import Counter
from pathlib import Path

from run_validation import CHECKS
from swap_absorption_checks import FEATURE_CHECKS


def passed(record):
    return record.get("passed") is True and not record.get("error")


def sha256(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def permutation(value):
    return (isinstance(value, list) and all(type(v) is int for v in value)
            and sorted(value) == list(range(10)))


def case_passed(record):
    """A passing flag alone does not establish correctness or actual absorption."""
    metrics = ("absorption_runs", "absorption_moves", "absorption_swaps_seen", "depth",
               "two_qubit_gates", "disabled_depth", "disabled_two_qubit_gates")
    return (
        passed(record)
        and all(type(record.get(key)) is int and record[key] >= 0 for key in metrics)
        and record["absorption_runs"] > 0 and record["absorption_moves"] > 0
        and record.get("enabled_passed") is True
        and record.get("disabled_passed") is True
        and record.get("absorption_changed") is True
        and permutation(record.get("final_permutation"))
        and permutation(record.get("disabled_final_permutation"))
    )


def build_report(records, results_path):
    """Validate the complete case matrix and return ``(success, Markdown)``."""
    if not all(isinstance(r, dict) for r in records):
        raise ValueError("Every result must be a JSON object")
    environments = [r for r in records if r.get("record") == "environment"]
    if len(environments) != 1:
        raise ValueError("Expected exactly one environment record")
    env = environments[0]
    if env.get("schema_version") != 4:
        raise ValueError("Expected swap-absorption schema version 4; rerun run_validation.py")
    provenance = (
        all(isinstance(env.get(key), str) and env[key]
            for key in ("sha", "qiskit_version", "qiskit_source", "accelerate_file"))
        and sha256(env.get("accelerate_sha256"))
        and env.get("comparison_method") == "dag"
        and env.get("comparison_mode") == "enabled_vs_disabled"
    )
    fixtures, seeds, levels = env["fixtures"], env["seeds"], env["optimization_levels"]
    if not (
        isinstance(fixtures, list) and all(isinstance(f, str) for f in fixtures)
        and len(fixtures) == len(set(fixtures)) == 11
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
        provenance and all(env.get(key) == value for key, value in settings.items())
        and len(records) == 1 + len(cases) + len(checks) + len(features) + len(coverage)
        and Counter((r.get("fixture"), r.get("seed"), r.get("optimization_level"))
                    for r in cases) == expected
        and Counter((r.get("name"), r.get("optimization_level")) for r in checks)
        == Counter((name, level) for name in CHECKS for level in levels)
        and Counter(r.get("name") for r in features) == Counter(FEATURE_CHECKS)
        and Counter(r.get("optimization_level") for r in coverage) == Counter(levels)
    )
    complete &= all(case_passed(rec) for rec in cases)
    for rec in coverage:
        selected = [r for r in cases if r.get("optimization_level") == rec.get("optimization_level")]
        moves = sum(r.get("absorption_moves", 0) for r in selected)
        complete &= (
            rec.get("absorption_moves") == moves and moves > 0
            and rec.get("absorption_runs") == sum(r.get("absorption_runs", 0) for r in selected)
            and rec.get("cases_with_moves") == sum(r.get("absorption_moves", 0) > 0 for r in selected)
            and rec.get("cases_with_moves") == len(selected)
            and rec.get("cases_absorption_changed")
            == sum(r.get("absorption_changed") is True for r in selected)
            and rec.get("cases_absorption_changed") == len(selected)
        )
    failed = [r for r in cases + checks + features + coverage if not passed(r)]
    ok = complete and not failed
    lines = [
        "# SWAP absorption correctness", "",
        f"**{'PASS' if ok else 'FAIL'}** — {sum(map(case_passed, cases))}/{sum(expected.values())} cases; "
        f"{sum(map(passed, checks))}/{len(CHECKS) * len(levels)} negative self-checks; "
        f"{sum(map(passed, features))}/{len(FEATURE_CHECKS)} focused checks.", "",
        f"Revision: `{env.get('sha', '?')}`. Seeds: `{seeds}`. Optimization levels: `{levels}`.",
        f"Run: {env.get('timestamp', '?')}. Qiskit {env.get('qiskit_version', '?')}; "
        f"Python {env.get('python', '?')}.",
        f"Qiskit source: `{env.get('qiskit_source', '?')}`.",
        f"Loaded extension: `{env.get('accelerate_file', '?')}`.",
        f"Loaded extension SHA-256: `{env.get('accelerate_sha256', '?')}`.", "",
        "Comparison: absorption enabled vs disabled in the same Qiskit checkout. "
        "Both runs use copies of the same prepared input and identical transpiler settings. "
        "The disabled run temporarily replaces the absorption pass with a no-op; "
        "all other passes still execute.", "",
        f"{len(fixtures)} circuits, 10 qubits each; full preset pipeline, bidirectional line, trivial initial "
        "layout, SABRE routing, and the `rz`, `sx`, `x`, `cx` basis. Exact synthesis; arbitrary "
        "input states (`qubits_initially_zero=False`).",
        "Both outputs must match the same original unitary (layout-aware, global phase kept, default "
        "tolerances), preserve width and layout metadata, and satisfy connectivity and basis "
        "constraints. Every enabled run must select absorption moves and produce a structurally "
        "different circuit from the run with absorption disabled. "
        "DAG comparison ignores circuit names and the ordering of independent operations. "
        "Phase-only and layout-only changes do not count as changes to gates. "
        "Every requested circuit/seed/level combination must have a passing result.", "",
        "Per cell: passing cases; cases changed by enabling absorption, out of all requested seeds.", "",
        "| Circuit | " + " | ".join(f"Level {level}" for level in levels) + " |",
        "|---|" + "---|" * len(levels),
    ]
    for fixture in fixtures:
        cells = []
        for level in levels:
            selected = [r for r in cases if r.get("fixture") == fixture
                        and r.get("optimization_level") == level]
            passing = sum(map(case_passed, selected))
            changed = sum(r.get("absorption_changed") is True for r in selected)
            cells.append(f"{passing}/{len(seeds)} pass; {changed}/{len(seeds)} changed")
        lines.append(f"| {fixture} | " + " | ".join(cells) + " |")
    lines += ["", "Actual changes and moves (required in every case):", "",
              "| Level | Changed vs absorption disabled | Cases with moves "
              "| Moves | Pass executions | Coverage |",
              "|---|---|---|---|---|---|"]
    for level in levels:
        matches = [r for r in coverage if r.get("optimization_level") == level]
        rec = matches[0] if len(matches) == 1 else {}
        total = len(fixtures) * len(seeds)
        lines.append(f"| {level} | {rec.get('cases_absorption_changed', '?')}/{total} | "
                     f"{rec.get('cases_with_moves', '?')} | "
                     f"{rec.get('absorption_moves', '?')} | {rec.get('absorption_runs', '?')} | "
                     f"{'PASS' if passed(rec) else 'FAIL'} |")
    lines += ["", "Totals across all cases (absorption disabled → enabled):", "",
              "| Level | Two-qubit gates | Circuit depths (sum) |", "|---|---|---|"]
    for level in levels:
        selected = [r for r in cases if r.get("optimization_level") == level]
        totals = []
        for metric in ("two_qubit_gates", "depth"):
            values = [r.get(key) for r in selected for key in ("disabled_" + metric, metric)]
            if all(type(value) is int and value >= 0 for value in values):
                totals.append(f"{sum(values[::2])} → {sum(values[1::2])}")
            else:
                totals.append("incomplete")
        lines.append(f"| {level} | " + " | ".join(totals) + " |")
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
        lines += ["", "FAIL: missing, duplicate, or unexpected results/settings, missing Qiskit "
                  "provenance, or incomplete per-case change/move evidence."]
    for rec in cases:
        if passed(rec) and not case_passed(rec):
            lines += ["", f"FAIL: {rec.get('fixture')} seed {rec.get('seed')} "
                      f"level {rec.get('optimization_level')}: incomplete correctness or change evidence."]
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
