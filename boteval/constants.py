import os
import logging

DEF_PORT = 7070
DEF_ADDR = '0.0.0.0'

ERROR = 'error'
SUCCESS = 'success'

DEF_MAX_TURNS_PER_THREAD = 100
BOT_DISPLAY_NAME = 'Moderator'

USER_ACTIVE_UPDATE_FREQ = 2 * 60 # seconds
MAX_PAGE_SIZE = 40

MAX_THREADS_PER_TOPIC = 3


ENV = {}
for env_key in ['GTAG']:
    ENV[env_key] = os.environ.get(env_key)


class Auth:
    ADMIN_USER = 'admin'
    BOT_USER = 'bot01'
    DEV_USER = 'dev'
    CONTEXT_USER = 'context'

    # TODO: find a better way to handle
    ADMIN_SECRET = os.environ.get('ADMIN_USER_SECRET', 'xyza')
    DEV_SECRET = os.environ.get('DEV_USER_SECRET', 'abcd')


MTURK = 'mturk'
MTURK_SANDBOX = 'mturk_sandbox'
MTURK_SANDBOX_URL = 'https://mturk-requester-sandbox.us-east-1.amazonaws.com'
AWS_MAX_RESULTS = 100 
MTURK_LOG_LEVEL = logging.INFO

