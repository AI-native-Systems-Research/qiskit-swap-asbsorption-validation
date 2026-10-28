#!/usr/bin/env python
"""Reproduce excluded baseline cases and distinguish phase errors from other errors.

Uses a fresh transpilation, not the validation runner's unitary cache. Identical
operator descriptions may share a matrix calculation within one input fixture;
every such reuse is identified in the output. All source imports remain lazy.
"""
import argparse
import hashlib
import io
import json
import sys
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from run_validation import fixtures, load_source, prepare_inputs, preset_manager, require


def operator_description(circuit):
    """Exact standard-gate data and the permutations used by Operator.from_circuit."""
    layout = circuit.layout
    inputs = sorted(layout.input_qubit_mapping, key=layout.input_qubit_mapping.get)
    gates = []
    for inst in circuit.data:
        require(inst.operation.name in ("rz", "sx", "x", "cx"),
                f"Unexpected instruction {inst.operation.name}")
        require(getattr(inst.operation, "_standard_gate", None) is not None,
                "Expected a standard basis gate")
        gates.append([inst.operation.name,
                      [circuit.find_bit(q).index for q in inst.qubits],
                      [float(p).hex() for p in inst.operation.params]])
    return dict(
        width=circuit.num_qubits, global_phase=float(circuit.global_phase).hex(), gates=gates,
        initial_permutation=list(layout.initial_layout.to_permutation(inputs)),
        final_permutation=(None if layout.final_layout is None
                           else list(layout.final_layout.to_permutation(circuit.qubits))),
    )


def measure(reference, output):
    import numpy as np

    a, b = reference.data, output.data
    overlap = np.vdot(a, b) / np.vdot(a, a)
    phase = float(np.angle(overlap))
    aligned = b * np.exp(-1j * phase)
    norm = float(np.linalg.norm(a))
    return dict(
        operator_equal=bool(output == reference),
        equivalent_up_to_global_phase=bool(output.equiv(reference)),
        best_fit_phase_radians=phase, best_fit_phase_over_pi=phase / np.pi,
        normalized_overlap_abs=float(abs(overlap)),
        max_absolute_error=float(np.max(np.abs(b - a))),
        phase_aligned_max_absolute_error=float(np.max(np.abs(aligned - a))),
        normalized_frobenius_error=float(np.linalg.norm(b - a)) / norm,
        phase_aligned_normalized_frobenius_error=float(np.linalg.norm(aligned - a)) / norm,
        atol=float(reference.atol), rtol=float(reference.rtol),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qiskit-src", type=Path,
                        default=Path(__file__).resolve().parent.parent / "qiskit-swap-absorption-before")
    parser.add_argument("--results", type=Path, default=Path("results/results.jsonl"))
    parser.add_argument("--inputs-dir", type=Path,
                        help="Optional directory of the original prepared fixture QPY files")
    parser.add_argument("--archive-dir", type=Path,
                        help="Optional directory of baseline-FIXTURE-LEVEL-SEED.qpy outputs to cross-check")
    parser.add_argument("--fixtures", nargs="+")
    parser.add_argument("--seeds", nargs="+", type=int)
    parser.add_argument("--representatives", action="store_true",
                        help="Only the first excluded level-2 case per fixture")
    parser.add_argument("--disable-absorption", action="store_true")
    parser.add_argument("--out", type=Path, default=Path("results/baseline_diagnostics.json"))
    args = parser.parse_args()
    source = args.qiskit_src.resolve()
    provenance = load_source(source)
    import numpy as np
    from qiskit import qpy
    from qiskit.quantum_info import Operator
    from qiskit.transpiler import CouplingMap

    records = [json.loads(line) for line in args.results.read_text().splitlines() if line.strip()]
    excluded = [r for r in records if r["record"] == "excluded_case"]
    if args.fixtures:
        excluded = [r for r in excluded if r["fixture"] in args.fixtures]
    if args.seeds:
        excluded = [r for r in excluded if r["seed"] in args.seeds]
    if args.representatives:
        seen = set()
        selected = []
        for rec in excluded:
            if rec["optimization_level"] == 2 and rec["fixture"] not in seen:
                selected.append(rec)
                seen.add(rec["fixture"])
        excluded = selected
    require(bool(excluded), "No excluded cases selected")
    if args.disable_absorption:
        from unittest.mock import patch
        from qiskit.transpiler.preset_passmanagers._swap_absorption import _AbsorbIntoSwaps
        absorption_context = patch.object(_AbsorbIntoSwaps, "run", lambda self, dag: dag)
    else:
        absorption_context = nullcontext()
    result = dict(
        schema_version=1, environment=provenance, python=sys.version.split()[0],
        numpy_version=np.__version__, timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        command=[sys.executable, *sys.argv], validation_results=str(args.results.resolve()),
        validation_baseline=records[0]["baseline"], absorption_disabled=args.disable_absorption,
        settings={key: records[0][key] for key in (
            "basis_gates", "coupling_map", "layout_method", "routing_method", "translation_method",
            "approximation_degree", "qubits_initially_zero", "routing_trials")},
        metric_definition="V = exp(i*phase) U; phase = arg(vdot(U,V)/vdot(U,U))",
        cases=[],
    )
    with TemporaryDirectory(prefix="baseline-diagnostics-") as temporary, absorption_context:
        if args.inputs_dir:
            inputs = {r["fixture"]: args.inputs_dir / (r["fixture"] + ".qpy") for r in excluded}
        else:
            # Build the complete ordered fixture list to reproduce original circuit
            # naming and QPY hashes, even when only a subset is being audited.
            prepared = prepare_inputs(fixtures(), Path(temporary))
            inputs = {name: Path(item["path"]) for name, item in prepared.items()}
        cmap = CouplingMap.from_line(10)
        for fixture in dict.fromkeys(r["fixture"] for r in excluded):
            path = inputs[fixture]
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            selected = [r for r in excluded if r["fixture"] == fixture]
            require(all(digest == r["input_sha256"] == r["baseline_input_sha256"] for r in selected),
                    f"Prepared input hash differs from recorded validation input: {fixture}")
            with path.open("rb") as stream:
                circuit, = qpy.load(stream)
            reference = Operator.from_circuit(circuit)
            measured = {}
            for original in selected:
                level, seed = original["optimization_level"], original["seed"]
                print(f"Diagnosing {fixture}, level {level}, seed {seed}", flush=True)
                out = preset_manager(circuit, cmap, seed, level).run(circuit.copy())
                description = operator_description(out)
                signature = hashlib.sha256(json.dumps(description, sort_keys=True).encode()).hexdigest()
                rec = dict(fixture=fixture, optimization_level=level, seed=seed,
                           input_sha256=digest, input_matches_recorded_hash=True,
                           output_operator_description_sha256=signature,
                           input_global_phase=float(circuit.global_phase),
                           output_global_phase=float(out.global_phase),
                           depth=out.depth(), two_qubit_gates=out.count_ops().get("cx", 0),
                           final_permutation=out.layout.final_index_layout())
                if signature in measured:
                    metrics, prior = measured[signature]
                    rec.update(metrics)
                    rec["matrix_evaluation_reused_from"] = prior
                else:
                    # This is the fresh, unserialized output; no validation cache is used.
                    metrics = measure(reference, Operator.from_circuit(out))
                    rec.update(metrics)
                    measured[signature] = (metrics, dict(optimization_level=level, seed=seed))
                # Check serialization separately after evaluating the raw output.
                stream = io.BytesIO()
                qpy.dump(out, stream, version=13)
                stream.seek(0)
                roundtrip, = qpy.load(stream)
                rec["qpy_roundtrip_exact_operator_description_equal"] = (
                    operator_description(roundtrip) == description)
                require(rec["qpy_roundtrip_exact_operator_description_equal"],
                        "QPY round trip changed exact operator data")
                if args.archive_dir:
                    archived_path = args.archive_dir / f"baseline-{fixture}-{level}-{seed}.qpy"
                    with archived_path.open("rb") as stream:
                        archived, = qpy.load(stream)
                    rec["archived_output_exact_operator_description_equal"] = (
                        operator_description(archived) == description)
                    require(rec["archived_output_exact_operator_description_equal"],
                            "Fresh output differs from archived baseline output")
                result["cases"].append(rec)
                args.out.parent.mkdir(parents=True, exist_ok=True)
                args.out.write_text(json.dumps(result, indent=2) + "\n")
                print(f"  equal={rec['operator_equal']}, phase_equiv={rec['equivalent_up_to_global_phase']}, "
                      f"phase/pi={rec['best_fit_phase_over_pi']:.12g}, "
                      f"aligned_max={rec['phase_aligned_max_absolute_error']:.3g}", flush=True)
    print(f"Wrote {len(result['cases'])} case diagnostics to {args.out}", flush=True)


if __name__ == "__main__":
    main()
