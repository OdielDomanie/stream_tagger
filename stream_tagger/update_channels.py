from ast import literal_eval

from .streams import channels_list


def update_from_file(f_path="twitch.py"):
    with open(f_path) as f:
        channels_dict: dict = literal_eval(f.read())

        for chn_id, val in channels_dict.items():
            twitch_url = "https://www.twitch.tv/" + val["twitch"]
            if chn_id in channels_list:
                (chn_urls, streamer_name, en_name) = channels_list[chn_id]
                new_chn_urls = set(chn_urls + (twitch_url,))
                channels_list[chn_id] = (tuple(new_chn_urls), streamer_name, en_name)
            else:
                pass  # too much info would be missing
                # Also, "else" doesn't happen in practice
