"""Offline approximate straight-line distances using GeoNames postal centroids."""
import json
import math
from pathlib import Path
import re
from functools import lru_cache


def validate_distance(config):
    if not isinstance(config, dict) or not isinstance(config.get('enabled', False), bool):
        raise ValueError('distance.enabled must be true or false')
    if config.get('enabled', False):
        for key, limit in [('origin_latitude', 90), ('origin_longitude', 180)]:
            value = config.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or abs(value) > limit:
                raise ValueError(f'distance.{key} is not a valid coordinate')


@lru_cache(maxsize=1)
def postal_coordinates():
    return json.loads((Path(__file__).parent / 'data' / 'postal_codes_no.json').read_text(encoding='utf-8'))


def approximate_distance(address, config):
    if not config.get('enabled', False):
        return None
    # FINN's location starts with a four-digit Norwegian postal code.
    match = re.match(r'^\s*(\d{4})(?:\s|$)', address)
    if not match:
        return None
    destination = postal_coordinates().get(match.group(1))
    if destination is None:
        return None
    lat1, lon1, lat2, lon2 = map(math.radians, [config['origin_latitude'], config['origin_longitude'], *destination])
    a = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return round(6371.0088 * 2 * math.asin(math.sqrt(min(1, max(0, a)))), 1)
