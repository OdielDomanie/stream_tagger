import sqlite3


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
            f"CREATE TABLE IF NOT EXISTS '{self.TABLE_NAME}'"
            " (msg_id INT PRIMARY KEY, guild INT, timestamp_ INT, message TEXT, votes INT, author INT, hidden BOOL)"
        )
        cur.execute(
            f"CREATE INDEX IF NOT EXISTS '{self.TABLE_NAME}_gt_index' on '{self.TABLE_NAME}' (guild, timestamp_)"
        )
        self.con.commit()

    def tag(
        self,
        msg_id: int,
        guild_id: int,
        time: float,
        text: str,
        author_id: int,
        hidden=False,
    ):
        time = int(time)
        cur = self.con.cursor()
        cur.execute(
            f"INSERT INTO {self.TABLE_NAME} VALUES (?, ?, ?, ?, 0, ?, ?)",
            (msg_id, guild_id, time, text, author_id, hidden),
        )
        self.con.commit()

    def update_text(self, msg_id: int, text: str):
        cur = self.con.cursor()
        cur.execute(
            f"UPDATE {self.TABLE_NAME} SET text=? WHERE ?=msg_id",
            (text, msg_id),
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
            f"DELETE {self.TABLE_NAME} WHERE msg_id = ?",
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
    ) -> list[tuple[int, str, int, int]]:
        "Returns a list of tuples of timestamp, text, votes, and msg_id."
        start, end = int(start), int(end)
        cur = self.con.cursor()
        assert isinstance(limit, int)
        if author_id:
            cur.execute(
                f"""SELECT timestamp_, message, votes, msg_id FROM {self.TABLE_NAME}
                WHERE guild_id=? AND timestamp_ BETWEEN ? AND ? AND author=?
                AND (hidden IS FALSE OR hidden=?)
                ORDER BY timestamp_ DESC LIMIT {limit}""",
                (guild_id, start, end, author_id, show_hidden),
            )
        else:
            cur.execute(
                f"""SELECT timestamp_, message, votes, msg_id FROM {self.TABLE_NAME}
                WHERE guild_id=? AND timestamp_ BETWEEN ? AND ?
                AND (hidden IS FALSE OR hidden=?)
                ORDER BY timestamp_ DESC LIMIT {limit}""",
                (guild_id, start, end, show_hidden),
            )
        return list(cur)

    def __contains__(self, msg_id: int):
        cur = self.con.cursor()
        cur.execute(f"SELECT FROM {self.TABLE_NAME} WHERE msg_id=?", (msg_id,))
        return bool(cur.fetchone())

    def __del__(self):
        self.con.close()
