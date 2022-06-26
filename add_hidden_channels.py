from stream_tagger.streams import hidden_chns


with open("omitted_chns.txt") as om_f:
    for line in om_f:
        chn_id = line.split()[0]

        hidden_chns[chn_id] = ""
