#!/usr/bin/env python2
import ConfigParser
import datetime
import dateutil
import getpass
import os
import sys
import time
import readline
import requests
import tempfile

CONFIG_FILE=None
DEBUG=False

def read_config_file(filename=None):
    """Read and parse the configuration file, returning it as a ConfigParser
       object."""
    global CONFIG_FILE

    config = ConfigParser.RawConfigParser()

    if filename is None:
        filename = CONFIG_FILE

    config.read(filename)
    CONFIG_FILE = filename

    return config

def write_config_file(config):
    """Writes the configuration object to the previously-read config file."""
    global CONFIG_FILE

    if CONFIG_FILE is None:
        raise RuntimeError('CONFIG_FILE is None')

    with open(CONFIG_FILE, 'w') as fp:
        config.write(fp)

def is_visible(config):
    """Should this post be visible, based on the time since the last
       visible post?"""
    if not config.has_section('history'):
        config.add_section('history')
        write_config_file(config)

    if not config.has_option('history', 'last_visible_post'):
        config.set('history', 'last_visible_post', 1)
        write_config_file(config)

    if not config.has_option('history', 'visible_every'):
        config.set('history', 'visible_every', 25*60*60)
        write_config_file(config)

    last_post = config.getint('history', 'last_visible_post')
    visible_every = config.getint('history', 'visible_every')

    if last_post + visible_every < time.time():
        config.set('history', 'last_visible_post', int(time.time()))
        write_config_file(config)
        return True

    return False

def get_mastodon(config):
    """Returns a Mastodon connection object."""
    from mastodon import Mastodon

    if not config.has_section('mastodon'):
        config.add_section('mastodon')
        write_config_file(config)

    while not config.has_option('mastodon', 'MASTODON_INSTANCE'):
        inst_raw = ''
        while len(inst_raw) == 0:
            print("Please enter the hostname of your Mastodon instance.")
            inst_raw = raw_input('--> https://')

        instance = 'https://' + inst_raw

        print("You entered: %s" % instance)
        confirm = raw_input('Is this correct [Y/n]? ')

        if confirm in ['Y', 'y', '']:
            config.set('mastodon', 'MASTODON_INSTANCE', instance)
            write_config_file(config)

    # create client/app credentials
    if (not config.has_option('mastodon', 'MASTODON_CLIENT_ID')
        or not config.has_option('mastodon', 'MASTODON_CLIENT_SECRET')):
            client_id, client_secret = Mastodon.create_app('pic2masto',
                api_base_url=config.get('mastodon', 'MASTODON_INSTANCE'))

            config.set('mastodon', 'MASTODON_CLIENT_ID', client_id)
            config.set('mastodon', 'MASTODON_CLIENT_SECRET', client_secret)
            write_config_file(config)

    # Log in
    if not config.has_option('mastodon', 'MASTODON_USER_SECRET'):
        mastodon = Mastodon(
                    client_id=config.get('mastodon', 'MASTODON_CLIENT_ID'),
                    client_secret=config.get('mastodon', 'MASTODON_CLIENT_SECRET'),
                    api_base_url=config.get('mastodon', 'MASTODON_INSTANCE'))
        print("Logging into %s..." % config.get('mastodon', 'MASTODON_INSTANCE'))
        username = raw_input('E-mail address: ')
        password = getpass.getpass('Password: ')
        access_token = mastodon.log_in(username, password)
        config.set('mastodon', 'MASTODON_USER_SECRET', access_token)
        write_config_file(config)

    return Mastodon(
            client_id=config.get('mastodon', 'MASTODON_CLIENT_ID'),
            client_secret=config.get('mastodon', 'MASTODON_CLIENT_SECRET'),
            api_base_url=config.get('mastodon', 'MASTODON_INSTANCE'),
            access_token=config.get('mastodon', 'MASTODON_USER_SECRET'))

def get_source_url(config):
    if (not config.has_section('webcam')
        or not config.has_option('webcam', 'URL')):
        raise RuntimeError("specify URL in webcam section of config")

    return config.get('webcam', 'URL')

def get_text(config):
    if (not config.has_section('webcam')
        or not config.has_option('webcam', 'text')):
        return ""

    return config.get('webcam', 'text')

def rehost_image(m, url):
    """Pulls an image from a URL and rehosts it to Mastodon, returning the
       media object."""
    r = requests.get(url)
    if r.status_code is 200:
        mimetype = r.headers.get('Content-Type', 'application/octet-stream')
        return m.media_post(r.content, mime_type=mimetype)

    return None

def status_iter(m, limit=20, min_days=0, tags=[], include_favorites=True):
    me = m.account_verify_credentials()
    max_id = None
    min_td = datetime.timedelta(days=min_days)
    tags = [t.lower() for t in tags]

    while limit > 0:
        #print("Fetching block (max_id %d, remaining %d)" % (max_id or -1, limit))
        statuses = m.account_statuses(me, max_id=max_id, limit=40)

        if len(statuses) == 0:
            break

        for s in statuses:
            candidate = False

            if max_id is None or max_id > s.id:
                max_id = s.id

            td = datetime.datetime.now(tz=dateutil.tz.tzutc()) - s.created_at
            #print("Considering: %d (%s) td=%s vs %s" % (s.id, s.created_at, td, min_td))

            candidate = td > min_td
            candidate = candidate and (include_favorites or (s.favourites_count == 0 and s.reblogs_count == 0))

            if candidate and len(tags) > 0:
                tag_found = False
                for t in s.tags:
                    tag_found = tag_found or t.name.lower() in tags

            if candidate:
                yield s
                limit -= 1

            if limit <= 0:
                break

def cleanup_old(m, min_days=30, tags=[]):
    for s in status_iter(m, min_days=min_days, tags=tags, include_favorites=False):
        #print("Deleting status: %d" % s.id)
        m.status_delete(s)

if __name__ == '__main__':
    if len(sys.argv) == 1:
        print('need config file param')
        sys.exit(1)

    config = read_config_file(sys.argv[1])

    mastodon = get_mastodon(config)

    config = read_config_file(sys.argv[1])

    cleanup_old(mastodon, tags=["Webcam"])

    source_url = get_source_url(config)

    media_id = rehost_image(mastodon, source_url)

    my_toot = get_text(config)

    mastodon.status_post(my_toot,
                         media_ids=[media_id],
                         visibility='public' if is_visible(config) else 'unlisted')


