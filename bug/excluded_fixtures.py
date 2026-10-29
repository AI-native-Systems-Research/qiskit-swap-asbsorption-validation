"""Historical circuit builders for investigating the cases in excluded_cases.md.

These builders are used only by the baseline diagnostic tool. They restore the
original 16-circuit order so circuit naming and QPY serialization are reproducible.
"""
from functools import partial

from run_validation import fixtures as validation_fixtures


def fixtures():
    """Return the original matrix, including the five removed circuit families."""
    import numpy as np
    from qiskit import QuantumCircuit
    from qiskit.quantum_info import random_unitary
    from test.benchmarks import utils

    def quantum_volume(seed):
        """The original QV-style diagonal/Haar variant, with fixed random pairings."""
        rng = np.random.default_rng(seed)
        qc = QuantumCircuit(10)
        for layer in range(10):
            perm = rng.permutation(10)
            for k in range(0, 10, 2):
                pair = [int(perm[k]), int(perm[k + 1])]
                if layer % 3 != 2:
                    phases = rng.uniform(0, 2 * np.pi, 4)
                    qc.unitary(np.diag(np.exp(1j * phases)), pair)
                else:
                    qc.append(random_unitary(4, seed=rng), pair)
        output = QuantumCircuit(10)
        output.compose(qc.remove_final_measurements(inplace=False),
                       [4, 6, 2, 7, 3, 5, 9, 0, 8, 1], inplace=True)
        return output

    random = partial(utils.random_circuit, 10, max_operands=2,
                     measure=False, conditional=False, reset=False)
    builders = validation_fixtures()
    builders.update({
        "random10x60": partial(random, 60, seed=42),
        "qv10": partial(quantum_volume, 42),
        "random10x20": partial(random, 20, seed=7),
        "random10x40": partial(random, 40, seed=123),
        "qv10_seed7": partial(quantum_volume, 7),
    })
    order = ("qft10", "random10x60", "adder4", "ring10", "qv10", "dtc10",
             "random10x20", "random10x40", "qv10_seed7", "bv10", "bvlike10",
             "trotter10", "qaoa10", "grover10", "mcx10", "modular_adder10")
    return {name: builders[name] for name in order}
