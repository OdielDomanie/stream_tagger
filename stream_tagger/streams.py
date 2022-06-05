import asyncio as aio
import logging
import re
import sqlite3
import time
from typing import Any, Generator, Iterable, Mapping
from urllib import parse

import aiohttp

from . import CHANNELS_LIST_DB, HOLODEX_TOKEN
from .stream import Stream
from .utils import ExpBackoff, PersistentDict
from .ytdl_extractor import fetch_yt_metadata


logger = logging.getLogger("taggerbot.stream_extractor")


# {chn_id: ((chn_url,), streamer_name, en_name)}
channels_list = PersistentDict[str, tuple[tuple[str, ...], str, str | None]](
    CHANNELS_LIST_DB, "channnels_list"
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
        last_update_ts = int((cur.fetchone() and cur.fetchone()[0]) or 0)
        sleep_for = UPDATE_INTV - (time.time() - last_update_ts)

        await aio.sleep(sleep_for)

        # await populate_channels_list()

        cur.execute("INSERT INTO 'last_update' VALUES (0, ?)", (int(time.time()),))

        con.commit()
        con.close()


def get_chns_from_name(
    q_name: str,
) -> tuple[str, tuple[str, ...], str, str | None]:
    "return the channel id, channel urls, channel name and en name. Raise KeyError if not found."
    for chn_id, tup in channels_list.items():
        chn_urls, name, en_name = tup
        if q_name in name or (en_name and q_name in en_name):
            return chn_id, chn_urls, name, en_name
    raise KeyError()


def get_all_chns_from_name(
    q_name: str,
) -> Generator[tuple[str, tuple[str, ...], str, str | None], None, None]:
    "return the channel id, channel urls, channel name and en name. Raise KeyError if not found."
    for chn_id, tup in channels_list.items():
        chn_urls, name, en_name = tup
        if q_name in name or (en_name and q_name in en_name):
            yield chn_id, chn_urls, name, en_name


async def _get_stream_idurl(
    stream_name: str,
) -> tuple[str, str, Mapping | None, str | None]:
    """Return a tuple of:
    youtube video id, or stream url for other platforms
    Maybe stream's info_dict.
    Maybe channel url
    """
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
                r"/(?<=\.tv\/)([a-zA-Z0-9\-_]+?)(?!\g<-1>)/gm", stream_name
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
                    playlist_items=range(1),
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
                    info_dict["channel_url"],
                )
            else:
                raise ValueError

    else:  # either a youtube channel id, youtube video id, or vtuber name

        try:
            chn_id, chn_urls, name, en_nam = get_chns_from_name(stream_name)
        except KeyError:
            pass
        else:
            for chn in chn_urls:
                try:
                    return await _get_stream_idurl(chn)
                except Exception:
                    pass
            raise ValueError

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
                info_dict = await aio.to_thread(
                    fetch_yt_metadata,
                    base_url,
                    no_playlist=False,
                    playlist_items=range(5),
                )
                assert info_dict
                last_live: Mapping | None = None
                for entry in info_dict["entries"]:
                    # Premieres don't show as "live", but they have live_chat
                    if entry.get("was_live") or "live_chat" in entry.get(
                        "subtitles", []
                    ):
                        last_live = entry
                        break
                assert last_live
                last_entry: Mapping = info_dict["entries"][0]
                return (
                    last_entry["id"],
                    "yt",
                    last_entry,
                    "https://www.youtube.com/channel/" + stream_name,
                )

        elif len(stream_name) == 11:  # video id
            return stream_name, "yt", None, None
        else:
            raise ValueError


async def get_stream(stream_name: str) -> Stream:
    id_url, platform, info_dict, chn_url = await _get_stream_idurl(stream_name)

    stream_url = (
        "https://www.youtube.com/watch?v=" + id_url if platform == "yt" else id_url
    )

    # Since yt-dlp is giving us exact actual_start values, we don't need Holodex.
    if not info_dict:
        info_dict = await aio.to_thread(fetch_yt_metadata, stream_url)
    assert info_dict

    if not chn_url:
        if platform == "ttv_live":
            chn_url = stream_url
        elif platform == "ttv_vod":
            chn_url = "https://www.twitch.tv/" + info_dict["uploader_id"]
        else:
            chn_url = info_dict["channel_url"]

    start_time: int | None = info_dict.get("timestamp") or info_dict.get(
        "release_timestamp"
    )
    assert start_time

    stream = Stream(
        unique_id=info_dict["id"],
        stream_id=info_dict["id"] if platform != "ttv_live" else None,
        stream_url=stream_url,
        chn_url=chn_url,
        inferred_start=start_time,
        actual_start=start_time,
        end_time=(dur := info_dict.get("duration")) and dur + start_time,
        info_dict=dict(info_dict),
        stream_url_temp=platform == "ttv_live",
    )
    return stream
