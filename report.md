# SWAP absorption correctness

**PASS** — 192/192 cases; 4/4 negative self-checks; 10/10 focused checks.

Revision: `6b5145d2e94799ba3b01494a706537efc2925242`. Seeds: `[0, 1, 42, 2, 7, 123]`. Optimization levels: `[2, 3]`.
Run: 2026-10-28T12:40:48+00:00. Qiskit 2.7.0.dev0+6b5145d; Python 3.11.8.
Loaded extension SHA-256: `627034a679e9e709e36d8e4b5726a78731abc4c126622e7b884364f6bfd058f8`.

16 circuits, 10 qubits each; full preset pipeline, bidirectional line, trivial initial layout, SABRE routing, and the `rz`, `sx`, `x`, `cx` basis. Exact synthesis; arbitrary input states (`qubits_initially_zero=False`).
Each output must match its original unitary (layout-aware, global phase kept, default tolerances), preserve width and layout metadata, satisfy connectivity and basis constraints, and execute the absorption pass.

| Circuit | Level 2 | Level 3 |
|---|---|---|
| qft10 | 6/6 | 6/6 |
| random10x60 | 6/6 | 6/6 |
| adder4 | 6/6 | 6/6 |
| ring10 | 6/6 | 6/6 |
| qv10 | 6/6 | 6/6 |
| dtc10 | 6/6 | 6/6 |
| random10x20 | 6/6 | 6/6 |
| random10x40 | 6/6 | 6/6 |
| qv10_seed7 | 6/6 | 6/6 |
| bv10 | 6/6 | 6/6 |
| bvlike10 | 6/6 | 6/6 |
| trotter10 | 6/6 | 6/6 |
| qaoa10 | 6/6 | 6/6 |
| grover10 | 6/6 | 6/6 |
| mcx10 | 6/6 | 6/6 |
| modular_adder10 | 6/6 | 6/6 |

Actual moves selected by the pass (required at every requested level):

| Level | Cases with moves | Moves | Pass executions | Coverage |
|---|---|---|---|---|
| 2 | 42 | 410 | 96 | PASS |
| 3 | 42 | 410 | 96 | PASS |

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
