import asyncio as aio
import csv
from io import BytesIO
import logging
import sqlite3
import time
from typing import Literal, Optional

import discord as dc
import discord.utils
from discord.abc import MessageableChannel
from discord.ext import commands as cm

from . import EMBED_COLOR
from .help_strings import tags_desc
from .streams import Stream, StreamCollector
from .utils import PersistentDict, PersistentSetDict


# Check if we are working with the unix epoch, as time does not guarantee it.
assert time.gmtime(0).tm_year == 1970 and time.gmtime(0).tm_yday == 1


logger = logging.getLogger("taggerbot")


class TaggerBot(cm.Bot):
    def __init__(self, *, intents: dc.Intents, database: str, **options):

        super().__init__(self.prefix_of, intents=intents, **options)  # type: ignore

        self.database = database

        self.tag_prefix = PersistentDict[int, str](database, "tag_prefix")
        self.prefixes = PersistentDict[int, str](database, "prefix")

        self.stream_col = StreamCollector(database)

        self.tags = TagDatabase(database)

        # {guild_id: {channel_url}}
        self.registered_chns = PersistentSetDict[str](database, "registered_chns", 1)
        # {guild_id: {text_chn_id}}
        self.watch_chns = PersistentSetDict[int](database, "watch_channels", 1)

        # {guild_id: stream.unique_id}
        self.guild_streams = PersistentDict[int, tuple[int, ...]](
            database, "guild_streams"
        )

        self.admins = PersistentDict[int, frozenset[int]](database, "admins")

    def is_admin(self, member: dc.Member | dc.User):
        return isinstance(member, dc.Member) and (
            member.guild_permissions.manage_guild
            or any(role.id in self.admins[member.guild.id] for role in member.roles)
            or member.id in self.admins
            or self.is_owner(member)
        )

    @staticmethod
    def check_perm(ctx: cm.Context["TaggerBot"]):
        return ctx.bot.is_admin(ctx.author)

    @staticmethod
    def prefix_of(bot: "TaggerBot", msg: dc.Message | dc.RawMessageUpdateEvent):
        if isinstance(msg, dc.RawMessageUpdateEvent):
            guild_id = msg.guild_id
            assert guild_id
        else:
            assert msg.guild
            guild_id = msg.guild.id
        if pre := bot.prefixes.get(guild_id):
            return cm.when_mentioned_or(pre)
        else:
            return cm.when_mentioned_or()

    ### add_admin
    @cm.command(name="admin add")  # type: ignore
    @cm.check(check_perm)
    async def add_admin(self, ctx: cm.Context, user_or_role: dc.Member | dc.Role):
        assert ctx.guild
        self.admins[ctx.guild.id] = self.admins.setdefault(
            ctx.guild.id, frozenset()
        ).union((user_or_role.id,))

        admins = (
            ctx.guild.get_role(id) or ctx.guild.get_member(id)
            for id in self.admins[ctx.guild.id]
        )
        admin_names = (admin.name for admin in admins if admin)

        await ctx.send(
            f"""Current people with admin permission for the bot (in addition to server admins):
            `{' ,'.join(admin_names)}`"""
        )

    ### remove_admin
    @cm.command(name="admin rem")  # type: ignore
    @cm.check(check_perm)
    async def remove_admin(self, ctx: cm.Context, user_or_role: dc.Member | dc.Role):
        assert ctx.guild
        self.admins[ctx.guild.id] = self.admins.setdefault(
            ctx.guild.id, frozenset()
        ).difference((user_or_role.id,))

        admins = (
            ctx.guild.get_role(id) or ctx.guild.get_member(id)
            for id in self.admins[ctx.guild.id]
        )
        admin_names = [admin.name for admin in admins if admin]

        await ctx.send(
            f"""Current people with admin permission for the bot (in addition to server admins):
            `{' ,'.join(admin_names) if admin_names else 'No one.'}`"""
        )

    ### list admins
    @cm.command(name="admin list")  # type: ignore
    @cm.check(check_perm)
    async def list_admins(self, ctx: cm.Context):
        assert ctx.guild

        admins = (
            ctx.guild.get_role(id) or ctx.guild.get_member(id)
            for id in self.admins[ctx.guild.id]
        )
        admin_names = [admin.name for admin in admins if admin]

        await ctx.send(
            f"""Current people with admin permission for the bot (in addition to server admins):
            `{' ,'.join(admin_names) if admin_names else 'No one.'}`"""
        )

    async def tag(self, msg: dc.Message, text: str):
        assert msg.guild

        # Permissions: read message history, add reactions
        aio.gather(msg.add_reaction("⭐"), msg.add_reaction("❌"))

        self.tags.tag(msg.id, msg.guild.id, msg.created_at.timestamp(), text)

    ### tag command
    @cm.command(name="tag", aliases=["t"])  # type: ignore
    async def t(self, ctx: cm.Context, *, tag: str):
        await self.tag(ctx.message, tag)

    ### tag with prefix, and watch for stream links
    async def on_message(self, message: dc.Message) -> None:
        if message.author.bot:
            return
        if message.content.startswith("`"):
            await self.tag(message, message.content[1:])

        elif (
            any(
                (url := word).startswith("https://") for word in message.content.split()
            )
            and message.guild
            and message.channel.id in self.watch_chns.get((message.guild.id,), [])
        ):
            assert message.guild
            guild_id = message.guild.id
            stream_url = url

            def add_latest_stream(stream: Stream):
                streams_list = self.guild_streams[guild_id]
                streams_list += (stream.unique_id,)

            try:
                await self.stream_col.add_stream_watch(
                    stream_url, hook=add_latest_stream
                )
            except ValueError:
                pass
            except Exception as e:
                logger.exception(e)

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
        if user != self.user:
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
        if user != self.user:
            if reaction.emoji == "⭐" and reaction.message.id in self.tags:
                self.tags.increment_vote(reaction.message.id, -1)

    ### dump tags
    @cm.command(name="tags", description=tags_desc)  # type: ignore
    async def tags_command(
        self,
        ctx: cm.Context,
        *options: str,
    ):
        "Dump the tags. See `help tags` for full options."
        assert ctx.guild

        opts_dict = {}

        match options:
            case ["as", stream_name, *other_opts]:
                opts_dict["as"] = stream_name
                options = other_opts  # type: ignore

        for opt in options:
            match opt:
                case "own":
                    opts_dict["own"] = True
                case "c":
                    opts_dict["all_servers"] = True
                case "yt" | "csv" | "info" | "text":
                    opts_dict["style"] = opt
                case start_time if start_time.startswith("start:"):
                    opts_dict["start_time"] = start_time  # TODO
                case stream_url:
                    opts_dict["stream_url"] = stream_url

        stream_url = opts_dict.get("stream_url")
        if stream_url == "_":
            stream_url = None

        try:
            tag_dump = self.dump_tags_from_stream(
                ctx.guild.id,
                stream_url=stream_url,
                style=opts_dict.get("style", "classic"),
                author_id=ctx.author.id if opts_dict.get("own") else None,
                as_stream_url=opts_dict.get("as"),
            )
        except KeyError:
            await ctx.send("Stream not found")
            return

        if not tag_dump:
            await ctx.send("No tags found.")
            return

        stream, tags_text = tag_dump

        if opts_dict.get("style") == "yt-text":
            txt_f = dc.File(BytesIO(bytes(tags_text, encoding="utf-8")), stream.stream_url + ".txt")
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

    def dump_tags_from_stream(
        self,
        guild_id: int,
        stream_url: str | None = None,
        style: str = "classic",
        all_guilds=False,
        author_id: int | None = None,
        as_stream_url: str | None = None,
    ) -> tuple[Stream, str] | None:
        """Returns formatted string of the tags of the latest stream,
        or `None` if no tags found or no latest stream.
        Raises `KeyError` if the specified `stream_url` is not found.
        """

        if stream_url:
            stream: Stream = self.stream_col.get_from_strmurl(stream_url)
        else:
            streams_list = self.guild_streams.get(guild_id)
            if not streams_list:
                return None

            stream_uid = streams_list[-1]
            stream = self.stream_col.streams[stream_uid]

        tags_text = self.dump_tags(
            None if all_guilds else guild_id,
            stream.stream_url,
            stream.start_time,
            stream.end_time or time.time(),
            style,
            author_id,
            as_stream_url=as_stream_url
        )
        if not tags_text:
            return
        else:
            return stream, tags_text

    def dump_tags(
        self,
        guild_id: int | None,
        stream_url: str,
        start: float,
        end: float,
        style: str = "classic",
        author_id: int | None = None,
        as_stream_url: str | None = None,
    ) -> str | None:
        "Between the start and end, returns formatted string of the tags, or `None` if no tags found."

        tags = self.tags.get_tags(guild_id, start, end, author_id)

        if not as_stream_url:
            as_stream_url = stream_url

        if len(tags) == 0:
            return None

        lines = list[str]()

        tags_per_minute = len(tags) / (end - start) * 60
        header_text = (
            f"{as_stream_url} <t:{start}:f> {len(tags)} tags ({tags_per_minute:.1f}/min)"
        )
        if style != "yt":
            lines.append(header_text)

        match style:
            case "classic":
                for ts, text, vote in tags:
                    line = (
                        text
                        + f"({vote}) [{td_to_str(ts)}]({timestamp_link(as_stream_url, ts)})"
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

        return "\n".join(lines)

    ### one time stream
    @cm.command()  # type: ignore
    async def stream(self, ctx: cm.Context, stream_url: str):
        assert ctx.guild
        guild_id = ctx.guild.id

        def add_latest_stream(stream: Stream):
            streams_list = self.guild_streams[guild_id]
            streams_list += (stream.unique_id,)

        try:
            real_stream_url: str = await self.stream_col.add_stream_watch(
                stream_url, hook=add_latest_stream
            )
        except ValueError:
            await ctx.send("Invalid url")
        else:
            await ctx.send(f"Will tag for {real_stream_url}")

    ### register channel
    @cm.command(name="sub add")  # type: ignore
    async def sub_add(self, ctx: cm.Context, channel_url: str):
        "Add a subscription to a channel. When that channel goes live, the bot will automatically detect the stream."
        assert ctx.guild
        guild_id = ctx.guild.id

        def add_latest_stream(stream: Stream):
            streams_list = self.guild_streams[guild_id]
            streams_list += (stream.unique_id,)

        try:
            real_channel_url: str = await self.stream_col.add_channel_watch(
                channel_url, hook=add_latest_stream
            )
        except ValueError:
            await ctx.send("Invalid url")
        else:
            await ctx.send(f"Will tag for {real_channel_url} every time it goes live.")

    ### watch fo stream link
    @cm.command(name="sub watch_channel")  # type: ignore
    async def sub_watch_channel(
        self, ctx: cm.Context, text_channel: Optional[dc.TextChannel]
    ):
        "Whenever a link is posted in this text channel, set the active stream to that."
        assert ctx.guild
        guild_id = ctx.guild.id

        if not text_channel:
            target_channel = ctx.channel
        else:
            target_channel = text_channel

        self.watch_chns.add(guild_id, value=target_channel.id)

        await ctx.send(
            "Whenever a link is posted in this text channel, I will set the active stream to that."
        )

    ### clear subscriptions
    @cm.command(name="sub clear")  # type: ignore
    async def sub_clear(self, ctx: cm.Context):
        "Remove all subscriptions."
        assert ctx.guild
        del self.watch_chns[
            ctx.guild.id,
        ]
        del self.registered_chns[
            ctx.guild.id,
        ]


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
        guild_id: int | None,
        start: float,
        end: float,
        author_id: int | None = None,
    ) -> list[tuple[int, str, int]]:
        "Returns a list of tuples of timestamp, text, and votes."
        start, end = int(start), int(end)
        cur = self.con.cursor()
        if guild_id:
            cur.execute(
                f"SELECT timestamp_, message, votes FROM {self.TABLE_NAME} WHERE guild_id=? AND timestamp_ BETWEEN ? AND ?",
                (guild_id, start, end),
            )
        else:
            cur.execute(
                f"SELECT timestamp_, message, votes FROM {self.TABLE_NAME} WHERE timestamp_ BETWEEN ? AND ?",
                (start, end),
            )
        if not author_id:
            return list(cur)
        else:
            return list(vals for vals in cur if vals[4] == author_id)

    def __contains__(self, msg_id: int):
        cur = self.con.cursor()
        cur.execute(f"SELECT FROM {self.TABLE_NAME} WHERE msg_id=?", (msg_id,))
        return bool(cur.fetchone())

    def __del__(self):
        self.con.close()
