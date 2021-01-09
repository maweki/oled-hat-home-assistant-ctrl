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
HOLD_DURATION = 0.4

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
    items = {}
    idx = 0
    temp_now = 0
    today = (0,0,"")
    tomorrow = (0,0,"")
    timeout = 0
    notification = None
    hold = False

    @staticmethod
    def selected():
        return list(View.items.values())[View.idx]

    @staticmethod
    def asleep():
        return View.timeout > TIMEOUT

### DEVICETYPES ###

class Entity:
    def __init__(self, entity_obj, config):
        self._last_update = time.time()
        self._entity_obj = entity_obj
        self._config = config

    @property
    def entity_id(self):
        return self._entity_obj["entity_id"]

    @property
    def name(self):
        try:
            return self._entity_obj["attributes"]["friendly_name"].replace("_", " ")
        except:
            return ""

    @property
    def state(self):
        return self._entity_obj["state"]

    @property
    def type(self):
        return self._entity_obj["entity_id"].split('.')[0]

    def draw(self, context, start, font, invert=False):
        (draw_color, inv_color) = ("black", "white") if invert else ("white", "black")

        if self.type == "script":
            if self.state == "on":
                context.polygon([(0,start),(0,start+4),(2,start+2)], fill=draw_color)
                context.polygon([(2,start),(2,start+4),(4,start+2)], fill=draw_color)
            elif self.state == "off":
                context.polygon([(0,start),(0,start+4),(4,start+2)], fill=draw_color)

        if self.type == "group":
            if self.state == "on":
                context.rectangle([(0,start),(2,start+2)], fill=draw_color, outline=draw_color)
                context.rectangle([(2,start+2),(4,start+4)], fill=draw_color, outline=draw_color)
            elif self.state == "off":
                context.rectangle([(0,start),(2,start+2)], fill=inv_color, outline=draw_color)
                context.rectangle([(2,start+2),(4,start+4)], fill=inv_color, outline=draw_color)

        if self.type == "light":
            if self.state == "on":
                context.ellipse([(0,start),(4,start+4)], fill=draw_color, outline=draw_color)
            elif self.state == "off":
                context.ellipse([(0,start),(4,start+4)], fill=inv_color, outline=draw_color)

        if self.type == "switch":
            if self.state == "on":
                context.rectangle([(1,start),(3,start+4)], fill=draw_color, outline=draw_color)
            elif self.state == "off":
                context.rectangle([(1,start),(3,start+4)], fill=inv_color, outline=draw_color)

        context.text((7,start), self.name, fill=draw_color, font=font)

    def toggle(self):
        if self.type == "group" and self.state == "on":
            service = self._config.api + 'services/homeassistant/turn_off'
        else:
            service = self._config.api + 'services/homeassistant/toggle'

        ret = requests.post(
            service,
            data = json.dumps({"entity_id": self._entity_obj["entity_id"]}),
            headers=get_headers(self._config.token),
        )
        asyncio.create_task(self.update())

    async def update(self):
        endpoint = self._config.api + 'states/' + self._entity_obj["entity_id"]
        self._last_update = time.time()
        ret = requests.get(
            endpoint,
            headers=get_headers(self._config.token),
        )
        self._entity_obj = ret.json()

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

async def update_states(config, queue):
    while True:

        if not View.asleep():
            page = View.idx // 5
            local_idx = View.idx % 5
            items = set(list(View.items.values())[page*5:page*5+5]) | set(View.items[fav] for fav in View.favs if fav in View.items)
            for item in items:
                await item.update()
            await asyncio.sleep(5) # check and update every 5 seconds if not asleep
        else:
            await asyncio.sleep(1) # react immediately after waking up


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
    if View.asleep():
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
        View.selected().toggle()

@handle.register
def _(event: KeyAction, config):
    idx = [KEY1_PIN, KEY2_PIN, KEY3_PIN].index(event.action)

    if event.duration > HOLD_DURATION:
        # save
        View.favs[idx] = View.selected().entity_id
        with shelve.open('favs') as db:
            db['favs'] = View.favs
    else:
        if View.favs[idx] in View.items:
            View.items[View.favs[idx]].toggle()

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

def get_headers(token):
    return { "Authorization" : "Bearer " + token,
             "Content-Type" : "application/json",
           }

streams = [
    check_weather,
    tick,
    control,
    update_scripts,
    update_states,
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

    services = {}
    for state in ret.json():
        if state["entity_id"].startswith("script."):
            scriptname = state["entity_id"][7:]
            if scriptname in scripts_without_fields:
                services[state["entity_id"]] = Entity(state, config)
        if state["entity_id"].startswith("light.") or state["entity_id"].startswith("switch.") or state["entity_id"].startswith("group."):
            services[state["entity_id"]] = Entity(state, config)

    View.items = services

    with shelve.open('favs') as db:
        if 'favs' in db:
            View.favs = db['favs']


def render(device):
    if View.asleep():
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
            if fav in View.items:
                draw.text((123, n*6), str(n+1), anchor="lt", fill="white", font=font_small)
                View.items[fav].draw(draw, n*6, font_small)

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
        items = list(View.items.values())[page*5:page*5+5]
        for n, item in enumerate(items):
            if local_idx == n:
                invert = True
                if not View.hold:
                    draw.rectangle([(0,20+n*6),(128,26+n*6)], fill="white")
                else:
                    draw.rectangle([(1,21+n*6),(127,25+n*6)], fill="white")
            else:
                invert = False

            item.draw(draw, 21+n*6, font_small, invert)

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
