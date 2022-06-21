import asyncio as aio
import logging
from typing import Iterable

import discord as dc
from discord.ext import commands as cm

from .admin_config import Settings
from .streams import update_channels_list
from .tag_command import Tagging


logger = logging.getLogger("taggerbot")


class TaggerBot(cm.Bot):
    def __init__(self, *, intents: dc.Intents, database: str, **options):

        super().__init__(self.prefix_of, intents=intents, **options)  # type: ignore

        self.database = database
        self.settings = Settings(self, database, self.check_perm)

    async def setup_hook(self) -> None:
        # aio.create_task(update_channels_list())  # Is useless rn.

        await self.add_cog(self.settings)
        await self.add_cog(Tagging(self, self.database))

        if not self.owner_id:
            self.owner_id = (await self.application_info()).owner.id

    async def on_ready(self):
        logger.info("Ready.")

    def is_admin(self, member: dc.Member | dc.User, ctx: cm.Context | str) -> bool:
        if isinstance(member, dc.User):
            logger.warning(f"{member} was a User, not a Member.")
            return False
        admins = self.settings.configs.get(("admins", member.guild.id), [])
        permission_oks = {
            "manage_guild": member.guild_permissions.manage_guild,
            "administrator": member.guild_permissions.administrator,
            "admin_role": any(role.id in admins for role in member.roles),
            "admin_member": member.id in admins,
            "owner": member.id == self.owner_id,
        }
        if isinstance(ctx, cm.Context) and ctx.invoked_with:
            logger.info((ctx.invoked_with, permission_oks))
        else:
            logger.info((ctx, permission_oks))
        return any(permission_oks.values())

    @staticmethod
    def check_perm(ctx: cm.Context["TaggerBot"]) -> bool:
        return ctx.bot.is_admin(ctx.author, ctx)

    @staticmethod
    def prefix_of(
        bot: "TaggerBot", msg: dc.Message | dc.RawMessageUpdateEvent
    ) -> Iterable[str]:
        if isinstance(msg, dc.RawMessageUpdateEvent):
            guild_id = msg.guild_id
            assert guild_id
        else:
            assert msg.guild
            guild_id = msg.guild.id
        prefixes: Iterable[str] = bot.settings.configs.get(("prefix", guild_id), [])
        # Second parameter is unimportant for this fucntion
        mention_pre = cm.when_mentioned(bot, msg)  # type: ignore
        return list(prefixes) + mention_pre

    async def on_command_error(
        self, context: cm.Context, exception: cm.errors.CommandError, /
    ):
        if isinstance(exception, cm.errors.UserInputError):
            logger.info(f"User entered wrong input: {context.message.content}")
        elif isinstance(exception, cm.errors.CommandInvokeError) and isinstance(
            exception.original, aio.TimeoutError
        ):
            logger.info(f"Timed out: {context.message.content}")
        else:
            return await super().on_command_error(context, exception)
