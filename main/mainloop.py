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
from ucollections import deque
import utime

from pybd_expansion.main.max3221e import MAX3221E
from pybd_expansion.main.powermodule import PowerModule
import sensor_payload.main.sensor_payload as sensor_payload

from uac_modem.main.unm3driver import MessagePacket, Nm3


import jotter

import micropython
micropython.alloc_emergency_exception_buf(100)
# https://docs.micropython.org/en/latest/reference/isr_rules.html#the-emergency-exception-buffer


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
        sta_if.config(antenna=1)  # select antenna, 0=chip, 1=external
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


# wifi_cfg['wifi']['ssid'], wifi_cfg['wifi']['password']
def start_connect_to_wifi(ssid, password):
    """Connect to the wifi. Return True if started ok."""
    """Starts connecting to the wifi with the given ssid and password. Returns before completion."""
    import network
    sta_if = network.WLAN(network.STA_IF)
    if not sta_if.isconnected():
        print('connecting to network...')
        sta_if.active(True)
        sta_if.config(antenna=1)  # select antenna, 0=chip, 1=external
        sta_if.connect(ssid, password)

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

    return True


def is_wifi_connecting():
    """Is the wifi currently trying to connect."""
    import network
    sta_if = network.WLAN(network.STA_IF)
    # Check the status
    status = sta_if.status()
    # Constants aren't implemented for PYBD as of MicroPython v1.13.
    # From: https://github.com/micropython/micropython/issues/4682
    # 'So "is-connecting" is defined as s.status() in (1, 2) and "is-connected" is defined as s.status() == 3.'
    #
    if status <= 0:
        # Error States?
        return False

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
    # NB: You cannot do anything that allocates memory in this interrupt handler.
    global _rtc_callback_flag
    # RTC Callback function - Toggle LED
    pyb.LED(2).toggle()
    _rtc_callback_flag = True


_nm3_callback_flag = False
_nm3_callback_seconds = 0  # used with utime.localtime(_nm3_callback_seconds) to make a timestamp
_nm3_callback_millis = 0  # loops after 12.4 days. pauses during sleep modes.
_nm3_callback_micros = 0  # loops after 17.8 minutes. pauses during sleep modes.


def nm3_callback(line):
    # NB: You cannot do anything that allocates memory in this interrupt handler.
    global _nm3_callback_flag
    global _nm3_callback_seconds
    global _nm3_callback_millis
    global _nm3_callback_micros
    # NM3 Callback function
    _nm3_callback_micros = pyb.micros()
    _nm3_callback_millis = pyb.millis()
    _nm3_callback_seconds = utime.time()
    _nm3_callback_flag = True


def do_local_sensor_reading():
    """Take readings from local sensors and send via wifi."""
    # Get from sensor payload: data as json
    #jotter.get_jotter().jot("Acquiring sensor data.", source_file=__name__)
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
        #jotter.get_jotter().jot("Sending sensor data to server.", source_file=__name__)
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
    global _nm3_callback_seconds
    global _nm3_callback_millis
    global _nm3_callback_micros

    # Set RTC to wakeup at a set interval
    rtc = pyb.RTC()
    rtc.init()  # reinitialise - there were bugs in firmware. This wipes the datetime.
    # A default wakeup to start with. To be overridden by network manager/sleep manager
    rtc.wakeup(1 * 60 * 1000, rtc_callback)  # milliseconds

    # Enable the NM3 power supply on the powermodule
    powermodule = PowerModule()
    powermodule.enable_nm3()

    # Enable power supply to 232 driver
    pyb.Pin.board.EN_3V3.on()
    pyb.Pin('Y5', pyb.Pin.OUT, value=0)  # enable Y5 Pin as output
    max3221e = MAX3221E(pyb.Pin.board.Y5)
    max3221e.tx_force_off()  # Disable Tx Driver

    # Set callback for nm3 pin change - line goes high on frame synchronisation
    # make sure it is clear first
    nm3_extint = pyb.ExtInt(pyb.Pin.board.Y3, pyb.ExtInt.IRQ_RISING, pyb.Pin.PULL_DOWN, None)
    nm3_extint = pyb.ExtInt(pyb.Pin.board.Y3, pyb.ExtInt.IRQ_RISING, pyb.Pin.PULL_DOWN, nm3_callback)

    # Serial Port/UART is opened with a 100ms timeout for reading - non-blocking.
    uart = machine.UART(1, 9600, bits=8, parity=None, stop=1, timeout=100)
    nm3_modem = Nm3(input_stream=uart, output_stream=uart)

    operating_mode = 1  # Mark Two

    last_nm3_message_received_time = utime.time()

    # Micropython needs a defined size of deque
    json_to_send_messages = deque((), 10)
    json_to_send_statuses = deque((), 10)

    # Wifi issues
    # https://github.com/micropython/micropython/issues/4681
    # The wifi may get stuck in a "connecting" state. Try timeouts and restart the process.
    wifi_connecting_start_time = 0

    while True:
        try:

            # Mark One
            # - Continually poll for incoming NM3 messages
            # - Then enable wifi and send to server
            # - After a time of no NM3 messages disable the wifi
            if operating_mode == 0:

                nm3_modem.poll_receiver()
                nm3_modem.process_incoming_buffer()

                wifi_connected = is_wifi_connected()

                if nm3_modem.has_received_packet() and not wifi_connected:
                    print("Has received nm3 message. Connecting to wifi")
                    # Connect to server over wifi
                    wifi_cfg = load_wifi_config()
                    if wifi_cfg:
                        wifi_connected = connect_to_wifi(wifi_cfg['wifi']['ssid'],
                                                         wifi_cfg['wifi']['password'])

                while nm3_modem.has_received_packet():
                    print("Has received nm3 message.")
                    last_nm3_message_received_time = utime.time()

                    message_packet = nm3_modem.get_received_packet()
                    # Copy the HW triggered timestamps over
                    message_packet.timestamp = utime.localtime(_nm3_callback_seconds)
                    message_packet.timestamp_millis = _nm3_callback_millis
                    message_packet.timestamp_micros = _nm3_callback_micros

                    # Send packet onwards
                    message_packet_json = message_packet.json()

                    wifi_connected = is_wifi_connected()
                    if wifi_connected:
                        # Put to server: sensor payload data
                        # jotter.get_jotter().jot("Sending nm3 message packet to server.", source_file=__name__)
                        print("Sending nm3 message to server")
                        import mainloop.main.httputil as httputil
                        http_client = httputil.HttpClient()
                        import gc
                        gc.collect()
                        response = http_client.post('http://192.168.4.1:3000/messages/',
                                                    json=message_packet_json)
                        # Check for success - resend/queue and resend - TODO
                        response = None
                        gc.collect()

                if utime.time() > last_nm3_message_received_time + 30:
                    # Disable the wifi
                    disconnect_from_wifi()

            # Mark Two
            # - Use the HW interrupt of NM3 Flag to wake-up
            # - poll for incoming NM3 messages
            # - Then enable wifi and send to server
            # - After a time of no NM3 messages disable the wifi and go to sleep or wfi
            # - Periodically send a status message over wifi
            elif operating_mode == 1:

                # Cause of wakeup
                # A) NM3 HW Callback Flag
                #    Poll the UART for packets
                #    Put packets as json strings into a queue
                # B) RTC Callback Flag
                #    Get some sensor data
                #    Put data as json string into a queue

                # While there are messages to be sent try and connect to the wifi
                # if connected to wifi send a message

                # If no flag is set and no messages to be sent (or unable to connect to wifi)
                # Go to sleep/wait for interrupt

                if _rtc_callback_flag:
                    _rtc_callback_flag = False  # Clear the flag
                    jotter.get_jotter().jot("RTC Flag. Getting sensor data.", source_file=__name__)
                    # battery
                    vbatt = powermodule.get_vbatt_reading()

                    # sensor payload
                    sensor = sensor_payload.get_sensor_payload_instance()
                    sensor.start_acquisition()
                    sensor_acquisition_start = utime.time()
                    while (not sensor.is_completed()) and (utime.time() < sensor_acquisition_start + 5):
                        sensor.process_acquisition()

                    sensor_data_json = sensor.get_latest_data_as_json()

                    status_json = {"Timestamp": utime.time(),
                                   "VBatt": vbatt,
                                   "Sensors": sensor_data_json}
                    json_to_send_statuses.append(status_json)

                # If we're within 30 seconds of the last timestamped NM3 synch arrival then poll for messages.
                if utime.time() < _nm3_callback_seconds + 30:
                    _nm3_callback_flag = False  # clear the flag
                    # There may or may not be a message for us. And it could take up to 0.5s to arrive at the uart.

                    nm3_modem.poll_receiver()
                    nm3_modem.process_incoming_buffer()

                    while nm3_modem.has_received_packet():
                        print("Has received nm3 message.")
                        jotter.get_jotter().jot("Has received nm3 message.", source_file=__name__)

                        message_packet = nm3_modem.get_received_packet()
                        # Copy the HW triggered timestamps over
                        message_packet.timestamp = utime.localtime(_nm3_callback_seconds)
                        message_packet.timestamp_millis = _nm3_callback_millis
                        message_packet.timestamp_micros = _nm3_callback_micros

                        # Send packet onwards
                        message_packet_json = message_packet.json()

                        # Append to the queue
                        json_to_send_messages.append(message_packet_json)

                # If messages or statuses are in the queue
                if json_to_send_messages or json_to_send_statuses:

                    wifi_connected = is_wifi_connected()

                    if not wifi_connected and not is_wifi_connecting():
                        # Start the connecting to the wifi
                        print("Has messages to send. Connecting to wifi.")
                        jotter.get_jotter().jot("Has messages to send. Connecting to wifi.", source_file=__name__)
                        # Connect to server over wifi
                        wifi_cfg = load_wifi_config()
                        if wifi_cfg:
                            # wifi_connected = connect_to_wifi(wifi_cfg['wifi']['ssid'],
                            #                                 wifi_cfg['wifi']['password'])  # blocking
                            start_connect_to_wifi(wifi_cfg['wifi']['ssid'],
                                                  wifi_cfg['wifi']['password'])  # non-blocking

                            wifi_connecting_start_time = utime.time()
                        else:
                            # Unable to ever connect
                            print("Unable to load wifi config data so cannot connect to wifi. Clearing any messages.")
                            jotter.get_jotter().jot("Unable to load wifi config data so cannot connect to wifi. "
                                                    "Clearing any messages.",
                                                    source_file=__name__)
                            json_to_send_messages.clear()
                            json_to_send_statuses.clear()

                    elif wifi_connected:
                        # Send the messages
                        print("Connected to wifi. Sending message to server.")
                        jotter.get_jotter().jot("Connected to wifi. Sending message to server.", source_file=__name__)
                        import mainloop.main.httputil as httputil
                        http_client = httputil.HttpClient()
                        import gc
                        while json_to_send_messages:
                            try:
                                message_packet_json = json_to_send_messages.popleft()
                                gc.collect()
                                response = http_client.post('http://192.168.4.1:8080/messages/',
                                                            json=message_packet_json)
                                # Check for success - resend/queue and resend - TODO
                                response = None
                                gc.collect()
                            except Exception as the_exception:
                                jotter.get_jotter().jot_exception(the_exception)
                                pass

                        while json_to_send_statuses:
                            try:
                                status_json = json_to_send_statuses.popleft()
                                gc.collect()
                                response = http_client.post('http://192.168.4.1:8080/statuses/',
                                                            json=status_json)
                                # Check for success - resend/queue and resend - TODO
                                response = None
                                gc.collect()
                            except Exception as the_exception:
                                jotter.get_jotter().jot_exception(the_exception)
                                pass

                    elif is_wifi_connecting() and (utime.time() > wifi_connecting_start_time + 30):
                        # Has been trying to connect for 30 seconds.
                        print("Connecting to wifi took too long. Disconnecting to retry.")
                        jotter.get_jotter().jot("Connecting to wifi took too long. Disconnecting to retry.", source_file=__name__)
                        # Disable the wifi
                        disconnect_from_wifi()


                # If no messages in the queue and too long since last synch and not rtc callback
                if (not json_to_send_messages) and (not json_to_send_statuses) \
                        and (utime.time() > _nm3_callback_seconds + 30) and not _rtc_callback_flag:
                    # Disable the wifi
                    disconnect_from_wifi()
                    jotter.get_jotter().jot("Going to sleep.", source_file=__name__)
                    while (not _rtc_callback_flag) and (not _nm3_callback_flag):
                        # Now wait
                        pyb.wfi()

                pass

            # Mark Three
            # - Network Manager TDA-MAC and RTC to control wakeup and sleep timing.
            elif operating_mode == 2:

                # Enable power supply to 232 driver
                pyb.Pin.board.EN_3V3.on()

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
                if _nm3_callback_flag:
                    _nm3_callback_flag = False  # Clear flag
                    # Packet incoming - although it may not be for us - try process for 2 seconds
                    start_millis =  pyb.millis()

                    while pyb.elapsed_millis(start_millis) < 2000:

                        nm3_modem.poll_receiver()
                        nm3_modem.process_incoming_buffer()

                        while nm3_modem.has_received_packet():
                            message_packet = nm3_modem.get_received_packet()
                            # Copy the HW triggered timestamps over
                            message_packet.timestamp = utime.localtime(_nm3_callback_seconds)
                            message_packet.timestamp_millis = _nm3_callback_millis
                            message_packet.timestamp_micros = _nm3_callback_micros

                            # Send packet onwards
                            message_packet_json = message_packet.json()

                            wifi_connected = is_wifi_connected()
                            if wifi_connected:
                                # Put to server: sensor payload data
                                #jotter.get_jotter().jot("Sending nm3 message packet to server.", source_file=__name__)
                                import mainloop.main.httputil as httputil
                                http_client = httputil.HttpClient()
                                import gc
                                gc.collect()
                                response = http_client.post('http://192.168.4.1:3000/messages/', json=message_packet_json)
                                # Check for success - resend/queue and resend - TODO
                                response = None
                                gc.collect()



                #
                # 2. Periodic Activity (RTC)
                #
                # _rtc_callback_flag

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
                #jotter.get_jotter().jot("Going to lightsleep.", source_file=__name__)
                _rtc_callback_flag = False  # Clear the callback flags
                machine.lightsleep()

                # b. Deep Sleep - followed by hard reset
                # pyb.standby()
                # machine.deepsleep()
                # c. poll flag without sleeping
                # while not _rtc_callback_flag:
                #    continue
                # _rtc_callback_flag = False
                # d. wait for interrupt (wfi)
                # pyb.wfi()

                #
                # Wake up
                #

                # RTC or incoming NM3 packet? Only way to know is by flags set in the callbacks.
                # machine.wake_reason() is not implemented on PYBD!
                # https://docs.micropython.org/en/latest/library/machine.html
                # jotter.get_jotter().jot("Wake up. _rtc_callback_flag=" + str(_rtc_callback_flag), source_file=__name__)

        except Exception as the_exception:
            jotter.get_jotter().jot_exception(the_exception)
            pass
            # Log to file

