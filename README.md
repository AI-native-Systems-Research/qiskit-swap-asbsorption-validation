# SWAP-absorption validation

This repo checks that Qiskit's SWAP-absorption optimization keeps circuits
correct and actually changes every tested circuit. It compares
**`../qiskit-swap-absorption` against `../qiskit-swap-absorption-before` at
optimization levels 2 and 3**, using the full transpilation pipeline.

The default run requests **192 cases**: 16 circuits, each with 10 qubits, run with
six seeds at both optimization levels. Cases whose baseline output has an
incorrect unitary are removed from the tested matrix and listed as excluded in
the report; they never count as passing tests.

Run these commands from this folder:

```bash
../qiskit-evolved/.venv/bin/python run_validation.py \
  --qiskit-src ../qiskit-swap-absorption \
  --baseline-src ../qiskit-swap-absorption-before
../qiskit-evolved/.venv/bin/python make_report.py
```

The `qiskit-evolved` environment supplies Python and dependencies. The Qiskit code
and compiled Rust extension for each run both come from that run's checkout.
Both checkouts must already be built from their current sources. The environment
also needs `ddt` for the circuit builders.

Open [report.md](report.md) for the results and circuit list. Detailed results
are saved in [results/results.jsonl](results/results.jsonl). The scripts return
a nonzero exit code on failure; the report also rejects missing or duplicate
results.

Before transpiling, the runner checks which code it is about to test in each checkout.
The `qiskit` package and its compiled `_accelerate` extension must both load from
the selected source tree; that tree must have no uncommitted changes, including
untracked files, under `qiskit/`, `crates/`, `test/benchmarks/` or the Cargo
files; and the extension must not be older than any Rust source. The run refuses
to start otherwise, so the revision and extension SHA-256 in the report identify
the code that was tested. If the extension is stale, rebuild it inside the
checkout with `python setup.py build_rust --inplace --release`.
For a fresh checkout, also run `python setup.py egg_info` there using the same
Python environment. This supplies the package metadata used to discover Qiskit's
built-in routing, translation, and synthesis plugins.

For every retained case, the suite checks that:

- Both the baseline and optimized output have the same full unitary as their
  input, including global phase, after accounting for the qubit layout.
- The number of qubits and layout information are preserved, and gates use
  connected qubits on a line and the requested `rz`, `sx`, `x`, `cx` basis.
- The absorption pass runs and selects moves in **every circuit/seed/level case**.
- Every output differs structurally from the baseline output and from the
  current checkout's output with absorption disabled. An unchanged circuit fails
  with a message to modify the fixture.
- Focused tests confirm which moves are allowed or blocked, and that the pass
  is enabled at levels 2 and 3 and absent at levels 0 and 1.

The baseline runs in a separate Python process so its Python modules and Rust
extension cannot mix with the optimized checkout. Each prepared input is saved
once as QPY and read by both processes; matching SHA-256 values are recorded.
Both runs use the same seed, layout, routing, basis, and synthesis settings.
Circuit comparison uses Qiskit's DAG equality, which compares gates, parameters,
and qubit dependencies while ignoring names, metadata, and the order of independent
operations. Phase-only and layout-only changes do not count; global phase is
still checked by the unitary comparison. The absorption-disabled control
also prevents unrelated differences between checkout revisions from satisfying
the change requirement. Both revisions and extension hashes appear in the report.

Two extra checks at each level deliberately break a passing result: one ignores
its layout, and the other adds an X gate. Both must be detected. Gate counts
and circuit depth are recorded, but they do not have to improve in every case.
The focused guard tests intentionally require no movement when absorption is
unsafe; the per-case change requirement applies to the retained end-to-end cases.

Only a baseline unitary mismatch permits exclusion. Baseline build, loading,
transpilation, connectivity, basis, or layout errors still fail the run, as does
any optimized-output correctness failure. Excluding every case at a requested
optimization level also fails. Identical outputs within one input fixture reuse
their exact unitary-comparison result; the cache includes phase, gates, parameters,
wires, and both layout permutations.

Nine original fixtures never selected absorption moves. They now exercise the
optimization with deterministic variants:

- `adder4`, `dtc10`, `bv10`, and `trotter10` use fixed qubit permutations.
- `ring10` uses commuting controlled-phase/RZ layers with RX mixers.
- `qv10` and `qv10_seed7` alternate two diagonal-unitary layers with one Haar
  layer, retaining random pairings. These are QV-style variants, not standard
  quantum-volume benchmarks.
- `bvlike10` computes parity, applies RZ, and uncomputes it, preventing the
  original oracle from collapsing into single-qubit work.
- `mcx10` uses the HP24 decomposition with eight controls, one target, and a
  tenth idle wire.

The seven other fixture families are unchanged. Every retained default case must
pass; arbitrary additional seeds may reveal fixtures that need further changes.

For a smaller run, add `--seeds 0`. To test just one level, add
`--optimization-levels 2` or `--optimization-levels 3`. Use `--out` to save a
separate results file, then pass that path to `make_report.py --results`.

To test the report checker and the source-tree checks themselves, without Qiskit:

```bash
python3 -m unittest test_make_report test_run_validation
```
