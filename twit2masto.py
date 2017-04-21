#!/usr/bin/env python2
import os

CONFIG_DIR=os.path.expanduser("~/.config/twit2masto")

def create_config_dir():
    # generate config dir if req'd
    if not os.path.isdir(CONFIG_DIR):
        if os.path.exists(CONFIG_DIR):
            raise "%s already exists and is not a directory" % CONFIG_DIR
        os.makedirs(CONFIG_DIR, 0700)

def get_twitter():
    import app_credentials
    import twitter

    # twitter oauth dance
    create_config_dir()

    MY_TWITTER_CREDS = os.path.join(CONFIG_DIR, 'twitter_user_credentials')
    if not os.path.exists(MY_TWITTER_CREDS):
        twitter.oauth_dance("twit2masto",
            app_credentials.TWITTER_CONSUMER_KEY,
            app_credentials.TWITTER_CONSUMER_SECRET,
            MY_TWITTER_CREDS)

    oauth_token, oauth_secret = twitter.read_token_file(MY_TWITTER_CREDS)

    return twitter.Twitter(auth=twitter.OAuth(oauth_token, oauth_secret,
        app_credentials.TWITTER_CONSUMER_KEY,
        app_credentials.TWITTER_CONSUMER_SECRET))

def get_mastodon():
    import getpass
    from mastodon import Mastodon
    import readline

    create_config_dir()

    # instance memory
    INSTANCE_FILE = os.path.join(CONFIG_DIR, 'mastodon_instance')
    while not os.path.exists(INSTANCE_FILE) or os.stat(INSTANCE_FILE).st_size == 0:
        inst_raw = ''
        while len(inst_raw) == 0:
            print("Please enter the hostname of your Mastodon instance.")
            inst_raw = raw_input('--> https://')

        instance = 'https://' + inst_raw

        print("You entered: %s" % instance)
        confirm = raw_input('Is this correct [Y/n]? ')

        if confirm in ['Y', 'y', '']:
            with open(INSTANCE_FILE, 'w') as fd:
                fd.write(instance + '\n')

    INSTANCE = open(INSTANCE_FILE, 'r').readline().strip()

    # create client/app credentials
    CLIENT_CREDS = os.path.join(CONFIG_DIR, 'mastodon_client_credentials')
    if not os.path.exists(CLIENT_CREDS):
        Mastodon.create_app('twit2masto',
            api_base_url=INSTANCE,
            to_file=CLIENT_CREDS)

    # Log in
    USER_CREDS = os.path.join(CONFIG_DIR, 'mastodon_user_credentials')
    if not os.path.exists(USER_CREDS):
        mastodon = Mastodon(client_id=CLIENT_CREDS, api_base_url=INSTANCE)
        print("Logging into %s..." % INSTANCE)
        username = raw_input('E-mail address: ')
        password = getpass.getpass('Password: ')
        mastodon.log_in(username, password, to_file=USER_CREDS)

    return Mastodon(api_base_url=INSTANCE, client_id=CLIENT_CREDS, access_token=USER_CREDS)

def get_twitter_whoami(t):
    return t.account.settings(_method="GET")['screen_name']

def get_twitter_statuses(t, screen_name, since=None, count=5):
    if since is not None:
        return t.statuses.user_timeline(screen_name=u'BSidesROC', since_id=since, count=count)

    return t.statuses.user_timeline(screen_name=u'BSidesROC', count=count)

def set_twitter_high_water_mark(last):
    HWM_FILE = os.path.join(CONFIG_DIR, 'twitter_high_water_mark')
    with open(HWM_FILE, 'w') as fd:
        fd.write(str(last))

def get_twitter_high_water_mark():
    HWM_FILE = os.path.join(CONFIG_DIR, 'twitter_high_water_mark')
    hwm = None

    if os.path.exists(HWM_FILE):
        with open(HWM_FILE, 'r') as fd:
            try:
                hwm = int(fd.read())
            except:
                pass

    return hwm

def rehost_image(m, url):
    import requests
    import tempfile
    r = requests.get(url)
    if r.status_code is 200:
        mimetype = r.headers.get('Content-Type', 'application/octet-stream')
        return m.media_post(r.content, mime_type=mimetype, is_raw_data=True)

    return None

if __name__ == '__main__':
    twitter = get_twitter()
    mastodon = get_mastodon()

    # get latest twitter stuff
    me_twitter = get_twitter_whoami(twitter)
    hwm = get_twitter_high_water_mark()

    twits = get_twitter_statuses(twitter, me_twitter, hwm)
    twits.reverse()

    # send it to the mastodon
    for t in twits:
        t_url = "https://twitter.com/%s/status/%d" % (t['user']['screen_name'], t['id'])
        print t['id'], t['created_at']
        if hwm is None or t['id'] > hwm: hwm = t['id']

        pics = None

        if 'entities' in t:
            if 'media' in t['entities']:
                for media in t['entities']['media']:
                    if 'media_url_https' in media:
                        media_id = rehost_image(mastodon, media['media_url_https'])
                        if pics is None: pics = []
                        pics.append(media_id)

        my_toot = t['text'] + '\n\n' + "via #twit2masto\n" + t_url

        mastodon.status_post(my_toot, media_ids=pics)

    # don't do anything more
    set_twitter_high_water_mark(hwm)

