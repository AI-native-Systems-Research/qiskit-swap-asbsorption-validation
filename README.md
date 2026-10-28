# SWAP-absorption validation

This repo checks that Qiskit's SWAP-absorption optimization keeps circuits
correct. It tests **`../qiskit-swap-absorption` at optimization levels 2 and 3**,
using the full transpilation pipeline.

The default run covers **192 cases**: 16 circuits, each with 10 qubits, run with
six seeds at both optimization levels.

Run these commands from this folder:

```bash
../qiskit-evolved/.venv/bin/python run_validation.py --qiskit-src ../qiskit-swap-absorption
../qiskit-evolved/.venv/bin/python make_report.py
```

The `qiskit-evolved` environment supplies Python and dependencies. The Qiskit code
and compiled Rust extension being tested both come from `qiskit-swap-absorption`.
That checkout must already be built from its current source. The environment
also needs `ddt` for the circuit builders.

Open [report.md](report.md) for the results and circuit list. Detailed results
are saved in [results/results.jsonl](results/results.jsonl). The scripts return
a nonzero exit code on failure; the report also rejects missing or duplicate
results.

The suite checks that:

- Each output has the same full unitary as its input, including global phase,
  after accounting for the qubit layout.
- The number of qubits and layout information are preserved, and gates use
  connected qubits on a line and the requested `rz`, `sx`, `x`, `cx` basis.
- The absorption pass runs in every case and makes moves in at least one case
  at each optimization level.
- Focused tests confirm which moves are allowed or blocked, and that the pass
  is enabled at levels 2 and 3 and absent at levels 0 and 1.

Two extra checks at each level deliberately break a passing result: one ignores
its layout, and the other adds an X gate. Both must be detected. Gate counts
and circuit depth are recorded, but they do not have to improve in every case.

For a smaller run, add `--seeds 0`. To test just one level, add
`--optimization-levels 2` or `--optimization-levels 3`. Use `--out` to save a
separate results file, then pass that path to `make_report.py --results`.

To test the report checker itself, without Qiskit:

```bash
python3 -m unittest test_make_report
```
