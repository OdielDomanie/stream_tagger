from typing import TYPE_CHECKING, Literal

import discord as dc
from discord.ext import commands as cm

from .utils import PersistentSetDict

if TYPE_CHECKING:
    from .bot import TaggerBot


class Settings(cm.Cog):
    def __init__(self, bot: TaggerBot, database: str) -> None:
        self.bot = bot
        self.configs = PersistentSetDict(database, "settings", 2)

    @cm.group(name="settings", invoke_without_command=True)
    async def settings(self, ctx: cm.Context[TaggerBot]):
        assert ctx.bot.help_command
        await ctx.bot.help_command.send_group_help(self.settings)

    @settings.command(name="admin add")
    async def admin_add(self, ctx: cm.Context, user_or_role: dc.Member | dc.Role):
        'Add a role or user as a bot admin. "Admins" can change bot\'s per-server settings.'
        assert ctx.guild

        self.configs.add("admins", ctx.guild.id, value=user_or_role.id)

        await self.admin_list(ctx)

    @settings.command(name="admin rem")
    async def admin_remove(self, ctx: cm.Context, user_or_role: dc.Member | dc.Role):
        assert ctx.guild
        try:
            self.configs.remove("admins", ctx.guild.id, value=user_or_role.id)
        except KeyError:
            await ctx.send("They are already not an admin.")

        await self.admin_list(ctx)

    @settings.command(name="admin list")
    async def admin_list(self, ctx: cm.Context):
        'List members and roles with the "Admin" permission for the bot.'
        assert ctx.guild
        admins = (
            ctx.guild.get_role(id) or ctx.guild.get_member(id)
            for id in self.configs["admins", ctx.guild.id]
        )
        admin_names = [admin.name for admin in admins if admin]

        await ctx.send(
            f"""Current people with admin permission for the bot (in addition to server admins):
            `{' ,'.join(admin_names) if admin_names else 'No one.'}`"""
        )

    @settings.command(name="quiet")
    async def quiet(self, ctx: cm.Context, be_quiet: bool):
        "Quiet mode, suitable to be used with another tagger bot. The bot will not post emojis. `False` by default."
        assert ctx.guild
        self.configs["quiet", ctx.guild.id] = (be_quiet,)
        await ctx.send("Set.")

    Tag_Formats = Literal[
        "classic",
        "alternative",
        "yt",
        "yt-text",
        "csv",
        "info",
    ]

    @settings.command(
        name="default_format",
        alias="default format",
        brief="The default format for the tags output.",
    )
    async def default_format(self, ctx: cm.Context[TaggerBot], format: Tag_Formats):
        """Change the default format of the `tags` command. Possible formats are:
        `classic`,
        `alternative`,
        `yt`,
        `yt-text`,
        `csv`,
        `info`,
        """
        assert ctx.guild
        self.configs["def_format", ctx.guild.id] = (format,)
        await ctx.send("Set.")

    @settings.command(
        name="allow_bots",
        alias="allow bots",
    )
    async def allow_bots(self, ctx: cm.Context[TaggerBot], allow: bool):
        "Allow other bots to tag. False by default. `settings allow_bots True` or `False`."
        assert ctx.guild
        self.configs["allow_bots", ctx.guild.id] = (allow,)
        await ctx.send("Set.")

    @settings.command(
        name="tags_limit",
        alias="tags limit",
    )
    async def fetch_limit(self, ctx: cm.Context[TaggerBot], limit: bool):
        """Limit the number of tags that can be output at one time,"
        just in case you accidently try to dump more than a thousand tags at once.
        Enabled by default. If that's not enough for you, disable it by
        `settings tags_limit False`."""
        assert ctx.guild
        self.configs["fetch_limit", ctx.guild.id] = (limit,)
        await ctx.send("Set.")

    @settings.command(
        name="prefix",
    )
    async def set_prefix(self, ctx: cm.Context[TaggerBot], prefix: str):
        """Set the prefix for commands, like `!`. This doesn't affect the backtick.
        Mentioning me is always a valid prefix."""
        assert ctx.guild
        assert len(prefix) > 0
        self.configs["prefix", ctx.guild.id] = (prefix,)
        await ctx.send("Set.")
