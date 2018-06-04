#!/usr/bin/env python2
import ConfigParser
import datetime
import dateutil
import ephem
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

def get_observer(config):
    if (not config.has_section('general')
        or not config.has_option('general', 'latitude')
        or not config.has_option('general', 'longitude')):
        return None

    obs = ephem.Observer()
    obs.lat = config.get('general', 'latitude')
    obs.long = config.get('general', 'longitude')

    return obs

def td_niceprint(tdelta):
    if not isinstance(tdelta, datetime.timedelta):
        tdelta = datetime.timedelta(seconds=tdelta)
    days = tdelta.days
    secs = tdelta.seconds
    mins = secs / 60
    secs -= (mins * 60)
    hours = mins / 60
    mins -= (hours * 60)

    outval = ""

    if days > 0:
        outval += "%d day%s, " % (days, '' if days == 1 else 's')

    outval += "%d:%02d:%02d" % (hours, mins, secs)

    return outval

def get_day_stats(config):
    obs = get_observer(config)
    if obs is None:
        return None

    prev_rise = obs.previous_rising(ephem.Sun())
    prev_set = obs.previous_setting(ephem.Sun())
    next_rise = obs.next_rising(ephem.Sun())
    next_set = obs.next_setting(ephem.Sun())

    up = next_rise > next_set
    rise_delta = obs.date - prev_rise if up else next_rise - obs.date
    set_delta = next_set - obs.date if up else obs.date - prev_set

    # the sun is up if the next sunrise is later than the next sunset
    if next_rise > next_set:
        # sun is up
        day_duration = next_set - prev_rise
        day_passed = obs.date - prev_rise
        day_remain = next_set - obs.date
        day_completed = day_passed / day_duration
    else:
        day_duration = next_set - next_rise
        day_passed = 1
        day_remain = 0
        day_completed = -1

    return {
        'sun': {
            'up': up,
            'rise': {
                'prev': prev_rise,
                'next': next_rise,
                'delta': datetime.timedelta(rise_delta),
                'str': ("%s ago" if up else "in %s") % td_niceprint(rise_delta*24*60*60),
            },
            'set': {
                'prev': prev_set,
                'next': next_set,
                'delta': datetime.timedelta(set_delta),
                'str': ("%s ago" if not up else "in %s") % td_niceprint(set_delta*24*60*60),
            },
        },
        'day': {
            'duration': day_duration,
            'time_passed': day_passed,
            'time_remaining': day_remain,
            'percent': day_completed,
        },
    }

if __name__ == '__main__':
    if len(sys.argv) == 1:
        print('need config file param')
        sys.exit(1)

    config = read_config_file(sys.argv[1])

    ds = get_day_stats(config)

    mastodon = get_mastodon(config)

    config = read_config_file(sys.argv[1])

    cleanup_old(mastodon, tags=["Webcam"])

    source_url = get_source_url(config)

    media_id = rehost_image(mastodon, source_url)

    my_toot = get_text(config)
    my_toot += "\n"
    my_toot += "\n"

    if ds['sun']['up']:
        my_toot += "Sunrise: %s\n" % (ds['sun']['rise']['str'])
        my_toot += "Sunset: %s\n" % (ds['sun']['set']['str'])
    else:
        my_toot += "Sunset: %s\n" % (ds['sun']['set']['str'])
        my_toot += "Sunrise: %s\n" % (ds['sun']['rise']['str'])

    visible = ds['sun']['up'] and is_visible(config)

    mastodon.status_post(my_toot,
                         media_ids=[media_id],
                         visibility='public' if visible else 'unlisted')

