import asyncio
import functools
from functools import partial, singledispatch
import requests
import json
from collections import namedtuple, deque
from luma.core.interface.serial import spi
from luma.core.render import canvas
from luma.oled.device import sh1106
from PIL import ImageFont, Image
import datetime
import shelve
import time
import sys

TIMEOUT = 300
HOLD_DURATION = 0.5

KEY_UP_PIN     = 6
KEY_DOWN_PIN   = 19
KEY_LEFT_PIN   = 5
KEY_RIGHT_PIN  = 26
KEY_PRESS_PIN  = 13

KEY1_PIN       = 21
KEY2_PIN       = 20
KEY3_PIN       = 16

class View:
    favs = ["","",""]
    items = []
    idx = 0
    temp_now = 0
    today = (0,0,"")
    tomorrow = (0,0,"")
    timeout = 0
    notification = None
    hold = False


### EVENTTYPES ###

class Event:
    def __init__(self, **kwargs):
        for (kw, val) in kwargs.items():
            self.__setattr__(kw, val)

class TimeoutTick(Event):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

class TimeoutReset(Event):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

class StickAction(Event):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

class KeyAction(Event):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

class SetNotification(Event):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

class Hold(Event):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

class UnHold(Event):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

Weather = namedtuple('Weather', ['now', 'today', 'tomorrow'])


### STREAMERS ###

async def tick(config, queue):
    while True:
        await asyncio.sleep(1)
        await queue.put(TimeoutTick())

async def update_scripts(config, queue):
    while True:
        await asyncio.sleep(60*60) # check and update hourly
        init_view(config)

async def control(config, queue):
    import RPi.GPIO as GPIO

    GPIO.setmode(GPIO.BCM)
    stick = [KEY_UP_PIN, KEY_DOWN_PIN, KEY_LEFT_PIN, KEY_RIGHT_PIN, KEY_PRESS_PIN]
    keys  = [KEY1_PIN, KEY2_PIN, KEY3_PIN]

    for pin in stick + keys:
        GPIO.setup(pin,      GPIO.IN, pull_up_down=GPIO.PUD_UP)      # Input with pull-up

    last_action = None
    hold_sent = False
    duration = 0
    while True:
        await asyncio.sleep(0.020)
        duration += 0.020
        action = None
        for pin in stick + keys:
            if not GPIO.input(pin):
                action = pin
        if action != last_action:
            if last_action in stick:
                await queue.put(StickAction(action=last_action, duration=duration))
            if last_action in keys:
                await queue.put(KeyAction(action=last_action, duration=duration))
            await queue.put(TimeoutReset())
            duration = 0
        if action and not hold_sent and duration > HOLD_DURATION:
            hold_sent = True
            await queue.put(Hold())
        if not action and hold_sent:
            hold_sent = False
            await queue.put(UnHold())
        last_action = action

async def check_weather(config, queue):
    weather_cache = None
    endpoint = config.api + 'states/weather.openweathermap'
    headers = get_headers(config.token)
    while True:
        try:
            ret = requests.get(
                endpoint,
                headers=headers,
            )
            attr = ret.json()["attributes"]
            temp_now = attr["temperature"]
            forecast_today = (attr["forecast"][0]["templow"], attr["forecast"][0]["temperature"], attr["forecast"][0]["condition"])
            forecast_tomorrow = (attr["forecast"][1]["templow"], attr["forecast"][1]["temperature"], attr["forecast"][1]["condition"])
            weather = Weather(temp_now, forecast_today, forecast_tomorrow)
            if weather != weather_cache:
                weather_cache = weather
                await queue.put(weather)
        except Exception as e:
            await queue.put(e)
        await asyncio.sleep(10*60)

### EVENTHANDLERS ###

@singledispatch
def handle(event, _):
    print("UNHANDLED EVENT", event)

@handle.register
def _(event: Weather, _):
    print("Weather:", str(event))
    View.temp_now = event.now
    View.today = event.today
    View.tomorrow = event.tomorrow

@handle.register
def _(event: StickAction, config):
    if View.timeout > TIMEOUT:
        return

    if event.action == KEY_UP_PIN:
        View.idx = (View.idx - 1 + len(View.items)) % len(View.items)
    if event.action == KEY_DOWN_PIN:
        View.idx = (View.idx + 1) % len(View.items)
    if event.action == KEY_RIGHT_PIN:
        View.idx = min(View.idx + 5, len(View.items) - 1)
    if event.action == KEY_LEFT_PIN:
        View.idx = max(View.idx - 5, 0)
    if event.action == KEY_PRESS_PIN and event.duration > HOLD_DURATION:
        return call_service(config, View.items[View.idx])  ## needs to be async

@handle.register
def _(event: KeyAction, config):
    idx = [KEY1_PIN, KEY2_PIN, KEY3_PIN].index(event.action)

    if event.duration > HOLD_DURATION:
        # save
        View.favs[idx] = View.items[View.idx]
        with shelve.open('favs') as db:
            db['favs'] = View.favs
    else:
        return call_service(config, View.favs[idx])

@handle.register
def _(event: TimeoutTick, _):
    View.timeout = View.timeout + 1

@handle.register
def _(event: TimeoutReset, _):
    View.timeout = 0

@handle.register
def _(event: Hold, _):
    View.hold = True

@handle.register
def _(event: UnHold, _):
    View.hold = False

@handle.register
def _(event: SetNotification, _):
    if not event.text:
        time.sleep(2)
    View.notification = event.text
    if event.text:
        return SetNotification(text=None)

@handle.register
def _(event: requests.Response, _):
    print("Response:", event.content)

### MAIN ###

def call_service(config, servicename): ## needs to be async
    print("CALL SERVICE", servicename, config)
    endpoint = config.api + 'services/script/' + servicename
    headers = get_headers(config.token)
    requests.post(
        endpoint,
        headers=headers,
    )
    return SetNotification(text=servicename)

def get_headers(token):
    return { "Authorization" : "Bearer " + token,
             "Content-Type" : "application/json",
           }

streams = [
    check_weather,
    tick,
    control,
    update_scripts,
]

def init_view(config):
    headers = get_headers(config.token)
    ret = requests.get(
        config.api + 'services',
        headers=headers,
    )
    for domain in ret.json():
        if domain["domain"] == "script":
            break
    scripts_without_fields = list(service for service in domain["services"] if not domain["services"][service]["fields"])

    ret = requests.get(
        config.api + 'states',
        headers=headers,
    )

    services = []
    for state in ret.json():
        if state["entity_id"].startswith("script."):
            scriptname = state["entity_id"][7:]
            if scriptname in scripts_without_fields:
                services.append(scriptname)
    View.items = services

    with shelve.open('favs') as db:
        if 'favs' in db:
            View.favs = db['favs']


def render(device):
    if View.timeout > TIMEOUT:
        device.hide()
        return
    else:
        device.show()
        device.contrast(0)

    with canvas(device) as draw:
        font_large = ImageFont.truetype("4x5.ttf", 10)
        font_small = ImageFont.truetype("4x5.ttf", 5)

        # Favorites
        for n, fav in enumerate(View.favs):
            draw.text((0, n*6), str(n+1) + ": " + fav.replace("_", " "), anchor="lt", fill="white", font=font_small)

        # full horizontal divider
        draw.line([(0,51),(128,51)], fill="white")

        if not View.notification:
            # Current Temperature
            draw.text((34, 63), '{:3.1f}'.format(View.temp_now), anchor="rb", fill="white", font=font_large)

            # Vertical divider
            draw.line([(33,51),(33,64)], fill="white")

            # Today's weather
            draw.text((64, 58), '{:3.1f}'.format(View.today[1]), anchor="rb", fill="white", font=font_small)
            draw.text((64, 64), '{:3.1f}'.format(View.today[0]), anchor="rb", fill="white", font=font_small)
            img = 'img/' + View.today[2] + '.bmp'
            try:
                bmp = Image.open(img).convert("1")
                draw.bitmap((35,53), bmp, fill="white")
            except Exception as e:
                print(e)

            # Vertical Divider
            draw.line([(64,51),(64,64)], fill="white")

            # Tomorrow's weather
            draw.text((95, 64), '{:3.1f}'.format(View.tomorrow[0]), anchor="rb", fill="white", font=font_small)
            draw.text((95, 58), '{:3.1f}'.format(View.tomorrow[1]), anchor="rb", fill="white", font=font_small)
            img = 'img/' + View.tomorrow[2] + '.bmp'
            try:
                bmp = Image.open(img).convert("1")
                draw.bitmap((66,53), bmp, fill="white")
            except Exception as e:
                print(e)

            # Vertical Divider
            draw.line([(95,51),(95,64)], fill="white")

            # Current date and time
            now = datetime.datetime.now()
            draw.text((129, 58), now.strftime("%a%H:%M"), anchor="rb", fill="white", font=font_small)
            draw.text((129, 64), now.strftime("%d.%b"), anchor="rb", fill="white", font=font_small)
        else:
            draw.text((0, 63), View.notification.replace("_", " "), anchor="lb", fill="white", font=font_large)

        # full horizontal divider
        draw.line([(0,18),(128,18)], fill="white")

        # Script list
        page = View.idx // 5
        local_idx = View.idx % 5
        items = View.items[page*5:page*5+5]
        for n, item in enumerate(items):
            if local_idx == n:
                draw.rectangle([(0,20+n*6),(128,26+n*6)], fill="white")
                if not View.hold:
                    draw.polygon([(0,21+n*6),(0,25+n*6),(4,23+n*6)], fill="black")
                else:
                    draw.rectangle([(0,21+n*6),(4,25+n*6)], fill="black")
                draw.text((6,21+n*6), items[n].replace("_", " "), fill="black", font=font_small)
            else:
                draw.text((6,21+n*6), items[n].replace("_", " "), fill="white", font=font_small)

async def main():

    import argparse
    parser = argparse.ArgumentParser(description='OLED HAT Home-Assistant Control')
    parser.add_argument('api', type=str, help="Example: http://192.168.1.4:8123/api/")
    parser.add_argument('token', type=str, help='long-living api token (can be obtained in profile)')
    config = parser.parse_args()

    # Init Config
    init_view(config)

    # Init Display
    serial = spi(device=0, port=0)
    device = sh1106(serial, rotate=2)

    # Init streamers
    eventqueue = asyncio.Queue()
    for s in streams:
        asyncio.create_task(s(config, eventqueue))

    while True:
        evnt = await eventqueue.get()
        return_event = handle(evnt, config)
        render(device)
        if return_event:
            asyncio.create_task(eventqueue.put(return_event))

if __name__ == '__main__':
    from locale import setlocale, LC_TIME
    setlocale(LC_TIME, "")
    asyncio.run(main())
