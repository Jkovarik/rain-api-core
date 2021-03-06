import logging
import os
import jwt
import base64
from boto3 import client as botoclient
from wsgiref.handlers import format_date_time as format_7231_date
from jinja2 import Environment, FileSystemLoader, select_autoescape, TemplateNotFound
from time import time

from .aws_util import retrieve_secret


log = logging.getLogger(__name__)

HTML_TEMPLATE_STATUS = ''
HTML_TEMPLATE_LOCAL_CACHEDIR = '/tmp/templates/'                         #nosec We want to leverage instance persistance

SESSTTL = int(os.getenv('SESSION_TTL', '168')) * 60 * 60

JWT_ALGO = os.getenv('JWT_ALGO', 'RS256')
JWT_KEYS = {}
JWT_COOKIE_NAME = os.getenv('JWT_COOKIENAME', 'asf-urs')


def get_jwt_keys():
    global JWT_KEYS

    if JWT_KEYS:
        # Cached
        return JWT_KEYS
    raw_keys = retrieve_secret(os.getenv('JWT_KEY_SECRET_NAME', ''))

    return_dict = {}

    for k in raw_keys:
        return_dict[k] = base64.b64decode(raw_keys[k].encode('utf-8'))

    JWT_KEYS = return_dict  # Cache it
    return return_dict


def cache_html_templates():
    try:
        os.mkdir(HTML_TEMPLATE_LOCAL_CACHEDIR, 0o700)
    except FileExistsError:
        # good.
        log.debug('somehow, {} exists already'.format(HTML_TEMPLATE_LOCAL_CACHEDIR))

    if os.getenv('HTML_TEMPLATE_DIR', '') == '':
        return 'DEFAULT'

    bucket = os.getenv('CONFIG_BUCKET')
    templatedir = os.getenv('HTML_TEMPLATE_DIR')
    if not templatedir[-1] == '/': #we need a trailing slash
        templatedir = '{}/'.format(templatedir)

    client = botoclient('s3')
    try:
        result = client.list_objects(Bucket=bucket, Prefix=templatedir, Delimiter='/')

        for o in result.get('Contents'):
            filename = os.path.basename(o['Key'])
            if filename:
                log.debug('attempting to save {}'.format(os.path.join(HTML_TEMPLATE_LOCAL_CACHEDIR, filename)))
                client.download_file(bucket, o['Key'], os.path.join(HTML_TEMPLATE_LOCAL_CACHEDIR, filename))
        return 'CACHED'
    except (TypeError, KeyError) as e:
        log.error(e)
        log.error('Trouble trying to download HTML templates from s3://{}/{}'.format(bucket, templatedir))
        return 'ERROR'


def get_html_body(template_vars: dict, templatefile: str='root.html'):

    global HTML_TEMPLATE_STATUS                                                       # pylint: disable=global-statement

    if HTML_TEMPLATE_STATUS == '':
        HTML_TEMPLATE_STATUS = cache_html_templates()

    jin_env = Environment(
        loader=FileSystemLoader([HTML_TEMPLATE_LOCAL_CACHEDIR, os.path.join(os.path.dirname(__file__), '../', "templates")]),
        autoescape=select_autoescape(['html', 'xml'])
    )
    try:
        jin_tmp = jin_env.get_template(templatefile)

    except TemplateNotFound as e:
        log.error('Template not found: {}'.format(e))
        return 'Cannot find the HTML template directory'

    return jin_tmp.render(**template_vars)


def get_cookie_vars(headers: dict):
    """
    Extracts and decodes and returns relevant cookies from http headers
    :param headers: dict of http headers
    :return: on success dict with keys env value of 'JWT_COOKIENAME' containing decoded jwt, 'urs-user-id', 'urs-access-token' on failure empty dict.
    :type: dict
    """
    cooks = get_cookies(headers)
    log.debug('cooks: {}'.format(cooks))
    cookie_vars = {}
    try:
        if JWT_COOKIE_NAME in cooks:
            decoded_payload = JWT_COOKIE_NAME
            cookie_vars.update({JWT_COOKIE_NAME: decoded_payload})
        else:
            log.debug('could not find jwt cookie in get_cookie_vars()')
            cookie_vars = {}
    except KeyError as e:
        log.debug('Key error trying to get cookie vars: {}'.format(e))
        cookie_vars = {}
    return cookie_vars


def get_exp_time():
    return int(time() + SESSTTL)


def get_cookie_expiration_date_str():
    return format_7231_date(get_exp_time())


def get_cookies(hdrs):

    cookies = {}
    pre_cookies = []
    if 'cookie' in hdrs:
        pre_cookies = hdrs['cookie'].split(';')
        for cook in pre_cookies:
            # print('x: {}'.format(cook))
            splitcook = cook.split('=')
            cookies.update({splitcook[0].strip(): splitcook[1].strip()})

    return cookies


def make_jwt_payload(payload, algo=JWT_ALGO):

    try:
        log.debug('using secret: {}'.format(os.getenv('JWT_KEY_SECRET_NAME', '')))
        encoded_bytes = jwt.encode(payload, get_jwt_keys()['rsa_priv_key'], algorithm=algo)
        encoded = encoded_bytes.decode('utf-8')
        return encoded
    except IndexError as e:
        log.error('jwt_keys may be malformed: ')
        log.error(e)
        return ''
    except (ValueError, AttributeError) as e:
        log.error('problem with encoding cookie: {}'.format(e))
        return ''


def decode_jwt_payload(jwt_payload, algo=JWT_ALGO):
    log.debug('pub key: "{}"'.format(get_jwt_keys()['rsa_pub_key']))
    try:
        cookiedecoded = jwt.decode(jwt_payload, get_jwt_keys()['rsa_pub_key'], algo)
    except jwt.ExpiredSignatureError as e:
        # Signature has expired
        log.info('JWT has expired')
        # TODO what more to do with this, if anything?
        return {}
    except jwt.InvalidSignatureError as e:
        log.info('JWT has failed verification. returning empty dict')
        return {}
    log.debug('cookiedecoded {}'.format(cookiedecoded))
    return cookiedecoded


def craft_cookie_domain_payloadpiece(cookie_domain):
    if cookie_domain:
        return '; Domain={}'.format(cookie_domain)

    return ''


def make_set_cookie_headers_jwt(payload, expdate='', cookie_domain=''):
    jwt_payload = make_jwt_payload(payload)
    cookie_domain_payloadpiece = craft_cookie_domain_payloadpiece(cookie_domain)

    if not expdate:
        expdate = get_cookie_expiration_date_str()
    headers = {'SET-COOKIE': '{}={}; Expires={}; Path=/{}'.format(JWT_COOKIE_NAME,
                                                                  jwt_payload,
                                                                  expdate,
                                                                  cookie_domain_payloadpiece)}
    return headers




