FROM ann-benchmarks

# python-oracledb thin mode needs no Instant Client / OS-level driver install.
RUN pip install oracledb

# No local database to build or start - this module connects to an
# already-running Oracle instance via ORACLE_CONN_ARGS at runtime, the same
# way the MariaDB module supports MARIADB_CONN_ARGS as a fallback.
#
# If a bundled/local instance is ever needed instead, this is where a
# `FROM container-registry.oracle.com/database/free:latest`-based multi-stage
# build or an install step would go - deferred for now.

WORKDIR /home/app