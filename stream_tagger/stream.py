from dataclasses import dataclass, asdict

import orjson


@dataclass(frozen=True)
class Stream:
    unique_id: int
    stream_id: str | None  # this is unique per stream if not None
    stream_url: str
    chn_url: str
    inferred_start: int
    actual_start: int | None
    end_time: int | None
    info_dict: dict
    stream_url_temp: bool = False  # the stream_url is temporary

    @property
    def start_time(self):
        return self.actual_start or self.inferred_start

    def __eq__(self, __o: "Stream | object") -> bool:
        if isinstance(__o, Stream):
            if self.stream_id:
                return self.stream_id == __o.stream_id
            else:
                return self is __o
        else:
            return NotImplemented

    def __hash__(self) -> int:
        return hash(self.stream_id) if self.stream_id else hash(id(self))


def stream_dump(stream: Stream) -> bytes:
    return orjson.dumps(asdict(stream))

def stream_load(serial_stream: bytes | str) -> Stream:
    d: dict = orjson.loads(serial_stream)
    return Stream(**d)


if __name__ == "__main__":

    stream = Stream(
        0,
        "1iftVwwY4UM",
        "https://www.youtube.com/watch?v=1iftVwwY4UM",
        "https://www.youtube.com/channel/UCL_qhgtOy0dy1Agp8vkySQg",
        1653750008,
        1653750000,
        1653751000,
        info_dict={"a":4,"b":5},
    )
    print(stream_dump(stream))

    stream_recon = stream_load(stream_dump(stream))

    print(stream_recon)

    assert stream.__dict__ == stream_recon.__dict__

    print(asdict(stream))
