import sqlite3
from typing import Callable, Collection, Hashable, TypeVar
import logging
import time
from collections.abc import MutableMapping
from ast import literal_eval


logger = logging.getLogger("clipping.bot")

assigned_table_names = set()

KT = TypeVar("KT", bound=Hashable)
VT = TypeVar("VT", bound=Hashable)


class PersistentDict(MutableMapping[KT, VT]):
    """Dictionary that loads from database upon initilization,
    and writes to it with every set operation.
    The cache goes stale in cache_duration seconds, if not None.
    """

    def __init__(
        self,
        database: str,
        table_name: str,
        cache_duration: float | None = None,
        dump_v: Callable[[VT], str | bytes] = repr,
        load_v: Callable[[bytes | str], VT] | Callable[[str], VT] = literal_eval,
    ):
        self.database = database
        self.table_name = table_name
        self.cache_duration = cache_duration
        self.dump_v = dump_v
        self.load_v = load_v

        self._store = dict[KT, VT]()
        self._cache_valid = False
        self._last_cache = float("-inf")

        assert table_name not in assigned_table_names
        self._create_table()

        assigned_table_names.add(table_name)

    def drop(self):
        "Drop the sql table. This object mustn't be used after that."
        con = sqlite3.connect(self.database)
        cur = con.cursor()
        cur.execute(f"DROP TABLE IF EXISTS '{self.table_name}'")
        con.commit()
        con.close()

    def _create_table(self):
        con = sqlite3.connect(self.database)
        cur = con.cursor()
        cur.execute(
            f"CREATE TABLE IF NOT EXISTS '{self.table_name}' (key_ PRIMARY KEY, value_)"
        )
        con.commit()
        con.close()

    def _populate_from_sql(self):
        con = sqlite3.connect(self.database)
        cur = con.cursor()
        cur.execute(f"SELECT key_, value_ FROM '{self.table_name}'")
        tuple_results = cur.fetchall()
        store = dict[KT, VT]()
        for key, value in tuple_results:
            store[literal_eval(key)] = self.load_v(value)
        self._store = store
        self._cache_valid = True
        self._last_cache = time.monotonic()

    def _calc_cache_staleness(self):
        if (
            self.cache_duration is not None
            and time.monotonic() - self._last_cache > self.cache_duration
        ):
            self._cache_valid = False

    def __getitem__(self, key: KT):
        self._calc_cache_staleness()
        if not self._cache_valid:
            self._populate_from_sql()
        return self._store[key]

    def __setitem__(self, key: KT, value: VT):
        # test validity
        if not (key == literal_eval(repr(key)) and value == self.load_v(self.dump_v(value))):  # type: ignore
            raise ValueError

        con = sqlite3.connect(self.database)
        cur = con.cursor()
        cur.execute(
            f"INSERT OR REPLACE INTO '{self.table_name}' VALUES (?, ?)",
            (repr(key), self.dump_v(value)),
        )
        con.commit()
        con.close()
        self._store[key] = value

    def __delitem__(self, key: KT):
        con = sqlite3.connect(self.database)
        cur = con.cursor()
        cur.execute(f"DELETE FROM '{self.table_name}' WHERE key_ = ?", (repr(key),))
        con.commit()
        con.close()
        try:
            del self._store[key]
        except KeyError:
            pass

    def __iter__(self):
        self._calc_cache_staleness()
        if not self._cache_valid:
            self._populate_from_sql()
        return iter(self._store)

    def __len__(self):
        self._calc_cache_staleness()
        if not self._cache_valid:
            self._populate_from_sql()
        return len(self._store)


KTT = tuple


class PersistentSetDict(MutableMapping[KTT, frozenset[VT]]):
    """Multiindex dictionary of sets that loads from database upon initilization,
    and writes to it with every set operation.
    The cache goes stale in cache_duration seconds, if not None.
    """

    def __init__(
        self,
        database: str,
        table_name: str,
        depth: int,
        dump_v: Callable[[VT], str | bytes] = repr,
        load_v: Callable[[bytes | str], VT] | Callable[[str], VT] = literal_eval,
    ):
        self.database = database
        self.table_name = table_name
        self.depth = depth
        self.dump_v = dump_v
        self.load_v = load_v

        self._store = dict[KTT, set[VT]]()
        self._cache_valid = False
        self._keys = [f"key_{i}" for i in range(self.depth)]
        self._key_names = ",".join(self._keys)

        assert table_name not in assigned_table_names
        self._create_table()

        assigned_table_names.add(table_name)

    def drop(self):
        "Drop the sql table. This object mustn't be used after that."
        con = sqlite3.connect(self.database)
        cur = con.cursor()
        cur.execute(f"DROP TABLE IF EXISTS '{self.table_name}'")
        con.commit()
        con.close()

    def _create_table(self):
        con = sqlite3.connect(self.database)
        cur = con.cursor()
        cur.execute(
            f"""CREATE TABLE IF NOT EXISTS '{self.table_name}' (
                {self._key_names} ,
                value_,
                UNIQUE({self._key_names}, value_)
                ON CONFLICT REPLACE)"""
        )
        con.commit()
        con.close()

    def _populate_from_sql(self):
        con = sqlite3.connect(self.database)
        cur = con.cursor()
        cur.execute(f"SELECT {self._key_names}, value_ FROM '{self.table_name}'")
        tuple_results = cur.fetchall()
        store = dict[KTT, set[VT]]()

        for result in tuple_results:
            keys = tuple(literal_eval(key) for key in result[:-1])
            value = result[-1]
            store.setdefault(keys, set()).add(self.load_v(value))

        self._store = store
        self._cache_valid = True

    def __getitem__(self, keys: KTT) -> frozenset[VT]:
        if len(keys) != self.depth:
            raise KeyError
        if not self._cache_valid:
            self._populate_from_sql()
        try:
            return frozenset(self._store[tuple(keys)])
        except Exception:
            return frozenset()

    def add(self, *keys, value: VT):
        # test validity
        if any(key != literal_eval(repr(key)) for key in keys) or value != self.load_v(
            self.dump_v(value)  # type: ignore
        ):
            raise ValueError

        if len(keys) != self.depth:
            raise KeyError

        key_strs = tuple(repr(key) for key in keys)

        con = sqlite3.connect(self.database)
        cur = con.cursor()
        cur.execute(
            f"""INSERT INTO '{self.table_name}'
            VALUES ({','.join(['?'] * self.depth)}, ?)""",
            key_strs + ((self.dump_v(value),)),
        )
        con.commit()
        con.close()

        self._store.setdefault(tuple(keys), set()).add(value)

    def __setitem__(self, keys: KTT, value_set: Collection[VT]):

        # test validity
        if any(key != literal_eval(repr(key)) for key in keys) or any(
            value != self.load_v(self.dump_v(value)) for value in value_set  # type: ignore
        ):
            raise ValueError

        if len(keys) != self.depth:
            raise KeyError

        key_strs = tuple(repr(key) for key in keys)

        con = sqlite3.connect(self.database)
        cur = con.cursor()

        for value in value_set:

            cur.execute(
                f"""INSERT INTO '{self.table_name}'
                VALUES ({','.join(['?'] * self.depth)}, ?)""",
                key_strs + ((self.dump_v(value),)),
            )

        con.commit()
        con.close()
        self._store[tuple(keys)] = set(value_set)

    def remove(self, *keys, value: VT):
        "Can raise `KeyError`."
        if len(keys) != self.depth:
            raise KeyError

        key_strs = tuple(repr(key) for key in keys)

        con = sqlite3.connect(self.database)
        cur = con.cursor()
        cur.execute(
            f"""DELETE FROM '{self.table_name}' WHERE
            {' AND '.join(key+' = ?' for key in self._keys)}
            AND value_ = ?""",
            key_strs + ((repr(value),)),
        )
        con.commit()
        con.close()
        try:
            self._store[tuple(keys)].remove(value)
        except KeyError:
            pass

    def __delitem__(self, keys: KTT):
        if len(keys) != self.depth:
            raise KeyError

        key_strs = tuple(repr(key) for key in keys)

        con = sqlite3.connect(self.database)
        cur = con.cursor()
        cur.execute(
            f"""DELETE FROM '{self.table_name}' WHERE
            {' AND '.join(key+' = ?' for key in self._keys)}""",
            key_strs,
        )
        con.commit()
        con.close()
        try:
            del self._store[tuple(keys)]
        except KeyError:
            pass

    def __iter__(self):
        if not self._cache_valid:
            self._populate_from_sql()
        return iter(self._store)

    def __len__(self):
        if not self._cache_valid:
            self._populate_from_sql()
        return len(self._store)

    def __contains__(self, keys) -> bool:
        if not self._cache_valid:
            self._populate_from_sql()
        return tuple(keys) in self._store


def str_to_time_d(time_str: str) -> int:
    match time_str.split(":"):
        case [seconds]:
            return int(seconds)
        case [minutes, seconds]:
            return int(minutes) * 60 + int(seconds)
        case [hours, minutes, seconds]:
            return int(hours) * 3600 + int(minutes) * 60 + int(seconds)
        case _:
            raise ValueError

def str_to_time(time_str: str) -> int:
    "If hours supplied, return today + hours. Otherwise, accept as timestamp"
    if ":" in time_str:
        hours = str_to_time_d(time_str)
        today_start_st = time.gmtime()
        today_start = time.mktime(today_start_st[:3] + (0,0,0) + today_start_st[6:])
        return hours + int(today_start)
    else:
        return int(time_str)


if __name__ == "__main__":
    t = str_to_time("12:03:00")
    print(t)
