"""
Drives the actual Oracle class from module.py through its real lifecycle -
this is what the framework itself will call, so it validates the class
wiring and the multiprocessing insert/query paths that smoke_test.py's raw
SQL calls didn't exercise.

Usage (run from the ann-benchmarks repo root so the package import resolves):
    export ORACLE_CONN_ARGS="annbmrk_app:<password>:129.40.76.82:1531/annbmrkpdb"
    python3 -m ann_benchmarks.algorithms.oracle.module_test

Note: the whole body is wrapped in `if __name__ == "__main__":` - required
on macOS/Windows where multiprocessing uses the 'spawn' start method.
Without this guard, each spawned worker process re-imports and re-executes
this file from the top, which re-triggers fit()'s own Pool creation and
cascades into runaway process spawning.
"""

import numpy

from ann_benchmarks.algorithms.oracle.module import Oracle


def main():
    print("Generating small synthetic dataset...")
    rng = numpy.random.default_rng(7)
    DIM = 16
    N_TRAIN = 200
    N_QUERIES = 10

    X_train = rng.random((N_TRAIN, DIM), dtype=numpy.float32)
    X_queries = rng.random((N_QUERIES, DIM), dtype=numpy.float32)

    print("Instantiating Oracle(metric='euclidean', method_param={neighbors, efConstruction})...")
    algo = Oracle(
        metric="euclidean",
        method_param={"neighbors": 16, "efConstruction": 100, "target_accuracy": 95},
    )

    print("\nCalling fit() - this exercises the multiprocessing insert path (many_inserts)...")
    algo.fit(X_train)

    print("\nCalling set_query_arguments(ef_search=40)...")
    algo.set_query_arguments(40)

    print("\nCalling query() for a single vector...")
    result = algo.query(X_queries[0], 5)
    print(f"  single query result: {result}")

    print("\nCalling batch_query() - this exercises the multiprocessing query path (many_queries)...")
    algo.batch_query(X_queries, 5)
    batch_results = list(algo.get_batch_results())
    print(f"  batch results ({len(batch_results)} queries):")
    for i, r in enumerate(batch_results):
        print(f"    query {i}: {r}")

    print(f"\nget_memory_usage(): {algo.get_memory_usage():.1f} kB")
    print(f"get_additional(): {algo.get_additional()}")
    print(f"__str__(): {algo}")

    print("\nCalling done()...")
    algo.done()

    print("\nAll steps completed without exceptions.")


if __name__ == "__main__":
    main()