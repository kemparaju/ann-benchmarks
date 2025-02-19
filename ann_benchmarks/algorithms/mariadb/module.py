import getpass
import glob
import os
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import time
from multiprocessing.pool import Pool
from itertools import chain
import inspect
import numpy

import mariadb

from ..base.module import BaseANN

def many_inserts(arg):
    conn = mariadb.connect(unix_socket=arg[0])
    cur = conn.cursor()
    cur.execute("USE ann")
    cur.execute("SET rand_seed1=1, rand_seed2=2")
    rps = 100
    lenX= len(arg[2])
    start_time = time.time()
    for i, embedding in enumerate(arg[2]):
        while True:
            try:
                cur.execute("INSERT INTO t1 (id, v) VALUES (%d, %s)", (i+arg[1], vector_to_hex(embedding)))
                break
            except mariadb.OperationalError:
                time.sleep(0.01*(11+arg[1]*17%13))
                pass
        if arg[1] == 0 and (i + 1) % int(rps + 1) == 0:
            rps=i/(time.time()-start_time)
            print(f"{i:6d} of {lenX}, {rps:4.2f} stmt/sec, ETA {(lenX-i)/rps:.0f} sec")
    cur.execute("commit")

def many_queries(arg):
    conn = mariadb.connect(unix_socket=arg[0])
    cur = conn.cursor()
    cur.execute("SET mhnsw_ef_search = %d" % arg[1])
    cur.execute("USE ann")
    res = []
    for v in arg[4]:
        cur.execute(f"SELECT id FROM t1 ORDER by vec_distance_{arg[2]}(v, %s) LIMIT %d", (vector_to_hex(v), arg[3]))
        res.append([id for id, in cur.fetchall()])
    return res

def vector_to_hex(v):
    return numpy.array(v, 'float32').tobytes()

class MariaDB(BaseANN):

    def __init__(self, metric, method_param):
        print ("line 58, module.py, method_param: ", method_param)
        print ("line 59, module.py, metring: ", metric)
        self._test_time = time.strftime("%Y-%m-%d-%H-%M-%S", time.localtime())
        self._metric = metric
        self._m = method_param['M']
        self._cur = None
        self._perf_proc = None
        self._perf_records = []
        self._perf_stats = []
        self._batch = inspect.stack()[2].frame.f_locals['batch'] # Yo-ho-ho!
        self._engine = method_param['engine']
        self._size = 0

        if metric == "angular":
            self._metric='cosine'
        elif metric == "euclidean":
            self._metric='euclidean'
        else:
            raise RuntimeError(f"unknown metric {metric}")
        
        self.prepare_perf()
        # self.prepare_options()
        # self.initialize_db()
        # self.start_db()

        # Connect to MariaDB using Unix socket
        # conn = mariadb.connect(unix_socket=self._socket_file)
        conn = mariadb.connect(
                host="129.40.75.147",
                port=3306,
               )
        self._cur = conn.cursor()

    def prepare_perf(self):
        self._perf_stat = os.environ.get('PERF', 'no') == 'yes' and MariaDB.can_run_perf()
        self._perf_record = os.environ.get('FLAMEGRAPH', 'no') == 'yes' and MariaDB.can_run_flamegraph()

        if self._perf_stat and self._perf_record:
            self._perf_stat = False
            print("\nWarning: Better not to enable both PERF and FLAMEGRAPH. Generating a flame graph only.\n") 

    def prepare_options(self):
        self._perf_stat = os.environ.get('PERF', 'no') == 'yes' and MariaDB.can_run_perf()
        self._perf_record = os.environ.get('FLAMEGRAPH', 'no') == 'yes' and MariaDB.can_run_flamegraph()

        if self._perf_stat and self._perf_record:
            self._perf_stat = False
            print("\nWarning: Better not to enable both PERF and FLAMEGRAPH. Generating a flame graph only.\n")

        # MariaDB build dir or installed dir
        mariadb_root_dir = os.environ.get('MARIADB_ROOT_DIR')
        if mariadb_root_dir is None:
            print("MariaDB path MARIADB_ROOT_DIR is not provided. It can be your local build dir or installation dir. "
                  "For local build dir, you also need to specify the MARIADB_SOURCE_DIR for database initialization.")
            raise RuntimeError(f"Could not initialize database.")

        # mariadb-install-db needs `--srcdir` option for local builds
        mariadb_source_dir = os.environ.get('MARIADB_SOURCE_DIR')

        # Initialize database when running locally
        self._do_init_mariadb = os.environ.get('DO_INIT_MARIADB', 'yes') == 'yes'

        # DB workfolder: data + error log
        mariadb_db_workspace = os.environ.get('MARIADB_DB_WORKSPACE')
        if mariadb_db_workspace is None:
            raise RuntimeError("Please specify path MARIADB_DB_WORKSPACE to define the database directory.")
        data_dir = mariadb_db_workspace + '/data'
        log_file = mariadb_db_workspace + '/mariadb.err'
        # Create data directory if not exist
        os.makedirs(f"{data_dir}", exist_ok=True)

        # Generate a socket file name under /tmp to make sure the file path is always under 107 character, to avoid "The socket file path is too long" error
        dset=inspect.stack()[3].frame.f_locals['dataset_name'] + '-'
        self._socket_file = tempfile.mktemp(prefix=dset, suffix='.sock', dir='/tmp')

        print("\nSetup paths:")
        print(f"MARIADB_ROOT_DIR: {mariadb_root_dir}")
        print(f"DATA_DIR: {data_dir}")
        print(f"LOG_FILE: {log_file}")
        print(f"SOCKET_FILE: {self._socket_file}\n")

        # Command for MariaDB initialization
        self._mariadb_init_cmd = [
            glob.glob(f"{mariadb_root_dir}/*/mariadb-install-db")[0],
            "--no-defaults",
            "--verbose",
            "--skip-name-resolve",
            "--skip-test-db",
            f"--datadir={data_dir}"
        ]
        if mariadb_source_dir is not None:
            self._mariadb_init_cmd.append(f"--srcdir={mariadb_source_dir}")

        # Command for starting MariaDB server
        self._mariadb_start_cmd = [
            #'perf','record','-g', '--user-callchains', '--timestamp-filename', '--output=perf.perf',
            #'rr','record','-h',
            glob.glob(f"{mariadb_root_dir}/*/mariadbd")[0],
            "--no-defaults",
            f"--datadir={data_dir}",
            f"--log_error={log_file}",
            f"--socket={self._socket_file}",
            "--skip_networking",
            "--loose-innodb-buffer-pool-size=16G",
            "--loose-mhnsw-cache-size=10G",
            f"--mhnsw_default_m={self._m}",
            "--skip_grant_tables"
        ]
        user_option = MariaDB.get_user_option()
        if user_option is not None:
            self._mariadb_start_cmd += user_option
        self._mariadbd_proc = None

    def initialize_db(self):
        try:
            # In ann-benchmarks build, the server was initialized in Docker image, but when running locally we want to start it with a new initialization
            if self._do_init_mariadb:
                print("\nInitialize MariaDB database...")
                print(self._mariadb_init_cmd)
                #subprocess.run(self._mariadb_init_cmd, shell=True, check=True, stdout=sys.stdout, stderr=sys.stderr)
                init_proc = subprocess.Popen(self._mariadb_init_cmd, stdout=sys.stdout, stderr=sys.stderr)
                init_proc.wait()
        except Exception as e:
            print("ERROR: Failed to initialize MariaDB database:", e)
            raise

    @staticmethod
    def get_user_option():
        # to support running with root user
        try:
            return ["--user=root"] if getpass.getuser() == "root" else None
        except Exception as e:
            print("Could not get current user, could be docker user mapping. Ignore.")
            return None

    @staticmethod
    def can_run_perf():
        try:
            subprocess.run(["perf", "record", "echo", "testing perf"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except FileNotFoundError:
            print("Warning: perf command not found. Skipping.")
        except Exception as e:
            print(f"Warning: perf does not have permission to run. Skipping. Error: {e}")
        return False

    @staticmethod
    def can_run_flamegraph():
        if not MariaDB.can_run_perf():
            return False
        if not shutil.which("stackcollapse-perf.pl"):
            print("Warning: Command 'stackcollapse-perf.pl' missing. Skipping.")
            return False
        if not shutil.which("flamegraph.pl"):
            print("Warning: Command 'flamegraph.pl' missing. Skipping.")
            return False
        return True

    def perf_start(self, name):
        if not self._perf_record and not self._perf_stat:
            return

        # Clean up previous record if any
        self.perf_stop()

        if self._perf_record:
            record_name = f"perf.data.{name}.{self._test_time}"
            # perf record -p $(pidof mariadbd) --freq=99 --output=perf.data.{name} --timestamp-filename"
            self._perf_proc = subprocess.Popen([
                "perf",
                "record",
                "-p",
                f"{self._mariadbd_proc.pid}",
                "-g",
                "--freq=100",
                "--output=results/" + record_name,
            ], stdout=sys.stdout, stderr=sys.stderr)
            self._perf_records.append(record_name)
        elif self._perf_stat:
            stat_name = f"perf.stat.{name}.{self._test_time}"
            self._perf_proc = subprocess.Popen([
                "perf",
                "stat",
                "-x,", # split by comma
                f"--output=results/{stat_name}",
                "-p",
                f"{self._mariadbd_proc.pid}"
            ], stdout=sys.stdout, stderr=sys.stderr)
            self._perf_stats.append(stat_name)

    def perf_stop(self):
        if (self._perf_record or self._perf_stat) and self._perf_proc is not None:
            self._perf_proc.send_signal(signal.SIGINT) # perf needs to be terminated gracefully with SIGINT
            try:
                self._perf_proc.wait(10)
                print("\nPerf process terminated.")
            except subprocess.TimeoutExpired:
                print("\nError: Perf process did not terminate within the timeout period.")
            self._perf_proc = None

    def perf_analysis(self):
        if self._perf_record:
            for record in self._perf_records:
                try:
                    flamegraph_cmd = f"perf script -i results/{record} | stackcollapse-perf.pl | flamegraph.pl > results/{record}.svg"
                    subprocess.run(flamegraph_cmd, shell=True, check=True, stdout=sys.stdout, stderr=sys.stderr)
                except subprocess.CalledProcessError as e:
                    print(f"Error: Failed to generate flame graph. Command '{e.cmd}' returned non-zero exit status {e.returncode}.")
        if self._perf_stat:
            for stat_file in self._perf_stats:
                try:
                    with open(f"results/{stat_file}", 'r') as file:
                        values = [int(line.split(',')[0]) for line in file if 'cpu_core/cycles/' in line or 'cpu_atom/cycles/' in line]
                        print(f"CPU cycles in {stat_file}: {sum(values):,.0f}" if values else "Error: No CPU cycle data found.")
                except (FileNotFoundError, IOError):
                    print("Error reading the perf stat file.")

    def start_db(self):
        try:
            print("\nStarting MariaDB server...")
            print(self._mariadb_start_cmd)
            self._mariadbd_proc = subprocess.Popen(self._mariadb_start_cmd, stdout=sys.stdout, stderr=sys.stderr)
        except Exception as e:
            print("ERROR: Failed to start MariaDB database:", e)
            raise

        # Server is expected to start in less than 30s
        start_time = time.time()
        while True:
            if time.time() - start_time > 30:
                raise TimeoutError("Timeout waiting for MariaDB server to start")
            try:
                if os.path.exists(self._socket_file):
                    print("\nMariaDB server started!")
                    break
            except FileNotFoundError:
                pass
            time.sleep(1)

    def fit(self, X):
        # Prepare database and table
        print("\nPreparing database and table...")
        self._cur.execute("DROP DATABASE IF EXISTS ann")
        self._cur.execute("CREATE DATABASE ann")
        self._cur.execute("USE ann")
        self._cur.execute(f"""
          CREATE TABLE t1 (
            id INT PRIMARY KEY,
            v VECTOR({len(X[0])}) NOT NULL,
            VECTOR INDEX (v) DISTANCE={self._metric}
          ) MIN_ROWS={len(X)} ENGINE={self._engine}
        """)

        # Insert data
        print("\nInserting data...")

        start_time = time.time()
        if self._batch:
            XX=[]
            for i in range(os.cpu_count()):
                n = int(len(X)/os.cpu_count()*i)
                XX.append((self._socket_file, n, X[n:int(len(X)/os.cpu_count()*(i+1))]))
            pool = Pool()
            self._res = pool.map(many_inserts, XX)
        else:
            self.perf_start("inserting")
            self._cur.execute("SET rand_seed1=1, rand_seed2=2")
            rps, rows, last, total=1000, 0, time.time(), 1
            for i, embedding in enumerate(X):
                self._cur.execute("INSERT INTO t1 (id, v) VALUES (%d, %s)", (i, vector_to_hex(embedding)))
                if i - rows > rps:
                    now=time.time()
                    rps=((i-rows)/(now-last) + 19*rps)/20
                    eta=(len(X)-i)/rps
                    total=now-start_time+eta
                    last, rows=now, i
                    print(f"{i:6_} of {len(X):_}, {rps:4.2f} stmt/sec ETA {eta:.0f} of {total:.0f} sec")
            self._cur.execute("commit")
            self.perf_stop()
        print(f"\nInsert time for {X.size:_} records: {time.time() - start_time:7.2f}")

        # Create index
        print("\nCreating index...")
        self.perf_start("indexing")
        start_time = time.time()
        if self._metric == "angular":
            pass
        elif self._metric == "euclidean":
            # The feature is being developed
            # Currently stack will be empty for indexing in perf data as nothing is executed
            #self._cur.execute("ALTER TABLE `t1` ADD VECTOR INDEX (v);")
            pass
        else:
            pass
        self.perf_stop()

        stem = os.environ.get('MARIADB_DB_WORKSPACE') + '/data/ann/t1#i#01.'
        for f in glob.glob(stem + 'ibd') + glob.glob(stem + 'MY[ID]'):
            self._size += os.stat(f).st_size

        self.perf_start("searching")

    def set_query_arguments(self, ef_search):
        # Set ef_search
        self._ef_search = ef_search
        self._cur.execute("SET mhnsw_ef_search = %d" % ef_search)

    def query(self, v, n):
        self._cur.execute(f"SELECT id FROM t1 ORDER by vec_distance_{self._metric}(v, %s) LIMIT %d", (vector_to_hex(v), n))
        return [id for id, in self._cur.fetchall()]

    def get_memory_usage(self):
        return self._size/1024 # kB

    def batch_query(self, X, n):
        ef = self._ef_search

        XX=[]
        for i in range(os.cpu_count()):
            XX.append((self._socket_file, self._ef_search, self._metric, n, X[int(len(X)/os.cpu_count()*i):int(len(X)/os.cpu_count()*(i+1))]))
        pool = Pool()
        self._res = pool.map(many_queries, XX)

    def get_batch_results(self):
        return chain(*self._res)

    def __str__(self):
        return f"MariaDB(m={self._m:2d}, ef_search={self._ef_search})"

    def done(self):
        # Shutdown MariaDB server when benchmarking done
        self._cur.execute("shutdown")
        # self._mariadbd_proc.wait(300)
        # Stop perf for searching and do final analysis
        self.perf_stop()
        self.perf_analysis()
