"""Check configured services; optionally send ONE Telegram test message."""
import argparse
from pathlib import Path

from finn_alert import Bot, ServiceError, load_config, parse_detail, parse_search, request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path('config.json'))
    parser.add_argument('--send-test', action='store_true')
    args = parser.parse_args()
    try:
        config = load_config(args.config.resolve())
    except Exception as exc:
        print('Config: FAILED (' + type(exc).__name__ + ')')
        return 1
    bot = Bot(config, None)
    failed = False

    def check(name, action):
        nonlocal failed
        try:
            result = action()
            print(name + ': OK', flush=True)
            return result
        except Exception as exc:
            failed = True
            detail = str(exc) if isinstance(exc, ServiceError) else type(exc).__name__
            print(name + ': FAILED (' + detail + ')', flush=True)

    def telegram(method, payload=None):
        token = config['telegram']['bot_token']
        data = request(bot.session, 'POST', f'https://api.telegram.org/bot{token}/{method}', json=payload or {}).json()
        if not data.get('ok'):
            raise ServiceError('Telegram rejected the request')
        return data['result']

    check('Telegram token (getMe)', lambda: telegram('getMe'))
    chat = check('Telegram destination (getChat)', lambda: telegram('getChat', {'chat_id': config['telegram']['chat_id']}))
    if args.send_test and chat:
        check('Telegram test delivery', lambda: bot.send('FINN Alert: test successful. Bot can send messages to this chat.'))
    items = check('FINN search', lambda: parse_search(bot.fetch(config['search_url']), config['search_url'])[0])
    detail = None
    if items:
        print('Listing count: ' + str(len(items)), flush=True)
        detail = check('FINN listing description', lambda: parse_detail(bot.fetch(next(iter(items.values())))))
    text = '\n\n'.join(detail) if detail else 'Hei verden'
    check('Translation (' + config['translation']['provider'] + ')', lambda: bot.translate(text))
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
