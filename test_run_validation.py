"""The runner must reject unreliable builds and ineffective comparison cases."""
import contextlib
import io
import json
import math
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

from run_validation import (OperatorMismatch, check_changes, check_output, gate_structure,
                            main as validation_main, operator_key, source_revision,
                            transpile_disabled_and_check)


class CircuitChangeTests(unittest.TestCase):
    def setUp(self):
        # These tests exercise the comparison policy without importing Qiskit.
        structure = patch("run_validation.gate_structure", side_effect=lambda circuit: circuit)
        structure.start()
        self.addCleanup(structure.stop)

    def test_changed_structure_does_not_require_better_gate_counts(self):
        # Equal-length gate sequences can have different gates or wire assignments.
        before = (("cx", (0, 1)), ("cx", (1, 2)))
        after = (("cx", (1, 2)), ("cx", (0, 1)))
        metrics = dict(absorption_moves=1)
        check_changes(after, before, metrics)
        self.assertIs(metrics["absorption_changed"], True)

    def test_no_moves_fails_even_when_outputs_differ(self):
        metrics = dict(absorption_moves=0)
        with self.assertRaisesRegex(ValueError, "No absorption moves"):
            check_changes("after", "disabled", metrics)
        self.assertIs(metrics["absorption_changed"], True)

    def test_unchanged_output_fails_despite_moves(self):
        metrics = dict(absorption_moves=2)
        with self.assertRaisesRegex(ValueError, "unchanged with absorption disabled"):
            check_changes("same", "same", metrics)
        self.assertIs(metrics["absorption_changed"], False)


class GateStructureTests(unittest.TestCase):
    def test_phase_only_changes_are_ignored_without_mutating_input_circuits(self):
        first = SimpleNamespace(global_phase=0.19, gates=(("cx", (0, 1)),))
        second = SimpleNamespace(global_phase=0.37, gates=first.gates)
        converters = ModuleType("qiskit.converters")
        converters.circuit_to_dag = Mock(
            side_effect=lambda circuit, **_: SimpleNamespace(**vars(circuit)))
        with patch.dict(sys.modules, {"qiskit.converters": converters}):
            first_structure, second_structure = gate_structure(first), gate_structure(second)
            self.assertEqual(first_structure, second_structure)
            self.assertEqual(first_structure.global_phase, 0)
            metrics = dict(absorption_moves=1)
            with self.assertRaisesRegex(ValueError, "unchanged with absorption disabled"):
                check_changes(first, second, metrics)
        self.assertEqual(first.global_phase, 0.19)
        self.assertEqual(second.global_phase, 0.37)
        self.assertIs(metrics["absorption_changed"], False)
        for call in converters.circuit_to_dag.call_args_list:
            self.assertEqual(call.kwargs, dict(copy_operations=False))


def fake_basis_output():
    """Minimal circuit with real cache inputs, independent of an installed Qiskit."""
    qubits = list(range(10))
    layout = SimpleNamespace(
        # Reverse insertion order to ensure the key follows the mapping's values.
        input_qubit_mapping={q: q for q in reversed(qubits)},
        initial_layout=SimpleNamespace(to_permutation=Mock(return_value=qubits.copy())),
        final_layout=SimpleNamespace(to_permutation=Mock(return_value=qubits.copy())),
        initial_index_layout=lambda: qubits.copy(), final_index_layout=lambda: qubits.copy())
    rz = SimpleNamespace(operation=SimpleNamespace(name="rz", _standard_gate="RZ",
                                                  params=[0.123], _directive=False), qubits=(0,))
    cx = SimpleNamespace(operation=SimpleNamespace(name="cx", _standard_gate="CX",
                                                  params=[], _directive=False), qubits=(0, 1))
    return SimpleNamespace(num_qubits=10, global_phase=0.19, qubits=qubits,
                           data=[rz, cx], layout=layout, depth=lambda: 2,
                           find_bit=lambda qubit: SimpleNamespace(index=qubit))


class OperatorCacheTests(unittest.TestCase):
    def test_phase_parameters_wires_order_and_layout_are_exact_key_inputs(self):
        original = fake_basis_output()
        key = operator_key(original)
        original.layout.initial_layout.to_permutation.assert_called_once_with(original.qubits)
        original.layout.final_layout.to_permutation.assert_called_once_with(original.qubits)
        variants = []
        phase = fake_basis_output()
        phase.global_phase = math.nextafter(phase.global_phase, math.inf)
        variants.append(("phase", phase))
        parameter = fake_basis_output()
        parameter.data[0].operation.params[0] = math.nextafter(0.123, math.inf)
        variants.append(("parameter", parameter))
        wire = fake_basis_output()
        wire.data[0].qubits = (1,)
        variants.append(("wire", wire))
        order = fake_basis_output()
        order.data.reverse()
        variants.append(("instruction order", order))
        for kind in ("initial_layout", "final_layout"):
            output = fake_basis_output()
            getattr(output.layout, kind).to_permutation.return_value = [1, 0, *range(2, 10)]
            variants.append((kind, output))
        for description, variant in variants:
            with self.subTest(description=description):
                self.assertNotEqual(operator_key(variant), key)

    def test_cached_mismatch_still_raises_on_every_check(self):
        output, reference, cache = fake_basis_output(), object(), {}
        quantum_info = ModuleType("qiskit.quantum_info")
        quantum_info.Operator = SimpleNamespace(from_circuit=Mock(return_value=object()))
        cmap = SimpleNamespace(get_edges=lambda: [(0, 1)])
        with patch.dict(sys.modules, {"qiskit.quantum_info": quantum_info}):
            for _ in range(2):
                metrics = {}
                with self.assertRaises(OperatorMismatch):
                    check_output(output, output, reference, cmap, metrics, cache)
                self.assertEqual(metrics["two_qubit_gates"], 1)
        quantum_info.Operator.from_circuit.assert_called_once_with(output)
        self.assertIs(cache[operator_key(output)], False)

    def test_cached_success_does_not_bypass_connectivity_validation(self):
        output, reference, cache = fake_basis_output(), object(), {}
        quantum_info = ModuleType("qiskit.quantum_info")
        quantum_info.Operator = SimpleNamespace(from_circuit=Mock(return_value=reference))
        with patch.dict(sys.modules, {"qiskit.quantum_info": quantum_info}):
            cmap = SimpleNamespace(get_edges=lambda: [(0, 1)])
            check_output(output, output, reference, cmap, {}, cache)
            check_output(output, output, reference, cmap, {}, cache)
            disconnected = SimpleNamespace(get_edges=lambda: [])
            with self.assertRaisesRegex(ValueError, "Unrouted gate") as error:
                check_output(output, output, reference, disconnected, {}, cache)
            self.assertNotIsInstance(error.exception, OperatorMismatch)
        quantum_info.Operator.from_circuit.assert_called_once_with(output)

    def test_custom_gate_named_like_basis_gate_is_not_cached(self):
        output, reference, cache = fake_basis_output(), object(), {}
        output.data[0].operation._standard_gate = None
        self.assertIsNone(operator_key(output))
        quantum_info = ModuleType("qiskit.quantum_info")
        quantum_info.Operator = SimpleNamespace(from_circuit=Mock(return_value=reference))
        cmap = SimpleNamespace(get_edges=lambda: [(0, 1)])
        with patch.dict(sys.modules, {"qiskit.quantum_info": quantum_info}):
            check_output(output, output, reference, cmap, {}, cache)
            check_output(output, output, reference, cmap, {}, cache)
        self.assertEqual(quantum_info.Operator.from_circuit.call_count, 2)
        self.assertEqual(cache, {})


class DisabledControlTests(unittest.TestCase):
    def test_only_absorption_is_bypassed_and_patch_is_restored(self):
        for fail_pipeline in (False, True):
            with self.subTest(fail_pipeline=fail_pipeline):
                class AbsorptionPass:
                    def run(self, dag):
                        dag.append("absorption")
                        return dag

                original_run = AbsorptionPass.run
                presets = ModuleType("qiskit.transpiler.preset_passmanagers")
                presets._swap_absorption = SimpleNamespace(_AbsorbIntoSwaps=AbsorptionPass)
                source = ["input"]
                qc = SimpleNamespace(copy=lambda: source.copy())
                output = []

                def pipeline(dag):
                    dag = AbsorptionPass().run(dag)
                    dag.append("later pass")
                    output.extend(dag)
                    if fail_pipeline:
                        raise ValueError("later pass failed")
                    return dag

                manager = SimpleNamespace(run=Mock(side_effect=pipeline))
                reference, cmap, metrics, cache = object(), object(), {}, {}
                with (patch.dict(sys.modules, {presets.__name__: presets}),
                      patch("run_validation.preset_manager", return_value=manager) as preset,
                      patch("run_validation.check_output") as check):
                    if fail_pipeline:
                        with self.assertRaisesRegex(ValueError, "later pass failed"):
                            transpile_disabled_and_check(qc, reference, cmap, 7, 3, metrics, cache)
                        check.assert_not_called()
                    else:
                        result = transpile_disabled_and_check(qc, reference, cmap, 7, 3, metrics, cache)
                        self.assertEqual(result, ["input", "later pass"])
                        check.assert_called_once_with(result, qc, reference, cmap, metrics, cache)
                    preset.assert_called_once_with(qc, cmap, 7, 3)
                self.assertEqual(output, ["input", "later pass"])
                self.assertEqual(source, ["input"])
                self.assertIs(AbsorptionPass.run, original_run)
                self.assertEqual(AbsorptionPass().run([]), ["absorption"])

    def test_disabled_output_must_pass_phase_sensitive_correctness_check(self):
        output, reference = fake_basis_output(), object()
        qc = SimpleNamespace(num_qubits=10, copy=lambda: object())
        presets = ModuleType("qiskit.transpiler.preset_passmanagers")
        presets._swap_absorption = SimpleNamespace(_AbsorbIntoSwaps=type(
            "AbsorptionPass", (), {"run": lambda self, dag: dag}))
        original_run = presets._swap_absorption._AbsorbIntoSwaps.run
        quantum_info = ModuleType("qiskit.quantum_info")
        quantum_info.Operator = SimpleNamespace(from_circuit=Mock(return_value=object()))
        manager = SimpleNamespace(run=Mock(return_value=output))
        cmap, metrics = SimpleNamespace(get_edges=lambda: [(0, 1)]), {}
        with (patch.dict(sys.modules, {presets.__name__: presets, quantum_info.__name__: quantum_info}),
              patch("run_validation.preset_manager", return_value=manager)):
            with self.assertRaisesRegex(OperatorMismatch, "Disabled control operator mismatch"):
                transpile_disabled_and_check(qc, reference, cmap, 0, 2, metrics)
        quantum_info.Operator.from_circuit.assert_called_once_with(output)
        self.assertEqual(metrics["depth"], 2)
        self.assertEqual(metrics["two_qubit_gates"], 1)
        self.assertIs(presets._swap_absorption._AbsorbIntoSwaps.run, original_run)


class CaseValidationTests(unittest.TestCase):
    def run_cases(self, *, disabled_error=None, enabled_error=None, preparation_error=None):
        qc, reference, cmap, builder = object(), object(), object(), object()
        quantum_info = ModuleType("qiskit.quantum_info")
        quantum_info.Operator = SimpleNamespace(from_circuit=Mock(return_value=reference))
        transpiler = ModuleType("qiskit.transpiler")
        transpiler.CouplingMap = SimpleNamespace(from_line=Mock(return_value=cmap))
        presets = ModuleType("qiskit.transpiler.preset_passmanagers")
        presets.builtin_plugins = SimpleNamespace(_get_trial_count=lambda _: 20)
        modules = {module.__name__: module for module in (quantum_info, transpiler, presets)}

        def disabled_run(qc, reference, cmap, seed, level, metrics, cache):
            metrics.update(depth=12, two_qubit_gates=7, final_permutation=list(range(10)))
            if disabled_error is not None:
                raise disabled_error
            return "disabled"

        def enabled_run(qc, reference, cmap, seed, level, metrics, cache):
            metrics.update(depth=10, two_qubit_gates=5, final_permutation=[1, 0, *range(2, 10)],
                           absorption_runs=1, absorption_moves=2, absorption_swaps_seen=3)
            if enabled_error is not None:
                raise enabled_error
            return "enabled"

        disabled = Mock(side_effect=disabled_run)
        enabled = Mock(side_effect=enabled_run)
        prepare = Mock(return_value=qc, side_effect=preparation_error)
        load = Mock(return_value={})
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "results.jsonl"
            argv = ["run_validation.py", "--seeds", "0", "7", "--out", str(output)]
            with (patch.dict(sys.modules, modules), patch("sys.argv", argv),
                  contextlib.redirect_stdout(io.StringIO()),
                  patch.multiple("run_validation", load_source=load,
                                 fixtures=Mock(return_value={"fixture": builder}),
                                 prepare_input=prepare, transpile_disabled_and_check=disabled,
                                 transpile_and_check=enabled,
                                 gate_structure=Mock(side_effect=lambda circuit: circuit),
                                 self_checks=Mock(return_value=[]), feature_checks=Mock(return_value=[]))):
                status = validation_main()
            records = [json.loads(line) for line in output.read_text().splitlines()]
        load.assert_called_once()
        prepare.assert_called_once_with(builder)
        return status, records, disabled, enabled, qc, reference, cmap

    def test_same_input_and_settings_are_used_for_both_modes_without_baseline(self):
        status, records, disabled, enabled, qc, reference, cmap = self.run_cases()
        self.assertEqual(status, 0)
        self.assertEqual(records[0]["schema_version"], 4)
        self.assertEqual(records[0]["comparison_mode"], "enabled_vs_disabled")
        self.assertNotIn("baseline", records[0])
        self.assertEqual(disabled.call_count, 4)
        self.assertEqual(enabled.call_count, 4)
        cases = [rec for rec in records if rec["record"] == "case"]
        self.assertEqual({(rec["optimization_level"], rec["seed"]) for rec in cases},
                         {(2, 0), (2, 7), (3, 0), (3, 7)})
        for disabled_call, enabled_call in zip(disabled.call_args_list, enabled.call_args_list):
            self.assertEqual(disabled_call.args[:5], enabled_call.args[:5])
            self.assertEqual(disabled_call.args[:3], (qc, reference, cmap))
            self.assertIs(disabled_call.args[6], enabled_call.args[6])
        for rec in cases:
            self.assertIs(rec["passed"], True)
            self.assertIs(rec["disabled_passed"], True)
            self.assertIs(rec["enabled_passed"], True)
            self.assertIs(rec["absorption_changed"], True)
            self.assertEqual(rec["disabled_depth"], 12)
            self.assertEqual(rec["disabled_two_qubit_gates"], 7)
            self.assertEqual(rec["depth"], 10)
            self.assertEqual(rec["two_qubit_gates"], 5)
            self.assertFalse(any(key.startswith("baseline") for key in rec))
        for rec in records:
            if rec["record"] == "coverage":
                self.assertIs(rec["passed"], True)
                self.assertEqual(rec["cases_absorption_changed"], 2)

    def test_disabled_and_enabled_errors_fail_every_required_case(self):
        scenarios = (
            (OperatorMismatch("Disabled control operator mismatch"), None),
            (ValueError("bad disabled layout"), None),
            (None, OperatorMismatch("bad enabled unitary")),
            (None, ValueError("bad enabled layout")),
        )
        for disabled_error, enabled_error in scenarios:
            with self.subTest(disabled=disabled_error, enabled=enabled_error):
                status, records, disabled, enabled, *_ = self.run_cases(
                    disabled_error=disabled_error, enabled_error=enabled_error)
                self.assertEqual(status, 1)
                self.assertEqual(disabled.call_count, 4)
                self.assertEqual(enabled.call_count, 4 if disabled_error is None else 0)
                cases = [rec for rec in records if rec["record"] == "case"]
                self.assertEqual(len(cases), 4)
                for rec in cases:
                    self.assertIs(rec["passed"], False)
                    self.assertIs(rec["disabled_passed"], disabled_error is None)
                    self.assertIs(rec["enabled_passed"], False)
                    self.assertEqual(rec["disabled_depth"], 12)
                    self.assertIn(str(disabled_error or enabled_error), rec["error"])
                    self.assertNotIn("reason", rec)
                for rec in records:
                    if rec["record"] == "coverage":
                        self.assertIs(rec["passed"], False)

    def test_preparation_error_is_recorded_for_the_full_requested_matrix(self):
        status, records, disabled, enabled, *_ = self.run_cases(
            preparation_error=ValueError("fixture construction failed"))
        self.assertEqual(status, 1)
        disabled.assert_not_called()
        enabled.assert_not_called()
        cases = [rec for rec in records if rec["record"] == "case"]
        self.assertEqual({(rec["optimization_level"], rec["seed"]) for rec in cases},
                         {(2, 0), (2, 7), (3, 0), (3, 7)})
        for rec in cases:
            self.assertIs(rec["passed"], False)
            self.assertIs(rec["disabled_passed"], False)
            self.assertIs(rec["enabled_passed"], False)
            self.assertIn("fixture construction failed", rec["error"])
        self.assertTrue(all(rec["record"] != "excluded_case" for rec in records))


class ResultOutputTests(unittest.TestCase):
    def test_initialization_error_or_interruption_invalidates_previous_success(self):
        from make_report import build_report
        from test_make_report import complete_records

        previous = complete_records()
        self.assertTrue(build_report(previous, "previous.jsonl")[0])
        for error in (ValueError("Qiskit checkout unavailable"), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__), tempfile.TemporaryDirectory() as temporary:
                output = Path(temporary) / "results.jsonl"
                output.write_text("".join(json.dumps(record) + "\n" for record in previous))
                argv = ["run_validation.py", "--out", str(output)]
                with (patch("sys.argv", argv),
                      patch("run_validation.load_source", side_effect=error) as load):
                    with self.assertRaises(type(error)):
                        validation_main()
                load.assert_called_once()
                records = [json.loads(line) for line in output.read_text().splitlines()]
                self.assertEqual(len(records), 1)
                self.assertEqual(records[0]["record"], "run_status")
                self.assertIs(records[0]["passed"], False)
                self.assertIn("Validation did not complete", records[0]["error"])
                with self.assertRaises(ValueError):
                    build_report(records, str(output))

    def test_invalid_output_path_fails_before_loading_qiskit(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "results.jsonl"
            output.mkdir()
            with (patch("sys.argv", ["run_validation.py", "--out", str(output)]),
                  patch("run_validation.load_source") as load):
                with self.assertRaises(OSError):
                    validation_main()
            load.assert_not_called()


class SourceRevisionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.source = Path(directory.name).resolve()
        for relative in ("qiskit/__init__.py", "crates/lib/src/lib.rs", "Cargo.toml", "Cargo.lock"):
            self.write(relative, "")
        self.write(".gitignore", "*.so\n")
        self.git("init", "-q")
        self.git("add", ".")
        self.git("-c", "user.name=test", "-c", "user.email=test@example.com",
                 "-c", "commit.gpgsign=false", "commit", "-q", "-m", "init")
        self.sha = self.git("rev-parse", "HEAD")
        self.extension = self.source / "qiskit" / "_accelerate.so"
        self.extension.write_text("")
        self.set_mtime(self.extension, self.newest() + 1)

    def write(self, relative, text):
        path = self.source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.source), *args], text=True).strip()

    def newest(self):
        return max(path.stat().st_mtime for path in self.source.rglob("*"))

    @staticmethod
    def set_mtime(path, stamp):
        os.utime(path, (stamp, stamp))

    def test_clean_checkout_returns_head(self):
        self.assertEqual(source_revision(self.source, self.extension), self.sha)

    def test_changes_outside_the_sources_are_allowed(self):
        self.write("notes.md", "scratch")
        self.write(".gitignore", "*.so\n/design/\n")
        self.assertEqual(source_revision(self.source, self.extension), self.sha)

    def test_untracked_source_file_fails(self):
        self.write("qiskit/extra.py", "")
        with self.assertRaisesRegex(ValueError, r"(?s)Uncommitted changes.*qiskit/extra\.py"):
            source_revision(self.source, self.extension)

    def test_modified_source_file_fails(self):
        for relative in ("Cargo.lock", "crates/lib/src/lib.rs", "qiskit/__init__.py"):
            with self.subTest(path=relative):
                self.write(relative, "changed")
                with self.assertRaisesRegex(ValueError, "(?s)Uncommitted changes.*" + relative):
                    source_revision(self.source, self.extension)
                self.write(relative, "")

    def test_rust_source_newer_than_extension_fails(self):
        # A git pull that rewrites a Rust file leaves it newer than the compiled extension.
        rust = self.source / "crates" / "lib" / "src" / "lib.rs"
        self.set_mtime(rust, self.extension.stat().st_mtime + 10)
        with self.assertRaisesRegex(ValueError, "older than crates/lib/src/lib.rs"):
            source_revision(self.source, self.extension)

    def test_revision_file_replaces_git(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        source = Path(directory.name).resolve()
        (source / "crates").mkdir()
        (source / "crates" / "lib.rs").write_text("")
        (source / "REVISION").write_text("abc123\n")
        extension = source / "_accelerate.so"
        extension.write_text("")
        self.set_mtime(extension, (source / "crates" / "lib.rs").stat().st_mtime + 1)
        self.assertEqual(source_revision(source, extension), "abc123")
        self.set_mtime(extension, extension.stat().st_mtime - 3600)
        with self.assertRaisesRegex(ValueError, "older than crates/lib.rs"):
            source_revision(source, extension)


if __name__ == "__main__":
    unittest.main()
