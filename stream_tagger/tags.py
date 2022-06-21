import collections
import sqlite3
from typing import Literal


Tag_t = collections.namedtuple("tag_t", ("ts", "text", "vote", "msg_id", "hier"))


class TagDatabase:

    TABLE_NAME = "tags"

    def __init__(self, database_name: str) -> None:
        self.database_name = database_name
        self.con = sqlite3.connect(
            self.database_name, detect_types=sqlite3.PARSE_DECLTYPES
        )
        self._create_table()

    def _create_table(self):
        cur = self.con.cursor()
        cur.execute(
            f"""CREATE TABLE IF NOT EXISTS '{self.TABLE_NAME}'
            (msg_id INT PRIMARY KEY, guild INT, timestamp_ INT, message TEXT, votes INT,
            author INT, hidden BOOL, hierarchy INT)"""
        )
        cur.execute(
            f"CREATE INDEX IF NOT EXISTS '{self.TABLE_NAME}_gt_index' on '{self.TABLE_NAME}' (guild, timestamp_)"
        )
        self.con.commit()

        # Migration
        CURRENT_VERSION = 2
        cur = self.con.cursor()
        cur.execute("PRAGMA user_version")
        version = cur.fetchall()[0][0]  # 0 if version is not set (new table)
        if version != 0 and version < CURRENT_VERSION:
            cur.execute(
                f"ALTER TABLE {self.TABLE_NAME} ADD COLUMN hierarchy INT DEFAULT 0"
            )

        cur.execute(f"PRAGMA user_version={CURRENT_VERSION}")
        self.con.commit()

    def tag(
        self,
        msg_id: int,
        guild_id: int,
        time: float,
        text: str,
        author_id: int,
        hidden=False,
        hierarchy=0,
    ):
        time = int(time)
        cur = self.con.cursor()
        cur.execute(
            f"INSERT INTO {self.TABLE_NAME} VALUES (?, ?, ?, ?, 0, ?, ?, ?)",
            (msg_id, guild_id, time, text, author_id, hidden, hierarchy),
        )
        self.con.commit()

    def update_text(self, msg_id: int, text: str, h: int):
        cur = self.con.cursor()
        cur.execute(
            f"UPDATE {self.TABLE_NAME} SET message=?, hierarchy=? WHERE ?=msg_id",
            (text, h, msg_id),
        )
        self.con.commit()

    def update_time(self, msg_id: int, time: float):
        time = int(time)
        cur = self.con.cursor()
        cur.execute(
            f"UPDATE {self.TABLE_NAME} SET timestamp_=? WHERE ?=msg_id",
            (time, msg_id),
        )
        self.con.commit()

    def increment_vote(self, msg_id: int, add=1):
        cur = self.con.cursor()
        cur.execute(
            f"UPDATE {self.TABLE_NAME} SET votes = votes+? WHERE msg_id = ?",
            (add, msg_id),
        )
        self.con.commit()

    def remove(self, msg_id: int):
        cur = self.con.cursor()
        cur.execute(
            f"DELETE FROM {self.TABLE_NAME} WHERE msg_id = ?",
            (msg_id,),
        )
        self.con.commit()

    def get_tags(
        self,
        guild_id: int,
        start: float,
        end: float,
        author_id: int | None = None,
        limit: int = 1_000,
        show_hidden=False,
        order: Literal["ASC", "DESC"] = "ASC",
    ) -> list[Tag_t]:  # list[tuple[int, str, int, int, int]]:
        "Returns a list of tuples of timestamp, text, votes, msg_id, and hierarchy."
        start, end = int(start), int(end)
        cur = self.con.cursor()
        assert isinstance(limit, int)
        assert order in ("ASC", "DESC")
        if author_id:
            cur.execute(
                f"""SELECT timestamp_, message, votes, msg_id, hierarchy FROM {self.TABLE_NAME}
                WHERE guild=? AND timestamp_ BETWEEN ? AND ? AND author=?
                AND (hidden IS FALSE OR hidden=?)
                ORDER BY timestamp_ {order} LIMIT {limit}""",
                (guild_id, start, end, author_id, show_hidden),
            )
        else:
            cur.execute(
                f"""SELECT timestamp_, message, votes, msg_id, hierarchy FROM {self.TABLE_NAME}
                WHERE guild=? AND timestamp_ BETWEEN ? AND ?
                AND (hidden IS FALSE OR hidden=?)
                ORDER BY timestamp_ {order} LIMIT {limit}""",
                (guild_id, start, end, show_hidden),
            )
        return list(Tag_t(*fetch) for fetch in cur.fetchall())

    def __contains__(self, msg_id: int):
        cur = self.con.cursor()
        cur.execute(f"SELECT * FROM {self.TABLE_NAME} WHERE msg_id=?", (msg_id,))
        return bool(cur.fetchone())

    def __del__(self):
        try:
            self.con.close()
        except sqlite3.ProgrammingError:
            pass
