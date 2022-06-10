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
        aio.create_task(update_channels_list())

        await self.add_cog(self.settings)
        await self.add_cog(Tagging(self, self.database))

        if not self.owner_id:
            self.owner_id = (await self.application_info()).owner.id

    async def on_ready(self):
        logger.info("Ready.")

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
