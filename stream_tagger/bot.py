import asyncio as aio
import csv
from io import BytesIO
import logging
import math
import sqlite3
import time
from typing import Literal, Optional

import discord as dc
import discord.utils
from discord.abc import MessageableChannel
from discord.ext import commands as cm

from . import EMBED_COLOR
from .admin_config import Settings
from .help_strings import tags_desc
from .streams import Stream, get_stream
from .utils import PersistentDict, PersistentSetDict, str_to_time, str_to_time_d


# Check if we are working with the unix epoch, as time does not guarantee it.
assert time.gmtime(0).tm_year == 1970 and time.gmtime(0).tm_yday == 1


logger = logging.getLogger("taggerbot")


class TaggerBot(cm.Bot):
    def __init__(self, *, intents: dc.Intents, database: str, **options):

        super().__init__(self.prefix_of, intents=intents, **options)  # type: ignore

        self.database = database

        self.settings = Settings(self, database)

        self.first_on_ready = True

        self.tags = TagDatabase(database)

        # {stream_url (perm) : {guild_id}}
        self.stream_guilds = PersistentSetDict[int](database, "stream_guilds", 1)

    async def on_ready(self):
        if self.first_on_ready:
            await self.add_cog(self.settings)
            self.settings.cog_check = self.check_perm
            self.first_on_ready = False

            if not self.owner_id:
                self.owner_id = (await self.application_info()).owner.id

    def is_admin(self, member: dc.Member | dc.User) -> bool:
        if isinstance(member, dc.User):
            return False
        admins = self.settings.configs.get(("admins", member.guild.id), [])
        return isinstance(member, dc.Member) and (
            member.guild_permissions.manage_guild
            or any(role.id in admins for role in member.roles)
            or member.id in admins
            or member.id == self.owner_id
        )

    @staticmethod
    def check_perm(ctx: cm.Context["TaggerBot"]) -> bool:
        return ctx.bot.is_admin(ctx.author)

    @staticmethod
    def prefix_of(bot: "TaggerBot", msg: dc.Message | dc.RawMessageUpdateEvent):
        if isinstance(msg, dc.RawMessageUpdateEvent):
            guild_id = msg.guild_id
            assert guild_id
        else:
            assert msg.guild
            guild_id = msg.guild.id
        return cm.when_mentioned_or(*bot.settings.configs.get(("prefix", guild_id), []))

    async def tag(self, msg: dc.Message, text: str, author_id: int):
        assert msg.guild

        if True in self.settings.configs.get(("quiet", msg.guild.id), []):
            # Permissions: read message history, add reactions
            try:
                await aio.gather(msg.add_reaction("⭐"), msg.add_reaction("❌"))
            except dc.Forbidden:
                pass

        self.tags.tag(msg.id, msg.guild.id, msg.created_at.timestamp(), text, author_id)

    ### tag command
    @cm.command(name="tag", aliases=["t"])  # type: ignore
    async def t(self, ctx: cm.Context, *, tag: str):
        await self.tag(ctx.message, tag, ctx.author.id)

    ### tag with prefix
    async def on_message(self, message: dc.Message) -> None:
        if message.author == self.user:
            return
        if message.author.bot and (  # Ignore bots unless set otherwise by the guild.
            (
                message.guild
                and True
                not in (self.settings.configs.get(("allow_bots", message.guild.id), []))
            )
        ):
            return
        if message.author.bot and not message.guild:
            return
        if message.content.startswith("`"):
            await self.tag(message, message.content[1:], message.author.id)

        else:
            return await super().on_message(message)

    ### edit a tag
    async def on_raw_message_edit(self, payload: dc.RawMessageUpdateEvent):
        if (content := payload.data.get("content")) and payload.message_id in self.tags:

            prefixes = await self.get_prefix(payload)  # type: ignore  # This ends up in prefix_of
            prefixes = [prefixes] if isinstance(prefixes, str) else prefixes

            if any(content.startswith(prefix) for prefix in prefixes):
                self.tags.update_text(payload.message_id, content)

    ### vote or delete
    async def on_reaction_add(self, reaction: dc.Reaction, user: dc.Member | dc.User):
        if user != self.user and not user.bot:
            if reaction.emoji == "⭐" and reaction.message.id in self.tags:
                self.tags.increment_vote(reaction.message.id)

            elif (
                reaction.emoji == "❌"
                and (reaction.message.author == user or self.is_admin(user))
                and reaction.message.id in self.tags
            ):
                self.tags.remove(reaction.message.id)

    ### remove vote
    async def on_reaction_remove(
        self, reaction: dc.Reaction, user: dc.Member | dc.User
    ):
        if user != self.user and not user.bot:
            if reaction.emoji == "⭐" and reaction.message.id in self.tags:
                self.tags.increment_vote(reaction.message.id, -1)

    ### dump tags
    @cm.command(name="tags", description=tags_desc)  # type: ignore
    async def tags_command(
        self,
        ctx: cm.Context,
        *options: str,
    ):
        "Dump the tags for a given stream url. See `help tags` for full options."
        # assert ctx.guild

        opts_dict = {}

        for opt in options:
            match opt:
                case "own":
                    opts_dict["own"] = True
                case "c":
                    opts_dict["all_servers"] = True
                case "yt" | "csv" | "info" | "text":
                    opts_dict["style"] = opt
                case start_time if start_time.startswith("start="):
                    opts_dict["start_time"] = str_to_time(start_time[6:])
                case duration if duration.startswith("duration="):
                    opts_dict["duration"] = str_to_time_d(duration[9:])
                case guild if guild.startswith("server="):
                    guild = guild[7:]
                    try:
                        guild_id = int(guild)
                    except ValueError:
                        guild_id = None
                        for g in self.guilds:
                            if g.name == guild:
                                guild_id = g.id
                                break
                    if not guild_id:
                        await ctx.send("Invalid server name or server id.")
                        return
                    opts_dict["guild"] = guild_id
                case stream_url:
                    opts_dict["stream_url"] = stream_url

        stream_url = opts_dict.get("stream_url")
        if (stream_url == "_" or not stream_url) and not opts_dict.get("start_time"):
            await ctx.send("I need either a stream url, or a manual `start:` time.")
            return

        if "guild" not in opts_dict:
            assert ctx.guild
            guild_id = ctx.guild.id
        else:
            guild_id = opts_dict["guild"]

        def_style_set = self.settings.configs.get(("def_format", guild_id))
        if not def_style_set:
            def_style = "classic"
        else:
            def_style: str = list(def_style)[0]

        try:
            tag_dump = await self.dump_tags_from_stream(
                guild_id,
                stream_url=stream_url,
                style=opts_dict.get("style", def_style),
                author_id=ctx.author.id if opts_dict.get("own") else None,
                start_time=opts_dict.get("start_time"),
                duration=opts_dict.get("duration"),
            )
        except ValueError:
            await ctx.send("Stream not found")
            return

        if not tag_dump:
            await ctx.send("No tags found.")
            return

        stream_url, url_is_perm, tags_text = tag_dump

        # Add
        if url_is_perm:
            assert ctx.guild
            self.stream_guilds.add("stream_url", value=ctx.guild.id)

        if opts_dict.get("style") == "yt-text":
            txt_f = dc.File(
                BytesIO(bytes(tags_text, encoding="utf-8")), stream_url + ".txt"
            )
            await ctx.send(file=txt_f)
            return

        if opts_dict.get("style") == "csv":
            txt_f = dc.File(
                BytesIO(bytes(tags_text, encoding="utf-8")), stream_url + ".csv"
            )
            await ctx.send(file=txt_f)
            return

        embed_texts: list[str] = []
        for line in tags_text.splitlines(keepends=True):
            embed_text = ""
            while True:
                if len(embed_text) + len(line) >= 4096:  # discord embed character limit
                    embed_texts.append(embed_text)
                    break
                embed_text += line

        embeds = [
            dc.Embed(color=EMBED_COLOR, description=embed_text)
            for embed_text in embed_texts
        ]
        embeds[0].title = "Tags"

        n = len(embeds) // 10 + (0 if len(embeds) % 10 == 0 else 1)
        for i in range(n):
            await ctx.send(embeds=embeds[i : i + 10])

    async def dump_tags_from_stream(
        self,
        guild_id: int,
        stream_url: str | None = None,
        *,
        style: str,
        author_id: int | None = None,
        start_time: int | None = None,
        duration: int | None = None,
    ) -> tuple[str, bool, str] | None:
        """Returns formatted string of the tags of the latest stream,
        or `None` if no tags found or no latest stream.
        Raises `ValueError` if the specified `stream_url` is not found.
        """

        if stream_url:
            stream: Stream = await get_stream(stream_url)
            real_url = stream.stream_url_temp and stream.stream_url
            start_time_ = stream.start_time
            end_time = stream.end_time or time.time()
            url_is_perm = not stream.stream_url_temp
        else:
            real_url = None
            start_time_: int = start_time  # type: ignore
            if duration:
                end_time = start_time_ + duration
            else:
                end_time = time.time()
            url_is_perm = False

        tags_text = self.dump_tags(
            guild_id,
            real_url,
            start_time_,
            end_time,
            style,
            author_id,
            url_is_perm=url_is_perm,
        )
        if not tags_text:
            return
        else:
            return real_url or str(start_time_), url_is_perm, tags_text

    def dump_tags(
        self,
        guild_id: int,
        stream_url: str | Literal[False] | None,
        start: float,
        end: float,
        style: str,
        author_id: int | None = None,
        *,
        url_is_perm: bool,
    ) -> str | None:
        "Between the start and end, returns formatted string of the tags, or `None` if no tags found."

        if False in self.settings.configs.get(("fetch_limit", guild_id), []):
            limit = 1_000_000
        else:
            limit = 1_000

        tags = self.tags.get_tags(guild_id, start, end, author_id, limit=limit)

        if len(tags) == 0:
            return None

        lines = list[str]()

        tags_per_minute = len(tags) / (end - start) * 60
        header_text = f"{stream_url or float:d} <t:{start}:f> {len(tags)} tags ({tags_per_minute:.1f}/min)"
        if style != "yt":
            lines.append(header_text)

        match style:
            case "classic":
                for ts, text, vote in tags:
                    line = (
                        text
                        + f"({vote})"
                        + (
                            f" [{td_to_str(ts)}]({timestamp_link(stream_url, ts)})"
                            if stream_url and url_is_perm
                            else f" {td_to_str(ts)}"
                        )
                    )
                    lines.append(line)
            case "yt" | "yt-text":
                for ts, text, vote in tags:
                    escaped_text = discord.utils.remove_markdown(text)
                    line = f"{td_to_str(ts, 'yt')} {text}"
                    lines.append(line)
            case "csv":
                for ts, text, vote in tags:
                    escaped_text = '"' + text.replace('"', '""') + '"'
                    line = ",".join((str(ts), escaped_text, str(vote)))
                    lines.append(line)
            case "info":
                pass
            case "alternative":
                avg_votes = sum(vote for _, _, vote in tags) / len(tags)
                adjusted_stars = lambda v: round(
                    math.log(round(v / (avg_votes + 1)) + 1, 2)
                )  # visually cool
                for ts, text, vote in tags:
                    line = (
                        (
                            f" [{td_to_str(ts)}]({timestamp_link(stream_url, ts)})"
                            if stream_url and url_is_perm
                            else f" {td_to_str(ts)}"
                        )
                        + text
                        + f" ({''.join('⭐' for _ in range(adjusted_stars(vote)))})"
                    )
                    lines.append(line)

        return "\n".join(lines)


def td_to_str(t: float, style: Literal["classic", "yt"] = "classic") -> str:
    "Time in seconds to a string reprsentation."
    hours = int(t // 3600)
    minutes = int((t // 60) % 60)
    seconds = int(t % 60)

    match style:
        case "classic":
            res = f"{minutes}m{seconds}s"
        case "yt":
            res = f"{minutes}:{seconds}"
    if hours != 0:
        match style:
            case "classic":
                res = f"{hours}h" + res
            case "yt":
                res = f"{hours}:" + res

    return res


def timestamp_link(stream_url: str, t: float) -> str:
    # Basic and not complete url modifying, but enough for our purposes.
    # Works for at least youtube and twitch.
    stream_url = stream_url.split("#")[0]
    if "?" in stream_url:
        return stream_url + f"?t={int(t)}s"
    else:
        return stream_url + f"&t={int(t)}s"


class TagDatabase:

    TABLE_NAME = "tags"

    def __init__(self, database_name: str) -> None:
        self.database_name = database_name
        self.con = sqlite3.connect(
            self.database_name, detect_types=sqlite3.PARSE_DECLTYPES
        )

    def _create_table(self):
        cur = self.con.cursor()
        cur.execute(
            f"CREATE TABLE IF NOT EXISTS '{self.TABLE_NAME}'"
            " (msg_id INT PRIMARY KEY, guild INT, timestamp_ INT, message TEXT, votes INT, author INT)"
        )
        cur.execute(
            f"CREATE INDEX IF NOT EXISTS '{self.TABLE_NAME}_gt_index' on '{self.TABLE_NAME}' (guild, timestamp_)"
        )
        cur.execute(
            f"CREATE INDEX IF NOT EXISTS '{self.TABLE_NAME}_t_index' on '{self.TABLE_NAME}' (timestamp_)"
        )
        self.con.commit()

    def tag(self, msg_id: int, guild_id: int, time: float, text: str, author_id: int):
        time = int(time)
        cur = self.con.cursor()
        cur.execute(
            f"INSERT INTO {self.TABLE_NAME} VALUES (?, ?, ?, ?, ?)",
            (msg_id, guild_id, time, text, author_id),
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
    ) -> list[tuple[int, str, int]]:
        "Returns a list of tuples of timestamp, text, and votes."
        start, end = int(start), int(end)
        cur = self.con.cursor()
        assert isinstance(limit, int)
        if author_id:
            cur.execute(
                f"SELECT timestamp_, message, votes FROM {self.TABLE_NAME}"
                f" WHERE guild_id=? AND timestamp_ BETWEEN ? AND ? AND author=LIMIT {limit}",
                (guild_id, start, end, author_id),
            )
        else:
            cur.execute(
                f"SELECT timestamp_, message, votes FROM {self.TABLE_NAME}"
                f" WHERE guild_id=? AND timestamp_ BETWEEN ? AND ? LIMIT {limit}",
                (guild_id, start, end),
            )
        return list(cur)

    def __contains__(self, msg_id: int):
        cur = self.con.cursor()
        cur.execute(f"SELECT FROM {self.TABLE_NAME} WHERE msg_id=?", (msg_id,))
        return bool(cur.fetchone())

    def __del__(self):
        self.con.close()
