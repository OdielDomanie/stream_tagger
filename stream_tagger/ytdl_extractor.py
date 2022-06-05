import logging
import time
from typing import Iterable, Mapping

import http.cookiejar
import yt_dlp as ytdl


logger = logging.getLogger("stream_extractor")

class RateLimited(Exception):
    pass


def fetch_yt_metadata(url: str, no_playlist=True, playlist_items: Iterable[int] = [0]) -> Mapping | None:
    """Fetches metadata of url.
    Returns `info_dict`, or `None` if no vid/stream found live. Can raise `RateLimited`.
    """

    ytdl_logger = logging.getLogger("ytdl_fetchinfo")
    ytdl_logger.addHandler(logging.NullHandler())  # f yt-dl logs

    # options referenced from
    # https://github.com/sparanoid/live-dl/blob/3e76be969d94747aa5d9f83b34bc22e14e0929be/live-dl
    #
    # Problem with cookies in current implementation:
    # https://github.com/ytdl-org/youtube-dl/issues/22613
    ydl_opts = {
        "logger": ytdl_logger,
        "noplaylist": no_playlist,
        "playlist_items": ",".join(str(it) for it in playlist_items),
        "skip_download": True,
        "forcejson": True,
        "no_color": True,
        "cookiefile": ".cookies.txt",
    }

    if "youtube.com/" in url:
        ydl_opts["referer"] = "https://www.youtube.com/feed/subscriptions"

    try:
        with ytdl.YoutubeDL(ydl_opts) as ydl:
            info_dict: Mapping = ydl.extract_info(url, download=False)
    except ytdl.utils.DownloadError as e:
        # "<channel_name> is offline error is possible in twitch
        if (
            "This live event will begin in" in e.args[0]
            or "is offline" in e.args[0]
        ):
            logger.debug(e)
        elif "HTTP Error 429" in e.args[0]:
            logger.critical(f"Got \"{e}\", for {url}.")
            raise RateLimited
        else:
            logger.error(f"{e}, for {url}.")

        return None
    except http.cookiejar.LoadError as e:
        logger.error(f"Cookie error: {e}. Trying again")
        time.sleep(1)
        return fetch_yt_metadata(url)
    except Exception as e:
        logger.exception(e)
        return None
    return info_dict


if __name__ == "__main__":
    # Example output
    info_dict = fetch_yt_metadata("https://www.twitch.tv/noxiouslive/")
    # with open("sample_metadata_ttv_live.py", "w") as f:
    #     f.write(repr(info_dict) + "\n")
