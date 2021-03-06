from typing import TYPE_CHECKING, Callable

import discord as dc
import discord.app_commands as ac
from discord.ext import commands as cm

from .tag_command import TagStyles
from .utils import PersistentSetDict

if TYPE_CHECKING:
    from .bot import TaggerBot


admin_def_perms = dc.Permissions()
admin_def_perms.manage_guild = True


class Settings(cm.Cog):
    def __init__(
        self, bot: "TaggerBot", database: str, check: Callable[[cm.Context], bool]
    ) -> None:
        self.bot = bot
        self.configs = PersistentSetDict(database, "settings", 2)
        self.__check = check

    def cog_check(self, ctx: cm.Context["TaggerBot"]) -> bool:
        return self.__check(ctx)

    @cm.hybrid_group(
        name="settings",
        invoke_without_command=True,
        default_permissions=None,
    )
    async def settings(self, ctx: cm.Context["TaggerBot"]):
        "Per server settings"
        assert ctx.bot.help_command
        ctx_help = ctx.bot.help_command.copy()
        ctx_help.context = ctx
        await ctx_help.send_group_help(self.settings)

    @settings.command(name="admin_add", with_app_command=False)
    @ac.describe(user_or_role="The user or role to grant permission.")
    async def admin_add(self, ctx: cm.Context, *, user_or_role: dc.Member | dc.Role):
        'Add a role or user as a bot admin. "Admins" can change bot\'s per-server settings.'
        assert ctx.guild

        self.configs.add("admins", ctx.guild.id, value=user_or_role.id)

        await self.admin_list(ctx)

    @settings.command(name="admin_rem", with_app_command=False)
    @ac.describe(user_or_role="The user or role to remove permission.")
    async def admin_remove(self, ctx: cm.Context, *, user_or_role: dc.Member | dc.Role):
        assert ctx.guild
        try:
            self.configs.remove("admins", ctx.guild.id, value=user_or_role.id)
        except KeyError:
            await ctx.send("They are already not an admin.")

        await self.admin_list(ctx)

    @settings.command(name="admin_list", with_app_command=False)
    async def admin_list(self, ctx: cm.Context):
        'List members and roles with the "Admin" permission for the bot.'
        assert ctx.guild
        admins = (
            ctx.guild.get_role(id) or ctx.guild.get_member(id)
            for id in self.configs["admins", ctx.guild.id]
        )
        admin_names = [admin.name for admin in admins if admin]

        await ctx.send(
            "Current people with admin permission for the bot (in addition to server admins):"
            f"\n`{', '.join(admin_names) if admin_names else 'No one.'}`"
        )

    @settings.command(name="quiet")
    @ac.default_permissions()
    @ac.describe(be_quiet='"True" for quiet')
    async def quiet(self, ctx: cm.Context, be_quiet: bool):
        "Quiet mode, suitable to be used with another tagger bot. The bot will not post emojis."
        assert ctx.guild
        self.configs["quiet", ctx.guild.id] = (be_quiet,)
        await ctx.send("Set.")

    @settings.command(
        name="default_format",
        alias="default format",
        brief='The default format for the tags output. Default is "alternative"',
    )
    @ac.default_permissions()
    async def default_format(self, ctx: cm.Context["TaggerBot"], format: TagStyles):
        """Change the default format of the `tags` command.
        Possible formats:
        * alternative
        * classic
        * yt
        * yt-text
        * info
        * csv
        """
        assert ctx.guild
        self.configs["def_format", ctx.guild.id] = (format.value,)
        await ctx.send("Set.")

    @settings.command(
        name="allow_bots",
        alias="allow bots",
    )
    @ac.default_permissions()
    async def allow_bots(self, ctx: cm.Context["TaggerBot"], allow: bool):
        """Allow other bots to tag using me. False by default.
        `settings allow_bots True` or `False`."""
        assert ctx.guild
        self.configs["allow_bots", ctx.guild.id] = (allow,)
        await ctx.send("Set.")

    @settings.command(
        name="tags_limit",
        alias="tags limit",
    )
    @ac.default_permissions()
    async def fetch_limit(self, ctx: cm.Context["TaggerBot"], limited: bool):
        """Limit the number of tags that can be output at one time,"
        just in case you accidently try to dump more than a thousand tags at once.
        Enabled by default. If that's not enough for you, disable it by
        `settings tags_limit False`."""
        assert ctx.guild
        self.configs["fetch_limit", ctx.guild.id] = (limited,)
        await ctx.send("Set.")

    @settings.command(name="prefix", with_app_command=False)
    @ac.default_permissions()
    async def set_prefix(self, ctx: cm.Context["TaggerBot"], prefix: str):
        """Set the prefix for commands, like `!`. This doesn't affect the backtick.
        Mentioning me is always a valid prefix."""
        assert ctx.guild
        assert len(prefix) > 0
        self.configs["prefix", ctx.guild.id] = (prefix,)
        await ctx.send("Set.")

    @settings.command(name="default_offset")
    @ac.default_permissions()
    async def default_offset(self, ctx: cm.Context, default_offset: int = -20):
        "Change the default offset applied in this server. Default is -20."
        assert ctx.guild
        self.configs["def_offset", ctx.guild.id] = (default_offset,)
        await ctx.send(f"Set to {default_offset}")

    @settings.command(name="set_private")
    @ac.default_permissions()
    async def set_private(self, ctx: cm.Context, private: bool):
        'Mark this text channel as private: Other servers can\'t "steal" the future tags made in this channel.'
        assert ctx.guild
        if private:
            self.configs.add("private_txtchn", ctx.guild.id, value=ctx.channel.id)
            await ctx.send("Marked as private.")
        else:
            try:
                self.configs.remove(
                    "private_txtchn", ctx.guild.id, value=ctx.channel.id
                )
            except KeyError:
                await ctx.send("Already not private.")
            else:
                await ctx.send("Set to non-private.")
