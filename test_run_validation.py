"""The runner must reject unreliable builds and ineffective comparison cases."""
import contextlib
import hashlib
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

import baseline_worker
from run_validation import (OperatorMismatch, check_changes, check_output, gate_structure,
                            main as validation_main, operator_key, run_baseline, source_revision)


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
        check_changes(after, before, before, metrics)
        self.assertIs(metrics["circuit_changed"], True)
        self.assertIs(metrics["absorption_changed"], True)

    def test_no_moves_fails_even_when_outputs_differ(self):
        metrics = dict(absorption_moves=0)
        with self.assertRaisesRegex(ValueError, "No absorption moves"):
            check_changes("after", "before", "disabled", metrics)
        self.assertIs(metrics["circuit_changed"], True)
        self.assertIs(metrics["absorption_changed"], True)

    def test_unchanged_baseline_fails_despite_moves(self):
        metrics = dict(absorption_moves=2)
        with self.assertRaisesRegex(ValueError, "unchanged from baseline"):
            check_changes("same", "same", "disabled", metrics)
        self.assertIs(metrics["circuit_changed"], False)

    def test_unrelated_baseline_difference_cannot_satisfy_absorption_check(self):
        metrics = dict(absorption_moves=2)
        with self.assertRaisesRegex(ValueError, "unchanged with absorption disabled"):
            check_changes("same", "older checkout", "same", metrics)
        self.assertIs(metrics["circuit_changed"], True)
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
            with self.assertRaisesRegex(ValueError, "unchanged from baseline"):
                check_changes(first, second, second, metrics)
        self.assertEqual(first.global_phase, 0.19)
        self.assertEqual(second.global_phase, 0.37)
        self.assertIs(metrics["circuit_changed"], False)
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


class BaselineSubprocessTests(unittest.TestCase):
    def test_fresh_interpreter_receives_identical_inputs_and_requested_matrix(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            inputs = {"fixture": dict(path=str(directory / "input.qpy"), sha256="a" * 64)}
            source = directory / "baseline checkout"
            response = dict(environment=dict(sha="before"), cases=[])

            def worker(command, *, check):
                self.assertTrue(check)
                self.assertEqual(command[0], sys.executable)
                self.assertEqual(Path(command[1]).name, "baseline_worker.py")
                self.assertEqual(command[command.index("--qiskit-src") + 1], str(source))
                request = Path(command[command.index("--request") + 1])
                self.assertEqual(json.loads(request.read_text()),
                                 dict(inputs=inputs, seeds=[7, 42], optimization_levels=[3]))
                Path(command[command.index("--out") + 1]).write_text(json.dumps(response))

            with patch("run_validation.subprocess.run", side_effect=worker) as launch:
                self.assertEqual(run_baseline(source, inputs, [7, 42], [3], directory), response)
            launch.assert_called_once()

    def test_failed_worker_does_not_reuse_a_previous_response(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "baseline.json").write_text('{"cases": []}')
            with patch("run_validation.subprocess.run",
                       side_effect=subprocess.CalledProcessError(1, ["worker"])):
                with self.assertRaises(subprocess.CalledProcessError):
                    run_baseline(directory / "baseline", {}, [0], [2], directory)

    def test_worker_rejects_tampered_input_for_every_case(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            circuit = directory / "input.qpy"
            circuit.write_bytes(b"changed contents")
            request, output = directory / "request.json", directory / "baseline.json"
            request.write_text(json.dumps(dict(
                inputs={"fixture": dict(path=str(circuit), sha256="a" * 64)},
                seeds=[0, 7], optimization_levels=[2, 3])))
            fake_qiskit, fake_transpiler = ModuleType("qiskit"), ModuleType("qiskit.transpiler")
            fake_qiskit.qpy = Mock()
            fake_transpiler.CouplingMap = Mock()
            argv = ["baseline_worker.py", "--qiskit-src", str(directory),
                    "--request", str(request), "--out", str(output)]
            with (patch.dict(sys.modules, {"qiskit": fake_qiskit,
                                           "qiskit.transpiler": fake_transpiler}),
                  patch("sys.argv", argv),
                  patch("baseline_worker.load_source", return_value=dict(sha="before")),
                  patch("baseline_worker.preset_manager") as transpile,
                  contextlib.redirect_stdout(io.StringIO())):
                baseline_worker.main()
            cases = json.loads(output.read_text())["cases"]
            self.assertEqual({(r["optimization_level"], r["seed"]) for r in cases},
                             {(2, 0), (2, 7), (3, 0), (3, 7)})
            for case in cases:
                self.assertIn("Input circuit changed in transit", case["error"])
                self.assertEqual(case["input_sha256"], hashlib.sha256(circuit.read_bytes()).hexdigest())
                self.assertNotIn("output", case)
            fake_qiskit.qpy.load.assert_not_called()
            transpile.assert_not_called()


class CaseFailureTests(unittest.TestCase):
    def test_baseline_and_candidate_errors_fail_required_cases(self):
        scenarios = (
            (OperatorMismatch("bad baseline unitary"), None, "Baseline operator mismatch"),
            (ValueError("bad baseline layout"), None, "bad baseline layout"),
            (None, OperatorMismatch("bad candidate unitary"), "bad candidate unitary"),
        )
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            circuit, output = directory / "circuit.qpy", directory / "results.jsonl"
            circuit.write_bytes(b"QPY decoding is mocked")
            inputs = {"fixture": dict(path=str(circuit), sha256="a" * 64)}
            baseline = dict(environment={}, cases=[dict(
                fixture="fixture", optimization_level=2, seed=0,
                input_sha256="a" * 64, output=str(circuit))])
            qiskit = ModuleType("qiskit")
            qiskit.qpy = SimpleNamespace(load=Mock(return_value=[object()]))
            quantum_info = ModuleType("qiskit.quantum_info")
            quantum_info.Operator = SimpleNamespace(from_circuit=Mock(return_value=object()))
            transpiler = ModuleType("qiskit.transpiler")
            transpiler.CouplingMap = SimpleNamespace(from_line=Mock(return_value=object()))
            presets = ModuleType("qiskit.transpiler.preset_passmanagers")
            presets.builtin_plugins = SimpleNamespace(_get_trial_count=lambda _: 20)
            presets._swap_absorption = Mock()
            modules = {module.__name__: module for module in (qiskit, quantum_info, transpiler, presets)}
            argv = ["run_validation.py", "--seeds", "0", "--optimization-levels", "2",
                    "--out", str(output)]
            for baseline_error, candidate_error, expected_error in scenarios:
                with self.subTest(baseline=baseline_error, candidate=candidate_error):
                    candidate = Mock(side_effect=candidate_error)
                    with (patch.dict(sys.modules, modules), patch("sys.argv", argv),
                          contextlib.redirect_stdout(io.StringIO()),
                          patch.multiple("run_validation",
                                         load_source=Mock(return_value={}),
                                         fixtures=Mock(return_value={"fixture": Mock()}),
                                         prepare_inputs=Mock(return_value=inputs),
                                         run_baseline=Mock(return_value=baseline),
                                         check_output=Mock(side_effect=baseline_error),
                                         transpile_and_check=candidate,
                                         self_checks=Mock(return_value=[]),
                                         feature_checks=Mock(return_value=[]))):
                        # A failed required case must fail both the run and coverage.
                        self.assertEqual(validation_main(), 1)
                    records = [json.loads(line) for line in output.read_text().splitlines()]
                    case, = [rec for rec in records if rec["record"] in ("case", "excluded_case")]
                    self.assertEqual(case["record"], "case")
                    self.assertIs(case["passed"], False)
                    self.assertEqual(candidate.call_count, int(baseline_error is None))
                    self.assertNotIn("reason", case)
                    self.assertIn(expected_error, case["error"])
                    coverage, = [rec for rec in records if rec["record"] == "coverage"]
                    self.assertIs(coverage["passed"], False)


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
