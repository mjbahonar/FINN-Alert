import unittest
from unittest.mock import patch
from postal_distance import approximate_distance, validate_distance
from finn_alert import Bot

CONFIG = {'enabled': True, 'origin_latitude': 60.37891870202219, 'origin_longitude': 5.356616006956909}


class DistanceTests(unittest.TestCase):
    def test_geometry_and_postal_code_leading_zero(self):
        with patch('postal_distance.postal_coordinates', return_value={'0123': [0, 1], '5056': [CONFIG['origin_latitude'], CONFIG['origin_longitude']]}):
            self.assertEqual(approximate_distance('0123 Oslo', {'enabled': True, 'origin_latitude': 0, 'origin_longitude': 0}), 111.2)
            self.assertEqual(approximate_distance('5056 Bergen', CONFIG), 0)
            self.assertIsNone(approximate_distance('9999 Unknown', CONFIG))
            self.assertIsNone(approximate_distance('Bergen', CONFIG))

    def test_disabled_never_loads_data(self):
        with patch('postal_distance.postal_coordinates') as lookup:
            self.assertIsNone(approximate_distance('5056 Bergen', {'enabled': False}))
            lookup.assert_not_called()

    def test_invalid_coordinates(self):
        for values in [{'enabled': 'true'}, {**CONFIG, 'origin_latitude': 91}, {**CONFIG, 'origin_longitude': float('nan')}]:
            with self.assertRaises(ValueError):
                validate_distance(values)

    def test_distance_display_and_real_dataset(self):
        bot = Bot({'distance': CONFIG}, None)
        bot.translate = lambda _: 'English'
        bot.detail_metadata['url'] = {'address': '5056 Bergen'}
        message = bot.listing_messages('url', 'Title', 'Description')[0]['text']
        self.assertIn('Approx. distance: 2.4 km', message)
        self.assertNotIn(str(CONFIG['origin_latitude']), message)
