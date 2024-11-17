import asyncio
import json
import os
from datetime import datetime
from pprint import pprint
from typing import Callable, Awaitable, Optional

import yaml
from discord_webhook import DiscordWebhook
from dotenv import load_dotenv
from ring_doorbell import Auth, AuthenticationError, Requires2FAError, Ring, RingEventListener, RingEvent

from otp_provider import OtpProvider

import paho.mqtt.client as mqtt

load_dotenv()

USER_AGENT = os.getenv('USER_AGENT', 'ring-mqtt-bridge')
TOKEN_CACHE_FILE = os.getenv('TOKEN_CACHE_FILE', 'token.cache')
CREDENTIALS_CACHE_FILE = os.getenv('CREDENTIALS_CACHE_FILE', 'credentials.cache')

DISCORD_WEBHOOK_URL = os.getenv('DISCORD_WEBHOOK_URL')

RING_USERNAME = os.getenv('RING_USERNAME')
RING_PASSWORD = os.getenv('RING_PASSWORD')

MQTT_BROKER_ADDRESS = os.getenv('MQTT_BROKER_ADDRESS', 'localhost')
MQTT_BROKER_PORT = int(os.getenv('MQTT_BROKER_PORT', '1883'))
MQTT_QOS = int(os.getenv('MQTT_QOS', '0'))
MQTT_RETAIN = os.getenv('MQTT_RETAIN', 'true') == 'true'

OTP_SERVER_PORT = int(os.getenv('OTP_SERVER_PORT', '5000'))
PROXIED_OTP_URL = os.getenv('PROXIED_OTP_URL', f'http://localhost:{OTP_SERVER_PORT}')

SECONDS_UNTIL_IDLE = int(os.getenv('SECONDS_UNTIL_IDLE', '150'))

DEVICES_CONFIG_FILE = os.getenv('DEVICES_CONFIG_FILE', 'devices.yml')
with open(DEVICES_CONFIG_FILE, 'r') as file:
    DEVICES = yaml.safe_load(file)['devices']

otp_provider = OtpProvider(OTP_SERVER_PORT)

mqttc: Optional[mqtt.Client] = None

last_updated: dict[str, datetime] = {}  # dict[topic, last_update_time]


async def provide_otp() -> str:
    message = f'Please go to {PROXIED_OTP_URL} and provide the Ring OTP.'

    print(message)
    webhook = DiscordWebhook(url=DISCORD_WEBHOOK_URL, content=message)
    webhook.execute()

    return await otp_provider.provide_otp()


async def setup_ring(user_agent: str, username: str, password: str, otp_provider: Callable[[], Awaitable[str]], token_cache_file='token.cache') -> Ring:
    def token_updated(_token) -> None:
        with open(token_cache_file, 'w') as _file:
            _file.write(json.dumps(_token))

    async def do_auth():
        _auth = Auth(user_agent, None, token_updated)
        try:
            await _auth.async_fetch_token(username, password)
        except Requires2FAError:
            otp = await otp_provider()
            await _auth.async_fetch_token(username, password, otp)
        return _auth

    if os.path.isfile(token_cache_file):  # auth token is cached
        with open(token_cache_file, 'r') as file:
            token = json.loads(file.read())

        auth = Auth(user_agent, token, token_updated)
        ring = Ring(auth)

        try:
            await ring.async_create_session()  # auth token still valid
        except AuthenticationError:  # auth token has expired
            auth = await do_auth()
            ring = Ring(auth)
    else:
        auth = await do_auth()  # Get new auth token
        ring = Ring(auth)

    await ring.async_update_data()
    return ring


async def setup_ring_event_listener(ring: Ring, credentials_cache_file='credentials.cache') -> RingEventListener:
    def credentials_updated(_credentials) -> None:
        with open(credentials_cache_file, 'w') as _file:
            _file.write(json.dumps(_credentials))

    credentials = None
    if os.path.isfile(credentials_cache_file):
        with open(credentials_cache_file, 'r') as file:
            credentials = json.loads(file.read())

    event_listener = RingEventListener(ring, credentials, credentials_updated)
    await event_listener.start()
    return event_listener


def ring_event_handler(event: RingEvent):
    device_config = DEVICES.get(event.device_name, None)
    if device_config is None:
        print(f'Received event from unconfigured device \'{event.device_name}\'')
        pprint(event)
        return

    print(f'Received event from device \'{event.device_name}\'')
    pprint(event)

    parameters = {
        'id': event.id,
        'doorbot_id': event.doorbot_id,
        'device_name': event.device_name,
        'device_kind': event.device_kind,
        'now': event.now,
        'expires_in': event.expires_in,
        'kind': event.kind,
        'state': event.state,
        'is_update': event.is_update,
    }

    for topic_format, value_format in device_config['topics']:
        topic = topic_format.format(**parameters)
        value = value_format.format(**parameters)

        mqttc.publish(topic, value, qos=MQTT_QOS, retain=MQTT_RETAIN)
        last_updated[topic] = datetime.now()


def update_idle_topics():
    now = datetime.now()
    for topic, update_time in list(last_updated.items()):
        since_updated = (now - update_time).total_seconds()
        if since_updated > SECONDS_UNTIL_IDLE:
            print(f'Setting idle state for topic {topic}')
            mqttc.publish(topic, 'idle', qos=MQTT_QOS, retain=MQTT_RETAIN)
            del last_updated[topic]


async def main() -> None:
    global mqttc

    print(f'ring-mqtt-bridge version {os.getenv("IMAGE_VERSION")}')

    print(f'{USER_AGENT=}')
    print(f'{TOKEN_CACHE_FILE=}')
    print(f'{CREDENTIALS_CACHE_FILE=}')
    print(f'DISCORD_WEBHOOK_URL={DISCORD_WEBHOOK_URL[:36]}...')
    print(f'{RING_USERNAME=}')
    print(f'RING_PASSWORD={"*" * len(RING_PASSWORD)}')
    print(f'{OTP_SERVER_PORT=}')
    print(f'{PROXIED_OTP_URL=}')
    print(f'{SECONDS_UNTIL_IDLE=}')
    print(f'{DEVICES_CONFIG_FILE=}')
    print(f'{DEVICES=}')

    server_task = asyncio.create_task(otp_provider.start_server())
    server_task.add_done_callback(lambda x: print('OTP server exited.'))

    mqttc = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    mqttc.connect(MQTT_BROKER_ADDRESS, MQTT_BROKER_PORT, 60)
    mqttc.loop_start()

    ring = await setup_ring(USER_AGENT, RING_USERNAME, RING_PASSWORD, otp_provider=provide_otp, token_cache_file=TOKEN_CACHE_FILE)
    event_listener = await setup_ring_event_listener(ring, credentials_cache_file=CREDENTIALS_CACHE_FILE)

    event_listener.add_notification_callback(ring_event_handler)

    print('Listening...')
    while True:
        update_idle_topics()
        await asyncio.sleep(1.0)


if __name__ == "__main__":
    asyncio.run(main())
