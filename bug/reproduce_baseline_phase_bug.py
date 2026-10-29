#!/usr/bin/env python
"""Reproduce the baseline's two-qubit global-sign defect in separate interpreters.

The six-gate circuit is a reduced version of a two-qubit block from the failing
random10x20 / level 2 / seed 0 fixture. The permutation matrix is also covered by
Qiskit's upstream test_zsx_with_sign_problem regression test added in #17163.
This script does not invoke the SWAP-absorption pass.
"""
import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from run_validation import load_source


# Collected two-qubit run 93 immediately before the offending peephole pass in
# random10x20 / optimization level 2 / transpiler seed 0. Gate parameters are
# copied exactly from the observed block, with its two wires relabeled 0 and 1.
EXTRACTED_BLOCK = [
    ("cx", [0, 1], []),
    ("rz", [1], [-3.3485617391004925]),
    ("sx", [1], []),
    ("rz", [1], [2.058814354552818]),
    ("sx", [1], []),
    ("rz", [1], [9.42477796076938]),
    ("cx", [0, 1], []),
    ("rz", [1], [0.0]),
    ("sx", [1], []),
    ("rz", [1], [4.224370952626768]),
    ("sx", [1], []),
    ("rz", [1], [10.706633130118373]),
    ("rz", [0], [3.141592653589793]),
    ("x", [0], []),
    ("cx", [1, 0], []),
    ("cx", [0, 1], []),
    ("cx", [1, 0], []),
]


def probe(source):
    environment = load_source(source.resolve())
    import numpy as np
    from qiskit import QuantumCircuit
    from qiskit.circuit.library import CXGate
    from qiskit.quantum_info import Operator
    from qiskit.synthesis import TwoQubitBasisDecomposer
    from qiskit.transpiler import CouplingMap, PassManager, Target
    from qiskit.transpiler.passes import TwoQubitPeepholeOptimization

    matrix = np.array([[0, 1, 0, 0], [0, 0, 0, 1],
                       [1, 0, 0, 0], [0, 0, 1, 0]], dtype=complex)
    target = Target.from_configuration(
        basis_gates=["rz", "sx", "x", "cx"], num_qubits=2,
        coupling_map=CouplingMap.from_line(2),
    )

    def metrics(name, original, output):
        a, b = Operator(original), Operator(output)
        overlap = np.vdot(a.data, b.data) / a.dim[0]
        phase = overlap / abs(overlap)
        residual = b.data - phase * a.data
        controlled_a = np.zeros((8, 8), dtype=complex)
        controlled_b = np.zeros((8, 8), dtype=complex)
        # Block ordering |0>⊗target, |1>⊗target: C(U) = diag(I, U).
        controlled_a[:4, :4] = controlled_b[:4, :4] = np.eye(4)
        controlled_a[4:, 4:], controlled_b[4:, 4:] = a.data, b.data
        return dict(
            name=name, operator_equal=bool(a == b), equiv_up_to_global_phase=bool(a.equiv(b)),
            phase_radians=float(np.angle(overlap)), overlap_magnitude=float(abs(overlap)),
            max_abs_error=float(np.max(abs(b.data - a.data))),
            max_abs_error_after_phase_alignment=float(np.max(abs(residual))),
            normalized_frobenius_error_after_phase_alignment=float(
                np.linalg.norm(residual) / np.linalg.norm(a.data)),
            controlled_operator_equal=bool(Operator(controlled_a) == Operator(controlled_b)),
            controlled_equiv_up_to_global_phase=bool(
                Operator(controlled_a).equiv(Operator(controlled_b))),
            controlled_max_abs_error=float(np.max(abs(controlled_b - controlled_a))),
            output_global_phase=float(output.global_phase),
            output_gates=dict(output.count_ops()),
        )

    direct = TwoQubitBasisDecomposer(CXGate(), euler_basis="ZSX")(matrix)
    results = [metrics("upstream_permutation_matrix_decomposer", matrix, direct)]

    minimal = QuantumCircuit(2)
    minimal.cx(0, 1)
    minimal.cx(0, 1)
    minimal.x(0)
    minimal.cx(1, 0)
    minimal.cx(0, 1)
    minimal.cx(1, 0)
    assert np.array_equal(Operator(minimal).data, matrix)
    pm = PassManager([TwoQubitPeepholeOptimization(target, approximation_degree=1.0)])
    results.append(metrics("six_gate_peephole_reproducer", minimal, pm.run(minimal)))

    extracted = QuantumCircuit(2)
    for name, wires, parameters in EXTRACTED_BLOCK:
        getattr(extracted, name)(*parameters, *wires)
    results.append(metrics("extracted_17_gate_fixture_block", extracted, pm.run(extracted)))
    return dict(environment=environment, cases=results)


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qiskit-src", type=Path,
                        help="Probe one checkout; otherwise compare baseline and candidate separately")
    parser.add_argument("--baseline-src", type=Path,
                        default=root.parent / "qiskit-swap-absorption-before")
    parser.add_argument("--candidate-src", type=Path,
                        default=root.parent / "qiskit-swap-absorption")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if args.qiskit_src is not None:
        result = probe(args.qiskit_src)
    else:
        runs = {}
        for label, source in (("baseline", args.baseline_src), ("candidate", args.candidate_src)):
            runs[label] = json.loads(subprocess.check_output(
                [sys.executable, str(Path(__file__).resolve()), "--qiskit-src", str(source)],
                text=True))
        result = dict(
            timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            python=sys.version.split()[0], known_fix="9aa3717153a5ed3261d91e79c5b66325c6f8a653",
            extracted_block_origin=dict(fixture="random10x20", optimization_level=2,
                                        seed=0, pass_name="TwoQubitPeepholeOptimization",
                                        collected_run_index=93, gate_count=17),
            extracted_block=EXTRACTED_BLOCK, runs=runs,
        )
    content = json.dumps(result, indent=2) + "\n"
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(content)
    else:
        print(content, end="")


if __name__ == "__main__":
    main()
