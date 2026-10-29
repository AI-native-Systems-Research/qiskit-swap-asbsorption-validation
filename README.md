# SWAP-absorption validation

This project checks that Qiskit's SWAP-absorption optimization preserves circuit
correctness and changes the generated gates. It uses **one Qiskit checkout**, with
absorption enabled and disabled, through the full transpilation pipeline.

For each circuit, transpiler seed, and optimization level, the runner:

1. **Transpiles with absorption disabled and enabled.** Both runs receive copies
   of the same prepared input and use identical settings.
2. **Checks both outputs against the original input circuit.** Each must preserve
   its full unitary, including global phase, after accounting for the qubit layout.
   Both must also preserve circuit width and use connected qubits on a line and
   the requested gate set (`rz`, `sx`, `x`, `cx`). Failure of either output fails
   the case.
3. **Checks that absorption has an effect.** The enabled run must execute the pass,
   select absorption moves, and produce a different gate structure from the
   disabled run. The comparison includes gates, parameters, and qubit connections;
   phase-only or layout-only differences do not count as changed gates.

To disable absorption, [run_validation.py](run_validation.py) temporarily replaces
`_AbsorbIntoSwaps.run` with `lambda self, dag: dag`: the pass returns the circuit
graph unchanged. All other passes still execute. Python's `patch.object` restores
the original method when that run ends, including on errors; Qiskit's source files
are not modified. This comparison isolates the effect of the pass within one
revision, so a separate baseline repository is unnecessary.

The default run tests **132 cases**: 11 circuits with 10 qubits each, six seeds,
and optimization levels 2 and 3. Every circuit must achieve **6/6 pass; 6/6 changed**
at both levels.

All cases must pass. In the report, "changed" means the enabled and disabled gate
structures differ. Gate-count and depth totals show **absorption disabled → enabled**;
they do not have to improve in every case. The report records the checkout's
revision and build identity.

The Qiskit checkout must contain the absorption implementation, have a clean
source tree, and have an up-to-date build. The Python environment must have the
required dependencies, including `ddt`. For a fresh checkout, also run
`python setup.py egg_info` in it using that environment.

Run validation from this folder and generate the report on success:

```bash
../qiskit-evolved/.venv/bin/python run_validation.py \
  --qiskit-src ../qiskit-swap-absorption && \
../qiskit-evolved/.venv/bin/python make_report.py
```

Read [report.md](report.md) for the circuit list and results. Raw results are saved
in [results/results.jsonl](results/results.jsonl). Both scripts return a nonzero
exit code on failure. Results now use schema version 4; rerun validation to replace
results from the earlier workflow before generating a report.
Starting validation marks its results file as incomplete until the run finishes.
A setup failure or interrupted run leaves incomplete results, which the reporter
rejects.

For a smaller run, add `--seeds 0`. To test one level, add `--optimization-levels 2`
or `--optimization-levels 3` to the validation command.

Run the runner and report unit tests without Qiskit:

```bash
python3 -m unittest test_make_report test_run_validation
```

The [older baseline investigation](bug/baseline_bug_report.md) and its reproducer
scripts remain under `bug/` as historical material, separate from this validation.
