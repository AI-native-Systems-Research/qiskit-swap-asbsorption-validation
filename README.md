# SWAP-absorption correctness

Validate the SWAP-absorption change in
`../qiskit-swap-absorption` (commit
`6b5145d2e94799ba3b01494a706537efc2925242`). The pass runs at the start of
**translation at optimization levels 2 and 3**, so this suite runs the complete
preset pipeline, including routing, translation, and optimization.

The default matrix is 16 deterministic ten-qubit circuits × six transpiler
seeds (`0 1 42 2 7 123`) × two optimization levels: **192 cases**.
Every output must match the original circuit's full unitary, including global
phase with Qiskit's default tolerances, and preserve width and valid layout
metadata. Every physical gate must satisfy the bidirectional line's connectivity
and the requested `rz`, `sx`, `x`, `cx` basis.

## Run with the existing local build

From this repository, the existing environment in `qiskit-evolved` can supply
Python and dependencies while `--qiskit-src` selects the new Qiskit checkout:

```bash
../qiskit-evolved/.venv/bin/python run_validation.py --qiskit-src ../qiskit-swap-absorption
../qiskit-evolved/.venv/bin/python make_report.py
```

`--qiskit-src` defaults to the sibling `qiskit-swap-absorption` checkout. Both
Qiskit's Python package and its compiled `_accelerate` extension must load from
that tree. The latest commit adds a Rust commutation-table API, so an extension
built from older source is insufficient even if importing Qiskit succeeds.

Results go to `results/results.jsonl`; the summary goes to `report.md`. Both
scripts return nonzero on failure. The reporter rejects missing, duplicate, or
unexpected cases and checks, including results from the old wait-time suite.
Exceptions, including Python-visible Rust panics, count as failures.

Reporter integrity tests run with `python3 -m unittest test_make_report` and do
not require Qiskit.

For a smaller run or separate output files:

```bash
../qiskit-evolved/.venv/bin/python run_validation.py --seeds 0 --optimization-levels 2 3 --out /tmp/swap-smoke.jsonl
../qiskit-evolved/.venv/bin/python make_report.py --results /tmp/swap-smoke.jsonl --out /tmp/swap-smoke.md
```

## What is checked

Each case uses an explicit identity initial layout, SABRE routing, the built-in
`translator` stage, and exact synthesis (`approximation_degree=1.0`).
`qubits_initially_zero=False` prevents optimizations from relying on a zero input
state, because the comparison covers every input state. The runner enforces
`QISKIT_PARALLEL=FALSE` and records the preset's routing trial count (normally 20
at both levels; Qiskit's `sabre_all_threads` setting can increase it).

A pass callback confirms `_AbsorbIntoSwaps` executes in every case. An observing
wrapper around its move finder records the moves selected by the actual run,
without changing them. At least one move must occur in the benchmark matrix at
**each requested optimization level**, so an inactive pass cannot pass the suite.
Two-qubit gate counts and final depths are diagnostic metrics; this pass does not
guarantee that either decreases for every circuit.

There are two negative controls at each requested level, using that level's
first passing result with a nonidentity final permutation:

- **Ignored layout** (`ignoring_final_layout_must_fail`): compare with
  `ignore_set_layout=True`, without undoing the routing permutation.
- **Extra X gate** (`extra_x_gate_must_fail`): append an X on physical qubit 0,
  while still applying the layout in the comparison.

Each control passes only when its deliberate error is detected. Separate small
feature checks exercise eligible moves and blocking conditions, preserve the
full unitary, and verify level gating for both built-in translation methods.
See `swap_absorption_checks.py` for the complete list.

## Circuits

The 16 fixtures are QFT, random circuits of depths 20/40/60, a four-bit ripple
adder, a ring circuit, two quantum-volume instances, DTC evolution,
Bernstein–Vazirani and BV-like circuits, Trotter evolution, QAOA, Grover,
multi-controlled X, and modular addition.

Builders come from the selected checkout's `test/benchmarks`, except the small
quantum-volume builder, which uses one seeded generator for both pairings and
unitaries. Fixture seeds are fixed in `fixtures()` and independent of transpiler
seeds. Trotter and QAOA use two repetitions; QAOA parameters are bound to
`0.1, 0.2, …` in Qiskit's parameter order. Final measurements are removed and
instructions on three or more qubits are expanded before building the reference.

## Fresh release build

Use Python 3.10+ and the Qiskit build toolchain. To build an isolated copy inside
this repository:

```bash
mkdir -p qiskit-src
git -C ../qiskit-swap-absorption archive HEAD | tar -x -C qiskit-src
git -C ../qiskit-swap-absorption rev-parse HEAD > qiskit-src/REVISION
~/.pyenv/versions/3.11.8/bin/python -m venv .venv
(cd qiskit-src && QISKIT_BUILD_PROFILE=release ../.venv/bin/pip install -e .)
.venv/bin/pip install 'ddt>=1.2.0'
.venv/bin/python run_validation.py --qiskit-src qiskit-src
.venv/bin/python make_report.py
```

`ddt` is needed for benchmark imports. Each run records its source revision,
loaded extension path and SHA-256, versions, settings, and UTC timestamp.
Commit results and report together. These checks validate the selected circuits,
seeds, and integration paths; they are not a general correctness proof.
