import logging

from stream_tagger.update_channels import update_from_file


logging.getLogger("taggerbot").addHandler(logging.StreamHandler())
logging.getLogger("taggerbot").setLevel(logging.DEBUG)


update_from_file()
