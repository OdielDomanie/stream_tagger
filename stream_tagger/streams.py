import asyncio as aio
import logging
import re
import sqlite3
import time
from typing import Generator, Iterable, Mapping
from urllib import parse

import aiohttp
import dateutil.parser

from . import CHANNELS_LIST_DB, HOLODEX_TOKEN
from .stream import Stream
from .utils import ExpBackoff, PersistentDict
from .ytdl_extractor import PayWalled, fetch_yt_metadata


logger = logging.getLogger("taggerbot.stream_extractor")


# {chn_id: ((chn_url,), streamer_name, en_name)}
channels_list = PersistentDict[str, tuple[tuple[str, ...], str, str | None]](
    CHANNELS_LIST_DB, "channnels_list", 24 * 60 * 60
)


_exp_backoff = ExpBackoff()
_next_req_at = 0


async def holodex_req(
    session: aiohttp.ClientSession,
    end_point: str,
    url_param: str | None,
    query_params: dict,
    *,
    __sem: list[aio.Semaphore] = [],
):
    """
    Holodex API License:
    https://holodex.stoplight.io/docs/holodex/ZG9jOjM4ODA4NzA-license
    """
    if not __sem:
        __sem.append(aio.Semaphore(1))
    base_url = "https://holodex.net/api/v2/"
    url = parse.urljoin(base_url, end_point)
    if url_param:
        url = parse.urljoin(url, url_param)
    headers = {"X-APIKEY": HOLODEX_TOKEN}

    global _next_req_at

    async with __sem[0]:
        # async with crl:
        while True:
            await _exp_backoff.wait()
            await aio.sleep(_next_req_at - time.time())
            logger.debug(f"Req to Holodex: {end_point} | {url_param} | {query_params}")
            async with session.get(
                url, headers=headers, params=query_params
            ) as response:

                if retry_after := response.headers.get("Retry-After"):
                    _next_req_at = time.time() + int(retry_after)
                    # crl.limit(0, float(retry_after))

                elif response.headers.get("X-RateLimit-Remaining") == "0":
                    _next_req_at = int(response.headers.get("X-RateLimit-Reset", 0))
                    # logger.debug(f"rl_rem: {rl_rem}, reset_at: {reset_at}")
                    # crl.limit(int(rl_rem), float(reset_at))

                if response.status in (403, 429) or (
                    response.status >= 500 and not retry_after
                ):
                    _exp_backoff.backoff()
                    continue
                else:
                    _exp_backoff.cooldown()

                    resp = await response.json()
                    return resp


async def _update_chn_list(
    session, chn_id: str, name: str, en_name: str | None, other_chns: Iterable
):
    q_params = {"channel_id": chn_id, "type": "stream"}
    videos_resp = await holodex_req(session, "videos", None, q_params)

    learnt_chns = {vid.get("link") for vid in videos_resp if vid.get("link")}

    known_chns = channels_list.get(chn_id, [set()])[0]
    chn_urls = set.union(
        {"https://www.youtube.com/channel/" + chn_id},
        known_chns,
        other_chns,
        learnt_chns,
    )
    channels_list[chn_id] = (tuple(chn_urls), name, en_name)


async def populate_channels_list():
    logger.info("Populating channels list.")
    # If we do too much too quickly, maybe Holodex won't like us.
    async with aiohttp.ClientSession() as session:

        # Get a list of all channels
        all_chns = list[
            tuple[str, str, str | None, set[str]]
        ]()  # [(id, name, en_name, {some_other_channels})]
        for i in range(0, 10**5, 50):
            q_params = {"limit": 50, "offset": i, "type": "vtuber"}
            chn_resp = await holodex_req(session, "channels", None, q_params)
            if len(chn_resp) == 0:
                break
            all_chns.extend(
                (
                    chn_info["id"],
                    chn_info["name"],
                    (chn_info["english_name"]),
                    {"https://twitter.com/" + chn_info["twitter"]}
                    if chn_info.get("twitter")
                    else set(),
                )
                for chn_info in chn_resp
                if not (chn_info.get("inactive") or chn_info.get("group") == "INACTIVE")
            )

        # Find non youtube channels

        await aio.gather(
            *(
                _update_chn_list(session, chn_id, name, en_name, other_chns)
                for chn_id, name, en_name, other_chns in all_chns
            )
        )


UPDATE_INTV = 1 * 24 * 60 * 60


async def update_channels_list():
    "Periodically update the channels database."
    while True:
        con = sqlite3.connect(CHANNELS_LIST_DB)
        cur = con.cursor()
        cur.execute(
            f"""CREATE TABLE IF NOT EXISTS 'last_update' (
                id INTEGER PRIMARY KEY ON CONFLICT REPLACE CHECK (id = 0) ,
                last_update_ts INT DEFAULT 0)"""
        )

        cur.execute("SELECT last_update_ts FROM 'last_update'")
        fetch = cur.fetchone()
        last_update_ts = int((fetch and fetch[0]) or 0)
        sleep_for = UPDATE_INTV - (time.time() - last_update_ts)

        await aio.sleep(sleep_for)

        await populate_channels_list()

        cur.execute("INSERT INTO 'last_update' VALUES (0, ?)", (int(time.time()),))

        con.commit()
        con.close()


def get_chns_from_name(
    q_name: str,
) -> tuple[str, tuple[str, ...], str, str | None]:
    "return the channel id, channel urls, channel name and en name. Raise KeyError if not found."

    # First check if a word starts with the query
    for chn_id, tup in channels_list.items():
        chn_urls, name, en_name = tup
        if any(
            word.lower().startswith(q_name.lower())
            for word in name.split() + (en_name or "").split()
        ):
            return chn_id, chn_urls, name, en_name
    # If not found, search query in string
    for chn_id, tup in channels_list.items():
        chn_urls, name, en_name = tup
        if q_name.lower() in name.lower() or (
            en_name and q_name.lower() in en_name.lower()
        ):
            return chn_id, chn_urls, name, en_name
    raise KeyError()


def get_all_chns_from_name(
    q_name: str,
) -> Generator[tuple[str, tuple[str, ...], str, str | None], None, None]:
    "return the channel id, channel urls, channel name and en name. Raise KeyError if not found."

    results = set()
    # First check if a word starts with the query
    for chn_id, tup in channels_list.items():
        chn_urls, name, en_name = tup
        if any(
            word.lower().startswith(q_name.lower())
            for word in name.split() + (en_name or "").split()
        ):
            result = chn_id, chn_urls, name, en_name
            yield result
            results.add(result)

    for chn_id, tup in channels_list.items():
        chn_urls, name, en_name = tup
        if q_name.lower() in name.lower() or (
            en_name and q_name.lower() in en_name.lower()
        ):
            result = chn_id, chn_urls, name, en_name
            try:
                results.remove(result)
            except KeyError:
                pass
            else:
                yield result


async def _get_stream_idurl(
    stream_name: str,
) -> tuple[str, str, Mapping | None, str | None]:
    """Return a tuple of:
    youtube video id, or stream url for other platforms
    Maybe stream's info_dict.
    Maybe channel url.
    """

    # Is it a youtube channel url?
    if chn_id := re.search(
        r"(?<=youtube\.com\/channel\/)([a-zA-Z0-9\-_]{24})(?![a-zA-Z0-9\-_])",
        stream_name,
    ):
        stream_name = chn_id.group()  # set to channel id

    if "." in stream_name:  # a url
        if "youtube.com/watch" in stream_name:
            yt_id = stream_name.split("=")[1]
            return yt_id, "yt", None, None
        elif "youtu.be/" in stream_name:
            yt_id = stream_name.split(".")[-1].split("/")[1]
            return yt_id, "yt", None, None

        elif "twitch.tv/videos/" in stream_name:  # twitch_vod
            twitch_url = stream_name
            return twitch_url, "ttv_vod", None, None

        elif "twitch.tv/" in stream_name:  # twitch channel, either live or latest vod
            # is it live
            info_dict = await aio.to_thread(fetch_yt_metadata, stream_name)
            streamer_name = re.search(
                r"(?<=\.tv\/)([a-zA-Z0-9\-_]+?)(?![a-zA-Z0-9\-_])", stream_name
            )
            assert streamer_name
            chn_url = "https://www.twitch.tv/" + streamer_name.group()
            if info_dict and info_dict.get("is_live"):  # is live
                return (
                    info_dict.get("webpage_url", stream_name),
                    "ttv_live",
                    info_dict,
                    chn_url,
                )
            else:
                # Get the latest VOD.
                info_dict = await aio.to_thread(
                    fetch_yt_metadata,
                    chn_url + "/videos",
                    no_playlist=False,
                    playlist_items=range(2),
                )
                assert info_dict
                last_entry: Mapping = info_dict["entries"][0]
                return last_entry["webpage_url"], "ttv_vod", last_entry, chn_url

        else:  # Unknown, try our best.  # TODO: Security vulnerability ???
            info_dict = await aio.to_thread(fetch_yt_metadata, stream_name)
            if info_dict:
                logger.warning(
                    f"Found data for unknown url: {stream_name}, {info_dict['webpage_url']}"
                )
                return (
                    info_dict["webpage_url"],
                    "unknown",
                    info_dict,
                    info_dict.get("channel_url") or info_dict.get("webpage_url"),
                )
            else:
                raise ValueError

    # either a youtube channel id, or youtube video id
    else:
        if len(stream_name) == 24:  # chn id
            # try to get stream if live
            base_url = "https://www.youtube.com/channel/" + stream_name
            info_dict = await aio.to_thread(fetch_yt_metadata, base_url + "/live")

            if info_dict and info_dict.get("is_live"):  # is live
                return (
                    info_dict["id"],
                    "yt",
                    info_dict,
                    "https://www.youtube.com/channel/" + stream_name,
                )

            else:  # is not live, get the last was_live vod
                info_dict_ls = await aio.to_thread(
                    fetch_yt_metadata,
                    base_url + "/videos?view=2&live_view=503",
                    no_playlist=False,
                    playlist_items=range(2),
                )
                info_dict_all = await aio.to_thread(
                    fetch_yt_metadata,
                    base_url + "/videos",
                    no_playlist=False,
                    playlist_items=range(2),
                )
                if info_dict_ls:
                    try:
                        ls_entry = info_dict_ls["entries"][0]
                    except (KeyError, IndexError):
                        ls_entry = None
                else:
                    ls_entry = None
                if info_dict_all:
                    try:
                        all_entry = info_dict_all["entries"][0]
                    except (KeyError, IndexError):
                        all_entry = None
                else:
                    all_entry = None

                if (
                    ls_entry
                    and all_entry
                    and (
                        all_entry.get("was_live")
                        or "live_chat" in all_entry.get("subtitles", [])
                    )
                ):
                    # Which is the earliest
                    last_live = (
                        ls_entry
                        if ls_entry["release_timestamp"]
                        > all_entry["release_timestamp"]
                        else all_entry
                    )
                else:
                    last_live = ls_entry or all_entry

                assert last_live
                return (
                    last_live["id"],
                    "yt",
                    last_live,
                    "https://www.youtube.com/channel/" + stream_name,
                )

        elif len(stream_name) == 11:  # video id
            return stream_name, "yt", None, None
        else:
            raise ValueError


chn_url_to_id = dict[str, str]()


async def get_stream(stream_name: str, *, __recurse=True) -> Stream:
    "Can raise ValueError"

    # Transform full channel url to a standard id. If not in dict, a fetch will be performed.
    if stream_name in chn_url_to_id:
        stream_name = chn_url_to_id[stream_name]

    # Is it a name.
    try:
        chn_id, chn_urls, name, en_nam = get_chns_from_name(stream_name)
    except KeyError:
        pass
    else:
        streams = list[Stream]()
        for chn in chn_urls:
            try:
                streams.append(await get_stream(chn))
            except (ValueError, AssertionError):
                pass
        if not streams:
            raise ValueError
        streams.sort(key=lambda s: s.start_time, reverse=True)
        return streams[0]

    id_url, platform, info_dict, chn_url = await _get_stream_idurl(stream_name)

    stream_url = (
        "https://www.youtube.com/watch?v=" + id_url if platform == "yt" else id_url
    )

    # Since yt-dlp is giving us exact actual_start values, we don't need Holodex.
    if not info_dict:
        try:
            info_dict = await aio.to_thread(fetch_yt_metadata, stream_url)
        except PayWalled:
            # Youtube, members only
            async with aiohttp.ClientSession() as session:
                info_dict = await holodex_req(
                    session, "videos/", url_param=stream_url[-11:], query_params={}
                )
                platform = "holodex"

    assert info_dict

    if not chn_url:
        if platform == "ttv_live":
            chn_url = stream_url
        elif platform == "ttv_vod":
            chn_url = "https://www.twitch.tv/" + info_dict["uploader_id"]
        elif platform == "holodex":
            chn_url = "https://www.youtube.com/channel/" + info_dict["channel"]["id"]
        else:
            chn_url = info_dict["channel_url"]

    start_time: int | str | None = (
        info_dict.get("timestamp")
        or info_dict.get("release_timestamp")
        or info_dict.get("start_actual")
        or info_dict.get("published_at")
        or info_dict.get("start_actual")
    )
    if not start_time:  # This may be a channel url (eg short channel name)
        if "channel_id" in info_dict and __recurse:
            chn_url_to_id[stream_name] = info_dict["channel_id"]
            return await get_stream(info_dict["channel_id"], __recurse=False)
        else:
            raise ValueError

    if isinstance(start_time, str):
        start_time = int(dateutil.parser.isoparse(start_time).timestamp())

    stream = Stream(
        unique_id=info_dict["id"],
        stream_id=info_dict["id"] if platform != "ttv_live" else None,
        stream_url=stream_url,
        chn_url=chn_url,
        inferred_start=start_time,
        actual_start=start_time,
        end_time=(dur := info_dict.get("duration")) and dur + start_time,
        info_dict=dict(info_dict),
        stream_url_temp=platform in ("ttv_live", "unknown"),
    )
    return stream
