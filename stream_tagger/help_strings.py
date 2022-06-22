bot_desc = """Tagger bot!
https://github.com/OdielDomanie/stream_tagger/"""

tags_desc = """Dump the tags. Possible options:
stream: Vtuber name, channel url or stream url.
start: Manually provide a start time. Either 13:00:00 in UTC today, or a Unix timestamp.
duration: Use with start_time. Leave empty for until current time, if no stream is provided.
format: How to format the tags: alternative, classic, yt, yt-text, info, csv.
server: A server name or id to steal the tags from.
own: Only show your tags.
delete_last: Delete the latest tag. Don't use this with other options.
offset: Offset the time stamps.
min_stars: Minimum treshold for number of stars.

Example command that uses all the options:
tags selen classic start=13:01:00 duration=2:00:00 server=123456 own offset=30 min_stars=2
"""
