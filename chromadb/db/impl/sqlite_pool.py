import sqlite3
from abc import ABC, abstractmethod
from queue import Queue
from typing import Any, Set
import threading
from overrides import override


class Connection:
    """A threadpool connection that returns itself to the pool on close()"""

    _pool: "Pool"
    _db_file: str
    _conn: sqlite3.Connection

    def __init__(
        self, pool: "Pool", db_file: str, is_uri: bool, *args: Any, **kwargs: Any
    ):
        self._pool = pool
        self._db_file = db_file
        self._conn = sqlite3.connect(
            db_file, timeout=1000, check_same_thread=False, uri=is_uri, *args, **kwargs
        )  # type: ignore
        self._conn.isolation_level = None  # Handle commits explicitly
        self._conn.execute("PRAGMA cache_size = 2000000;")
        self._conn.execute("PRAGMA temp_store = MEMORY;")
        self._conn.execute("PRAGMA journal_mode = OFF;")

        mmap_size = 2048 * 1024 * 1024
        self._conn.execute(f'PRAGMA mmap_size = {mmap_size};')
        self._conn.execute('PRAGMA synchronous = OFF;')
        self._conn.execute("PRAGMA optimize;")
        self._conn.execute("ANALYZE embedding_metadata;")
        self._conn.execute("ANALYZE embeddings;")

    def execute(self, sql: str, parameters=...) -> sqlite3.Cursor:  # type: ignore
        if parameters is ...:
            return self._conn.execute(sql)
        return self._conn.execute(sql, parameters)

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def cursor(self) -> sqlite3.Cursor:
        return self._conn.cursor()

    def close_actual(self) -> None:
        """Actually closes the connection to the db"""
        self._conn.close()


class Pool(ABC):
    """Abstract base class for a pool of connections to a sqlite database."""

    @abstractmethod
    def __init__(self, db_file: str, is_uri: bool) -> None:
        pass

    @abstractmethod
    def connect(self, *args: Any, **kwargs: Any) -> Connection:
        """Return a connection from the pool."""
        pass

    @abstractmethod
    def close(self) -> None:
        """Close all connections in the pool."""
        pass

    @abstractmethod
    def return_to_pool(self, conn: Connection) -> None:
        """Return a connection to the pool."""
        pass


class LockPool(Pool):
    """A pool that has a single connection per thread but uses a lock to ensure that only one thread can use it at a time.
    This is used because sqlite does not support multithreaded access with connection timeouts when using the
    shared cache mode. We use the shared cache mode to allow multiple threads to share a database.
    """

    _connections: Set[Connection]
    _lock: threading.RLock
    _connection: threading.local
    _db_file: str
    _is_uri: bool

    def __init__(self, db_file: str, is_uri: bool = False):
        self._connections = set()
        self._connection = threading.local()
        self._lock = threading.RLock()
        self._db_file = db_file
        self._is_uri = is_uri

    @override
    def connect(self, *args: Any, **kwargs: Any) -> Connection:
        self._lock.acquire()
        if hasattr(self._connection, "conn") and self._connection.conn is not None:
            return self._connection.conn  # type: ignore # cast doesn't work here for some reason
        else:
            new_connection = Connection(
                self, self._db_file, self._is_uri, *args, **kwargs
            )
            self._connection.conn = new_connection
            self._connections.add(new_connection)
            return new_connection

    @override
    def return_to_pool(self, conn: Connection) -> None:
        try:
            self._lock.release()
        except RuntimeError:
            pass

    @override
    def close(self) -> None:
        for conn in self._connections:
            conn.close_actual()
        self._connections.clear()
        self._connection = threading.local()
        try:
            self._lock.release()
        except RuntimeError:
            pass


class PerThreadPool(Pool):
    """Maintains a connection per thread. For now this does not maintain a cap on the number of connections, but it could be
    extended to do so and block on connect() if the cap is reached.
    """

    _connections: Set[Connection]
    _lock: threading.Lock
    _connection: threading.local
    _db_file: str
    _is_uri_: bool

    def __init__(self, db_file: str, is_uri: bool = False):
        self._connections = set()
        self._connection = threading.local()
        self._lock = threading.Lock()
        self._db_file = db_file
        self._is_uri = is_uri

    @override
    def connect(self, *args: Any, **kwargs: Any) -> Connection:
        if hasattr(self._connection, "conn") and self._connection.conn is not None:
            return self._connection.conn  # type: ignore # cast doesn't work here for some reason
        else:
            new_connection = Connection(
                self, self._db_file, self._is_uri, *args, **kwargs
            )
            self._connection.conn = new_connection
            with self._lock:
                self._connections.add(new_connection)
            return new_connection

    @override
    def close(self) -> None:
        with self._lock:
            for conn in self._connections:
                conn.close_actual()
            self._connections.clear()
            self._connection = threading.local()

    @override
    def return_to_pool(self, conn: Connection) -> None:
        pass  # Each thread gets its own connection, so we don't need to return it to the pool


class ReusableConnectionPool(Pool):
    """Maintains a reusable connection pool. Connections are shared across threads."""

    def __init__(self, db_file: str, is_uri: bool = False, max_connections: int = 10,):
        self._available_connections = Queue(maxsize=max_connections)
        self._all_connections: Set[Connection] = set()
        self._lock = threading.Lock()
        self._db_file = db_file
        self._is_uri = is_uri
        self._max_connections = max_connections
        self._initialized = threading.Event()

        # Pre-initialize connections
        for _ in range(max_connections):
            self._available_connections.put(self._create_new_connection())

        self._initialized.set()

    def _create_new_connection(self,*args, **kwargs) -> Connection:
        new_connection = Connection(self, self._db_file, self._is_uri, *args, **kwargs)
        self._all_connections.add(new_connection)
        return new_connection

    @override
    def connect(self, *args: Any, **kwargs: Any) -> Connection:
        self._initialized.wait()  # Ensure pool is initialized
        connection = self._available_connections.get()
        if connection is None:
            connection = self._create_new_connection(*args, **kwargs)
        return connection

    @override
    def close(self) -> None:
        with self._lock:
            while not self._available_connections.empty():
                conn = self._available_connections.get()
                conn.close_actual()
            for conn in self._all_connections:
                conn.close_actual()
            self._all_connections.clear()

    @override
    def return_to_pool(self, conn: Connection) -> None:
        if conn in self._all_connections:
            self._available_connections.put(conn)