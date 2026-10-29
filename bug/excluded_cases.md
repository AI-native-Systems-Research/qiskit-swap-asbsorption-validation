# Cases removed from SWAP-absorption validation

The validation suite keeps the 11 circuits that achieved **6/6 pass; 6/6 changed**
at both optimization levels in the original run. Five entire fixture families
were removed because at least one of their six transpiler seeds failed the
baseline unitary check. This removes **60 circuit/seed/level combinations**:
34 baseline mismatches and 26 cases that previously passed and changed. The
26 passing cases were removed to keep complete fixture families in the suite;
they are not additional failures.

The active suite now has 132 mandatory cases. Its [report](../report.md) is dedicated
to validating the SWAP-absorption change. A baseline mismatch in any active case
fails the run; there is no automatic exclusion policy.

The original run started at `2026-10-28T18:13:44+00:00` (20:13:44 in Jerusalem).
Its complete evidence is preserved in
[results_before_fixture_selection.jsonl](results/results_before_fixture_selection.jsonl)
and the [archived report](results/report_before_fixture_selection.md).
That run requested 192 cases, recorded 158 passing cases and 34 baseline
exclusions, and used:

| Component | Revision | Qiskit version |
|---|---|---|
| Candidate | `6b5145d2e94799ba3b01494a706537efc2925242` | `2.7.0.dev0+6b5145d` |
| Baseline | `abe422d0ad6eb0e3acbb2f4160a0d336ed4d26cb` | `2.7.0.dev0+abe422d` |

Python was 3.11.8. The candidate extension SHA-256 was
`627034a679e9e709e36d8e4b5726a78731abc4c126622e7b884364f6bfd058f8`;
the baseline extension SHA-256 was
`d21180442265c1788a786a8cb5e2f52a0feaa06b490e72a0026cf18e09812472`.

All seeds below are **transpiler seeds**, distinct from the fixed circuit-builder
seeds. Each row covers both levels 2 and 3: a seed listed in either status column
therefore accounts for two cases. The two seed columns partition the complete
default seed list `[0, 1, 42, 2, 7, 123]` for every removed fixture.

| Removed fixture | Baseline-mismatch seeds, at each level | Previously passing and changed seeds, at each level | Baseline mismatches | Passing cases removed | Total removed |
|---|---|---|---:|---:|---:|
| `random10x60` | `1, 7` | `0, 42, 2, 123` | 4 | 8 | 12 |
| `qv10` | `123` | `0, 1, 42, 2, 7` | 2 | 10 | 12 |
| `random10x20` | `0, 1, 42, 2, 7, 123` | None | 12 | 0 | 12 |
| `random10x40` | `0, 1, 2, 7` | `42, 123` | 8 | 4 | 12 |
| `qv10_seed7` | `0, 1, 42, 123` | `2, 7` | 8 | 4 | 12 |
| **Total** | | | **34** | **26** | **60** |

Every baseline mismatch was recorded as `record="excluded_case"`,
`reason="baseline_operator_mismatch"`, `error="Baseline operator mismatch"`,
and `baseline_passed=false`. The original runner stopped that case before
transpiling the candidate. These records establish baseline failures, not
candidate failures or passes. The 26 passing records verified both outputs'
unitaries, selected absorption moves, and confirmed structural changes against
both the baseline and the candidate with absorption disabled.

The removed builders are preserved in [excluded_fixtures.py](excluded_fixtures.py)
for reproducing the investigation:

| Fixture | Original construction |
|---|---|
| `random10x60` | Qiskit benchmark random circuit: 10 qubits, depth 60, circuit seed 42. |
| `random10x20` | Same builder: 10 qubits, depth 20, circuit seed 7. |
| `random10x40` | Same builder: 10 qubits, depth 40, circuit seed 123. |
| `qv10` | Local QV-style diagonal/Haar variant: 10 qubits, 10 layers, circuit seed 42. |
| `qv10_seed7` | Same local QV-style builder with circuit seed 7. |

The random builders allow gates on at most two qubits and disable measurements,
classical conditions, and resets. The QV-style builders alternate two diagonal
unitary layers with one Haar-random layer, use seeded random pairings, and embed
the result on wires `[4, 6, 2, 7, 3, 5, 9, 0, 8, 1]`. They are variants designed
to expose commuting interactions, rather than standard quantum-volume circuits.

The [baseline investigation](baseline_bug_report.md) documents the separate
diagnostic run: all 34 baseline mismatches reproduce, with output approximately
`V = -U`. They fail the phase-sensitive correctness check but pass equivalence
up to global phase. One extracted failing block was traced to a sign correction
in two-qubit synthesis; attribution of every failing case to the same source
line was not established. The investigation preserves this distinction.

To repeat the baseline audit using the archived case selection and original
prepared inputs:

```bash
../../qiskit-evolved/.venv/bin/python diagnose_baseline.py \
  --qiskit-src ../../qiskit-swap-absorption-before \
  --results results/results_before_fixture_selection.jsonl \
  --inputs-dir results/baseline_inputs \
  --out results/baseline_diagnostics.json
```

The diagnostic script can also rebuild the original fixtures when `--inputs-dir`
is omitted. It checks their serialized hashes against the archived run before
comparing operators. [reproduce_baseline_phase_bug.py](reproduce_baseline_phase_bug.py)
contains the independent two-qubit reproducer. Detailed measurements are in
[baseline_diagnostics.json](results/baseline_diagnostics.json), and reproducer
results are in [baseline_reproducer_results.json](results/baseline_reproducer_results.json).
