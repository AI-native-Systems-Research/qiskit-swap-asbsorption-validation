#!/usr/bin/env python
"""Internal subprocess for transpiling identical inputs with the baseline checkout."""
import argparse
import hashlib
import json
from pathlib import Path

from run_validation import load_source, preset_manager, require


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qiskit-src", type=Path, required=True)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    environment = load_source(args.qiskit_src.resolve())
    from qiskit import qpy
    from qiskit.transpiler import CouplingMap

    request = json.loads(args.request.read_text())
    cases = []
    cmap = CouplingMap.from_line(10)
    for name, item in request["inputs"].items():
        error = item.get("error")
        digest = None
        try:
            require(error is None, error)
            path = Path(item["path"])
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            require(digest == item["sha256"], "Input circuit changed in transit")
            with path.open("rb") as stream:
                qc, = qpy.load(stream)
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException as exc:
            error = repr(exc)
        for level in request["optimization_levels"]:
            for seed in request["seeds"]:
                print(f"Baseline {name}, level {level}, seed {seed}", flush=True)
                rec = dict(fixture=name, optimization_level=level, seed=seed, input_sha256=digest)
                try:
                    require(error is None, error)
                    out = preset_manager(qc, cmap, seed, level).run(qc.copy())
                    path = args.out.parent / f"baseline-{name}-{level}-{seed}.qpy"
                    with path.open("wb") as stream:
                        qpy.dump(out, stream, version=13)
                    rec["output"] = str(path)
                except (KeyboardInterrupt, SystemExit):
                    raise
                except BaseException as exc:
                    rec["error"] = repr(exc)
                cases.append(rec)
    args.out.write_text(json.dumps(dict(environment=environment, cases=cases)))


if __name__ == "__main__":
    main()
