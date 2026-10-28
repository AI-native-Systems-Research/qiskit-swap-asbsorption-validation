"""The reporter must reject incomplete or stale validation evidence."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from make_report import build_report, main
from run_validation import CHECKS
from swap_absorption_checks import FEATURE_CHECKS


def complete_records():
    env = dict(record="environment", schema_version=3, fixtures=[f"fixture{i}" for i in range(16)],
               seeds=[0], optimization_levels=[2, 3], basis_gates=["rz", "sx", "x", "cx"],
               coupling_map="line(10)", layout_method="trivial", routing_method="sabre",
               translation_method="translator", approximation_degree=1.0,
               qubits_initially_zero=False, sha="test", timestamp="test", qiskit_version="test",
               python="test", accelerate_sha256="a" * 64, comparison_method="dag",
               baseline=dict(sha="baseline-test", qiskit_version="test",
                             qiskit_source="/test/baseline", accelerate_file="/test/baseline/ext.so",
                             accelerate_sha256="b" * 64))
    records = [env]
    for level in env["optimization_levels"]:
        for fixture in env["fixtures"]:
            records.append(dict(record="case", fixture=fixture, seed=0, optimization_level=level,
                                passed=True, absorption_runs=1, absorption_moves=1,
                                absorption_swaps_seen=1, depth=10, two_qubit_gates=5,
                                final_permutation=[1, 0, *range(2, 10)], baseline_passed=True,
                                circuit_changed=True, absorption_changed=True,
                                input_sha256="c" * 64, baseline_input_sha256="c" * 64,
                                baseline_depth=12, baseline_two_qubit_gates=7,
                                baseline_final_permutation=[1, 0, *range(2, 10)]))
        records.extend(dict(record="self_check", name=name, optimization_level=level,
                            fixture="fixture0", seed=0, passed=True) for name in CHECKS)
        records.append(dict(record="coverage", optimization_level=level, passed=True,
                            absorption_runs=16, absorption_moves=16, cases_with_moves=16,
                            cases_changed=16, cases_absorption_changed=16))
    records.extend(dict(record="feature_check", name=name, passed=True) for name in FEATURE_CHECKS)
    return records


def exclude_case(records, index=1):
    case = records[index]
    excluded = {key: case[key] for key in (
        "fixture", "seed", "optimization_level", "input_sha256", "baseline_input_sha256",
        "baseline_depth", "baseline_two_qubit_gates", "baseline_final_permutation")}
    excluded.update(record="excluded_case", passed=False, baseline_passed=False,
                    reason="baseline_operator_mismatch", error="Baseline operator mismatch")
    records[index] = excluded
    coverage = next(r for r in records if r["record"] == "coverage"
                    and r["optimization_level"] == case["optimization_level"])
    for key in ("absorption_runs", "absorption_moves", "cases_with_moves", "cases_changed",
                "cases_absorption_changed"):
        coverage[key] -= 1
    return excluded


class ReportTests(unittest.TestCase):
    def test_complete_matrix_passes(self):
        ok, report = build_report(complete_records(), "sample.jsonl")
        self.assertTrue(ok)
        self.assertIn("32/32 cases", report)
        self.assertIn("Level 2 | Level 3", report)
        self.assertIn("1/1 pass; 1/1 changed", report)
        self.assertIn("112 → 80", report)
        self.assertIn("192 → 160", report)
        self.assertIn("Baseline revision: `baseline-test`", report)
        self.assertIn("Baseline source: `/test/baseline`", report)
        self.assertIn("0 excluded baseline cases", report)

    def test_evidenced_baseline_exclusion_is_removed_from_retained_matrix(self):
        records = complete_records()
        exclude_case(records)
        ok, report = build_report(records, "sample.jsonl")
        self.assertTrue(ok)
        self.assertIn("31/31 cases; 1 excluded baseline cases", report)
        self.assertIn("0/0 pass; 0/0 changed; 1 excluded", report)
        self.assertIn("| fixture0 | 2 | `[0]` | Baseline operator mismatch |", report)
        self.assertIn("| 2 | 15/15 | 15/15 |", report)
        self.assertIn("105 → 75", report)

    def test_exclusion_requires_only_baseline_operator_mismatch(self):
        for key, value in (("reason", "operator_mismatch"), ("reason", "no_absorption_moves"),
                           ("error", "Baseline routing failure"), ("passed", True),
                           ("baseline_passed", True), ("absorption_runs", 1),
                           ("absorption_moves", 1)):
            with self.subTest(key=key, value=value):
                records = complete_records()
                exclude_case(records)[key] = value
                self.assertFalse(build_report(records, "sample.jsonl")[0])

    def test_exclusion_requires_complete_baseline_evidence(self):
        for key in ("reason", "error", "passed", "baseline_passed", "input_sha256",
                    "baseline_input_sha256", "baseline_depth", "baseline_two_qubit_gates",
                    "baseline_final_permutation"):
            with self.subTest(key=key):
                records = complete_records()
                del exclude_case(records)[key]
                self.assertFalse(build_report(records, "sample.jsonl")[0])
        for key, value in (("baseline_input_sha256", "d" * 64), ("input_sha256", "invalid"),
                           ("baseline_depth", -1), ("baseline_two_qubit_gates", "7"),
                           ("baseline_final_permutation", [0] * 10)):
            with self.subTest(key=key, value=value):
                records = complete_records()
                exclude_case(records)[key] = value
                self.assertFalse(build_report(records, "sample.jsonl")[0])

    def test_missing_duplicate_or_overlapping_exclusions_fail(self):
        for kind in ("missing", "duplicate", "overlap", "unexpected"):
            with self.subTest(kind=kind):
                records = complete_records()
                original = copy.deepcopy(records[1])
                excluded = exclude_case(records)
                if kind == "missing":
                    records.remove(excluded)
                elif kind == "duplicate":
                    records.append(copy.deepcopy(excluded))
                elif kind == "overlap":
                    records.append(original)
                else:
                    excluded["seed"] = 123
                self.assertFalse(build_report(records, "sample.jsonl")[0])

    def test_each_level_must_retain_at_least_one_case(self):
        records = complete_records()
        for index in range(1, 17):
            exclude_case(records, index)
        self.assertFalse(build_report(records, "sample.jsonl")[0])

    def test_requested_single_level_passes(self):
        records = complete_records()
        records[0]["optimization_levels"] = [3]
        records = [r for r in records if r.get("optimization_level") != 2]
        self.assertTrue(build_report(records, "sample.jsonl")[0])

    def test_missing_or_duplicate_evidence_fails(self):
        for kind in ("case", "self_check", "feature_check", "coverage"):
            for duplicate in (False, True):
                with self.subTest(kind=kind, duplicate=duplicate):
                    records = complete_records()
                    index = next(i for i, r in enumerate(records) if r["record"] == kind)
                    if duplicate:
                        records.append(copy.deepcopy(records[index]))
                    else:
                        records.pop(index)
                    self.assertFalse(build_report(records, "sample.jsonl")[0])

    def test_missing_entire_level_fails(self):
        records = [r for r in complete_records() if r.get("optimization_level") != 3]
        self.assertFalse(build_report(records, "sample.jsonl")[0])

    def test_false_coverage_fails(self):
        records = complete_records()
        for rec in records:
            if rec["record"] == "case":
                rec["absorption_moves"] = 0
        self.assertFalse(build_report(records, "sample.jsonl")[0])

    def test_unexecuted_pass_fails(self):
        records = complete_records()
        records[1]["absorption_runs"] = 0
        self.assertFalse(build_report(records, "sample.jsonl")[0])

    def test_each_case_must_change_and_pass_baseline(self):
        for key in ("circuit_changed", "absorption_changed", "baseline_passed"):
            for value in (False, None, 1, "true"):
                with self.subTest(key=key, value=value):
                    records = complete_records()
                    records[1][key] = value
                    ok, report = build_report(records, "sample.jsonl")
                    self.assertFalse(ok)
                    self.assertIn("31/32 cases", report)

    def test_zero_move_case_fails_even_with_consistent_coverage(self):
        records = complete_records()
        records[1]["absorption_moves"] = 0
        coverage = next(r for r in records if r["record"] == "coverage")
        coverage.update(absorption_moves=15, cases_with_moves=15)
        self.assertFalse(build_report(records, "sample.jsonl")[0])

    def test_missing_case_comparison_evidence_fails(self):
        for key in ("baseline_passed", "circuit_changed", "absorption_changed", "input_sha256",
                    "baseline_input_sha256", "baseline_depth", "baseline_two_qubit_gates",
                    "baseline_final_permutation"):
            with self.subTest(key=key):
                records = complete_records()
                del records[1][key]
                self.assertFalse(build_report(records, "sample.jsonl")[0])

    def test_input_mismatch_or_invalid_digest_fails(self):
        for value in ("d" * 64, "invalid", None, 3):
            with self.subTest(value=value):
                records = complete_records()
                records[1]["baseline_input_sha256"] = value
                self.assertFalse(build_report(records, "sample.jsonl")[0])
        records = complete_records()
        records[1].update(input_sha256="invalid", baseline_input_sha256="invalid")
        self.assertFalse(build_report(records, "sample.jsonl")[0])

    def test_invalid_baseline_metrics_or_layout_fail(self):
        for key, value in (("baseline_depth", -1), ("baseline_two_qubit_gates", "7"),
                           ("baseline_final_permutation", [0] * 10),
                           ("baseline_final_permutation", [False, *range(1, 10)])):
            with self.subTest(key=key, value=value):
                records = complete_records()
                records[1][key] = value
                self.assertFalse(build_report(records, "sample.jsonl")[0])

    def test_missing_baseline_provenance_fails(self):
        for key in ("sha", "qiskit_version", "qiskit_source", "accelerate_file", "accelerate_sha256"):
            with self.subTest(key=key):
                records = complete_records()
                del records[0]["baseline"][key]
                self.assertFalse(build_report(records, "sample.jsonl")[0])
        for value in ({}, None, "baseline"):
            with self.subTest(value=value):
                records = complete_records()
                records[0]["baseline"] = value
                self.assertFalse(build_report(records, "sample.jsonl")[0])

    def test_missing_or_false_change_coverage_fails(self):
        for key in ("cases_changed", "cases_absorption_changed"):
            for value in (None, 15, 17):
                with self.subTest(key=key, value=value):
                    records = complete_records()
                    next(r for r in records if r["record"] == "coverage")[key] = value
                    self.assertFalse(build_report(records, "sample.jsonl")[0])

    def test_failure_or_changed_settings_fail(self):
        for key, value in (("translation_method", "synthesis"), ("qubits_initially_zero", True),
                           ("comparison_method", "gate-count")):
            with self.subTest(key=key):
                records = complete_records()
                records[0][key] = value
                self.assertFalse(build_report(records, "sample.jsonl")[0])
        records = complete_records()
        records[1]["error"] = "unitary mismatch"
        self.assertFalse(build_report(records, "sample.jsonl")[0])

    def test_old_schema_writes_failure_without_traceback(self):
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / "old.jsonl", Path(directory) / "report.md"
            source.write_text(json.dumps(dict(record="environment", heuristic="wait_time")))
            with patch("sys.argv", ["make_report.py", "--results", str(source), "--out", str(output)]):
                self.assertEqual(main(), 1)
            self.assertIn("**FAIL**", output.read_text())
            self.assertIn("schema version 3", output.read_text())

    def test_schema_two_is_stale_without_comparison(self):
        records = complete_records()
        records[0]["schema_version"] = 2
        with self.assertRaisesRegex(ValueError, "schema version 3"):
            build_report(records, "sample.jsonl")


if __name__ == "__main__":
    unittest.main()
