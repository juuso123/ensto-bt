import asyncio
import json
import logging
import struct
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path

from dateutil import parser
from dotenv import load_dotenv, dotenv_values
import os
from dateutil.relativedelta import *
import calendar
import aiomqtt
from bleak import BleakClient, BleakError
from binascii import crc_hqx

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# If an environment variable is not found in the .env file, load_dotenv will then search for a variable by the given name in the host environment.
# loading variables from .env file
load_dotenv()

config = dotenv_values(".env")

# Define the UUIDs for the characteristics we are interested in (update with correct UUIDs)
DEVICE_NAME_UUID = "00002a00-0000-1000-8000-00805f9b34fb"  # Example: Device Name
DATA_UUID = "66ad3e6b-3135-4ada-bb2b-8b22916b21d4"  # Example: Data
POWER_UUID = "ecc794d2-c790-4abd-88a5-79abf9417908"  # Example: Power
REAL_TIME_POWER_UUID = "c1686f28-fa1b-4791-9eca-35523fb3597e"
CALENDAR_MODE_UUID = "636d45fd-d7be-491f-966c-380f8631b2c6"
VACATION_TIME_UUID = "6584e9c6-4784-41aa-ac09-c899191048ae"
CALENDAR_UUID = "20db94b9-bd18-4f84-bf16-de1163adfd8c"
FLOOR_AREA_UUID = "5c897ab6-354c-443d-9f36-f3f7263868dd"
HEATING_POWER_UUID = "53b7bf87-6cf0-4790-839a-e72d3afbec44"
SENSOR_TYPE_UUID = "f561ce1f-61fb-4fa2-8bef-5fecc949b55b"
ADAPTIVE_CONTROL_UUID = "c2dc85e9-47bf-4968-9562-d2e1980ed4e4"
TEMPERATURE_LIMITS_UUID ="89b4c78f-6d5e-4cfa-8e81-4eca9738bbfd"

# Define the factory reset ID characteristic UUID
FACTORY_RESET_ID_UUID = "f366dddb-ebe2-43ee-83c0-472ded74c8fa"

BASE_TOPIC = "ECO16BT"

# Define MQTT broker details, planning on using .env file (dotenv)
MQTT_BROKER = os.getenv("MQTT_BROKER")
MQTT_PORT = int(os.getenv("MQTT_PORT"))
MQTT_CLIENT_ID = os.getenv("MQTT_CLIENT_ID")
MQTT_USERNAME = os.getenv("MQTT_USERNAME")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD")
PAIRING_FILES_PATH = os.getenv("PAIRING_FILES_PATH") or "./"

ADAPTER_ADDRESS = "hci0"  #Your bleuetooth controller hci address


def extract_mac_address(filename):
    mac_address = filename[-17:][:12].upper()
    mac_address = ':'.join(mac_address[i:i + 2] for i in range(0, len(mac_address), 2))
    return mac_address


def get_reset_key(filename):
    with open(filename) as f:
        pairing_data = json.load(f)
        return bytes(pairing_data["resetCode"])


async def mqtt_publish(topic, msg):
    try:
        async with aiomqtt.Client(hostname=MQTT_BROKER, port=MQTT_PORT, identifier=MQTT_CLIENT_ID,
                                  username=MQTT_USERNAME, password=MQTT_PASSWORD) as client:

            await client.publish(topic, msg)
            log.info(f"published to {topic}")
            log.info(f"msg: {msg}")

    except BleakError as e:
        log.error(f"Bleak error. {e}")
    except Exception as e:
        log.error(f"MQTT publish failed. {e}")


# Define a function to calculate the CRC
def calculate_crc(data):
    crc = crc_hqx(data, 0xffff)
    crc = crc & 0x3fff  # Mask out the higher two bits
    return crc


async def read_gatt(client, uuid):
    data_buffer = bytearray()
    more_data = True
    packet_index = 0
    data_old = {}
    while more_data:
        data = await client.read_gatt_char(uuid)
        header = data[0]
        data_content = data[1:]

        if packet_index == 0:
            data_buffer.extend(data_content)
        else:
            if header & 0x40:
                # check not same as previous
                crc_received = calculate_crc(data)
                crc_previous = calculate_crc(data_old)
                if crc_received != crc_previous:
                    data_buffer.extend(data_content)
                more_data = False
            else:
                data_buffer.extend(data_content)
        data_old = data
        packet_index += 1
    return data_buffer


def get_vacation_json(byte_sequence):
    timestamp_from = datetime(2000 + byte_sequence[0], byte_sequence[1], byte_sequence[2], byte_sequence[3],
                              byte_sequence[4], 0)
    timestamp_to = datetime(2000 + byte_sequence[5], byte_sequence[6], byte_sequence[7], byte_sequence[8],
                            byte_sequence[9], 0)

    temp, = struct.unpack('<h', byte_sequence[10:12])
    temp_float = float(temp / 100)

    json_list_dict = {"dateFrom": timestamp_from.isoformat(), "dateTo": timestamp_to.isoformat(),
                      "offsetTemperature": temp_float, "enabled": bool(byte_sequence[13]),
                      "currentMode": byte_sequence[14]}
    return json_list_dict


def get_real_time_json(byte_sequence):
    # Decode each field according to the specified structure
    temp_setting_target = struct.unpack('<H', byte_sequence[1:3])[0] * (45.0 / 450)
    temp_setting_percent = byte_sequence[3]  # Assuming uint8_t, range 0-100
    room_temperature = struct.unpack('<h', byte_sequence[4:6])[0] * (40.0 / 400)
    floor_temperature = struct.unpack('<h', byte_sequence[6:8])[0] * (55.0 / 550)
    active_relay_state = byte_sequence[8]  # Assuming 0=off, 1=on
    alarm_code = struct.unpack('<I', byte_sequence[9:13])[0]  # Assuming unsigned int (32>
    active_mode = byte_sequence[13]  # Assuming 1=manual, 2=calendar, 3=vacation
    active_heating_mode = byte_sequence[14]  # Assuming specific characteristics for heat>
    boost_mode_enabled = byte_sequence[15]  # Assuming 1=enable, 0=disable
    boost_mode_setpoint_minutes = struct.unpack('<H', byte_sequence[16:18])[0]  # Assumin>
    boost_mode_remaining_minutes = struct.unpack('<H', byte_sequence[18:20])[0]  # Assumi>
    potentiometer_value = byte_sequence[20]  # A

    # Print decoded values
    log.debug(f"Temp setting (target): {temp_setting_target:.2f}   C")
    log.debug(f"Temp setting %: {temp_setting_percent}%")
    log.debug(f"Room temperature: {room_temperature:.2f}   C")
    log.debug(f"Floor temperature: {floor_temperature:.2f}   C")
    log.debug(f"Active relay state: {'On' if active_relay_state == 1 else 'Off'}")
    log.debug(f"Alarm code: {alarm_code}")
    log.debug(f"Active mode: {active_mode}")
    log.debug(f"Active heating mode: {active_heating_mode}")
    log.debug(f"Boost mode enabled: {'Enabled' if boost_mode_enabled == 1 else 'Disabled'}")
    log.debug(f"Boost mode setpoint: {boost_mode_setpoint_minutes} minutes")
    log.debug(f"Boost mode remaining time: {boost_mode_remaining_minutes} minutes")
    log.debug(f"Potentiometer value: {potentiometer_value}%")

    json_dict = {"roomTemperature": round(room_temperature, 2),
                 "targetTemperature": round(temp_setting_target, 2),
                 "floorTemperature": round(floor_temperature, 2),
                 "heat": bool(active_relay_state),
                 "boostEnabled": bool(boost_mode_enabled),
                 "boostSetpointMinutes": boost_mode_setpoint_minutes,
                 "boostRemainingMinutes": boost_mode_remaining_minutes}
    return json_dict


def get_calendar_day_json(byte_sequence, wall_clock_stamp):
    # Extract header
    header_bytes = byte_sequence[0:1]

    # Extract data pairs
    data_pairs = byte_sequence[2:]

    # List to hold the parsed data
    json_list = []

    header = parser.parse(wall_clock_stamp)

    for i in range(0, len(data_pairs) - 8, 8):
        hours_from = data_pairs[i]
        minutes_from = data_pairs[i + 1]

        hours_to = data_pairs[i + 2]
        minutes_to = data_pairs[i + 3]

        timestamp_from = header.replace(minute=minutes_from, hour=hours_from)
        timestamp_to = header.replace(minute=minutes_to, hour=hours_to)

        temp, = struct.unpack('<h', data_pairs[i + 4:i + 6])
        temp_float = float(temp / 100)

        percent = round(data_pairs[i + 7] / 255 * 100, 2) if data_pairs[i + 7] > 0 else 0

        enabled = data_pairs[i + 8]

        json_dict = {"timestamp_from": timestamp_from.isoformat(), "timestamp_to": timestamp_to.isoformat(),
                     "offsetTemperature": temp_float,
                     "offsetPercent": percent, "enabled": bool(enabled)
                     }

        # Append result
        json_list.append(json_dict)

    return json_list


def get_monitoring_json(byte_sequence):
    # Extract header
    header_bytes = byte_sequence[0:3]

    # Extract data pairs
    data_pairs = byte_sequence[3:]

    json_msg = {}
    json_list = []

    # Calculate timestamp for the current measurement
    header = datetime(2000 + header_bytes[2], header_bytes[1], header_bytes[0], 0, 0, 0)

    json_msg = {'wallClockStamp': header.isoformat()}

    json_list_dict = {'onOffRatio7Days': []}
    # Process each pair (last week)
    for i in range(0, 16, 2):
        delta_day = data_pairs[i]
        ratio = data_pairs[i + 1]

        # Calculate the actual timestamp
        timestamp = header - relativedelta(days=delta_day)

        json_dict = {"timestamp": timestamp.isoformat(), "ratio": ratio}

        # Append result
        json_list_dict["onOffRatio7Days"].append(json_dict)

    json_list.append(json_list_dict)

    header2_year = data_pairs[17]
    header2_month = data_pairs[16]
    header2 = datetime(2000 + header2_year, header2_month, calendar.monthrange(header2_year, header2_month)[1], 0, 0, 0)

    json_list_dict = {'onOffRatio12Months': []}
    for i in range(18, 44, 2):
        delta_month = data_pairs[i]
        ratio = data_pairs[i + 1]

        # Calculate the actual timestamp
        timestamp2 = header2 - relativedelta(months=delta_month)

        json_dict = {"timestamp": timestamp2.isoformat(), "ratio": ratio}

        # Append result
        json_list_dict["onOffRatio12Months"].append(json_dict)

    json_list.append(json_list_dict)

    header3_year = 2000 + data_pairs[47]
    header3_month = data_pairs[46]
    header3_day = data_pairs[45]
    header3_hour = data_pairs[44]
    header3 = datetime(header3_year, header3_month, header3_day, header3_hour, 0, 0)

    json_list_dict = {'temperature24Hours7Days': []}
    for i in range(48, len(data_pairs), 5):
        delta_hour = data_pairs[i]
        ratio1, = struct.unpack('<h', data_pairs[i + 1:i + 3])
        ratio1_float = float(ratio1 / 10)

        ratio2, = struct.unpack('<h', data_pairs[i + 3:i + 5])
        ratio2_float = float(ratio2 / 10)

        # Calculate the actual timestamp
        timestamp3 = header3 - relativedelta(hours=delta_hour)

        json_dict = {"timestamp": timestamp3.isoformat(), "floorTemperature": ratio1_float,
                     "roomTemperature": ratio2_float}

        # Append result
        json_list_dict["temperature24Hours7Days"].append(json_dict)

    json_list.append(json_list_dict)

    json_msg['values'] = json_list

    log.debug(f"{json.dumps(json_msg, indent=2)}")

    return json_msg


def get_realtime_power_json(byte_sequence):
    # Extract header
    header_bytes = byte_sequence[0:4]

    # Extract data pairs
    data_pairs = byte_sequence[4:]

    # Calculate timestamp for the current measurement
    header = datetime(2000 + header_bytes[3], header_bytes[2], header_bytes[1], header_bytes[0], 0, 0)

    # List to hold the parsed data
    json_list_dict = []

    # Process each pair
    for i in range(0, len(data_pairs), 2):
        delta_hour = data_pairs[i]
        ratio = data_pairs[i + 1]

        # Calculate the actual timestamp
        timestamp = header - timedelta(hours=delta_hour)

        # Append result
        json_dict = {"timestamp": timestamp.isoformat(), "ratio": ratio}

        # Append result
        json_list_dict.append(json_dict)

    return json_list_dict


def get_sensor_json(byte_sequence):
    sensor_type_dict = {
        1: "6,8kOhm",
        2: "10kOhm",
        3: "12kOhm",
        4: "15kOhm",
        5: "20kOhm",
        6: "33kOhm",
        7: "47kOhm"
    }
    log.info(f"{sensor_type_dict}")
    sensor_type = byte_sequence[0]

    return {"sensorType": sensor_type, "sensorTypeName": sensor_type_dict[sensor_type]}


async def connect(thermostat_address, reset_key, device_name):
    max_retries = 3
    retry_delay = 20  # seconds
    json_dict = {}

    device_name = unicodedata.normalize('NFKD',
                                        device_name).encode(
        'ascii', 'ignore').decode('utf-8').lower().replace(' ', '')

    for attempt in range(1, max_retries + 1):
        try:
            log.info(f"Connection attempt {attempt}")

            # Connect to the device
            async with BleakClient(thermostat_address, adapter=ADAPTER_ADDRESS, timeout=15) as client:
                log.debug("Connected to the device")

                if client._backend.__class__.__name__ == "BleakClientBlueZDBus":
                    await client._backend._acquire_mtu()

                log.debug(f"mtu: {client.mtu_size}")

                # Pairing (read and write factory reset ID)
                await client.write_gatt_char(FACTORY_RESET_ID_UUID, reset_key)
                log.debug("Paired with the device")

                # Real Time Indication temperature and mode
                data_bytes = await client.read_gatt_char(DATA_UUID)
                real_time_json_dict = get_real_time_json(data_bytes)

                # Monitoring data
                monitoring_bytes = await read_gatt(client, POWER_UUID)
                log.debug("monitoring data:")
                log.debug(f"{monitoring_bytes}")
                monitoring_json_dict = get_monitoring_json(monitoring_bytes)
                wall_clock_stamp = monitoring_json_dict["wallClockStamp"]

                # Real time indication power consumption
                real_time_power_bytes = await read_gatt(client, REAL_TIME_POWER_UUID)
                log.debug("real time:")
                log.debug(f"{real_time_power_bytes}")
                real_time_power_json_dict = get_realtime_power_json(real_time_power_bytes)

                # Calendar mode
                calendar_mode_bytes = await client.read_gatt_char(CALENDAR_MODE_UUID)
                calendar_mode = calendar_mode_bytes[0]  # Assuming uint8_t, range 0-1
                log.debug(f"Calendar mode: {bool(calendar_mode)}")

                # Calendar
                calendar_bytes = await client.read_gatt_char(CALENDAR_UUID)
                log.debug("calendar day:")
                log.debug(f"{calendar_bytes}")
                calendar_json_dict = get_calendar_day_json(calendar_bytes, wall_clock_stamp)

                # Vacation time
                vacation_bytes = await client.read_gatt_char(VACATION_TIME_UUID)
                log.debug("vacation time:")
                log.debug(f"{vacation_bytes}")
                vacation_json_dict = get_vacation_json(vacation_bytes)

                # Floor area
                floor_bytes = await client.read_gatt_char(FLOOR_AREA_UUID)
                area, = struct.unpack('<h', floor_bytes[0:2])
                log.info(f"area: {area}")

                # Heating power
                heating_bytes = await client.read_gatt_char(HEATING_POWER_UUID)
                power, = struct.unpack('<h', heating_bytes[0:2])
                log.info(f"power: {power}")

                # Sensor type
                sensor_bytes = await client.read_gatt_char(SENSOR_TYPE_UUID)
                sensor_json_dict = get_sensor_json(sensor_bytes)
                log.debug(f"sensor: {sensor_json_dict}")

                # Adaptive temp control
                adaptive_bytes = await client.read_gatt_char(ADAPTIVE_CONTROL_UUID)
                adaptive = adaptive_bytes[0]
                log.debug(f"adaptive: {adaptive}")

                # Temp limits
                limits_bytes = await client.read_gatt_char(TEMPERATURE_LIMITS_UUID)
                low, = struct.unpack('<h', limits_bytes[0:2])
                high, = struct.unpack('<h', limits_bytes[2:4])
                log.debug(f"limits: low={low}, high={high}")

                # Creating message to be sent
                json_dict = {"information": {"floorArea": area, "heatingPower": power, "sensor": sensor_json_dict,
                                             "adaptive": adaptive,
                                             "temperatureLimits": {"low": float(low / 100), "high": float(high / 100)}},
                             "realtime": real_time_json_dict, "vacation": vacation_json_dict,
                             "calendar": {"mode": calendar_mode, "days": calendar_json_dict},
                             "power": real_time_power_json_dict}

                for d in monitoring_json_dict['values']:
                    for x, y in d.items():
                        json_dict[x] = y

                json_msg = json.dumps(json_dict)
                topic = BASE_TOPIC + "/" + device_name
                await mqtt_publish(topic, json_msg)

                return  #exit if successful

        except Exception as e:
            log.warning(f"Attempt {attempt} failed with error: {e}", e)
            if attempt < max_retries:
                log.info(f"Retrying in {retry_delay} seconds...")
                await asyncio.sleep(retry_delay)
            else:
                log.error("Max retries reached, exiting.")
                break


async def main():
    pairing_files = Path(PAIRING_FILES_PATH).glob('pairing-*.json')
    for file in pairing_files:
        if str(file) != 'pairing-000000000000.json':
            # because path is object not string
            pairing_file = str(file)
            thermostat_address = extract_mac_address(pairing_file)
            log.info(f"Connecting to {thermostat_address}")
            with open(pairing_file) as f:
                pairing_data = json.load(f)

            reset_key = bytes(pairing_data["resetCode"])
            device_name = pairing_data["deviceName"]

            await connect(thermostat_address, reset_key, device_name)


if __name__ == "__main__":
    asyncio.run(main())
