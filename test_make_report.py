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
    env = dict(record="environment", schema_version=2, fixtures=[f"fixture{i}" for i in range(16)],
               seeds=[0], optimization_levels=[2, 3], basis_gates=["rz", "sx", "x", "cx"],
               coupling_map="line(10)", layout_method="trivial", routing_method="sabre",
               translation_method="translator", approximation_degree=1.0,
               qubits_initially_zero=False, sha="test", timestamp="test", qiskit_version="test",
               python="test", accelerate_sha256="test")
    records = [env]
    for level in env["optimization_levels"]:
        for fixture in env["fixtures"]:
            records.append(dict(record="case", fixture=fixture, seed=0, optimization_level=level,
                                passed=True, absorption_runs=1, absorption_moves=1,
                                absorption_swaps_seen=1, depth=10, two_qubit_gates=5,
                                final_permutation=[1, 0, *range(2, 10)]))
        records.extend(dict(record="self_check", name=name, optimization_level=level,
                            fixture="fixture0", seed=0, passed=True) for name in CHECKS)
        records.append(dict(record="coverage", optimization_level=level, passed=True,
                            absorption_runs=16, absorption_moves=16, cases_with_moves=16))
    records.extend(dict(record="feature_check", name=name, passed=True) for name in FEATURE_CHECKS)
    return records


class ReportTests(unittest.TestCase):
    def test_complete_matrix_passes(self):
        ok, report = build_report(complete_records(), "sample.jsonl")
        self.assertTrue(ok)
        self.assertIn("32/32 cases", report)
        self.assertIn("Level 2 | Level 3", report)

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

    def test_failure_or_changed_settings_fail(self):
        for key, value in (("translation_method", "synthesis"), ("qubits_initially_zero", True)):
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
            self.assertIn("schema version 2", output.read_text())


if __name__ == "__main__":
    unittest.main()
