import asyncio as aio
import logging

from stream_tagger.streams import populate_channels_list


logging.getLogger("taggerbot").addHandler(logging.StreamHandler())
logging.getLogger("taggerbot").setLevel(logging.DEBUG)


aio.run(populate_channels_list(), debug=True)
