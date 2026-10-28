# SWAP-absorption validation

This project checks that Qiskit's SWAP-absorption optimization preserves circuit
correctness and changes the generated gates. It compares the checkout with the
optimization against a baseline using the full transpilation pipeline.

The default run tests **132 cases**: 11 circuits with 10 qubits each, six seeds,
and optimization levels 2 and 3. Every circuit must achieve **6/6 pass; 6/6 changed**
at both levels.

For every case, the suite checks that:

- Both outputs preserve the original circuit's behavior, including global phase,
  after accounting for the qubit layout.
- Gates use connected qubits on a line and the requested gate set.
- SWAP absorption selects moves and changes the gates compared with both the
  baseline and the same checkout with absorption disabled.

All cases must pass. Gate counts and circuit depth are reported, but do not have
to improve in every case.

Both Qiskit checkouts need clean source trees and up-to-date builds. The Python
environment must have the required dependencies, including `ddt`. For fresh
checkouts, also run `python setup.py egg_info` in each using that environment.

Run from this folder:

```bash
../qiskit-evolved/.venv/bin/python run_validation.py \
  --qiskit-src ../qiskit-swap-absorption \
  --baseline-src ../qiskit-swap-absorption-before
../qiskit-evolved/.venv/bin/python make_report.py
```

Read [report.md](report.md) for the circuit list and results. Raw results are saved
in [results/results.jsonl](results/results.jsonl). Both scripts return a nonzero
exit code on failure.

For a smaller run, add `--seeds 0`. To test one level, add `--optimization-levels 2`
or `--optimization-levels 3` to the validation command.

Run the runner and report unit tests without Qiskit:

```bash
python3 -m unittest test_make_report test_run_validation
```
