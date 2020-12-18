# Home Assistant OLED Hat Controller

This is an application to run home-assistant scripts using the waveshare 1.3" OLED HAT.

![HAT in Action](https://github.com/maweki/oled-hat-home-assistant-ctrl/blob/main/example_img.jpg?raw=true)

## Usage

Start it with `python3 main.py http://url-to-api/api/ here-comes-the-long-living-token-of-auser` and it will automatically retrieve available scripts without parameter arguments from the home-assistant instance.

* **UP/DOWN:** Select next/previous script
* **LEFT/RIGHT:** Skip a page of scripts
* **Hold Center Button:** Calls the script that currently is selected
* **Hold Key Button:** Saves the script that currently is selected as favorite in that position
* **Key Button:** Calls the script that currently is selected as favorite in that position

## Requirements

Infrastructure and Hardware:

* Network Access
* Home assistant somewhere with an available http api
* openweathermap as HA module
* Raspberry Pi with [1.3" OLED HAT from waveshare](https://www.waveshare.com/wiki/1.3inch_OLED_HAT)

Software

* Raspbian/Debian packages: `python3-dev, python3-pip, python3-pillow, libfreetype6-dev, libjpeg-dev, libopenjp2-7, python3-pil, zlib1g-dev, liblcms2-dev, libtiff5`
* Python packages: `luma.oled, requests`

## Font

Public Domain font by `vyznev`, https://fontstruct.com/fontstructions/show/1404171

## License

GPL3
