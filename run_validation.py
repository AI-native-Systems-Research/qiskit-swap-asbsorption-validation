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
from unittest.mock import patch

from swap_absorption_checks import feature_checks

os.environ["QISKIT_PARALLEL"] = "FALSE"
SEEDS = [0, 1, 42, 2, 7, 123]
LEVELS = [2, 3]
BASIS_GATES = ["rz", "sx", "x", "cx"]
CHECKS = ("ignoring_final_layout_must_fail", "extra_x_gate_must_fail")


def fixtures():
    """Return the 16 named test circuits as zero-argument builder callables.

    The builders are lazy so that ``main`` can build each circuit inside its own
    ``try`` block: a fixture that fails to build is recorded as failing for every
    seed instead of aborting the whole run. Every builder is deterministic (fixed
    seeds), and every circuit must end up with exactly 10 qubits.
    """
    # Imported here, not at module level, because these must come from the Qiskit
    # source tree that main() has just put at the front of sys.path.
    # NumPy provides the seeded random generator for the quantum-volume builder.
    import numpy as np
    # Circuit classes used by the local quantum-volume builder and by the QFT fixture.
    from qiskit import QuantumCircuit, QuantumRegister
    # Haar-random unitaries for the two-qubit blocks of the quantum-volume circuit.
    from qiskit.quantum_info import random_unitary
    # Circuit builders from Qiskit's own benchmark suite (needs `ddt` installed).
    from test.benchmarks import qft, random_circuit_hex, utils

    def quantum_volume(seed):
        """Build a 10-qubit, 10-layer quantum-volume circuit from one seeded generator."""
        # One generator drives both the qubit pairings and the unitaries, so the
        # whole circuit is fixed by `seed`.
        rng = np.random.default_rng(seed)
        # Start from an empty 10-qubit circuit with no classical bits.
        qc = QuantumCircuit(10)
        # Quantum volume uses depth equal to width: 10 layers for 10 qubits.
        for _ in range(10):
            # Shuffle the qubits; consecutive entries form this layer's pairs.
            perm = rng.permutation(10)
            # Step through the permutation two at a time: 5 disjoint pairs per layer.
            for k in range(0, 10, 2):
                # Apply a random 4x4 (two-qubit) unitary to the pair; int() turns the
                # NumPy integers into plain qubit indices.
                qc.append(random_unitary(4, seed=rng), [int(perm[k]), int(perm[k + 1])])
        # Every layer touches all 10 qubits, so the result is fully two-qubit-entangled.
        return qc

    # Shared random-circuit builder: 10 qubits, gates on at most two qubits, and no
    # measurements, classically conditioned gates or resets, so the result is a
    # pure unitary. Callers still supply the depth (positionally) and the seed.
    random = partial(utils.random_circuit, 10, max_operands=2,
                     measure=False, conditional=False, reset=False)
    # Fixture name -> builder. Insertion order is the order cases run and are reported.
    return {
        # Quantum Fourier transform on a 10-qubit register: H gates plus all-to-all
        # controlled-phase gates, which forces many SWAPs on a line.
        "qft10": lambda: qft.build_model_circuit(QuantumRegister(10)),
        # Random circuit of depth 60 with circuit seed 42.
        "random10x60": partial(random, 60, seed=42),
        # 4-bit ripple-carry adder: registers a(4) + b(4) + cin(1) + cout(1) = 10 qubits.
        # It contains Toffoli gates (expanded later) and final measurements (removed later).
        "adder4": partial(utils.build_ripple_adder_circuit, 4),
        # Ring circuit: 10 qubits, depth 20, seed 0. The builder returns
        # [circuit, circuit_with_measurements, num_qubits]; [0] takes the unmeasured one.
        "ring10": lambda: random_circuit_hex.make_circuit_ring(10, 20, 0)[0],
        # Quantum-volume circuit from the local builder above, seed 42.
        "qv10": partial(quantum_volume, 42),
        # Floquet unitary for discrete-time-crystal (DTC) evolution, using the
        # builder's default parameters (g=0.95, seed=12345).
        "dtc10": partial(utils.dtc_unitary, 10),
        # Shallower random circuit: depth 20, circuit seed 7.
        "random10x20": partial(random, 20, seed=7),
        # Medium random circuit: depth 40, circuit seed 123.
        "random10x40": partial(random, 40, seed=123),
        # Second quantum-volume instance with a different seed (7).
        "qv10_seed7": partial(quantum_volume, 7),
        # Bernstein-Vazirani for the all-ones secret: every data qubit has a CX
        # onto the last qubit (star-shaped interactions).
        "bv10": partial(utils.bv_all_ones, 10),
        # BV-like circuit: CX from every qubit onto the last one, then X and Z gates.
        "bvlike10": partial(utils.trivial_bvlike_circuit, 10),
        # Trotterized evolution of a nearest-neighbour XX+YY+ZZ chain with a Z
        # field, two Trotter steps.
        "trotter10": partial(utils.trotter_circuit, 10, reps=2),
        # QAOA ansatz with seeded random ZZ weights, two repetitions. It has free
        # parameters, which main() binds to fixed values.
        "qaoa10": partial(utils.qaoa_circuit, 10, reps=2),
        # Grover search: 9 state qubits plus 1 target qubit.
        "grover10": partial(utils.grover_circuit, 10),
        # Multi-controlled X: 8 controls, 1 target and 1 clean ancilla.
        "mcx10": partial(utils.mcx_circuit, 10),
        # Modular adder on two 5-qubit registers (10 qubits in total).
        "modular_adder10": partial(utils.modular_adder_circuit, 10),
    }


def require(condition, message):
    if not condition:
        raise ValueError(message)


def transpile_and_check(qc, reference, cmap, seed, level, metrics):
    """Run the complete preset pipeline and verify its full, layout-aware unitary.

    Observe the real move finder without changing its return value. A callback
    separately confirms that the pass ran, including invocations with no SWAPs.
    Metrics remain available to the caller even if a later correctness check fails.
    """
    from qiskit.quantum_info import Operator
    from qiskit.transpiler import generate_preset_pass_manager
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

    pm = generate_preset_pass_manager(
        optimization_level=level, coupling_map=cmap, basis_gates=BASIS_GATES,
        initial_layout=list(range(qc.num_qubits)), layout_method="trivial",
        routing_method="sabre", translation_method="translator",
        seed_transpiler=seed, approximation_degree=1.0, qubits_initially_zero=False,
    )
    with patch.object(_swap_absorption, "_find_moves", side_effect=observe_moves):
        out = pm.run(qc.copy(), callback=callback)

    require(metrics["absorption_runs"] > 0, "SWAP absorption pass did not execute")
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
    # Equality includes global phase; do not use Operator.equiv here.
    require(Operator.from_circuit(out) == reference, "Operator mismatch")
    metrics.update(depth=out.depth(), two_qubit_gates=sum(
        len(inst.qubits) == 2 and not inst.operation._directive for inst in out.data))
    return out


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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qiskit-src", type=Path,
                        default=Path(__file__).resolve().parent.parent / "qiskit-swap-absorption")
    parser.add_argument("--seeds", type=int, nargs="+", default=SEEDS)
    parser.add_argument("--optimization-levels", type=int, nargs="+", choices=LEVELS, default=LEVELS)
    parser.add_argument("--out", type=Path, default=Path("results/results.jsonl"))
    args = parser.parse_args()
    if len(set(args.seeds)) != len(args.seeds) or any(s < 0 or s >= 2**64 for s in args.seeds):
        parser.error("Seeds must be distinct unsigned 64-bit integers")
    if len(set(args.optimization_levels)) != len(args.optimization_levels):
        parser.error("Optimization levels must be distinct")
    source = args.qiskit_src.resolve()
    require((source / "qiskit").is_dir(), f"Qiskit source directory not found: {source}")
    sys.path.insert(0, str(source))
    import qiskit
    import qiskit._accelerate
    from qiskit.quantum_info import Operator
    from qiskit.transpiler import CouplingMap, PassManager
    from qiskit.transpiler.passes import Unroll3qOrMore
    from qiskit.transpiler.preset_passmanagers import builtin_plugins

    # Refuse to validate an unrelated installed package or compiled extension.
    for module in (qiskit, qiskit._accelerate):
        require(Path(module.__file__).resolve().is_relative_to(source), "Wrong Qiskit source")
    revision = source / "REVISION"
    sha = (revision.read_text().strip() if revision.exists() else
           subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip())
    builders = fixtures()
    records = [dict(
        record="environment", schema_version=2, sha=sha, qiskit_version=qiskit.__version__,
        qiskit_source=str(source), accelerate_file=qiskit._accelerate.__file__,
        accelerate_sha256=hashlib.sha256(Path(qiskit._accelerate.__file__).read_bytes()).hexdigest(),
        python=sys.version.split()[0], timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        fixtures=list(builders), seeds=args.seeds, optimization_levels=args.optimization_levels,
        basis_gates=BASIS_GATES, coupling_map="line(10)", layout_method="trivial",
        routing_method="sabre", translation_method="translator", approximation_degree=1.0,
        qubits_initially_zero=False, routing_trials=builtin_plugins._get_trial_count(20),
    )]
    cmap = CouplingMap.from_line(10)
    samples = dict.fromkeys(args.optimization_levels)
    for name, builder in builders.items():
        error = None
        print(f"Preparing {name}", flush=True)
        try:
            qc = builder().remove_final_measurements(inplace=False)
            qc = qc.assign_parameters([0.1 * (i + 1) for i in range(qc.num_parameters)])
            qc = PassManager([Unroll3qOrMore()]).run(qc)
            require(qc.num_qubits == 10 and all(len(i.qubits) <= 2 for i in qc.data),
                    "Expected 10 qubits and only one- or two-qubit instructions")
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
                           absorption_swaps_seen=0)
                try:
                    require(error is None, error)
                    out = transpile_and_check(qc, reference, cmap, seed, level, rec)
                    final = out.layout.final_index_layout()
                    rec.update(passed=True, final_permutation=final)
                    if samples[level] is None and final != list(range(10)):
                        samples[level] = (out, reference, name, seed)
                except (KeyboardInterrupt, SystemExit):
                    raise
                except BaseException as exc:
                    rec["error"] = repr(exc)
                records.append(rec)
                print(f"PASS ({rec['absorption_moves']} moves)" if rec["passed"]
                      else f"FAIL {rec['error']}", flush=True)

    for level in args.optimization_levels:
        records += self_checks(samples[level], level)
        cases = [r for r in records if r["record"] == "case" and r["optimization_level"] == level]
        moves = sum(r["absorption_moves"] for r in cases)
        coverage = dict(record="coverage", optimization_level=level, passed=moves > 0,
                        absorption_moves=moves, absorption_runs=sum(r["absorption_runs"] for r in cases),
                        cases_with_moves=sum(r["absorption_moves"] > 0 for r in cases))
        if not coverage["passed"]:
            coverage["error"] = "No absorption moves observed in the preset pipeline"
        records.append(coverage)
    records += feature_checks()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    failed = sum(not r["passed"] for r in records[1:])
    print(f"{failed} failures; wrote {args.out}")
    return int(failed != 0)


if __name__ == "__main__":
    sys.exit(main())
