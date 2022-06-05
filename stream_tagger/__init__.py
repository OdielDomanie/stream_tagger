import os
import time

import dotenv

from config import *


dotenv.load_dotenv()

HOLODEX_TOKEN = os.getenv("HOLODEX_TOKEN")


# Check if we are working with the unix epoch, as time does not guarantee it.
assert (
    time.gmtime(0).tm_year == 1970
    and time.gmtime(0).tm_yday == 1
    and time.gmtime(0).tm_hour == 0
)
