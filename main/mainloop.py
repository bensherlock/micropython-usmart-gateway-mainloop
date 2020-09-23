#! /usr/bin/env python
#
# MicroPython MainLoop for USMART Gateway Application.
#
# This file is part of micropython-usmart-gateway-mainloop
# https://github.com/bensherlock/micropython-usmart-gateway-mainloop
#
# Standard Interface for MainLoop
# - def run_mainloop() : never returns
#
# MIT License
#
# Copyright (c) 2020 Benjamin Sherlock <benjamin.sherlock@ncl.ac.uk>
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
#
"""MicroPython MainLoop for USMART Gateway Application."""

import json
import pyb
import machine

from pybd_expansion.main.powermodule import *
from sensor_payload.main.sensor_payload import *

import jotter


# WiFi
def load_wifi_config():
    """Load Wifi Configuration from JSON file."""
    wifi_config = None
    config_filename = '../../config/wifi_cfg.json'
    try:
        with open(config_filename) as json_config_file:
            wifi_config = json.load(json_config_file)
    except Exception:
        pass

    return wifi_config


# wifi_cfg['wifi']['ssid'], wifi_cfg['wifi']['password']
def connect_to_wifi(ssid, password):
    """Connect to the wifi. Return True if successful."""
    """Connects to the wifi with the given ssid and password."""
    import network
    sta_if = network.WLAN(network.STA_IF)
    if not sta_if.isconnected():
        print('connecting to network...')
        sta_if.active(True)
        #sta_if.config(antenna=1)  # select antenna, 0=chip, 1=external
        sta_if.connect(ssid, password)
        while not sta_if.isconnected():
            # Check the status
            status = sta_if.status()
            # Constants aren't implemented for PYBD as of MicroPython v1.13.
            # From: https://github.com/micropython/micropython/issues/4682
            # 'So "is-connecting" is defined as s.status() in (1, 2) and "is-connected" is defined as s.status() == 3.'
            #
            if status <= 0:
                # Error States?
                return False
            # if ((status == network.WLAN.STAT_IDLE) or (status == network.WLAN.STAT_WRONG_PASSWORD)
            #        or (status == network.WLAN.STAT_NO_AP_FOUND) or (status == network.WLAN.STAT_CONNECT_FAIL)):
            # Problems so return
            #    return False

    print('network config:', sta_if.ifconfig())
    return True


def is_wifi_connected():
    """Is the WiFi connected."""
    import network
    sta_if = network.WLAN(network.STA_IF)
    return sta_if.isconnected()


def disconnect_from_wifi():
    """Disconnect from the wifi and power down the wifi module."""
    import network
    sta_if = network.WLAN(network.STA_IF)
    if sta_if.isconnected():
        sta_if.disconnect()

    # Deactivate the WLAN
    sta_if.active(False)

_rtc_callback_flag = False
def rtc_callback(unknown):
    global _rtc_callback_flag
    # RTC Callback function - Toggle LED
    pyb.LED(2).toggle()
    _rtc_callback_flag = True


# Standard Interface for MainLoop
# - def run_mainloop() : never returns
def run_mainloop():
    """Standard Interface for MainLoop. Never returns."""
    global _rtc_callback_flag

    # Set RTC to wakeup at a set interval
    rtc = pyb.RTC()
    rtc.init()  # reinitialise - there were bugs in firmware. This wipes the datetime.
    # A default wakeup to start with. To be overridden by network manager/sleep manager
    rtc.wakeup(30 * 1000, rtc_callback)  # milliseconds

    while True:
        try:

            wifi_connected = is_wifi_connected()

            if not wifi_connected:
                # Connect to server over wifi
                wifi_cfg = load_wifi_config()
                if wifi_cfg:
                    wifi_connected = connect_to_wifi(wifi_cfg['wifi']['ssid'], wifi_cfg['wifi']['password'])

            # Put to server: current configuration information - module versions etc.

            # If is time to take a sensor reading (eg hourly)
            # Get from sensor payload: data as json
            jotter.get_jotter().jot("Acquiring sensor data.", source_file=__name__)
            sensor = get_sensor_payload_instance()
            sensor.start_acquisition()
            while not sensor.is_completed():
                sensor.process_acquisition()

            sensor_data_json = sensor.get_latest_data_as_json()
            #sensor_data_str = json.dumps(sensor_data_json)
            #print(sensor_data_str)

            if wifi_connected:
                # Put to server: sensor payload data
                jotter.get_jotter().jot("Sending data to server.", source_file=__name__)
                import mainloop.main.httputil as httputil
                http_client = httputil.HttpClient()
                import gc
                gc.collect()
                response = http_client.post('http://192.168.4.1:3000/sensors/', json=sensor_data_json)
                # Check for success - resend/queue and resend
                response = None
                gc.collect()

            # Get from logger: logs as json
            # Put to server: logs
            # Get from server: UAC Network Configuration as json
            # Save to disk: UAC Network Configuration as json

            # Get NTP Network time from shore and set the RTC.


            # Relay all incoming NM3 packets via wifi.

            # Disconnect from wifi
            disconnect_from_wifi()

            # Wait for WLAN to switch off before going to sleep. Otherwise WLAN event will wake us up early.

            # Sleep
            # a. Light Sleep
            #pyb.stop()
            jotter.get_jotter().jot("Going to lightsleep.", source_file=__name__)
            _rtc_callback_flag = False  # Clear the callback flags
            machine.lightsleep()
            # b. Deep Sleep - followed by hard reset
            # pyb.standby()
            # machine.deepsleep()
            # c. poll flag without sleeping
            #while not _rtc_callback_flag:
            #    continue
            #_rtc_callback_flag = False

            # Wake up
            # RTC or incoming NM3 packet? Only way to know is by flags set in the callbacks.
            # machine.wake_reason() is not implemented on PYBD!
            # https://docs.micropython.org/en/latest/library/machine.html
            jotter.get_jotter().jot("Wake up. _rtc_callback_flag=" + str(_rtc_callback_flag), source_file=__name__)


        except Exception as the_exception:
            jotter.get_jotter().jot_exception(the_exception)
            pass
            # Log to file

