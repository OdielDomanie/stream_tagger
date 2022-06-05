import asyncio as aio
import enum
import logging
import math
import random
import sqlite3
import time
from io import BytesIO
from typing import Literal, Optional

import discord as dc
import discord.utils
import discord.app_commands as ac
from discord.ext import commands as cm

from . import DEFAULT_OFFSET, EMBED_COLOR
from .admin_config import Settings
from .help_strings import tags_desc
from .stream import Stream, stream_dump, stream_load
from .streams import (
    get_all_chns_from_name,
    get_stream,
    update_channels_list,
    get_chns_from_name,
)
from .utils import PersistentSetDict, str_to_time, str_to_time_d


logger = logging.getLogger("taggerbot")


class _Styles(enum.Enum):
    classic = "classic"
    yt = "yt"
    yt_text = "yt-text"
    alternative = "alternative"
    info = "info"
    csv = "csv"


class TaggerBot(cm.Bot):
    def __init__(self, *, intents: dc.Intents, database: str, **options):

        super().__init__(self.prefix_of, intents=intents, **options)  # type: ignore

        self.settings = Settings(self, database)

        self.tags = TagDatabase(database)

        # {stream_url (perm) : {guild_id}}
        # self.stream_guilds = PersistentSetDict[int](database, "stream_guilds", 1)
        # {txtchn_id: {msg}
        self.last_dump = dict[int, set[dc.Message | dc.Interaction]]()
        # {guild_id: {stream}}
        self.guild_streams = PersistentSetDict[Stream](
            database, "guild_streams", 1, dump_v=stream_dump, load_v=stream_load
        )

    async def setup_hook(self) -> None:
        aio.create_task(update_channels_list())

        await self.add_cog(self.settings)
        self.settings.cog_check = self.check_perm

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

        def_offset = self.settings.configs.get(("def_offset",))
        offset = list(def_offset)[0] if def_offset else DEFAULT_OFFSET
        adjusted_ts = msg.created_at.timestamp() + offset
        self.tags.tag(msg.id, msg.guild.id, adjusted_ts, text, author_id)

    ### tag command
    @cm.command(name="tag", aliases=["t"])  # type: ignore
    async def t(self, ctx: cm.Context, *, tag: str):
        await self.tag(ctx.message, tag, ctx.author.id)

    ### Adjust
    @cm.command()  # type: ignore
    async def adjust(self, ctx: cm.Context, amount: int):
        "Adjust time of the last tag."
        assert ctx.guild
        last_tags = self.tags.get_tags(ctx.guild.id, 0, time.time() + 26*60*60, ctx.author.id, limit=1)
        if last_tags:
            og_ts, msg_id = last_tags[0][0], last_tags[0][3]
            self.tags.update_time(msg_id, og_ts + amount)
            last_msg = await ctx.fetch_message(msg_id)
            # Permissions: Read message history, Add reactions
            if not self.settings.configs.get(("quiet", ctx.guild.id)):
                try:
                    await last_msg.add_reaction("👍")
                except dc.Forbidden:
                    pass

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

    ### auto complete
    async def stream_autocomp(self, it: dc.Interaction, curr: str) -> list[ac.Choice]:
        "Return streamer names."
        AUTOCOMP_LIM = 10  # Discord's limit is 25, but a lower limit looks better
        # First look at the latest stream
        assert it.guild_id
        streams = self.guild_streams[
            it.guild_id,
        ]
        ordered_streams = sorted(streams, key=lambda s: s.start_time, reverse=True)
        chns = set(get_all_chns_from_name(curr))
        if not chns:
            return []

        pos_chns = []
        for stream in ordered_streams:
            if len(pos_chns) >= AUTOCOMP_LIM:
                break
            for chn_id, chn_urls, name, en_name in chns:
                en_name = en_name or ""
                if (
                    (curr in name or curr in en_name)
                    and stream.chn_url in chn_urls
                    and name not in pos_chns
                ):
                    pos_chns.append(name)
                    break

        if len(curr) >= 3:
            for chn_id, chn_urls, name, en_name in chns:
                if len(pos_chns) >= AUTOCOMP_LIM:
                    break
                en_name = en_name or ""
                if (curr in name or curr in en_name) and name not in pos_chns:
                    pos_chns.append(name)

        return [ac.Choice(name=name, value=name) for name in pos_chns]

    async def server_autocomp(self, it: dc.Interaction, curr: str) -> list[ac.Choice]:
        "Return server names."
        AUTOCOMP_LIM = 10  # Discord's limit is 25, but a lower limit looks better

        try:
            guild_id = int(curr)
        except ValueError:  # A name is being entered
            is_name = True
        else:
            is_name = False

        pos_guilds = [
            g for g in self.guilds if curr in (g.name if is_name else str(g.id))
        ]
        if len(pos_guilds) > AUTOCOMP_LIM:
            random.shuffle(pos_guilds)
        return [
            ac.Choice(name=g.name, value=str(g.id)) for g in pos_guilds[:AUTOCOMP_LIM]
        ]

    ### dump tags slash command
    @ac.command(name="tags")  # type: ignore
    @ac.autocomplete(stream=stream_autocomp, server=server_autocomp)  # type: ignore
    @ac.describe(
        stream="Stream url, channel url, or the streamer name.",
        start_time="Only needed if the stream argument is not provided. See /help.",
        duration="Use with `start_time`. Leave empty for until current time.",
        style="How to format the tags.",
        server="A server name or id to steal the tags from.",
        own="Only show my tags",
        delete_last="Don't dump anything, just delete the last dump.",
        offset="Offset the time stamps.",
        offset_from="Only apply offset starting from this time stamp.",
    )
    async def tags_appcommand(
        self,
        it: dc.Interaction,
        stream: Optional[str],
        start_time: Optional[str],
        duration: Optional[str],
        style: Optional[_Styles],
        server: Optional[str],
        own: Optional[bool],
        delete_last: Optional[bool],
        offset: str = "0",
    ):
        "Dump tags."
        if not (stream or start_time or server):
            await it.response.send_message(
                "I need either a stream, or a start time, or another server.",
                ephemeral=True,
            )
            return
        options = list[str]()
        if stream:
            options.append(stream)
        if start_time:
            options.append("start=" + start_time)
        if duration:
            options.append("duration=" + duration)
        if style:
            options.append(style.value)
        if server:
            options.append("server=" + server)
        if own:
            options.append("own")
        if delete_last:
            options.append("delete")

        await self.tags_command(it, *options)

    ### dump tags
    @cm.command(name="tags", description=tags_desc)  # type: ignore
    async def tags_command(
        self,
        ctx_it: cm.Context | dc.Interaction,
        *options: str,
    ):
        "Dump the tags for a given stream url. See `help tags` for full options."

        if isinstance(ctx_it, dc.Interaction):
            send = ctx_it.response.send_message
            author = ctx_it.user
        else:
            send = ctx_it.send
            author = ctx_it.author

        assert ctx_it.channel

        opts_dict = {}

        for opt in options:
            match opt:
                case "own":
                    opts_dict["own"] = True
                case style if style in _Styles:
                    opts_dict["style"] = style
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
                        await send("Invalid server name or server id.", ephemeral=True)
                        return
                    opts_dict["guild"] = guild_id
                case "delete" | "delete_last":
                    opts_dict["delete"] = True
                case offset if offset.startswith("offset="):
                    offset = int(offset[7:])
                    opts_dict["offset"] = offset
                case stream_url:
                    opts_dict["stream_url"] = stream_url

        # If the command was called to delete the last dump, instead of a dump.
        if "delete" in opts_dict:
            last_msg = self.last_dump.get(ctx_it.channel.id)
            if last_msg:
                for msg in last_msg:
                    if isinstance(msg, dc.Interaction):
                        await msg.delete_original_message()
                    else:
                        await msg.delete()
            return

        stolen_stream = False
        stream_url = opts_dict.get("stream_url")
        if (stream_url == "_" or not stream_url) and not "start_time" in opts_dict:
            if "guild_id" in opts_dict:
                streams = self.guild_streams.get(opts_dict["guild_id"])
                if streams:
                    streams_sorted = sorted(
                        streams, key=lambda s: s.start_time, reverse=True
                    )
                    stolen_stream = streams_sorted[0]
                    opts_dict["start_time"] = stolen_stream.start_time
                    if "duration" not in opts_dict:
                        # This will always be filled, even if with a not actual value.
                        assert stolen_stream.end_time
                        opts_dict["duration"] = (
                            stolen_stream.end_time - stolen_stream.start_time
                        )

            else:
                await send(
                    "I need either a stream url, or a manual `start:` time, or another server to steal from.",
                    ephemeral=True,
                )
                return

        if "guild" not in opts_dict:
            assert ctx_it.guild
            guild_id = ctx_it.guild.id
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
                author_id=author.id if opts_dict.get("own") else None,
                start_time=opts_dict.get("start_time"),
                duration=opts_dict.get("duration"),
                **opts_dict,
            )
        except ValueError:
            await send("Stream not found", ephemeral=True)
            return

        if not tag_dump:
            await send("No tags found.", ephemeral=True)
            return

        stream, tags_text = tag_dump

        # Add
        if stream.stream_url_temp:
            assert ctx_it.guild
            self.guild_streams.add(ctx_it.guild.id, value=stream)

        if opts_dict.get("style") in ("yt-text", "csv"):
            txt_f = dc.File(
                BytesIO(bytes(tags_text, encoding="utf-8")),
                stream.stream_url + ".txt"
                if opts_dict.get("style") == "yt-text"
                else ".csv",
            )
            msg = await send(file=txt_f)
            self.last_dump[ctx_it.channel.id] = {msg or ctx_it}  # type: ignore
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
            if isinstance(ctx_it, dc.Interaction):
                if ctx_it.response.is_done:
                    it_send = ctx_it.followup.send
                else:
                    it_send = send
                msg = await it_send(embeds=embeds[i : i + 10])
                self.last_dump.setdefault(ctx_it.channel.id, set()).add(msg or ctx_it)
            else:
                msg = await ctx_it.send(embeds=embeds[i : i + 10])
                self.last_dump.setdefault(ctx_it.channel.id, set()).add(msg)

    async def dump_tags_from_stream(
        self,
        guild_id: int,
        stream_url: str | None = None,
        *,
        style: str,
        author_id: int | None = None,
        start_time: int | None = None,
        duration: int | None = None,
        **opts_dict,
    ) -> tuple[Stream, str] | None:
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
            stream = Stream(
                unique_id=random.randrange(0, 2**64),
                stream_id=None,
                stream_url="",
                chn_url="",
                inferred_start=int(start_time_),
                actual_start=None,
                end_time=int(end_time),
                info_dict={},
                stream_url_temp=True,
            )

        tags_text = self.dump_tags(
            guild_id,
            real_url,
            start_time_,
            end_time,
            style,
            author_id,
            url_is_perm=url_is_perm,
            offset = opts_dict.get("offset", 0)
        )
        if not tags_text:
            return
        else:
            return stream, tags_text

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
        offset: int = 0,
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
                for ts, text, vote, _ in tags:
                    ts += offset
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
                for ts, text, vote, _ in tags:
                    ts += offset
                    escaped_text = discord.utils.remove_markdown(text)
                    line = f"{td_to_str(ts, 'yt')} {text}"
                    lines.append(line)
            case "csv":
                for ts, text, vote, _ in tags:
                    ts += offset
                    escaped_text = '"' + text.replace('"', '""') + '"'
                    line = ",".join((str(ts), escaped_text, str(vote)))
                    lines.append(line)
            case "info":
                pass
            case "alternative":
                avg_votes = sum(vote for _, _, vote, _ in tags) / len(tags)
                adjusted_stars = lambda v: round(
                    math.log(round(v / (avg_votes + 1)) + 1, 2)  # visually cool
                )
                for ts, text, vote, _ in tags:
                    ts += offset
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
            case _:
                raise ValueError

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
    ) -> list[tuple[int, str, int, int]]:
        "Returns a list of tuples of timestamp, text, votes, and msg_id."
        start, end = int(start), int(end)
        cur = self.con.cursor()
        assert isinstance(limit, int)
        if author_id:
            cur.execute(
                f"SELECT timestamp_, message, votes, msg_id FROM {self.TABLE_NAME}"
                " WHERE guild_id=? AND timestamp_ BETWEEN ? AND ? AND author=?"
                f" ORDER BY timestamp_ DESC LIMIT {limit}",
                (guild_id, start, end, author_id),
            )
        else:
            cur.execute(
                f"SELECT timestamp_, message, votes, msg_id FROM {self.TABLE_NAME}"
                " WHERE guild_id=? AND timestamp_ BETWEEN ? AND ?"
                f" ORDER BY timestamp_ DESC LIMIT {limit}",
                (guild_id, start, end),
            )
        return list(cur)

    def __contains__(self, msg_id: int):
        cur = self.con.cursor()
        cur.execute(f"SELECT FROM {self.TABLE_NAME} WHERE msg_id=?", (msg_id,))
        return bool(cur.fetchone())

    def __del__(self):
        self.con.close()
