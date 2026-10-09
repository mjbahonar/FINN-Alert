import json
import unittest
from unittest.mock import Mock, patch
from finn_alert import Bot, STOP, ServiceError, HttpError, TelegramMediaRejected, database, parse_photos, photo_messages

URL = "https://www.finn.no/recommerce/forsale/item/123"
PHOTO = "https://images.finncdn.no/dynamic/960w/item/123/a"


class PhotoTests(unittest.TestCase):
    def test_rejected_album_uploads_images_without_mutating_queue(self):
        bot = Bot({'telegram': {'bot_token': 'fake', 'chat_id': '-123'}}, None)
        message = photo_messages(URL, [PHOTO, PHOTO+'b'])[0]
        before = json.dumps(message)
        image = Mock()
        image.__enter__ = Mock(return_value=image)
        image.__exit__ = Mock(return_value=False)
        image.iter_content.return_value = [b'jpeg bytes']
        success = Mock()
        success.json.return_value = {'ok': True}
        with patch('finn_alert.request', side_effect=[HttpError(400), image, image, success]) as call:
            bot.send(message)
        uploaded = call.call_args.kwargs
        self.assertEqual(len(uploaded['files']), 2)
        self.assertEqual(json.loads(uploaded['data']['media'])[0]['media'], 'attach://photo0')
        self.assertEqual(json.dumps(message), before)

    def test_rejected_photo_does_not_block_next_listing(self):
        db = database(':memory:')
        try:
            bot = Bot({'search_url':URL, 'initial_mode':'send', 'request_delay_seconds':0}, db)
            bot.enqueue({'124': URL+'4', '123':URL})
            db.execute('UPDATE ads SET messages=? WHERE id=?', (json.dumps(photo_messages(URL,[PHOTO])), '123'))
            db.execute('UPDATE ads SET messages=? WHERE id=?', (json.dumps(['next listing']), '124'))
            db.commit()
            bot.send = Mock(side_effect=[TelegramMediaRejected('Rejected photos'), None])
            with patch.object(STOP, 'wait'):
                self.assertTrue(bot.deliver())
            self.assertEqual(db.execute("SELECT count(*) FROM ads WHERE status='sent'").fetchone()[0], 2)
        finally:
            db.close()

    def tearDown(self):
        STOP.clear()

    def test_gallery_deduplicates_and_excludes_related_and_unsafe_images(self):
        source = """<template><img id="image-0" data-src="https://images.finncdn.no/dynamic/960w/item/123/a">
        <img id="image-1" srcset="https://images.finncdn.no/dynamic/1600w/item/123/b 1600w, https://images.finncdn.no/dynamic/320w/item/123/b 320w">
        <img id="image-2" src="https://images.finncdn.no/dynamic/142w/item/123/a">
        <img src="https://images.finncdn.no/dynamic/960w/item/456/c">
        <img id="image-3" src="https://images.finncdn.no/dynamic/960w/item/456/d">
        <img id="image-4" src="https://evil.example/item/123/e"></template>"""
        self.assertEqual(parse_photos(source, URL), [PHOTO, PHOTO[:-1] + "b"])

    def test_thumbnail_and_single_meta_fallback(self):
        self.assertEqual(parse_photos('<img alt="Miniatyrgalleribilde" data-src="'+PHOTO+'">',URL),[PHOTO])
        self.assertEqual(parse_photos('<meta property="og:image" content="'+PHOTO+'">',URL),[PHOTO])
        self.assertEqual(parse_photos('<h1>No photos</h1>',URL),[])

    def test_all_photos_split_into_valid_groups_and_single_photo(self):
        photos = [PHOTO + str(i) for i in range(21)]
        messages = photo_messages(URL, photos)
        self.assertEqual([m['_method'] for m in messages], ['sendMediaGroup','sendMediaGroup','sendPhoto'])
        self.assertEqual([len(m['media']) for m in messages[:2]], [10,10])
        reconstructed = [p['media'] for m in messages[:2] for p in m['media']] + [messages[2]['photo']]
        self.assertEqual(reconstructed, photos)
        self.assertIn(URL, messages[0]['media'][0]['caption'])
        self.assertEqual(photo_messages(URL, []), [])

    def test_sender_routes_media_and_does_not_modify_cached_payload(self):
        bot = Bot({'telegram':{'bot_token':'123:fake','chat_id':'-123'}},None)
        response = Mock()
        response.json.return_value={'ok':True}
        for message in photo_messages(URL,[PHOTO,PHOTO+'b']) + photo_messages(URL,[PHOTO]):
            before=json.dumps(message)
            with patch('finn_alert.request',return_value=response) as call:
                bot.send(message)
            self.assertTrue(call.call_args.args[2].endswith('/'+message['_method']))
            self.assertNotIn('_method',call.call_args.kwargs['json'])
            self.assertEqual(json.dumps(message),before)

    def test_album_retry_resumes_without_resending_successful_album(self):
        db=database(':memory:')
        bot=Bot({'search_url':URL,'initial_mode':'send','request_delay_seconds':0},db)
        bot.enqueue({'123':URL})
        messages=photo_messages(URL,[PHOTO+str(i) for i in range(21)]) + [{'text':'Original and English'}]
        db.execute('UPDATE ads SET messages=?',(json.dumps(messages),))
        db.commit()
        bot.send=Mock(side_effect=[None,ServiceError('HTTP 429')])
        with patch.object(STOP,'wait',return_value=False):
            self.assertFalse(bot.deliver())
        self.assertEqual(db.execute('SELECT next_part,status FROM ads').fetchone(),(1,'pending'))
        bot.send=Mock()
        with patch.object(STOP,'wait',return_value=False):
            self.assertTrue(bot.deliver())
        self.assertEqual([c.args[0] for c in bot.send.call_args_list],messages[1:])
        self.assertEqual(db.execute('SELECT status FROM ads').fetchone()[0],'sent')
        db.close()

    def test_bilingual_text_precedes_photos_and_survives_translation_failure(self):
        bot=Bot({},None)
        bot.translate=Mock(return_value='English')
        bot.detail_metadata[URL]={'photos':[PHOTO,PHOTO+'b']}
        messages=bot.listing_messages(URL,'Original','Description')
        self.assertEqual(messages[-1]['_method'],'sendMediaGroup')
        self.assertIn('Original text:',messages[0]['text'])
        self.assertIn('Translated to English:',messages[0]['text'])
        bot.translate.side_effect=ServiceError('offline failed')
        bot.detail_metadata[URL]={'photos':[PHOTO]}
        messages=bot.listing_messages(URL,'Original','Description')
        self.assertEqual(messages[-1]['_method'],'sendPhoto')
        self.assertIn('Translation failed',messages[0]['text'])
