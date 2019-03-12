# -*- coding: utf-8 -*-

import os
import json

from third_party import source_utils
from utils import DEV_MODE

def get_json(json_url, filename):
    try:
        if DEV_MODE:
            raise

        response = source_utils.serenRequests().get(json_url)
        return json.loads(response.text)
    except:
        json_path = os.path.join(os.path.dirname(__file__), filename)
        with open(json_path) as hosters_json:
            return json.load(hosters_json)

trackers_json_url = 'https://raw.githubusercontent.com/newt-sc/a4kScrapers/master/providers/a4kScrapers/en/torrent/lib/trackers.json'
trackers = get_json(trackers_json_url, 'trackers.json')

hosters_json_url = 'https://raw.githubusercontent.com/newt-sc/a4kScrapers/master/providers/a4kScrapers/en/torrent/lib/hosters.json'
hosters = get_json(hosters_json_url, 'hosters.json')
