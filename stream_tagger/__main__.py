import os

import discord as dc
import dotenv

from . import DATABASE
from .bot import TaggerBot


dotenv.load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")


intents = dc.Intents()
intents.message_content = True
intents.guilds = True
intents.reactions = True

bot = TaggerBot(intents=intents, database=DATABASE)

bot.run(DISCORD_TOKEN)
