#!/usr/bin/env python2
import ConfigParser
import getpass
import os
import sys
import time
import readline
import requests
import tempfile

CONFIG_FILE=None
MAX_COUNT=1
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

def is_list(config):
    """Are we configured to gate a Twitter list?"""
    if not config.has_section('twitter'):
        config.add_section('twitter')
        write_config_file(config)

    return (config.has_option('twitter', 'twitter_list_owner')
        and config.has_option('twitter', 'twitter_list_name'))

def is_user(config):
    """Are we configured to gate a Twitter user timeline?"""
    if not config.has_section('twitter'):
        config.add_section('twitter')
        write_config_file(config)

    return config.has_option('twitter', 'twitter_screen_name')

def is_pics_only_feed(config):
    """Are we configured to omit non-media posts (i.e. pics only)?"""
    if not config.has_section('general'):
        config.add_section('general')
        write_config_file(config)

    return (config.has_option('general', 'pics_only')
        and config.getboolean('general', 'pics_only'))

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

def get_twitter(config):
    """Returns a Twitter connection object."""
    import app_credentials
    import twitter

    if not config.has_section('twitter'):
        config.add_section('twitter')
        write_config_file(config)

    if (not config.has_option('twitter', 'TWITTER_OAUTH_TOKEN')
        or not config.has_option('twitter', 'TWITTER_OAUTH_SECRET')):
            oauth_token, oauth_token_secret = twitter.oauth_dance(
                "twit2masto",
                app_credentials.TWITTER_CONSUMER_KEY,
                app_credentials.TWITTER_CONSUMER_SECRET)
            config.set('twitter', 'TWITTER_OAUTH_TOKEN', oauth_token)
            config.set('twitter', 'TWITTER_OAUTH_SECRET', oauth_token_secret)
            write_config_file(config)

    return twitter.Twitter(auth=twitter.OAuth(
        config.get('twitter', 'TWITTER_OAUTH_TOKEN'),
        config.get('twitter', 'TWITTER_OAUTH_SECRET'),
        app_credentials.TWITTER_CONSUMER_KEY,
        app_credentials.TWITTER_CONSUMER_SECRET))

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
            client_id, client_secret = Mastodon.create_app('twit2masto',
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

def get_twitter_whoami(t):
    return t.account.settings(_method="GET")['screen_name']

def get_twitter_statuses(config, t, since=None, count=20):
    if not config.has_section('twitter'):
        config.add_section('twitter')
        write_config_file(config)

    if is_user(config):
        return t.statuses.user_timeline(
                screen_name=config.get('twitter', 'TWITTER_SCREEN_NAME'),
                since_id=since, count=count)

    elif is_list(config):
        return t.lists.statuses(
                owner_screen_name=config.get('twitter', 'twitter_list_owner'),
                slug=config.get('twitter', 'twitter_list_name'),
                since_id=since, count=count)

    else:
        raise RuntimeError('need more config: TWITTER_SCREEN_NAME or TWITTER_LIST_(OWNER,NAME)')

def set_twitter_high_water_mark(config, last):
    """Set the marker for the latest Twitter status processed."""
    if not config.has_section('twitter'):
        config.add_section('twitter')

    config.set('twitter', 'HIGH_WATER_MARK', last)
    write_config_file(config)

def get_twitter_high_water_mark(config):
    """Get the marker for the latest Twitter status processed."""
    if (not config.has_section('twitter')
        or not config.has_option('twitter', 'HIGH_WATER_MARK')):
            return 1

    return config.getint('twitter', 'HIGH_WATER_MARK')

def rehost_image(m, url):
    """Pulls an image from a URL and rehosts it to Mastodon, returning the
       media object."""
    r = requests.get(url)
    if r.status_code is 200:
        mimetype = r.headers.get('Content-Type', 'application/octet-stream')
        return m.media_post(r.content, mime_type=mimetype)

    return None

if __name__ == '__main__':
    if len(sys.argv) == 1:
        print('need config file param')
        sys.exit(1)

    config = read_config_file(sys.argv[1])

    twitter = get_twitter(config)
    config = read_config_file(sys.argv[1])
    mastodon = get_mastodon(config)

    # get latest twitter stuff
    #me_twitter = get_twitter_whoami(twitter)
    hwm = get_twitter_high_water_mark(config)
    config = read_config_file(sys.argv[1])

    twits = get_twitter_statuses(config, twitter, hwm)
    twits.reverse()

    # send it to the mastodon
    countdown = MAX_COUNT

    for t in twits:
        t_url = "https://twitter.com/%s/status/%d" % (t['user']['screen_name'], t['id'])
        if DEBUG: print(t['id'], t['created_at'], t['user']['screen_name'], "considering")
        if hwm is None or t['id'] > hwm: hwm = t['id']

        pics = None

        if 'entities' in t:
            if 'media' in t['entities']:
                for media in t['entities']['media']:
                    if 'media_url_https' in media:
                        if pics is None: pics = []
                        media_id = rehost_image(mastodon, media['media_url_https'])
                        pics.append(media_id)
                        if DEBUG: print(t['id'], t['created_at'], t['user']['screen_name'], "media added", media, media_id)

        if (pics is None or len(pics) == 0) and is_pics_only_feed(config):
            if DEBUG: print(t['id'], t['created_at'], t['user']['screen_name'], "skipping due to no pics")
            continue

        my_toot = "%s\n\n---\n * Origin: Twitter (%s)\n#bot" % (t['text'], t_url)

        if is_list(config):
            my_toot = "From: @%s@twitter.com\n\n%s" % (t['user']['screen_name'], my_toot)

        mastodon.status_post(my_toot, media_ids=pics, visibility='public' if is_visible(config) else 'unlisted')

        countdown -= 1
        if countdown <= 0:
            break

    # don't do anything more
    set_twitter_high_water_mark(config, hwm)

