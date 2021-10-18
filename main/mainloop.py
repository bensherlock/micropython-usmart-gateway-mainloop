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
import network
import os
from ucollections import deque
import utime

from pybd_expansion.main.max3221e import MAX3221E
from pybd_expansion.main.powermodule import PowerModule
import sensor_payload.main.sensor_payload as sensor_payload

from uac_modem.main.unm3driver import MessagePacket, Nm3

import uac_network.main.gw_node as gw_node


import jotter

import micropython
micropython.alloc_emergency_exception_buf(100)
# https://docs.micropython.org/en/latest/reference/isr_rules.html#the-emergency-exception-buffer


_wifi_transition_static = 0
_wifi_transition_connecting = 1
_wifi_transition_disconnecting = 2
_wifi_current_transition = _wifi_transition_static

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
    sta_if = network.WLAN(network.STA_IF)
    if not sta_if.isconnected():
        print('connecting to network...')
        sta_if.active(True)
        sta_if.config(antenna=1)  # select antenna, 0=chip, 1=external
        #sta_if.config(antenna=0)  # select antenna, 0=chip, 1=external DEV Mode
        sta_if.connect(ssid, password)

        # Yield
        utime.sleep_ms(100)

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
    sta_if = network.WLAN(network.STA_IF)
    # Check if active
    if not sta_if.active():
        return False

    # Active so check the status
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
    sta_if = network.WLAN(network.STA_IF)
    return sta_if.isconnected()


def disconnect_from_wifi():
    """Disconnect from the wifi and power down the wifi module."""
    sta_if = network.WLAN(network.STA_IF)

    # Disconnect
    sta_if.disconnect()

    # Deactivate the WLAN
    sta_if.active(False)

    # https://github.com/micropython/micropython/issues/4681
    sta_if.deinit()

    # Give it time to shutdown
    utime.sleep_ms(100)


_rtc_callback_flag = False
_rtc_alarm_period_s = 10
_rtc_next_alarm_time_s = 0


def rtc_set_next_alarm_time_s(alarm_time_s_from_now):
    global _rtc_next_alarm_time_s

    if 0 < alarm_time_s_from_now <= 7200:  # above zero and up to two hours
        _rtc_next_alarm_time_s = utime.time() + alarm_time_s_from_now
        print("_rtc_next_alarm_time_s=" + str(_rtc_next_alarm_time_s) + " time now=" + str(utime.time()))


def rtc_set_alarm_period_s(alarm_period_s):
    """Set the alarm period in seconds. Updates the next alarm time from now. If 0 then cancels the alarm."""
    global _rtc_alarm_period_s
    global _rtc_next_alarm_time_s
    _rtc_alarm_period_s = alarm_period_s
    if _rtc_alarm_period_s > 0:
        _rtc_next_alarm_time_s = utime.time() + _rtc_alarm_period_s
    else:
        _rtc_next_alarm_time_s = 0  # cancel the alarm

    print("_rtc_next_alarm_time_s=" + str(_rtc_next_alarm_time_s) + " time now=" + str(utime.time()))


_rtc_callback_seconds = 0  # can be used to stay awake for X seconds after the last RTC wakeup


def rtc_callback(unknown):
    # NB: You cannot do anything that allocates memory in this interrupt handler.
    global _rtc_callback_flag
    global _rtc_callback_seconds
    global _rtc_alarm_period_s
    global _rtc_next_alarm_time_s
    # RTC Callback function -
    # pyb.LED(2).toggle()
    # Only set flag if it is alarm time
    if 0 < _rtc_next_alarm_time_s <= utime.time():
        _rtc_callback_flag = True
        _rtc_callback_seconds = utime.time()
        _rtc_next_alarm_time_s = _rtc_next_alarm_time_s + _rtc_alarm_period_s  # keep the period consistent


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


def send_usmart_alive_message(modem):
    # Send a standard broadcast Alive message. Usually called on startup and on request by external message.
    # Grab address and voltage from the modem
    if modem:
        nm3_address = modem.get_address()
        utime.sleep_ms(20)
        nm3_voltage = modem.get_battery_voltage()
        utime.sleep_ms(20)
        # print("NM3 Address {:03d} Voltage {:0.2f}V.".format(nm3_address, nm3_voltage))
        # jotter.get_jotter().jot("NM3 Address {:03d} Voltage {:0.2f}V.".format(nm3_address, nm3_voltage),
        #                        source_file=__name__)
        # So here we will broadcast an I'm Alive message. Payload: U (for USMART), A (for Alive), Address, B, Battery
        # Plus a version/date so we can determine if an OTA update has worked
        alive_string = "UA" + "{:03d}".format(nm3_address) + "B{:0.2f}V".format(nm3_voltage) + "REV:2021-04-07T11:49:00"
        modem.send_broadcast_message(alive_string.encode('utf-8'))


_env_variables = None


# - def set_environment_variables()
def set_environment_variables(env_variables_dict=None):
    """Set a global dictionary of variables."""
    global _env_variables
    _env_variables = env_variables_dict


# Standard Interface for MainLoop
# - def run_mainloop() : never returns
def run_mainloop():
    """Standard Interface for MainLoop. Never returns."""

    global _env_variables
    global _rtc_callback_flag
    global _rtc_callback_seconds
    global _nm3_callback_flag
    global _nm3_callback_seconds
    global _nm3_callback_millis
    global _nm3_callback_micros
    global _wifi_current_transition

    # Firstly Initialise the Watchdog machine.WDT. This cannot now be stopped and *must* be fed.
    wdt = machine.WDT(timeout=30000)  # 30 seconds timeout on the watchdog.

    # Now if anything causes us to crashout from here we will reboot automatically.

    # Last reset cause
    last_reset_cause = "PWRON_RESET"
    if machine.reset_cause() == machine.PWRON_RESET:
        last_reset_cause = "PWRON_RESET"
    elif machine.reset_cause() == machine.HARD_RESET:
        last_reset_cause = "HARD_RESET"
    elif machine.reset_cause() == machine.WDT_RESET:
        last_reset_cause = "WDT_RESET"
    elif machine.reset_cause() == machine.DEEPSLEEP_RESET:
        last_reset_cause = "DEEPSLEEP_RESET"
    elif machine.reset_cause() == machine.SOFT_RESET:
        last_reset_cause = "SOFT_RESET"
    else:
        last_reset_cause = "UNDEFINED_RESET"

    print("last_reset_cause=" + last_reset_cause)

    # https://pybd.io/hw/pybd_sfxw.html
    # The CPU frequency can be set to any multiple of 2MHz between 48MHz and 216MHz, via machine.freq(<freq>).
    # By default the SF2 model runs at 120MHz and the SF6 model at 144MHz in order to conserve electricity.
    # It is possible to go below 48MHz but then the WiFi cannot be used.
    #machine.freq(48000000)  # Set to lowest usable frequency

    # Feed the watchdog
    wdt.feed()

    # Set RTC to wakeup at a set interval
    rtc = pyb.RTC()
    rtc.init()  # reinitialise - there were bugs in firmware. This wipes the datetime.
    # A default wakeup to start with. To be overridden by network manager/sleep manager
    rtc.wakeup(2 * 1000, rtc_callback)  # milliseconds - # Every 2 seconds

    rtc_set_alarm_period_s(60 * 60)  # Every 60 minutes to do the status
    _rtc_callback_flag = True  # Set the flag so we do a status message on startup.

    pyb.LED(2).on()  # Green LED On

    # Cycle the NM3 power supply on the powermodule
    powermodule = PowerModule()
    powermodule.disable_nm3()

    # Enable power supply to 232 driver and sensors and sdcard
    pyb.Pin.board.EN_3V3.on()
    pyb.Pin('Y5', pyb.Pin.OUT, value=0)  # enable Y5 Pin as output
    max3221e = MAX3221E(pyb.Pin.board.Y5)
    max3221e.tx_force_on()  # Enable Tx Driver

    # Set callback for nm3 pin change - line goes high on frame synchronisation
    # make sure it is clear first
    nm3_extint = pyb.ExtInt(pyb.Pin.board.Y3, pyb.ExtInt.IRQ_RISING, pyb.Pin.PULL_DOWN, None)
    nm3_extint = pyb.ExtInt(pyb.Pin.board.Y3, pyb.ExtInt.IRQ_RISING, pyb.Pin.PULL_DOWN, nm3_callback)

    # Serial Port/UART is opened with a 100ms timeout for reading - non-blocking.
    uart = machine.UART(1, 9600, bits=8, parity=None, stop=1, timeout=100)
    nm3_modem = Nm3(input_stream=uart, output_stream=uart)
    utime.sleep_ms(20)

    # Feed the watchdog
    wdt.feed()

    utime.sleep_ms(10000)
    powermodule.enable_nm3()
    utime.sleep_ms(10000)  # Await end of bootloader

    # Feed the watchdog
    wdt.feed()

    # Grab address and voltage from the modem
    nm3_address = nm3_modem.get_address()
    utime.sleep_ms(20)
    nm3_voltage = nm3_modem.get_battery_voltage()
    utime.sleep_ms(20)
    print("NM3 Address {:03d} Voltage {:0.2f}V.".format(nm3_address, nm3_voltage))

    # Sometimes (maybe from brownout) restarting the modem leaves it in a state where you can talk to it on the
    # UART fine, but there's no ability to receive incoming acoustic comms until the modem has been fired.
    send_usmart_alive_message(nm3_modem)


    # Feed the watchdog
    wdt.feed()

    # Delay for transmission of broadcast packet
    utime.sleep_ms(500)

    # sensor payload
    sensor = sensor_payload.get_sensor_payload_instance()

    operating_mode = 2  # Mark Three

    _wifi_current_transition = _wifi_transition_static
    wifi_connection_retry_count = 0

    last_nm3_message_received_time = utime.time()

    # Micropython needs a defined size of deque
    json_to_send_messages = deque((), 50)  # Incoming NM3 Messages
    json_to_send_statuses = deque((), 20)  # Sensors and VBatt and Uptime
    json_to_send_network_topologies = deque((), 40)  # Network topology from uac_network

    # Sequence Numbers to identify duplicate http sends.
    status_seq = 0
    message_seq = 0
    network_topology_seq = 0

    # Wifi issues
    # https://github.com/micropython/micropython/issues/4681
    # The wifi may get stuck in a "connecting" state. Try timeouts and restart the process.
    wifi_connecting_start_time = 0
    wifi_disconnecting_start_time = 0  # to allow a cooldown time before reconnecting.

    # Network configuration
    network_nm3_gateway_stay_awake = True  # Stay awake in transparent gateway mode by default
    network_nm3_sensor_stay_awake = True
    network_node_addresses = []  # No nodes by default
    network_guard_interval_ms = 500
    network_cycle_counter = 0
    network_cycle_limit = 6  # 6 hourly
    network_partials_counter = 0
    network_partials_per_full_discovery = 6  # 6x6=36 hourly
    network_frame_interval_s = 3600  # 1 hour
    network_link_quality_threshold = 4 # 1-5
    network_next_frame_time_s = utime.time()  # Non-zero value to start from
    network_is_configured = False
    network_do_full_configuration = False
    network_do_partial_configuration = False
    network_config_is_stale = True

    # Create the network protocol object
    net_protocol = gw_node.NetProtocol()

    # Uptime
    uptime_start = utime.time()



    # Turn off the USB
    pyb.usb_mode(None)

    while True:
        try:
            # First entry in the while loop and also after a caught exception
            # pyb.LED(2).on()  # Awake

            # Feed the watchdog
            wdt.feed()

            # Enable power supply to 232 driver, sensors, and SDCard
            pyb.Pin.board.EN_3V3.on()

            # Brief pause instead of tight loop
            utime.sleep_ms(10)

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

                    status_json = {"Status": {"Timestamp": utime.time(),
                                              "Uptime": (utime.time() - uptime_start),
                                              "LastResetCause": last_reset_cause,
                                              "VBatt": vbatt,
                                              "Sensors": sensor_data_json},
                                   "SeqNo": status_seq,
                                   "Retry": 0}

                    status_seq = status_seq + 1
                    if status_seq >= 65536:  # Aribtrary limit to 16-bit uint.
                        status_seq = 0

                    # Append to queue
                    json_to_send_statuses.append(status_json)

                # If we're within 30 seconds of the last timestamped NM3 synch arrival then poll for messages.
                if utime.time() < _nm3_callback_seconds + 30:
                    _nm3_callback_flag = False  # clear the flag

                    # There may or may not be a message for us. And it could take up to 0.5s to arrive at the uart.

                    nm3_modem.poll_receiver()
                    nm3_modem.process_incoming_buffer()

                    while nm3_modem.has_received_packet():
                        # print("Has received nm3 message.")
                        jotter.get_jotter().jot("Has received nm3 message.", source_file=__name__)

                        message_packet = nm3_modem.get_received_packet()
                        # Copy the HW triggered timestamps over
                        message_packet.timestamp = utime.localtime(_nm3_callback_seconds)
                        message_packet.timestamp_millis = _nm3_callback_millis
                        message_packet.timestamp_micros = _nm3_callback_micros

                        # Send packet onwards
                        message_packet_json = message_packet.json()

                        message_json = {"Message": message_packet_json,
                                        "SeqNo": message_seq,
                                        "Retry": 0}
                        message_seq = message_seq + 1
                        if message_seq >= 65536:  # Aribtrary limit to 16-bit uint.
                            message_seq = 0

                        # Append to the queue
                        json_to_send_messages.append(message_json)

                        # Process special packets
                        if message_packet.packet_payload and bytes(message_packet.packet_payload) == b'USMRT':
                            # print("Reset message received.")
                            jotter.get_jotter().jot("Reset message received.", source_file=__name__)
                            # Reset the device
                            machine.reset()

                # If messages or statuses are in the queue
                if json_to_send_messages or json_to_send_statuses:

                    wifi_connected = is_wifi_connected()

                    # Messages to Send - Wifi Connection States
                    # Idle and disconnecting-cooldown time expired - start connection to wifi
                    # Connected - send all the messages
                    # Connecting and Timed out - Disconnect
                    # Otherwise pause

                    if (not wifi_connected) and \
                            not (_wifi_current_transition == _wifi_transition_connecting) \
                            and (utime.time() > wifi_disconnecting_start_time + 2):  # allow short cooldown time on last connection
                        # Start the connecting to the wifi
                        # print("Has messages to send. Connecting to wifi.")
                        jotter.get_jotter().jot("Has messages to send. Connecting to wifi.", source_file=__name__)
                        # Connect to server over wifi
                        wifi_cfg = load_wifi_config()
                        if wifi_cfg:
                            # wifi_connected = connect_to_wifi(wifi_cfg['wifi']['ssid'],
                            #                                 wifi_cfg['wifi']['password'])  # blocking
                            if start_connect_to_wifi(wifi_cfg['wifi']['ssid'],
                                                  wifi_cfg['wifi']['password']):  # non-blocking
                                wifi_connecting_start_time = utime.time()
                                _wifi_current_transition = _wifi_transition_connecting
                                wifi_connection_retry_count = wifi_connection_retry_count + 1
                        else:
                            # Unable to ever connect
                            # print("Unable to load wifi config data so cannot connect to wifi. Clearing any messages.")
                            jotter.get_jotter().jot("Unable to load wifi config data so cannot connect to wifi. "
                                                    "Clearing any messages.",
                                                    source_file=__name__)
                            json_to_send_messages.clear()
                            json_to_send_statuses.clear()

                            _wifi_current_transition = _wifi_transition_static

                    elif wifi_connected:
                        # Send the messages
                        _wifi_current_transition = _wifi_transition_static
                        wifi_connection_retry_count = 0

                        # print("Connected to wifi. Sending message to server.")
                        jotter.get_jotter().jot("Connected to wifi. Sending message to server.", source_file=__name__)
                        import mainloop.main.httputil as httputil
                        http_client = httputil.HttpClient()
                        import gc
                        while json_to_send_messages:
                            message_json = json_to_send_messages.popleft()
                            retry_count = 0
                            success_flag = False

                            while not success_flag and retry_count < 4:
                                message_json["Retry"] = retry_count
                                retry_count = retry_count + 1

                                try:
                                    gc.collect()
                                    response = http_client.post('http://192.168.4.1:8080/messages/',
                                                                json=message_json)
                                    # Check for success - resend/queue and resend
                                    if 200 <= response.status_code < 300:
                                        # Success
                                        success_flag = True
                                    response = None
                                    gc.collect()
                                except Exception as the_exception:
                                    jotter.get_jotter().jot_exception(the_exception)
                                    pass

                                # Brief delay
                                utime.sleep_ms(10)

                        while json_to_send_statuses:
                            status_json = json_to_send_statuses.popleft()
                            retry_count = 0
                            success_flag = False

                            while not success_flag and retry_count < 4:
                                status_json["Retry"] = retry_count
                                retry_count = retry_count + 1

                                try:
                                    gc.collect()
                                    response = http_client.post('http://192.168.4.1:8080/statuses/',
                                                                json=status_json)
                                    # Check for success - resend/queue and resend
                                    if 200 <= response.status_code < 300:
                                        # Success
                                        success_flag = True
                                    response = None
                                    gc.collect()
                                except Exception as the_exception:
                                    jotter.get_jotter().jot_exception(the_exception)
                                    pass

                                # Brief delay
                                utime.sleep_ms(10)

                    elif (_wifi_current_transition == _wifi_transition_connecting) and \
                            (utime.time() > wifi_connecting_start_time + 30):
                        # Has been trying to connect for 30 seconds.
                        print("Connecting to wifi took too long. Disconnecting to retry.")
                        jotter.get_jotter().jot("Connecting to wifi took too long. Disconnecting to retry.", source_file=__name__)
                        # Disable the wifi
                        wifi_disconnecting_start_time = utime.time()
                        disconnect_from_wifi()
                        _wifi_current_transition = _wifi_transition_disconnecting

                    else:
                        # Brief pause instead of tight loop
                        utime.sleep_ms(10)

                # If no messages in the queue and too long since last synch and not rtc callback
                if not _rtc_callback_flag and \
                        ((wifi_connection_retry_count > 5) or
                         ((not json_to_send_messages) and (not json_to_send_statuses)
                          and (utime.time() > _nm3_callback_seconds + 30))):
                    # Disable the wifi
                    wifi_disconnecting_start_time = utime.time()
                    disconnect_from_wifi()  # Need to give the OS time to do this and power down the wifi chip.
                    wifi_connection_retry_count = 0
                    _wifi_current_transition = _wifi_transition_disconnecting
                    while (not _rtc_callback_flag) and (not _nm3_callback_flag) and (utime.time() < wifi_disconnecting_start_time + 5):
                        # Feed the watchdog
                        wdt.feed()
                        # Give the wifi time to sleep
                        utime.sleep_ms(100)

                    # Double check the flags before powering things off
                    if (not _rtc_callback_flag) and (not _nm3_callback_flag):
                        jotter.get_jotter().jot("Going to sleep.", source_file=__name__)
                        # Disable the I2C pullups
                        pyb.Pin('PULL_SCL', pyb.Pin.IN)  # disable 5.6kOhm X9/SCL pull-up
                        pyb.Pin('PULL_SDA', pyb.Pin.IN)  # disable 5.6kOhm X10/SDA pull-up
                        # Disable power supply to 232 driver, sensors, and SDCard
                        pyb.Pin.board.EN_3V3.off()
                        pyb.LED(2).off()  # Asleep
                        utime.sleep_ms(10)

                    while (not _rtc_callback_flag) and (not _nm3_callback_flag):
                        # Feed the watchdog
                        wdt.feed()
                        # Now wait
                        utime.sleep_ms(10)
                        # pyb.wfi()  # wait-for-interrupt (can be ours or the system tick every 1ms or anything else)
                        machine.lightsleep()  # lightsleep - don't use the time as this then overrides the RTC

                    # Wake-up
                    # pyb.LED(2).on()  # Awake
                    # Feed the watchdog
                    wdt.feed()
                    # Enable power supply to 232 driver, sensors, and SDCard
                    pyb.Pin.board.EN_3V3.on()
                    # Enable the I2C pullups
                    pyb.Pin('PULL_SCL', pyb.Pin.OUT, value=1)  # enable 5.6kOhm X9/SCL pull-up
                    pyb.Pin('PULL_SDA', pyb.Pin.OUT, value=1)  # enable 5.6kOhm X10/SDA pull-up

                pass  # end of elif operating_mode == 1:

            # Mark Three
            # - Network Manager TDA-MAC and RTC to control wakeup and sleep timing.
            # - Also act as transparent gateway to relay all NM3 messages to shore.
            # - Use the HW interrupt of NM3 Flag to wake-up
            # - poll for incoming NM3 messages
            # - Then enable wifi and send to server
            # - After a time of no NM3 messages disable the wifi and go to sleep or wfi
            #
            # Default: NM3 on. HW wakeup to relay unsolicited messages back to server.
            # RTC (Hourly by default): Wifi connect, sensors read, NM3 power on.
            #
            # On wake up, if any messages/statuses/etc in the queues then connect to wifi and send to server.
            elif operating_mode == 2:

                # Cause of wakeup
                # A) NM3 HW Callback Flag
                #    Poll the UART for packets
                #    Put packets as json strings into a queue
                # B) RTC Callback Flag
                #    Get some sensor data
                #    Put data as json string into a queue

                # While there are messages to be sent try and connect to the wifi
                # if connected to wifi send a message

                # Download configuration from server
                # + Transparent (NM3 always on) / Network (NM3 off in sleep).
                # + Do Network Configuration (True/False) Run a network configuration process now.

                # Send network configuration info to server.

                # If no flag is set and no messages to be sent (or unable to connect to wifi)
                # Go to sleep/wait for interrupt

                if _rtc_callback_flag:
                    _rtc_callback_flag = False  # Clear the flag
                    print("RTC Flag. Powering up NM3 and getting sensor data." + " time now=" + str(utime.time()))
                    jotter.get_jotter().jot("RTC Flag. Powering up NM3 and getting sensor data. ", source_file=__name__)

                    # Enable power supply to 232 driver and sensors and sdcard
                    pyb.Pin.board.EN_3V3.on()
                    max3221e.tx_force_on()  # Enable Tx Driver

                    # Start Power up NM3
                    utime.sleep_ms(100)
                    powermodule.enable_nm3()
                    nm3_startup_time = utime.time()

                    # battery
                    vbatt = powermodule.get_vbatt_reading()

                    # sensor payload
                    # sensor = sensor_payload.get_sensor_payload_instance()
                    sensor.start_acquisition()
                    sensor_acquisition_start = utime.time()
                    while (not sensor.is_completed()) and (utime.time() < sensor_acquisition_start + 5):
                        sensor.process_acquisition()
                        utime.sleep_ms(100)  # yield

                    sensor_data_json = sensor.get_latest_data_as_json()
                    # Needs changing: https://google.github.io/styleguide/jsoncstyleguide.xml?showone=Property_Name_Format#Property_Name_Format
                    # camelCase for propertyNames.
                    status_json = {"status": {"timestamp": utime.time(),
                                              "uptime": (utime.time() - uptime_start),
                                              "lastResetCause": last_reset_cause,
                                              "vbatt": vbatt,
                                              "sensors": sensor_data_json},
                                   "seqNo": status_seq,
                                   "retry": 0}

                    status_seq = status_seq + 1
                    if status_seq >= 65536:  # Aribtrary limit to 16-bit uint.
                        status_seq = 0

                    # Append to queue
                    json_to_send_statuses.append(status_json)

                    # Need to get new network config
                    network_config_is_stale = True

                    # Wait for completion of NM3 bootup (if it wasn't already powered)
                    while utime.time() < nm3_startup_time + 7:
                        utime.sleep_ms(100)  # yield
                        pass


                # If we're within 30 seconds of the last timestamped NM3 synch arrival then poll for messages.
                if _nm3_callback_flag or (utime.time() < _nm3_callback_seconds + 30):
                    if _nm3_callback_flag:
                        print("Has received nm3 synch flag.")

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
                        # Needs changing: https://google.github.io/styleguide/jsoncstyleguide.xml?showone=Property_Name_Format#Property_Name_Format
                        # camelCase for propertyNames.
                        message_json = {"message": message_packet_json,
                                        "timestamp": utime.time(),
                                        "seqNo": message_seq,
                                        "retry": 0}
                        message_seq = message_seq + 1
                        if message_seq >= 65536:  # Aribtrary limit to 16-bit uint.
                            message_seq = 0

                        # Append to the queue
                        json_to_send_messages.append(message_json)

                        # Process special packets
                        # Only unicast command will work for gateway.
                        if message_packet.packet_type == MessagePacket.PACKETTYPE_UNICAST and \
                                message_packet.packet_payload and bytes(message_packet.packet_payload) == b'USMRT':
                            # print("Reset message received.")
                            jotter.get_jotter().jot("Reset message received.", source_file=__name__)
                            # Reset the device
                            machine.reset()

                        # Only unicast command will work for gateway.
                        if message_packet.packet_type == MessagePacket.PACKETTYPE_UNICAST and \
                                message_packet.packet_payload and bytes(message_packet.packet_payload) == b'USOTA':
                            # print("OTA message received.")
                            jotter.get_jotter().jot("OTA message received.", source_file=__name__)
                            # Write a special flag file to tell us to OTA on reset
                            try:
                                with open('.USOTA', 'w') as otaflagfile:
                                    # otaflagfile.write(latest_version)
                                    otaflagfile.close()
                            except Exception as the_exception:
                                jotter.get_jotter().jot_exception(the_exception)

                                import sys
                                sys.print_exception(the_exception)
                                pass

                            # Reset the device
                            machine.reset()

                        # Only unicast command will work for gateway.
                        if message_packet.packet_type == MessagePacket.PACKETTYPE_UNICAST and \
                                message_packet.packet_payload and bytes(message_packet.packet_payload) == b'USPNG':
                            # print("PNG message received.")
                            jotter.get_jotter().jot("PNG message received.", source_file=__name__)
                            send_usmart_alive_message(nm3_modem)

                        # Only unicast command will work for gateway.
                        if message_packet.packet_type == MessagePacket.PACKETTYPE_UNICAST and \
                                message_packet.packet_payload and bytes(message_packet.packet_payload) == b'USMOD':
                            # print("MOD message received.")
                            jotter.get_jotter().jot("MOD message received.", source_file=__name__)
                            # Send the installed modules list as single packets with 1 second delay between each -
                            # Only want to be calling this after doing an OTA command and ideally not in the sea.

                            nm3_address = nm3_modem.get_address()

                            if _env_variables and "installedModules" in _env_variables:
                                installed_modules = _env_variables["installedModules"]
                                if installed_modules:
                                    for (mod, version) in installed_modules.items():
                                        mod_string = "UM" + "{:03d}".format(nm3_address) + ":" + str(mod) + ":" \
                                                     + str(version if version else "None")
                                        nm3_modem.send_broadcast_message(mod_string.encode('utf-8'))

                                        # delay whilst sending
                                        utime.sleep_ms(1000)

                                        # Feed the watchdog
                                        wdt.feed()

                        # Only unicast command will work for gateway.
                        if message_packet.packet_type == MessagePacket.PACKETTYPE_UNICAST and \
                                message_packet.packet_payload and bytes(message_packet.packet_payload) == b'USCALDO':
                            # print("CAL message received.")
                            jotter.get_jotter().jot("CAL message received.", source_file=__name__)

                            nm3_address = nm3_modem.get_address()

                            # Reply with an acknowledgement then start the calibration
                            msg_string = "USCALMSG" + "{:03d}".format(nm3_address) + ":Starting Calibration"
                            nm3_modem.send_broadcast_message(msg_string.encode('utf-8'))
                            # delay whilst sending
                            utime.sleep_ms(1000)
                            # Feed the watchdog
                            wdt.feed()
                            # start calibration
                            (x_min, x_max, y_min, y_max, z_min, z_max) = sensor.do_calibration(duration=20)
                            # Feed the watchdog
                            wdt.feed()
                            # magneto values are int16
                            caldata_string = "USCALDATA" + "{:03d}".format(nm3_address) + ":" \
                                             + "{:06d},{:06d},{:06d},{:06d},{:06d},{:06d}".format(x_min, x_max,
                                                                                                  y_min, y_max,
                                                                                                  z_min, z_max)
                            nm3_modem.send_broadcast_message(caldata_string.encode('utf-8'))
                            # delay whilst sending
                            utime.sleep_ms(1000)

                # If time to do the network data gather/configuration Only do network if we have any nodes to talk to
                if network_node_addresses and network_next_frame_time_s <= utime.time():
                    print("Time for network frame.")
                    jotter.get_jotter().jot("Time for network frame.", source_file=__name__)

                    if network_cycle_counter >= network_cycle_limit:
                        # Configuration required
                        if network_partials_counter >= network_partials_per_full_discovery:
                            network_do_partial_configuration = False
                            network_do_full_configuration = True
                        else:
                            network_do_partial_configuration = True

                    if network_do_full_configuration or network_do_partial_configuration:
                        print("Configuring network.")
                        jotter.get_jotter().jot("Configuring network.", source_file=__name__)

                        if network_do_full_configuration:
                            print("Configuring network with full discovery.")
                            jotter.get_jotter().jot("Configuring network with full discovery.", source_file=__name__)
                            # Reinitialise the network protocol
                            net_protocol.init(nm3_modem, network_node_addresses, wdt)
                            network_partials_counter = 0

                        # Then do discovery
                        network_is_configured = False
                        net_protocol.set_link_quality_threshold(network_link_quality_threshold)  # set the link quality threshold
                        if net_protocol.do_net_discovery(full_rediscovery=network_do_full_configuration):
                            net_protocol.setup_net_schedule(network_guard_interval_ms)  # guard interval [msec] can be specified as function input (default: 500)
                            network_cycle_counter = 0
                            if network_do_partial_configuration:
                                network_partials_counter = network_partials_counter + 1

                            network_do_full_configuration = False
                            network_do_partial_configuration = False
                            network_is_configured = True
                            network_next_frame_time_s = utime.time()  # Set the epoch to now

                    # Extract network topology and schedule information as JSON
                    net_info_json = net_protocol.get_net_info_json()
                    # Also send back the current config information
                    net_config_json = {"nm3GatewayStayAwake": network_nm3_gateway_stay_awake,
                                       "nm3SensorStayAwake": network_nm3_sensor_stay_awake,
                                       "cycleLimit": network_cycle_limit,
                                       "cycleCounter": network_cycle_counter,
                                       "partialsCounter": network_partials_counter,
                                       "partialsPerFullDiscovery": network_partials_per_full_discovery,
                                       "guardIntervalMs": network_guard_interval_ms,
                                       "frameIntervalS": network_frame_interval_s,
                                       "linkQualityThreshold": network_link_quality_threshold,
                                       "nodeAddresses": network_node_addresses}

                    # variable to hold the data gathering info
                    data_gathering_info_json = None

                    if network_is_configured:
                        print("Gathering data from network.")
                        jotter.get_jotter().jot("Gathering data from network.", source_file=__name__)
                        # Do a data gather
                        network_next_frame_time_s = network_next_frame_time_s + network_frame_interval_s
                        # time_till_next_frame = network_frame_interval_s * 1000
                        time_till_next_frame = (network_next_frame_time_s - utime.time()) * 1000  # for sleep synchronisation (this can also be variable between frames)
                        rtc_set_next_alarm_time_s(network_next_frame_time_s - utime.time() - 60)  # set the next wakeup time to be 60 seconds before the next frame time

                        print("network_next_frame_time_s=" + str(network_next_frame_time_s)
                              + " time_till_next_frame=" + str(time_till_next_frame))

                        packets = net_protocol.gather_sensor_data(time_till_next_frame, network_nm3_sensor_stay_awake)
                        network_cycle_counter = network_cycle_counter + 1

                        data_gathering_info_json = net_protocol.get_data_gathering_info_json()

                        for message_packet in packets:
                            # Send packet onwards
                            message_packet_json = message_packet.json()
                            # Needs changing: https://google.github.io/styleguide/jsoncstyleguide.xml?showone=Property_Name_Format#Property_Name_Format
                            # camelCase for propertyNames.
                            message_json = {"message": message_packet_json,
                                            "timestamp": utime.time(),
                                            "seqNo": message_seq,
                                            "retry": 0}
                            message_seq = message_seq + 1
                            if message_seq >= 65536:  # Aribtrary limit to 16-bit uint.
                                message_seq = 0

                            # Append to the queue
                            json_to_send_messages.append(message_json)

                    pass

                    network_topology_json = {"topology": net_info_json,
                                             "config": net_config_json,
                                             "data_gathering": data_gathering_info_json,
                                             "timestamp": utime.time(),
                                             "seqNo": network_topology_seq,
                                             "retry": 0}

                    network_topology_seq = network_topology_seq + 1
                    if network_topology_seq >= 65536:  # Aribtrary limit to 16-bit uint.
                        network_topology_seq = 0

                    json_to_send_network_topologies.append(network_topology_json)


                # If messages or statuses are in the queue or we need to refresh the network config
                if json_to_send_messages or json_to_send_statuses or json_to_send_network_topologies or network_config_is_stale:

                    wifi_connected = is_wifi_connected()

                    # Messages to Send - Wifi Connection States
                    # Idle and disconnecting-cooldown time expired - start connection to wifi
                    # Connected - send all the messages
                    # Connecting and Timed out - Disconnect
                    # Otherwise pause

                    if (not wifi_connected) and \
                            not (_wifi_current_transition == _wifi_transition_connecting) \
                            and (
                            utime.time() > wifi_disconnecting_start_time + 2):  # allow short cooldown time on last connection
                        # Start the connecting to the wifi
                        print("Has messages to send. Connecting to wifi.")
                        jotter.get_jotter().jot("Has messages to send. Connecting to wifi.", source_file=__name__)
                        # Connect to server over wifi
                        wifi_cfg = load_wifi_config()
                        if wifi_cfg:
                            # wifi_connected = connect_to_wifi(wifi_cfg['wifi']['ssid'],
                            #                                 wifi_cfg['wifi']['password'])  # blocking
                            if start_connect_to_wifi(wifi_cfg['wifi']['ssid'],
                                                     wifi_cfg['wifi']['password']):  # non-blocking
                                wifi_connecting_start_time = utime.time()
                                _wifi_current_transition = _wifi_transition_connecting
                                wifi_connection_retry_count = wifi_connection_retry_count + 1
                        else:
                            # Unable to ever connect
                            # print("Unable to load wifi config data so cannot connect to wifi. Clearing any messages.")
                            jotter.get_jotter().jot("Unable to load wifi config data so cannot connect to wifi. "
                                                    "Clearing any messages.",
                                                    source_file=__name__)
                            json_to_send_messages.clear()
                            json_to_send_statuses.clear()
                            json_to_send_network_topologies.clear()

                            network_config_is_stale = False

                            _wifi_current_transition = _wifi_transition_static

                    elif wifi_connected:
                        # Send the messages and download the network config
                        _wifi_current_transition = _wifi_transition_static
                        wifi_connection_retry_count = 0

                        print("Connected to wifi.")
                        jotter.get_jotter().jot("Connected to wifi.", source_file=__name__)
                        import mainloop.main.httputil as httputil
                        http_client = httputil.HttpClient()
                        import gc

                        if network_config_is_stale:
                            print("Getting network config from server.")
                            jotter.get_jotter().jot("Getting network config from server.", source_file=__name__)
                            retry_count = 0
                            success_flag = False
                            network_config_json = None

                            while not success_flag and retry_count < 4:
                                retry_count = retry_count + 1

                                try:
                                    gc.collect()
                                    response = http_client.get('http://192.168.4.1:8080/networkconfig/latest/')
                                    # Check for success - reget
                                    if 200 <= response.status_code < 300:
                                        # Success
                                        success_flag = True
                                        network_config_json = response.json()
                                    response = None
                                    gc.collect()
                                except Exception as the_exception:
                                    import sys
                                    sys.print_exception(the_exception)
                                    jotter.get_jotter().jot_exception(the_exception)
                                    pass

                                # Brief delay
                                utime.sleep_ms(10)

                            if network_config_json:
                                network_nm3_gateway_stay_awake = network_config_json["nm3GatewayStayAwake"]  # Bool
                                network_nm3_sensor_stay_awake = network_config_json["nm3SensorStayAwake"]  # Bool
                                network_cycle_limit = network_config_json["cycleLimit"]  # Integer
                                network_partials_per_full_discovery = network_config_json["partialsPerFullDiscovery"]  # Integer
                                network_guard_interval_ms = network_config_json["guardIntervalMs"]  # Integer
                                network_frame_interval_s = network_config_json["frameIntervalS"]  # Integer
                                network_link_quality_threshold = network_config_json["linkQualityThreshold"] # Integer
                                node_addresses = network_config_json["nodeAddresses"]  # List of Integers
                                # If change in node addresses then we trigger a network configuration
                                if len(node_addresses) != len(network_node_addresses):
                                    network_do_full_configuration = True
                                else:
                                    for the_address in node_addresses:
                                        if the_address not in network_node_addresses:
                                            network_do_full_configuration = True
                                            break
                                network_node_addresses = node_addresses

                            # Even if we failed to download it, set as not stale so we go to sleep and try next time.
                            # If wifi works but the server isn't running we would get stuck in a tight loop retrying
                            # to get network config.
                            network_config_is_stale = False

                        if json_to_send_messages:
                            print("Sending messages to server.")
                            jotter.get_jotter().jot("Sending messages to server.", source_file=__name__)

                        while json_to_send_messages:
                            message_json = json_to_send_messages.popleft()
                            retry_count = 0
                            success_flag = False

                            while not success_flag and retry_count < 4:
                                message_json["retry"] = retry_count
                                retry_count = retry_count + 1

                                try:
                                    gc.collect()
                                    response = http_client.post('http://192.168.4.1:8080/messages/',
                                                                json=message_json)
                                    # Check for success - resend/queue and resend
                                    if 200 <= response.status_code < 300:
                                        # Success
                                        success_flag = True
                                    response = None
                                    gc.collect()
                                except Exception as the_exception:
                                    import sys
                                    sys.print_exception(the_exception)
                                    jotter.get_jotter().jot_exception(the_exception)
                                    pass

                                # Brief delay
                                utime.sleep_ms(10)

                        if json_to_send_statuses:
                            print("Sending statuses to server.")
                            jotter.get_jotter().jot("Sending statuses to server.", source_file=__name__)

                        while json_to_send_statuses:
                            status_json = json_to_send_statuses.popleft()
                            retry_count = 0
                            success_flag = False

                            while not success_flag and retry_count < 4:
                                status_json["retry"] = retry_count
                                retry_count = retry_count + 1

                                try:
                                    gc.collect()
                                    response = http_client.post('http://192.168.4.1:8080/statuses/',
                                                                json=status_json)
                                    # Check for success - resend/queue and resend
                                    if 200 <= response.status_code < 300:
                                        # Success
                                        success_flag = True
                                    response = None
                                    gc.collect()
                                except Exception as the_exception:
                                    import sys
                                    sys.print_exception(the_exception)
                                    jotter.get_jotter().jot_exception(the_exception)
                                    pass

                                # Brief delay
                                utime.sleep_ms(10)

                        if json_to_send_network_topologies:
                            print("Sending network topologies to server.")
                            jotter.get_jotter().jot("Sending network topologies to server.", source_file=__name__)

                        while json_to_send_network_topologies:
                            network_topology_json = json_to_send_network_topologies.popleft()
                            retry_count = 0
                            success_flag = False

                            while not success_flag and retry_count < 4:
                                network_topology_json["retry"] = retry_count
                                retry_count = retry_count + 1

                                try:
                                    gc.collect()
                                    response = http_client.post('http://192.168.4.1:8080/networklogs/',
                                                                json=network_topology_json)
                                    # Check for success - resend/queue and resend
                                    if 200 <= response.status_code < 300:
                                        # Success
                                        success_flag = True
                                    response = None
                                    gc.collect()
                                except Exception as the_exception:
                                    import sys
                                    sys.print_exception(the_exception)
                                    jotter.get_jotter().jot_exception(the_exception)
                                    pass

                                # Brief delay
                                utime.sleep_ms(10)

                    elif (_wifi_current_transition == _wifi_transition_connecting) and \
                            (utime.time() > wifi_connecting_start_time + 30):
                        # Has been trying to connect for 30 seconds.
                        # print("Connecting to wifi took too long. Disconnecting to retry.")
                        jotter.get_jotter().jot("Connecting to wifi took too long. Disconnecting to retry.",
                                                source_file=__name__)
                        # Disable the wifi
                        wifi_disconnecting_start_time = utime.time()
                        disconnect_from_wifi()
                        _wifi_current_transition = _wifi_transition_disconnecting

                    else:
                        # Brief pause instead of tight loop
                        utime.sleep_ms(10)

                # If no messages in the queue and too long since last synch and not rtc callback
                # and next frame time is more than a minute away
                if (not _rtc_callback_flag) and \
                    (not _nm3_callback_flag) and \
                        ((wifi_connection_retry_count > 5) or
                         ((not json_to_send_messages) and (not json_to_send_statuses)
                          and (utime.time() > _nm3_callback_seconds + 30)
                          and (not network_node_addresses or (utime.time() + 60 < network_next_frame_time_s)))):
                    # network frame time is only updated if we have node addresses
                    # Disable the wifi
                    wifi_disconnecting_start_time = utime.time()
                    disconnect_from_wifi()  # Need to give the OS time to do this and power down the wifi chip.
                    wifi_connection_retry_count = 0
                    _wifi_current_transition = _wifi_transition_disconnecting
                    while (not _rtc_callback_flag) and (not _nm3_callback_flag) and (
                            utime.time() < wifi_disconnecting_start_time + 5):
                        # Feed the watchdog
                        wdt.feed()
                        # Give the wifi time to sleep
                        utime.sleep_ms(100)

                    # Double check the flags before powering things off
                    if (not _rtc_callback_flag) and (not _nm3_callback_flag):
                        jotter.get_jotter().jot("Going to sleep.", source_file=__name__)
                        print("Going to sleep.")
                        if not network_nm3_gateway_stay_awake:
                            print("NM3 powering down.")
                            powermodule.disable_nm3()  # power down the NM3
                            pass
                        # Disable the I2C pullups
                        pyb.Pin('PULL_SCL', pyb.Pin.IN)  # disable 5.6kOhm X9/SCL pull-up
                        pyb.Pin('PULL_SDA', pyb.Pin.IN)  # disable 5.6kOhm X10/SDA pull-up
                        # Disable power supply to 232 driver, sensors, and SDCard
                        max3221e.tx_force_off()  # Disable Tx Driver
                        pyb.Pin.board.EN_3V3.off() # except in dev
                        pyb.LED(2).off()  # Asleep
                        utime.sleep_ms(10)

                    while (not _rtc_callback_flag) and (not _nm3_callback_flag):
                        # Feed the watchdog
                        wdt.feed()
                        # Now wait
                        # utime.sleep_ms(100)
                        # pyb.wfi()  # wait-for-interrupt (can be ours or the system tick every 1ms or anything else)
                        machine.lightsleep()  # lightsleep - don't use the time as this then overrides the RTC

                    # Wake-up
                    # pyb.LED(2).on()  # Awake
                    # Feed the watchdog
                    wdt.feed()
                    # Enable power supply to 232 driver, sensors, and SDCard
                    pyb.Pin.board.EN_3V3.on()
                    max3221e.tx_force_on()  # Enable Tx Driver
                    # Enable the I2C pullups
                    pyb.Pin('PULL_SCL', pyb.Pin.OUT, value=1)  # enable 5.6kOhm X9/SCL pull-up
                    pyb.Pin('PULL_SDA', pyb.Pin.OUT, value=1)  # enable 5.6kOhm X10/SDA pull-up

                pass  # end of elif operating_mode == 2:

        except Exception as the_exception:
            import sys
            sys.print_exception(the_exception)
            jotter.get_jotter().jot_exception(the_exception)
            pass
            # Log to file

