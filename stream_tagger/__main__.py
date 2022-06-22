import logging
import os

import discord as dc
import dotenv

from . import DATABASE
from .bot import TaggerBot


root_logger = logging.getLogger()
handler = logging.StreamHandler()
handler.setFormatter(
    logging.Formatter("%(asctime)s:%(levelname)s:%(name)s: %(message)s")
)
root_logger.addHandler(handler)
root_logger.setLevel(logging.INFO)


dotenv.load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
assert DISCORD_TOKEN
TEST_GUILD = (
    int(os.getenv("TEST_GUILD")) if os.getenv("TEST_GUILD") else None  # type:ignore
)


intents = dc.Intents()
intents.guild_messages = True
intents.message_content = True
intents.guilds = True
intents.reactions = True
intents.members = True

bot = TaggerBot(intents=intents, database=DATABASE, test_guild=TEST_GUILD)

bot.run(DISCORD_TOKEN, log_handler=None)
