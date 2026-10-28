#!/usr/bin/env python
"""Validate SWAP absorption in Qiskit preset optimization levels 2 and 3."""
import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from swap_absorption_checks import feature_checks

os.environ["QISKIT_PARALLEL"] = "FALSE"
SEEDS = [0, 1, 42, 2, 7, 123]
LEVELS = [2, 3]
BASIS_GATES = ["rz", "sx", "x", "cx"]
CHECKS = ("ignoring_final_layout_must_fail", "extra_x_gate_must_fail")
# Uncommitted changes under these paths would make the recorded revision misdescribe the code
# under test, and the compiled extension must not predate any of these Rust inputs.
SOURCE_PATHS = ("qiskit", "crates", "test/benchmarks", "Cargo.toml", "Cargo.lock")
RUST_SOURCES = ("Cargo.toml", "Cargo.lock", "crates/**/Cargo.toml", "crates/**/*.rs")


def fixtures():
    """Return the 11 named test circuits as zero-argument builder callables.

    The builders are lazy so that ``main`` can build each circuit inside its own
    ``try`` block: a fixture that fails to build is recorded as failing for every
    seed instead of aborting the whole run. Every builder is deterministic (fixed
    seeds), and every circuit must end up with exactly 10 qubits.
    """
    # Imported here, not at module level, because these must come from the Qiskit
    # source tree that main() has just put at the front of sys.path.
    # Circuit classes used by the local phase builders and by the QFT fixture.
    from qiskit import QuantumCircuit, QuantumRegister
    from qiskit.synthesis import synth_mcx_noaux_hp24
    # Circuit builders from Qiskit's own benchmark suite (needs `ddt` installed).
    from test.benchmarks import qft, utils

    # Fixed wire permutations make these workloads exercise routing on a line.
    # They are part of the fixture, so every checkout receives the same circuit.
    spread = [4, 6, 2, 7, 3, 5, 9, 0, 8, 1]
    spread_bv = [8, 4, 7, 0, 1, 2, 5, 9, 6, 3]
    spread_chain = [2, 0, 7, 6, 9, 5, 3, 4, 8, 1]

    def on_wires(circuit, wires):
        """Embed the unmeasured circuit on a fixed ordering of ten wires."""
        output = QuantumCircuit(10)
        output.compose(circuit.remove_final_measurements(inplace=False), wires, inplace=True)
        return output

    def phase_ring():
        """A 20-layer ring with commuting CP/RZ layers and RX mixers."""
        qc = QuantumCircuit(10)
        qc.h(range(10))
        for layer in range(20):
            for i in range(5):
                first = (2 * i + 1 + layer % 2) % 10
                qc.cp(0.19 * (i + 1), first, (first + 1) % 10)
            for i in range(10):
                qc.rz(0.13 * (i + 1), i)
            if layer % 2:
                qc.rx(0.21, range(10))
        return on_wires(qc, spread)

    def phase_bvlike():
        """Compute parity, rotate it, then uncompute it on a permuted star."""
        qc = QuantumCircuit(10)
        for control in range(9):
            qc.cx(control, 9)
        # The benchmark's central X commutes through the whole oracle and leaves
        # a trivial circuit. RZ preserves a nontrivial parity-phase interaction.
        qc.rz(0.37, 9)
        qc.z(8)
        for control in reversed(range(9)):
            qc.cx(control, 9)
        return on_wires(qc, spread)

    # Fixture name -> builder. Insertion order is the order cases run and are reported.
    return {
        # Quantum Fourier transform on a 10-qubit register: H gates plus all-to-all
        # controlled-phase gates, which forces many SWAPs on a line.
        "qft10": lambda: qft.build_model_circuit(QuantumRegister(10)),
        # 4-bit ripple-carry adder: registers a(4) + b(4) + cin(1) + cout(1) = 10 qubits.
        # It contains Toffoli gates (expanded later) and final measurements (removed later).
        "adder4": lambda: on_wires(utils.build_ripple_adder_circuit(4), spread_chain),
        # Ring variant with commuting phases, rather than generic 1q rotations
        # that block every candidate move in the original benchmark.
        "ring10": phase_ring,
        # Floquet unitary for discrete-time-crystal (DTC) evolution, using the
        # builder's default parameters (g=0.95, seed=12345).
        "dtc10": lambda: on_wires(utils.dtc_unitary(10), spread_chain),
        # Bernstein-Vazirani for the all-ones secret: every data qubit has a CX
        # onto the last qubit (star-shaped interactions).
        "bv10": lambda: on_wires(utils.bv_all_ones(10), spread_bv),
        # BV-like parity-phase circuit with compute/uncompute CX stars.
        "bvlike10": phase_bvlike,
        # Trotterized evolution of a nearest-neighbour XX+YY+ZZ chain with a Z
        # field, two Trotter steps.
        "trotter10": lambda: on_wires(utils.trotter_circuit(10, reps=2), spread),
        # QAOA ansatz with seeded random ZZ weights, two repetitions. It has free
        # parameters, which main() binds to fixed values.
        "qaoa10": partial(utils.qaoa_circuit, 10, reps=2),
        # Grover search: 9 state qubits plus 1 target qubit.
        "grover10": partial(utils.grover_circuit, 10),
        # Multi-controlled X: 8 controls and 1 target, with a tenth idle wire.
        # HP24's no-ancilla decomposition exposes moves that KG24's does not.
        "mcx10": lambda: on_wires(synth_mcx_noaux_hp24(8), spread[:9]),
        # Modular adder on two 5-qubit registers (10 qubits in total).
        "modular_adder10": partial(utils.modular_adder_circuit, 10),
    }


def require(condition, message):
    if not condition:
        raise ValueError(message)


def preset_manager(qc, cmap, seed, level):
    """Use identical settings in the candidate, baseline, and disabled-pass control."""
    from qiskit.transpiler import generate_preset_pass_manager

    return generate_preset_pass_manager(
        optimization_level=level, coupling_map=cmap, basis_gates=BASIS_GATES,
        initial_layout=list(range(qc.num_qubits)), layout_method="trivial",
        routing_method="sabre", translation_method="translator",
        seed_transpiler=seed, approximation_degree=1.0, qubits_initially_zero=False,
    )


def transpile_and_check(qc, reference, cmap, seed, level, metrics, unitary_cache=None):
    """Run the complete preset pipeline and verify its full, layout-aware unitary.

    Observe the real move finder without changing its return value. A callback
    separately confirms that the pass ran, including invocations with no SWAPs.
    Metrics are filled in before the unitary comparison, so they remain available
    to the caller when that comparison fails.
    """
    from qiskit.transpiler.preset_passmanagers import _swap_absorption

    find_moves = _swap_absorption._find_moves

    def observe_moves(dag):
        result = find_moves(dag)
        metrics["absorption_moves"] += len(result[2])
        return result

    def callback(pass_, dag, **_):
        if isinstance(pass_, _swap_absorption._AbsorbIntoSwaps):
            metrics["absorption_runs"] += 1
            metrics["absorption_swaps_seen"] += dag.count_ops().get("swap", 0)

    pm = preset_manager(qc, cmap, seed, level)
    with patch.object(_swap_absorption, "_find_moves", side_effect=observe_moves):
        out = pm.run(qc.copy(), callback=callback)

    require(metrics["absorption_runs"] > 0, "SWAP absorption pass did not execute")
    check_output(out, qc, reference, cmap, metrics, unitary_cache)
    return out


class OperatorMismatch(ValueError):
    """A structurally valid output fails the phase-sensitive unitary comparison."""


def operator_key(out):
    """Exact operator inputs for standard basis gates; no numerical rounding.

    Repeated seeds/levels often produce identical outputs. A cache scoped to one
    input fixture can reuse their comparison result without rebuilding a 1024x1024
    matrix. Use the same initial/final permutations as Operator.from_circuit.
    Custom instructions are deliberately not cached.
    """
    instructions = []
    for inst in out.data:
        if inst.operation.name == "barrier":
            continue
        standard = getattr(inst.operation, "_standard_gate", None)
        if standard is None or inst.operation.name not in BASIS_GATES:
            return None
        instructions.append((standard, tuple(out.find_bit(q).index for q in inst.qubits),
                             tuple(float(p) for p in inst.operation.params)))
    layout = out.layout
    inputs = sorted(layout.input_qubit_mapping, key=layout.input_qubit_mapping.get)
    initial = tuple(layout.initial_layout.to_permutation(inputs))
    final = (None if layout.final_layout is None
             else tuple(layout.final_layout.to_permutation(out.qubits)))
    return out.num_qubits, float(out.global_phase), initial, final, tuple(instructions)


def check_output(out, qc, reference, cmap, metrics, unitary_cache=None):
    """Apply the same correctness checks to candidate and baseline outputs."""
    from qiskit.quantum_info import Operator

    require(out.num_qubits == qc.num_qubits == 10, "Output width changed")
    require(out.layout is not None and out.layout.initial_layout is not None,
            "Missing layout metadata")
    # Routing may legitimately be skipped when a circuit already fits the line;
    # in that case final_layout=None represents the identity permutation.
    require(out.layout.initial_index_layout() == list(range(10)), "Initial layout changed")
    require(sorted(out.layout.final_index_layout()) == list(range(10)),
            "Invalid final layout permutation")
    edges = set(map(tuple, cmap.get_edges()))
    for inst in out.data:
        if inst.operation._directive:
            continue
        qubits = tuple(out.find_bit(q).index for q in inst.qubits)
        require(len(qubits) <= 2 and (len(qubits) != 2 or qubits in edges),
                f"Unrouted gate: {inst.operation.name}{qubits}")
        require(inst.operation.name in BASIS_GATES,
                f"Gate outside requested basis: {inst.operation.name}")
    metrics.update(final_permutation=out.layout.final_index_layout(),
                   depth=out.depth(), two_qubit_gates=sum(
        len(inst.qubits) == 2 and not inst.operation._directive for inst in out.data))
    # Operator.__eq__ compares the matrices elementwise (np.allclose, default tolerances),
    # so a global-phase difference fails. Operator.equiv would ignore it; do not use it.
    key = operator_key(out) if unitary_cache is not None else None
    if key is not None and key in unitary_cache:
        matches = unitary_cache[key]
    else:
        matches = Operator.from_circuit(out) == reference
        if key is not None:
            unitary_cache[key] = matches
    if not matches:
        raise OperatorMismatch("Operator mismatch")


def gate_structure(circuit):
    """Compare gate DAGs without letting phase roundoff count as changed gates."""
    from qiskit.converters import circuit_to_dag

    dag = circuit_to_dag(circuit, copy_operations=False)
    dag.global_phase = 0
    return dag


def check_changes(out, baseline, disabled, metrics):
    """Require structural changes, ignoring names and independent instruction ordering.

    DAG equality compares gates, parameters and wires. Phase and layout metadata
    alone are not evidence of changed gate structure. Phase is checked separately
    by the full operator comparison.
    The disabled-pass control excludes differences caused by unrelated baseline changes.
    """
    structure = gate_structure(out)
    metrics.update(circuit_changed=structure != gate_structure(baseline),
                   absorption_changed=structure != gate_structure(disabled))
    require(metrics["absorption_moves"] > 0,
            "No absorption moves; modify this fixture to exercise SWAP absorption")
    require(metrics["circuit_changed"],
            "Circuit unchanged from baseline; modify this fixture")
    require(metrics["absorption_changed"],
            "Circuit unchanged with absorption disabled; modify this fixture")


def self_checks(sample, level):
    """Confirm that ignoring layout and adding an X each break a passing result."""
    from qiskit.quantum_info import Operator

    results = []
    for name in CHECKS:
        rec = dict(record="self_check", name=name, optimization_level=level, passed=False)
        try:
            require(sample is not None, "No passing case with a changed final permutation")
            out, reference, fixture, seed = sample
            rec.update(fixture=fixture, seed=seed)
            wrong = out.copy()
            ignore_layout = name == CHECKS[0]
            if not ignore_layout:
                wrong.x(0)
            require(Operator.from_circuit(wrong, ignore_set_layout=ignore_layout) != reference,
                    "Deliberate error was not detected")
            rec["passed"] = True
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException as exc:  # Includes Python-visible Rust panics.
            rec["error"] = repr(exc)
        results.append(rec)
    return results


def source_revision(source, extension):
    """Return the revision of ``source``, refusing trees that revision would not describe.

    Without a ``REVISION`` file the tree must be a git checkout with no uncommitted changes,
    including untracked files, under ``SOURCE_PATHS``. In every case ``extension`` (the
    compiled ``qiskit._accelerate`` module) must not be older than any Rust input matched by
    ``RUST_SOURCES``; rebuild it with ``python setup.py build_rust --inplace --release``.
    """
    revision = source / "REVISION"
    if revision.exists():
        sha = revision.read_text().strip()
    else:
        git = ["git", "-C", str(source)]
        sha = subprocess.check_output([*git, "rev-parse", "HEAD"], text=True).strip()
        changes = subprocess.check_output(
            [*git, "status", "--porcelain", "--untracked-files=all", "--", *SOURCE_PATHS],
            text=True)
        require(not changes, f"Uncommitted changes in the Qiskit source tree:\n{changes}")
    rust_sources = [path for pattern in RUST_SOURCES for path in source.glob(pattern)]
    if rust_sources:
        newest = max(rust_sources, key=lambda path: path.stat().st_mtime)
        require(extension.stat().st_mtime >= newest.stat().st_mtime,
                f"Compiled extension is older than {newest.relative_to(source)}; rebuild it")
    return sha


def load_source(source):
    """Load exactly one checkout per process and record its Python/Rust provenance."""
    require((source / "qiskit").is_dir(), f"Qiskit source directory not found: {source}")
    sys.path.insert(0, str(source))
    import qiskit
    import qiskit._accelerate

    for module in (qiskit, qiskit._accelerate):
        require(Path(module.__file__).resolve().is_relative_to(source), "Wrong Qiskit source")
    extension = Path(qiskit._accelerate.__file__)
    return dict(sha=source_revision(source, extension), qiskit_version=qiskit.__version__,
                qiskit_source=str(source), accelerate_file=str(extension),
                accelerate_sha256=hashlib.sha256(extension.read_bytes()).hexdigest())


def prepare_inputs(builders, directory):
    """Serialize each prepared input once, so both checkouts receive identical circuits."""
    from qiskit import qpy
    from qiskit.transpiler import PassManager
    from qiskit.transpiler.passes import Unroll3qOrMore

    inputs = {}
    for name, builder in builders.items():
        print(f"Preparing {name}", flush=True)
        item = {}
        try:
            qc = builder().remove_final_measurements(inplace=False)
            qc = qc.assign_parameters([0.1 * (i + 1) for i in range(qc.num_parameters)])
            qc = PassManager([Unroll3qOrMore()]).run(qc)
            require(qc.num_qubits == 10 and all(len(i.qubits) <= 2 for i in qc.data),
                    "Expected 10 qubits and only one- or two-qubit instructions")
            path = directory / f"{name}.qpy"
            with path.open("wb") as stream:
                # Version 13 is supported by both checkouts; all fixtures are unitary.
                qpy.dump(qc, stream, version=13)
            item.update(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException as exc:
            item["error"] = f"Preparation failed: {exc!r}"
        inputs[name] = item
    return inputs


def run_baseline(source, inputs, seeds, levels, directory):
    """Use a fresh interpreter: Qiskit's Rust modules cannot be switched in-process."""
    request = directory / "request.json"
    response = directory / "baseline.json"
    request.write_text(json.dumps(dict(inputs=inputs, seeds=seeds, optimization_levels=levels)))
    subprocess.run([sys.executable, str(Path(__file__).with_name("baseline_worker.py")),
                    "--qiskit-src", str(source), "--request", str(request),
                    "--out", str(response)], check=True)
    return json.loads(response.read_text())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qiskit-src", type=Path,
                        default=Path(__file__).resolve().parent.parent / "qiskit-swap-absorption")
    parser.add_argument("--baseline-src", type=Path,
                        default=Path(__file__).resolve().parent.parent / "qiskit-swap-absorption-before",
                        help="Built Qiskit checkout without SWAP absorption")
    parser.add_argument("--seeds", type=int, nargs="+", default=SEEDS)
    parser.add_argument("--optimization-levels", type=int, nargs="+", choices=LEVELS, default=LEVELS)
    parser.add_argument("--out", type=Path, default=Path("results/results.jsonl"))
    args = parser.parse_args()
    if len(set(args.seeds)) != len(args.seeds) or any(s < 0 or s >= 2**64 for s in args.seeds):
        parser.error("Seeds must be distinct unsigned 64-bit integers")
    if len(set(args.optimization_levels)) != len(args.optimization_levels):
        parser.error("Optimization levels must be distinct")
    source = args.qiskit_src.resolve()
    baseline_source = args.baseline_src.resolve()
    require(source != baseline_source, "Candidate and baseline must be different checkouts")
    provenance = load_source(source)
    from qiskit import qpy
    from qiskit.quantum_info import Operator
    from qiskit.transpiler import CouplingMap
    from qiskit.transpiler.preset_passmanagers import builtin_plugins, _swap_absorption

    builders = fixtures()
    records = [dict(
        record="environment", schema_version=3, **provenance, comparison_method="dag",
        python=sys.version.split()[0], timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        fixtures=list(builders), seeds=args.seeds, optimization_levels=args.optimization_levels,
        basis_gates=BASIS_GATES, coupling_map="line(10)", layout_method="trivial",
        routing_method="sabre", translation_method="translator", approximation_degree=1.0,
        qubits_initially_zero=False, routing_trials=builtin_plugins._get_trial_count(20),
    )]
    cmap = CouplingMap.from_line(10)
    samples = dict.fromkeys(args.optimization_levels)
    with TemporaryDirectory(prefix="swap-absorption-") as temporary:
        directory = Path(temporary)
        inputs = prepare_inputs(builders, directory)
        baseline = run_baseline(baseline_source, inputs, args.seeds, args.optimization_levels, directory)
        records[0]["baseline"] = baseline["environment"]
        baseline_cases = {(r["fixture"], r["optimization_level"], r["seed"]): r
                          for r in baseline["cases"]}
        for name, item in inputs.items():
            # Never reuse an operator comparison across different reference inputs.
            unitary_cache = {}
            error = item.get("error")
            print(f"Checking {name}", flush=True)
            try:
                require(error is None, error)
                with Path(item["path"]).open("rb") as stream:
                    qc, = qpy.load(stream)
                reference = Operator.from_circuit(qc)
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException as exc:
                error = f"Preparation failed: {exc!r}"
            for level in args.optimization_levels:
                for seed in args.seeds:
                    print(f"  level {level}, seed {seed}: ", end="", flush=True)
                    rec = dict(record="case", fixture=name, seed=seed, optimization_level=level,
                               passed=False, absorption_runs=0, absorption_moves=0,
                               absorption_swaps_seen=0, baseline_passed=False,
                               circuit_changed=False, absorption_changed=False,
                               input_sha256=item.get("sha256"))
                    try:
                        require(error is None, error)
                        before = baseline_cases[name, level, seed]
                        rec["baseline_input_sha256"] = before.get("input_sha256")
                        require(before.get("error") is None, f"Baseline failed: {before.get('error')}")
                        require(rec["input_sha256"] == rec["baseline_input_sha256"],
                                "Baseline used a different input circuit")
                        with Path(before["output"]).open("rb") as stream:
                            baseline_out, = qpy.load(stream)
                        baseline_metrics = {}
                        try:
                            check_output(baseline_out, qc, reference, cmap, baseline_metrics,
                                         unitary_cache)
                        except OperatorMismatch as exc:
                            raise OperatorMismatch("Baseline operator mismatch") from exc
                        finally:
                            rec.update({f"baseline_{key}": value
                                        for key, value in baseline_metrics.items()})
                        rec["baseline_passed"] = True
                        out = transpile_and_check(qc, reference, cmap, seed, level, rec, unitary_cache)
                        with patch.object(_swap_absorption._AbsorbIntoSwaps, "run",
                                          lambda self, dag: dag):
                            disabled = preset_manager(qc, cmap, seed, level).run(qc.copy())
                        check_changes(out, baseline_out, disabled, rec)
                        rec["passed"] = True
                        if samples[level] is None and rec["final_permutation"] != list(range(10)):
                            samples[level] = (out, reference, name, seed)
                    except (KeyboardInterrupt, SystemExit):
                        raise
                    except BaseException as exc:
                        rec["error"] = repr(exc)
                    records.append(rec)
                    print(f"PASS ({rec['absorption_moves']} moves, changed from baseline and disabled control)"
                          if rec["passed"] else f"FAIL {rec['error']}", flush=True)

    for level in args.optimization_levels:
        records += self_checks(samples[level], level)
        cases = [r for r in records if r["record"] == "case" and r["optimization_level"] == level]
        moves = sum(r["absorption_moves"] for r in cases)
        changed = sum(r["circuit_changed"] for r in cases)
        absorption_changed = sum(r["absorption_changed"] for r in cases)
        coverage = dict(record="coverage", optimization_level=level,
                        passed=bool(cases) and all(r["absorption_moves"] > 0 for r in cases)
                        and changed == absorption_changed == len(cases),
                        absorption_moves=moves, absorption_runs=sum(r["absorption_runs"] for r in cases),
                        cases_with_moves=sum(r["absorption_moves"] > 0 for r in cases),
                        cases_changed=changed, cases_absorption_changed=absorption_changed)
        if not coverage["passed"]:
            coverage["error"] = "Every case must make absorption moves and change both comparisons"
        records.append(coverage)
    records += feature_checks()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    failed = sum(not r["passed"] for r in records[1:])
    print(f"{failed} failures; wrote {args.out}")
    return int(failed != 0)


if __name__ == "__main__":
    sys.exit(main())
