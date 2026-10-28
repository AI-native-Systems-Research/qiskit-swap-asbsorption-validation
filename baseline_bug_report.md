# Baseline correctness investigation

Investigation date: **28 October 2026**. Original validation: [report.md](report.md), using [results/results.jsonl](results/results.jsonl).

**Yes: the excluded cases expose a reproducible global-phase/sign bug in the baseline.** All 34 exclusions reproduce when the baseline freshly transpiles the original inputs. In every case, its output operator is approximately **`V = −U`**, where `U` is the input operator. The discrepancy is an overall phase of π, rather than a different transformation after global phase is removed.

This distinction matters. All 34 outputs fail the suite's phase-sensitive operator comparison, but all 34 pass `Operator.equiv`, which ignores global phase. They do not establish incorrect standalone measurement probabilities. They do establish a failure to preserve the complete operator, which matters when a circuit is subsequently used as a controlled operation.

A two-qubit reproducer confirms the defect independently of the validation harness, routing, QPY files, and SWAP absorption. An existing upstream fix is present in the newer checkout and absent from the baseline. The source-level attribution is directly supported for one extracted failing fixture block; the other exclusions have the same sign-error signature but were not individually traced to the same source line.

**Affected cases.** These are 17 fixture/seed combinations, each failing at both optimization levels 2 and 3—not 34 distinct bugs. The seeds below are **transpiler seeds**, separate from the fixed seeds used to build the circuits.

| Fixture | Excluded transpiler seeds | Levels | Cases |
|---|---|---|---:|
| `random10x60` | `1, 7` | 2 and 3 | 4 |
| `qv10` | `123` | 2 and 3 | 2 |
| `random10x20` | `0, 1, 42, 2, 7, 123` | 2 and 3 | 12 |
| `random10x40` | `0, 1, 2, 7` | 2 and 3 | 8 |
| `qv10_seed7` | `0, 1, 42, 123` | 2 and 3 | 8 |
| **Total** | | | **34** |

The QV fixtures here are the suite's diagonal/Haar variants, not standard quantum-volume benchmarks. The exclusions remain outside the tested matrix, as requested. The existing **158/158 PASS** result describes retained cases; it does not mean all 192 requested cases passed.

**Numerical evidence.** The new audit evaluated the full 1024×1024 operators of the 10-qubit circuits. Let `U` be the input operator and `V` the layout-corrected baseline output. It computed:

```text
z             = vdot(U, V) / vdot(U, U)
phi           = angle(z)
raw_error     = max(abs(V - U))
aligned_error = max(abs(exp(-1j * phi) * V - U))
```

For every excluded case, `phi ≈ −π` and `|z| ≈ 1`. The raw normalized Frobenius error is approximately 2, as expected for `V = −U`. After phase alignment, the normalized Frobenius error ranges from `3.03e-15` to `3.27e-13`.

| Fixture | Strict equality | Equal up to global phase | Raw maximum element error, rounded | Largest phase-aligned element error |
|---|---:|---:|---:|---:|
| `random10x60` | 0/4 | 4/4 | 0.221155 | 1.66e-15 |
| `qv10` | 0/2 | 2/2 | 0.365728 | 7.80e-15 |
| `random10x20` | 0/12 | 12/12 | 0.371820 | 8.65e-16 |
| `random10x40` | 0/8 | 8/8 | 0.250062 | 4.10e-14 |
| `qv10_seed7` | 0/8 | 8/8 | 0.381246 | 1.17e-14 |

The raw errors, `0.2211546852–0.3812455878`, are far beyond numerical roundoff. The tiny aligned residuals show that the observed discrepancy is the sign. Exact per-case values, including phase, layout, and input hashes, are in [baseline_diagnostics.json](results/baseline_diagnostics.json).

The strict comparison uses the checkout's default `atol=1e-8` and `rtol=1e-5`. Qiskit's `Operator.equiv` explicitly permits global phase, while `Operator.from_circuit` accounts for circuit layout. Both comparisons were recorded; phase-insensitive equivalence was used diagnostically, not substituted for the suite's correctness requirement. [Operator documentation](https://quantum.cloud.ibm.com/docs/en/api/qiskit/qiskit.quantum_info.Operator#equiv)

**Checks against an investigation artifact.** [diagnose_baseline.py](diagnose_baseline.py) freshly transpiled every selected case inside the baseline's own Python/Rust environment. It measured the raw output before any output serialization. It also established that:

- All five prepared input files have exactly the SHA-256 values recorded by the original validation run.
- All 34 freshly generated outputs match the archived outputs' exact gate, parameter, phase, and layout descriptions.
- QPY serialization and reloading preserve those descriptions in all 34 cases.
- Initial and final layout permutations are included in the operator interpretation.
- The audit does not call the validation runner's unitary-comparison cache.

There were 18 direct output-matrix evaluations. The remaining 16 cases reused an exactly identical operator description within the same input fixture; each reuse is identified in the diagnostic JSON. All 34 cases were freshly transpiled. This evidence rules out QPY transport and the validation cache as explanations for the exclusions.

**The confirmed defect.** A trace of `random10x20`, optimization level 2, transpiler seed 0 localized its first observed sign error to `TwoQubitPeepholeOptimization`. That initial localization used a statevector probe. A collected 17-gate two-qubit block was then extracted and checked independently using its complete 4×4 operator, confirming the sign error without relying on the statevector probe. The block and its parameters are preserved in [reproduce_baseline_phase_bug.py](reproduce_baseline_phase_bug.py).

The implicated code is the sign correction in `TwoQubitBasisDecomposer`. The baseline tests whether the target matrix's `(0, 0)` entry approximately equals the negative of the synthesized matrix's `(0, 0)` entry, then adds π to the circuit phase. If both entries are zero, that test cannot distinguish the two signs: `0 == −0`. It can therefore add π to an already correctly signed decomposition. The corrected code selects a nonzero entry from the first row before making the comparison.

This correction was merged upstream in **PR #17163**, commit **`9aa3717153a5ed3261d91e79c5b66325c6f8a653`**, on 15 October 2026. The PR also fixes a separate missing-RZ defect near a two-/three-CX synthesis boundary. The measured sign errors here should not be conflated with that separate defect. [Upstream PR](https://github.com/Qiskit/qiskit/pull/17163), [fix commit](https://github.com/Qiskit/qiskit/commit/9aa3717153a5ed3261d91e79c5b66325c6f8a653)

The relevant implementation and regression are available at pinned revisions:

- [Baseline sign correction](https://github.com/Qiskit/qiskit/blob/abe422d0ad6eb0e3acbb2f4160a0d336ed4d26cb/crates/synthesis/src/two_qubit_decompose/basis_decomposer.rs#L463)
- [Corrected sign correction](https://github.com/Qiskit/qiskit/blob/9aa3717153a5ed3261d91e79c5b66325c6f8a653/crates/synthesis/src/two_qubit_decompose/basis_decomposer.rs)
- [Upstream synthesis regression tests](https://github.com/Qiskit/qiskit/blob/9aa3717153a5ed3261d91e79c5b66325c6f8a653/test/python/synthesis/test_synthesis.py)

The common `−U` result is consistent with this defect explaining the other exclusions too. Establishing that every excluded run reaches this precise erroneous branch would require further per-case tracing; this report does not claim that was done.

**Small reproducible example.** The extracted block was reduced to this two-qubit circuit:

```python
qc = QuantumCircuit(2)
qc.cx(0, 1)
qc.cx(0, 1)
qc.x(0)
qc.cx(1, 0)
qc.cx(0, 1)
qc.cx(1, 0)
```

Its exact operator is the permutation matrix used by the upstream sign-regression test:

```text
U = [[0, 1, 0, 0],
     [0, 0, 0, 1],
     [1, 0, 0, 0],
     [0, 0, 1, 0]]
```

The reproducer tests both direct `TwoQubitBasisDecomposer(CXGate(), euler_basis="ZSX")` synthesis of this matrix and `TwoQubitPeepholeOptimization` on the six-gate circuit. It uses the `rz`, `sx`, `x`, `cx` basis and exact synthesis. Neither probe invokes SWAP absorption. It also retains a third probe for the original 17-gate extracted block.

| Probe | Baseline raw maximum error | Baseline phase-aligned error | Newer checkout raw maximum error |
|---|---:|---:|---:|
| Direct two-qubit decomposer | 2.0 | 2.48e-16 | 1.65e-15 |
| Six-gate peephole circuit | 2.0 | 2.48e-16 | 1.57e-15 |
| Extracted 17-gate block | 2.0 | 7.16e-16 | 7.84e-16 |

All three baseline probes fail strict equality and pass phase-insensitive equivalence. All three newer-checkout probes pass strict equality. Results and build identities are saved in [baseline_reproducer_results.json](results/baseline_reproducer_results.json).

Run this from the validation repository to reproduce both checkouts in separate interpreters:

```bash
../qiskit-evolved/.venv/bin/python reproduce_baseline_phase_bug.py \
  --baseline-src ../qiskit-swap-absorption-before \
  --candidate-src ../qiskit-swap-absorption \
  --out results/baseline_reproducer_results.json
```

This small reproducer does not need the saved 10-qubit input files. Its baseline records should show `operator_equal: false` and `equiv_up_to_global_phase: true`; the newer-checkout records should show both as true. It reports observations rather than treating the expected baseline failure as a command failure.

**Why the phase error is a bug despite unchanged standalone probabilities.** For a standalone circuit, `U|ψ⟩` and `−U|ψ⟩` give the same measurement probabilities. However, with block ordering determined by the control qubit:

```text
C(U)  = diag(I,  U)
C(−U) = diag(I, −U) = (Z_control ⊗ I) C(U)
```

The phase becomes relative between the control branches. The reproducer explicitly constructs these controlled matrices: on the baseline, they are unequal and are not even equivalent up to global phase; their maximum element difference is 2. On the newer checkout, the controlled matrices agree within tolerance. This is a concrete composability consequence, not evidence that the original standalone benchmark would produce different measurement counts.

**Relationship to SWAP absorption.** The selected baseline is an older Qiskit revision, not the immediate parent of the absorption change. There are **46 commits** between the baseline and that parent. The known synthesis fix is already in the parent, before the SWAP-absorption commit.

| Role | Revision |
|---|---|
| Baseline, dated 5 October 2026 | `abe422d0ad6eb0e3acbb2f4160a0d336ed4d26cb` |
| Known two-qubit synthesis fix | `9aa3717153a5ed3261d91e79c5b66325c6f8a653` |
| Immediate parent of the absorption change | `f70269034d2ec07699d6c69e33594273c73f8df2` |
| Checkout with absorption | `6b5145d2e94799ba3b01494a706537efc2925242` |

As an additional control, the first excluded level-2 case from each affected fixture was freshly run in the newer checkout, both with absorption enabled and with its pass disabled. All ten comparisons pass strict operator equality:

| Fixture | Seed | Absorption disabled: maximum error | Absorption enabled: maximum error |
|---|---:|---:|---:|
| `random10x60` | 1 | 1.12e-14 | 1.14e-14 |
| `qv10` | 123 | 8.13e-15 | 9.10e-15 |
| `random10x20` | 0 | 3.79e-15 | 4.66e-15 |
| `random10x40` | 0 | 5.86e-14 | 5.83e-14 |
| `qv10_seed7` | 0 | 1.53e-14 | 1.51e-14 |

See [disabled controls](results/baseline_candidate_disabled_controls.json) and [enabled controls](results/baseline_candidate_enabled_controls.json). These controls cover five representatives, not all 34 excluded combinations. In the original validation run, candidate execution was skipped for excluded cases. The evidence supports an older baseline synthesis defect; it does not support crediting SWAP absorption with fixing it.

**Reproducing the full exclusion audit.** The original prepared inputs are preserved under [results/baseline_inputs/](results/baseline_inputs/). Their hashes are checked against the original validation records before any circuit runs. Use:

```bash
../qiskit-evolved/.venv/bin/python diagnose_baseline.py \
  --qiskit-src ../qiskit-swap-absorption-before \
  --inputs-dir results/baseline_inputs \
  --out results/baseline_diagnostics.json
```

To reproduce the five newer-checkout controls with absorption disabled:

```bash
../qiskit-evolved/.venv/bin/python diagnose_baseline.py \
  --qiskit-src ../qiskit-swap-absorption \
  --inputs-dir results/baseline_inputs \
  --representatives --disable-absorption \
  --out results/baseline_candidate_disabled_controls.json
```

Omit `--disable-absorption` and use `--out results/baseline_candidate_enabled_controls.json` for the enabled controls. The optional `--archive-dir` flag can compare fresh outputs against an existing baseline-output archive; the audit recorded here also performed that check. The saved input files suffice for a fresh audit without that temporary archive.

**Environment and scope.** The investigation used Python **3.11.8** and NumPy **2.4.6**. Each source tree's own compiled extension was loaded, with the same source-cleanliness and extension-freshness checks as the validation runner. Both checkouts report Qiskit `2.7.0.dev0` with their respective revision suffixes.

| Checkout | Compiled `_accelerate` SHA-256 |
|---|---|
| Baseline | `d21180442265c1788a786a8cb5e2f52a0feaa06b490e72a0026cf18e09812472` |
| With absorption | `627034a679e9e709e36d8e4b5726a78731abc4c126622e7b884364f6bfd058f8` |

The 10-qubit checks retained the original settings: a bidirectional line, trivial identity initial layout, SABRE routing with the listed seed, translator translation, `rz/sx/x/cx` basis, `approximation_degree=1.0`, and `qubits_initially_zero=False`. Full provenance and input hashes are included in the diagnostic JSON files. This investigation added evidence and reproducers; it did not patch either Qiskit checkout or change the validation exclusions.

**Recommended follow-up.** For a comparison that isolates the absorption commit, use its immediate parent as the baseline, or update the older baseline to include the known synthesis correction and rebuild it. Then rerun the full validation before restoring excluded cases. Keep the phase-sensitive check: replacing it with `Operator.equiv` would conceal this defect. If reporting the issue upstream, reference the existing fix and these exact revisions rather than presenting it as an unreported defect in current Qiskit.
