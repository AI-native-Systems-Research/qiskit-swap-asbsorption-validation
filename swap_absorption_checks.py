"""Small, deterministic checks of SWAP absorption and its preset integration.

Qiskit imports stay lazy so the runner can select its source tree first.
"""

from functools import partial


FEATURE_CHECKS = (
    "preset_translation_levels",
    "translation_helper_levels",
    "commuting_later_interaction",
    "commuting_earlier_interaction",
    "cx_rz_cx_block",
    "diagonal_unitary",
    "noncommuting_guard",
    "barrier_guard",
    "terminal_wire_guard",
    "intervening_swap_guard",
)


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _check_absorption_placement(manager, enabled, description):
    from qiskit.passmanager import BaseController
    from qiskit.transpiler.preset_passmanagers._swap_absorption import _AbsorbIntoSwaps

    def contains_absorption(tasks):
        for task in tasks:
            if isinstance(task, _AbsorbIntoSwaps):
                return True
            if isinstance(task, BaseController) and contains_absorption(task.tasks):
                return True
        return False

    tasks = manager.to_flow_controller().tasks
    if enabled:
        _require(
            bool(tasks) and isinstance(tasks[0], _AbsorbIntoSwaps),
            f"Absorption must be the first translation pass: {description}",
        )
    else:
        _require(
            not contains_absorption(tasks),
            f"Absorption must be absent from translation: {description}",
        )


def _preset_levels():
    from qiskit.transpiler import CouplingMap, generate_preset_pass_manager

    for method in ("translator", "synthesis"):
        for level in range(4):
            manager = generate_preset_pass_manager(
                optimization_level=level,
                basis_gates=["cx", "rz", "sx", "x"],
                coupling_map=CouplingMap.from_line(3),
                translation_method=method,
                seed_transpiler=7,
            )
            _check_absorption_placement(
                manager.translation, level in (2, 3), f"{method}, level {level}"
            )


def _helper_levels():
    from qiskit.transpiler.preset_passmanagers import common

    # The no-target branch must still add the pass when a level requests it.
    for basis in (None, ["cx", "rz", "sx", "x"]):
        default = common.generate_translation_passmanager(None, basis_gates=basis)
        _check_absorption_placement(default, False, f"default helper, basis {basis}")
        for level in (None, 0, 1, 2, 3):
            manager = common.generate_translation_passmanager(
                None, basis_gates=basis, optimization_level=level
            )
            _check_absorption_placement(
                manager, level in (2, 3), f"helper level {level}, basis {basis}"
            )


def _check_circuit(kind):
    from qiskit import QuantumCircuit
    from qiskit.quantum_info import Operator
    from qiskit.transpiler import PassManager
    from qiskit.transpiler.preset_passmanagers._swap_absorption import _AbsorbIntoSwaps

    circuit = QuantumCircuit(3)
    circuit.global_phase = 0.19
    moving = kind in ("later", "earlier", "block", "unitary")

    if kind in ("earlier", "terminal", "swap"):
        circuit.cp(0.37, 0, 1)
        if kind == "swap":
            circuit.swap(0, 2)
        else:
            circuit.cz(0, 2)
        circuit.swap(0, 1)
    else:
        circuit.swap(0, 1)
        if kind == "noncommuting":
            circuit.h(0)
        elif kind == "barrier":
            circuit.barrier(0, 1)
        else:
            circuit.cz(0, 2)
        if kind == "block":
            circuit.cx(0, 1)
            circuit.rz(0.37, 1)
            circuit.cx(0, 1)
        elif kind == "unitary":
            # A diagonal two-qubit unitary with exact zeros off its diagonal.
            circuit.unitary(
                [[1, 0, 0, 0], [0, 1j, 0, 0], [0, 0, -1, 0], [0, 0, 0, -1j]],
                [0, 1],
            )
        else:
            circuit.cp(0.37, 0, 1)

    circuit.cx(0, 2)
    if kind == "terminal":
        # Qubit 1 has only single-qubit work after the SWAP.
        circuit.h(1)
    else:
        circuit.cx(1, 2)

    output = PassManager([_AbsorbIntoSwaps()]).run(circuit)
    _require(Operator(output) == Operator(circuit), "Full operator or global phase changed")
    _require(dict(output.count_ops()) == dict(circuit.count_ops()), "Pass changed the gate inventory")
    _require((output != circuit) == moving, "Unexpected movement or missing expected movement")
    if moving:
        # Every operation in these fixtures touches qubit 0 or 1. Check the moved
        # interaction directly beside the SWAP, including all three block gates.
        swap = next(i for i, inst in enumerate(output.data) if inst.operation.name == "swap")
        if kind == "earlier":
            _require(swap > 0, "No interaction before SWAP")
            neighbor = output.data[swap - 1]
            _require(neighbor.operation.name == "cp", "CP did not reach SWAP")
            _require(neighbor.qubits == output.data[swap].qubits, "Wrong neighboring pair")
        else:
            names = [inst.operation.name for inst in output.data[swap + 1:]]
            expected = ["cx", "rz", "cx"] if kind == "block" else [
                "unitary" if kind == "unitary" else "cp"
            ]
            _require(names[:len(expected)] == expected, "Interaction did not reach SWAP")


def feature_checks():
    """Return one pass/fail record for each targeted feature check.

    Exceptions, including Python-visible Rust panics, fail the current check;
    interruption and explicit process exits are allowed to propagate.
    """
    checks = (
        _preset_levels,
        _helper_levels,
        partial(_check_circuit, "later"),
        partial(_check_circuit, "earlier"),
        partial(_check_circuit, "block"),
        partial(_check_circuit, "unitary"),
        partial(_check_circuit, "noncommuting"),
        partial(_check_circuit, "barrier"),
        partial(_check_circuit, "terminal"),
        partial(_check_circuit, "swap"),
    )
    records = []
    for name, check in zip(FEATURE_CHECKS, checks, strict=True):
        record = dict(record="feature_check", name=name, passed=False)
        try:
            check()
            record["passed"] = True
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException as exc:
            record["error"] = repr(exc)
        records.append(record)
    return records
