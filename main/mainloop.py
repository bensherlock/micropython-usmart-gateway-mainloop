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
import utime

from pybd_expansion.main.max3221e import MAX3221E
from pybd_expansion.main.powermodule import PowerModule
import sensor_payload.main.sensor_payload as sensor_payload


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

_nm3_callback_flag = False
_nm3_callback_datetime = None
_nm3_callback_millis = None
_nm3_callback_micros = None
def nm3_callback(line):
    global _nm3_callback_flag
    global _nm3_callback_datetime
    global _nm3_callback_millis
    global _nm3_callback_micros
    # NM3 Callback function
    _nm3_callback_micros = pyb.micros()
    _nm3_callback_millis = pyb.millis()
    _nm3_callback_datetime = utime.localtime()
    _nm3_callback_flag = True



def do_local_sensor_reading():
    """Take readings from local sensors and send via wifi."""
    # Get from sensor payload: data as json
    jotter.get_jotter().jot("Acquiring sensor data.", source_file=__name__)
    sensor = sensor_payload.get_sensor_payload_instance()
    sensor.start_acquisition()
    while not sensor.is_completed():
        sensor.process_acquisition()

    sensor_data_json = sensor.get_latest_data_as_json()
    # sensor_data_str = json.dumps(sensor_data_json)
    # print(sensor_data_str)

    wifi_connected = is_wifi_connected()

    #
    # Send Sensor Readings to server
    #
    if wifi_connected:
        # Put to server: sensor payload data
        jotter.get_jotter().jot("Sending sensor data to server.", source_file=__name__)
        import mainloop.main.httputil as httputil
        http_client = httputil.HttpClient()
        import gc
        gc.collect()
        response = http_client.post('http://192.168.4.1:3000/sensors/', json=sensor_data_json)
        # Check for success - resend/queue and resend - TODO
        response = None
        gc.collect()




# Standard Interface for MainLoop
# - def run_mainloop() : never returns
def run_mainloop():
    """Standard Interface for MainLoop. Never returns."""
    global _rtc_callback_flag
    global _nm3_callback_flag
    global _nm3_callback_timestamp

    # Set RTC to wakeup at a set interval
    rtc = pyb.RTC()
    rtc.init()  # reinitialise - there were bugs in firmware. This wipes the datetime.
    # A default wakeup to start with. To be overridden by network manager/sleep manager
    rtc.wakeup(30 * 1000, rtc_callback)  # milliseconds

    # Enable the NM3 power supply on the powermodule
    powermodule = PowerModule()
    powermodule.enable_nm3()

    # Enable power supply to 232 driver
    pyb.Pin.board.EN_3V3.on()
    pyb.Pin('Y5', pyb.Pin.OUT, value=0)  # enable Y5 Pin as output
    max3221e = MAX3221E(pyb.Pin.board.Y5)
    max3221e.tx_force_on() # Enable Tx Driver

    # Set callback for nm3 pin change - line goes high on frame synchronisation
    nm3_extint = pyb.ExtInt(pyb.Pin.board.Y3, pyb.ExtInt.IRQ_FALLING, pyb.Pin.PULL_DOWN, nm3_callback)


    while True:
        try:

            # Start of the wake loop
            # Wifi connection will be needed for:
            # 1. Incoming NM3 MessagePackets (HW Wakeup)
            # 2. Periodic Sensor Readings (RTC)

            #
            # Connect to wifi
            #
            wifi_connected = is_wifi_connected()

            if not wifi_connected:
                # Connect to server over wifi
                wifi_cfg = load_wifi_config()
                if wifi_cfg:
                    wifi_connected = connect_to_wifi(wifi_cfg['wifi']['ssid'], wifi_cfg['wifi']['password'])

            # Put to server: current configuration information - module versions etc.

            #
            # 1. Incoming NM3 MessagePackets (HW Wakeup)
            #
            # _nm3_callback_flag
            # _nm3_callback_datetime
            # _nm3_callback_millis - loops after 12.4 days. pauses during sleep modes.
            # _nm3_callback_micros - loops after 17.8 minutes. pauses during sleep modes.



            #
            # 2. Periodic Sensor Readings (RTC)
            #
            # _rtc_callback_flag
            # If is time to take a sensor reading (eg hourly)
            do_local_sensor_reading()


            #
            # 3. Get NTP Network time from shore and set the RTC.
            #

            #
            # 4. Download configuration updates from server
            #
            # Get from server: UAC Network Configuration as json
            # Save to disk: UAC Network Configuration as json




            #
            # Prepare to sleep
            #

            # Disconnect from wifi
            disconnect_from_wifi()

            # Power down 3V3 regulator
            pyb.Pin.board.EN_3V3.off()

            # WLAN event may wake us up early.

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

            #
            # Wake up
            #

            # RTC or incoming NM3 packet? Only way to know is by flags set in the callbacks.
            # machine.wake_reason() is not implemented on PYBD!
            # https://docs.micropython.org/en/latest/library/machine.html
            jotter.get_jotter().jot("Wake up. _rtc_callback_flag=" + str(_rtc_callback_flag), source_file=__name__)



        except Exception as the_exception:
            jotter.get_jotter().jot_exception(the_exception)
            pass
            # Log to file

