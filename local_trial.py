"""Run a bounded local trial with an isolated database and one initial sample."""
import argparse
import logging
from pathlib import Path
import threading
import time

from finn_alert import Bot, STOP, ServiceError, database, load_config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=int, default=900)
    args = parser.parse_args()
    if args.seconds <= 0:
        parser.error('--seconds must be positive')
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    config = load_config(Path('config.json').resolve())
    config['initial_mode'] = 'skip'
    path = Path('trial-' + str(time.time_ns()) + '.sqlite3')
    db = database(path)
    bot = Bot(config, db)
    deadline = time.monotonic() + args.seconds
    timer = threading.Timer(args.seconds, STOP.set)
    timer.daemon = True
    timer.start()
    polls = 0
    seeded = False
    logging.info('Trial started for %s seconds; isolated database: %s', args.seconds, path.name)
    try:
        while not STOP.is_set():
            started = time.monotonic()
            polls += 1
            try:
                items = bot.discover()
                bot.enqueue(items)
                if not seeded:
                    # Baseline old ads; attempt only one existing ad as a sample.
                    ident = next(iter(items))
                    with db:
                        db.execute("UPDATE ads SET status='pending' WHERE id=?", (ident,))
                    seeded = True
                bot.deliver()
            except ServiceError as exc:
                logging.error('Trial poll failed: %s', exc)
            counts = dict(db.execute('SELECT status,count(*) FROM ads GROUP BY status'))
            logging.info('Poll %s finished; counts=%s; remaining=%ss', polls, counts, max(0, int(deadline-time.monotonic())))
            STOP.wait(max(1, config['poll_interval_seconds'] - (time.monotonic() - started)))
    except KeyboardInterrupt:
        STOP.set()
    finally:
        timer.cancel()
        logging.info('Trial finished after %s polls; counts=%s', polls, dict(db.execute('SELECT status,count(*) FROM ads GROUP BY status')))
        db.close()


if __name__ == '__main__':
    main()
