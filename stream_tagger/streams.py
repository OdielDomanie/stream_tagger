from .stream import Stream, stream_dump, stream_load
from .utils import PersistentDict, PersistentSetDict


class StreamCollector:
    def __init__(self, db_file: str) -> None:
        # {chn_url: Stream}
        self.streams = PersistentDict[int, Stream](
            db_file, "Streams", dump_v=stream_dump, load_v=stream_load
        )
