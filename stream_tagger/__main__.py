import logging
import os

import discord as dc
import dotenv

from . import DATABASE
from .bot import TaggerBot


loggers = logging.getLogger("discord"), logging.getLogger("taggerbot")
handler = logging.StreamHandler()
handler.setFormatter(
    logging.Formatter("%(asctime)s:%(levelname)s:%(name)s: %(message)s")
)
for logger in loggers:
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


dotenv.load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")


intents = dc.Intents()
intents.messages = True
intents.message_content = True
intents.guilds = True
intents.reactions = True

bot = TaggerBot(intents=intents, database=DATABASE)

bot.run(DISCORD_TOKEN)
