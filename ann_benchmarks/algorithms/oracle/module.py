import inspect
import os
import time
import array
from itertools import chain
from multiprocessing.pool import Pool

import numpy
import oracledb

from ..base.module import BaseANN


def get_connection(conn_args):
    """Open a connection to Oracle using the thin driver (no Instant Client needed)."""
    return oracledb.connect(
        user=conn_args["user"],
        password=conn_args["password"],
        dsn=conn_args["dsn"],
    )


def to_vector(v):
    """Convert a numpy row into the array type python-oracledb expects for VECTOR binds."""
    return array.array("f", v)


# ORA error codes worth retrying on during concurrent inserts: 60 = deadlock
# detected, 54 = resource busy (NOWAIT), 4021 = timeout on locked object,
# 25408 = replay retry needed (transparent app continuity). Anything else
# (e.g. constraint violations, bad data) should still raise immediately -
# retrying those would just hide a real bug.
_RETRYABLE_ORA_CODES = {60, 54, 4021, 25408}


def many_inserts(arg):
    """Worker used by fit() when running in --batch mode: each process gets its own
    connection and inserts a slice of the training set."""
    conn_args, start_id, rows = arg
    conn = get_connection(conn_args)
    cur = conn.cursor()
    lenX = len(rows)
    rps = 100
    start_time = time.time()
    for i, embedding in enumerate(rows):
        while True:
            try:
                cur.execute(
                    "INSERT INTO t1 (id, v) VALUES (:1, :2)",
                    [start_id + i, to_vector(embedding)],
                )
                break
            except oracledb.DatabaseError as e:
                (error_obj,) = e.args
                if getattr(error_obj, "code", None) in _RETRYABLE_ORA_CODES:
                    # Mirrors the MariaDB module's backoff-and-retry approach
                    # for OperationalError under write contention.
                    time.sleep(0.01 * (11 + start_id * 17 % 13))
                    continue
                raise
        if start_id == 0 and (i + 1) % int(rps + 1) == 0:
            rps = i / (time.time() - start_time + 1e-9)
            print(f"{i:6d} of {lenX}, {rps:4.2f} stmt/sec, ETA {(lenX - i) / rps:.0f} sec")
    conn.commit()
    cur.close()
    conn.close()


def many_queries(arg):
    """Worker used by batch_query(): each process gets its own connection and runs
    a slice of the query set."""
    conn_args, metric, n, rows = arg
    conn = get_connection(conn_args)
    cur = conn.cursor()
    res = []
    for v in rows:
        cur.execute(
            f"""
            SELECT id FROM t1
            ORDER BY VECTOR_DISTANCE(v, :1, {metric})
            FETCH FIRST :2 ROWS ONLY
            """,
            [to_vector(v), n],
        )
        res.append([row[0] for row in cur.fetchall()])
    cur.close()
    conn.close()
    return res


# Map ann-benchmarks metric names to Oracle's VECTOR_DISTANCE metric keywords.
_METRIC_MAP = {
    "angular": "COSINE",
    "euclidean": "EUCLIDEAN",
}


class Oracle(BaseANN):
    def __init__(self, metric, method_param):
        if metric not in _METRIC_MAP:
            raise RuntimeError(f"unknown metric {metric}")
        self._metric = _METRIC_MAP[metric]

        # Index build parameters (swept via config.yml arg_groups)
        self._neighbors = method_param.get("neighbors", 32)
        self._ef_construction = method_param.get("efConstruction", 100)
        self._target_accuracy = method_param.get("target_accuracy", 95)

        self._ef_search = None
        self._size = 0
        self._batch_concurrency = int(os.environ.get("ORACLE_BATCH_CONCURRENCY", str(os.cpu_count())))

        # Whether run.py was invoked with --batch. Mirrors the MariaDB
        # module's stack-inspection trick - the framework doesn't pass this
        # through the constructor, so it's read off the caller's frame.
        # Used to pick between the parallel (--batch) and serial insert
        # paths in fit(), matching MariaDB's behavior instead of always
        # running the parallel path regardless of the flag.
        try:
            self._batch = inspect.stack()[2].frame.f_locals["batch"]
        except (IndexError, KeyError):
            self._batch = False
        print(f"--batch is {self._batch}")

        # Connect to an already-running Oracle instance. No local-instance
        # lifecycle management here (unlike the MariaDB module) - Oracle isn't
        # something we build/start from scratch for a benchmark run.
        conn_str = os.environ.get("ORACLE_CONN_ARGS")
        if conn_str is None:
            raise RuntimeError(
                "Please set ORACLE_CONN_ARGS as 'user:password:dsn' "
                "(dsn e.g. 'host:1521/FREEPDB1')"
            )
        parts = conn_str.split(":", 2)
        if len(parts) != 3:
            raise RuntimeError(f"Could not parse ORACLE_CONN_ARGS - {conn_str}")
        self._conn_args = {"user": parts[0], "password": parts[1], "dsn": parts[2]}

        self._conn = get_connection(self._conn_args)
        self._cur = self._conn.cursor()

    def fit(self, X):
        dim = len(X[0])

        print("\nPreparing table...")
        try:
            self._cur.execute("DROP TABLE t1 PURGE")
        except oracledb.DatabaseError:
            pass  # table may not exist yet

        self._cur.execute(
            f"""
            CREATE TABLE t1 (
                id NUMBER PRIMARY KEY,
                v VECTOR({dim}, FLOAT32) NOT NULL
            )
            """
        )
        self._conn.commit()

        print("\nInserting data...")
        start_time = time.time()

        if self._batch:
            # Parallel insert across processes, one connection per worker -
            # mirrors the MariaDB module's approach, since DB
            # connections/cursors shouldn't be shared across processes.
            concur = self._batch_concurrency
            chunks = []
            for i in range(concur):
                lo = int(len(X) / concur * i)
                hi = int(len(X) / concur * (i + 1))
                chunks.append((self._conn_args, lo, X[lo:hi]))
            with Pool(concur) as pool:
                pool.map(many_inserts, chunks)
        else:
            # Serial insert on the module's own connection when --batch
            # wasn't passed - mirrors MariaDB's non-batch path. Useful for
            # single-threaded debugging/comparison runs, and means the
            # --batch flag actually changes Oracle's behavior instead of
            # always running the parallel path either way.
            rps, rows, last = 1000, 0, time.time()
            for i, embedding in enumerate(X):
                self._cur.execute(
                    "INSERT INTO t1 (id, v) VALUES (:1, :2)",
                    [i, to_vector(embedding)],
                )
                if i - rows > rps:
                    now = time.time()
                    rps = ((i - rows) / (now - last) + 19 * rps) / 20
                    eta = (len(X) - i) / rps
                    last, rows = now, i
                    print(f"{i:6_} of {len(X):_}, {rps:4.2f} stmt/sec, ETA {eta:.0f} sec")
            self._conn.commit()

        load_secs = time.time() - start_time
        print(f"\nInsert time for {X.size:_} values: {load_secs:7.2f}s")

        print("\nCreating vector index...")
        start_time = time.time()
        ddl = f"""
            CREATE VECTOR INDEX t1_v_idx ON t1 (v)
            ORGANIZATION INMEMORY NEIGHBOR GRAPH
            DISTANCE {self._metric}
            WITH TARGET ACCURACY {self._target_accuracy}
            PARAMETERS (
                TYPE HNSW,
                NEIGHBORS {self._neighbors},
                EFCONSTRUCTION {self._ef_construction}
            )
        """
        self._cur.execute(ddl)
        self._conn.commit()
        index_secs = time.time() - start_time
        print(f"Index build time: {index_secs:7.2f}s")

        self._size = self._get_vector_pool_usage()
        table_size = self._get_segment_size("T1")
        MB = 1024 * 1024
        print(
            f"table {table_size / MB:.1f} MB in {load_secs:.1f}s, "
            f"index {self._size / MB:.1f} MB in {index_secs:.1f}s"
        )

    def _get_vector_pool_usage(self):
        """Oracle's IN MEMORY NEIGHBOR GRAPH vector index is memory-resident and
        doesn't register as a normal on-disk segment in USER_SEGMENTS, unlike
        MariaDB's InnoDB-backed index - V$VECTOR_MEMORY_POOL is the real
        source for its memory footprint."""
        try:
            self._cur.execute(
                "SELECT SUM(used_bytes) FROM v$vector_memory_pool WHERE con_id = SYS_CONTEXT('USERENV', 'CON_ID')"
            )
            row = self._cur.fetchone()
            return int(row[0]) if row and row[0] is not None else 0
        except oracledb.DatabaseError as e:
            print(f"Warning: could not read vector memory pool usage: {e}")
            return 0

    def _get_segment_size(self, segment_name):
        try:
            self._cur.execute(
                "SELECT bytes FROM user_segments WHERE segment_name = :1",
                [segment_name],
            )
            row = self._cur.fetchone()
            return int(row[0]) if row else 0
        except oracledb.DatabaseError as e:
            print(f"Warning: could not read segment size for {segment_name}: {e}")
            return 0

    def set_query_arguments(self, ef_search):
        self._ef_search = ef_search
        # Oracle's HNSW query-time search width is passed per-query via a hint
        # rather than a session variable - see query()/many_queries() below.

    def query(self, v, n):
        self._cur.execute(
            f"""
            SELECT /*+ VECTOR_INDEX_EFFICIENT_SEARCH({self._ef_search}) */ id
            FROM t1
            ORDER BY VECTOR_DISTANCE(v, :1, {self._metric})
            FETCH FIRST :2 ROWS ONLY
            """,
            [to_vector(v), n],
        )
        return [row[0] for row in self._cur.fetchall()]

    def batch_query(self, X, n):
        concur = self._batch_concurrency
        chunks = []
        for i in range(concur):
            lo = int(len(X) / concur * i)
            hi = int(len(X) / concur * (i + 1))
            chunks.append((self._conn_args, self._metric, n, X[lo:hi]))
        with Pool(concur) as pool:
            self._res = pool.map(many_queries, chunks)

    def get_batch_results(self):
        return chain(*self._res)

    def get_memory_usage(self):
        return self._size / 1024  # kB, matches BaseANN's convention

    def get_additional(self):
        return {
            "neighbors": self._neighbors,
            "efConstruction": self._ef_construction,
        }

    def __str__(self):
        return f"Oracle(neighbors={self._neighbors}, ef_search={self._ef_search})"

    def done(self):
        self._cur.close()
        self._conn.close()