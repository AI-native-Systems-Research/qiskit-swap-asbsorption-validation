# SWAP absorption correctness

**PASS** — 132/132 cases; 4/4 negative self-checks; 10/10 focused checks.

Revision: `6b5145d2e94799ba3b01494a706537efc2925242`. Seeds: `[0, 1, 42, 2, 7, 123]`. Optimization levels: `[2, 3]`.
Run: 2026-10-28T20:17:11+00:00. Qiskit 2.7.0.dev0+6b5145d; Python 3.11.8.
Loaded extension SHA-256: `627034a679e9e709e36d8e4b5726a78731abc4c126622e7b884364f6bfd058f8`.

Baseline revision: `abe422d0ad6eb0e3acbb2f4160a0d336ed4d26cb`; Qiskit 2.7.0.dev0+abe422d.
Baseline source: `/Users/ophir/PycharmProjects/qiskit-swap-absorption-before`.
Baseline extension: `/Users/ophir/PycharmProjects/qiskit-swap-absorption-before/qiskit/_accelerate.cpython-311-darwin.so`.
Baseline extension SHA-256: `d21180442265c1788a786a8cb5e2f52a0feaa06b490e72a0026cf18e09812472`.

11 circuits, 10 qubits each; full preset pipeline, bidirectional line, trivial initial layout, SABRE routing, and the `rz`, `sx`, `x`, `cx` basis. Exact synthesis; arbitrary input states (`qubits_initially_zero=False`).
Both outputs must match the same original unitary (layout-aware, global phase kept, default tolerances), preserve width and layout metadata, and satisfy connectivity and basis constraints. Every case must select absorption moves and produce a structurally different circuit from both the baseline and the current checkout with absorption disabled. DAG comparison ignores circuit names and the ordering of independent operations. Phase-only and layout-only changes do not count as changes to gates. Matching input SHA-256 values verify that both checkouts received the same circuit. Every requested circuit/seed/level combination must have a passing result.

Per cell: passing cases; cases changed from baseline, out of all requested seeds.

| Circuit | Level 2 | Level 3 |
|---|---|---|
| qft10 | 6/6 pass; 6/6 changed | 6/6 pass; 6/6 changed |
| adder4 | 6/6 pass; 6/6 changed | 6/6 pass; 6/6 changed |
| ring10 | 6/6 pass; 6/6 changed | 6/6 pass; 6/6 changed |
| dtc10 | 6/6 pass; 6/6 changed | 6/6 pass; 6/6 changed |
| bv10 | 6/6 pass; 6/6 changed | 6/6 pass; 6/6 changed |
| bvlike10 | 6/6 pass; 6/6 changed | 6/6 pass; 6/6 changed |
| trotter10 | 6/6 pass; 6/6 changed | 6/6 pass; 6/6 changed |
| qaoa10 | 6/6 pass; 6/6 changed | 6/6 pass; 6/6 changed |
| grover10 | 6/6 pass; 6/6 changed | 6/6 pass; 6/6 changed |
| mcx10 | 6/6 pass; 6/6 changed | 6/6 pass; 6/6 changed |
| modular_adder10 | 6/6 pass; 6/6 changed | 6/6 pass; 6/6 changed |

Actual changes and moves (required in every case):

| Level | Changed vs baseline | Changed vs absorption disabled | Cases with moves | Moves | Pass executions | Coverage |
|---|---|---|---|---|---|---|
| 2 | 66/66 | 66/66 | 66 | 435 | 66 | PASS |
| 3 | 66/66 | 66/66 | 66 | 435 | 66 | PASS |

Totals across all cases (baseline → with absorption):

| Level | Two-qubit gates | Circuit depths (sum) |
|---|---|---|
| 2 | 18187 → 17405 | 19665 → 20124 |
| 3 | 18187 → 17405 | 19665 → 20118 |

Negative controls use a passing output with a nonidentity final permutation at each level. Ignoring its layout and appending an extra X must each break the unitary comparison.

- Level 2, `ignoring_final_layout_must_fail`: PASS (`qft10`, seed 0).
- Level 2, `extra_x_gate_must_fail`: PASS (`qft10`, seed 0).
- Level 3, `ignoring_final_layout_must_fail`: PASS (`qft10`, seed 0).
- Level 3, `extra_x_gate_must_fail`: PASS (`qft10`, seed 0).

Focused checks:

- `preset_translation_levels`: PASS.
- `translation_helper_levels`: PASS.
- `commuting_later_interaction`: PASS.
- `commuting_earlier_interaction`: PASS.
- `cx_rz_cx_block`: PASS.
- `diagonal_unitary`: PASS.
- `noncommuting_guard`: PASS.
- `barrier_guard`: PASS.
- `terminal_wire_guard`: PASS.
- `intervening_swap_guard`: PASS.

Scope: full preset correctness on these circuits and seeds, not a general proof. Gate counts and depths are diagnostics; an improvement is not required for every case. Raw results and build path: `results/results.jsonl`.
