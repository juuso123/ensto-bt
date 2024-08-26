import calendar
import json
import struct
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from binascii import crc_hqx

from dateutil import tz
from dateutil.relativedelta import relativedelta
from dotenv import load_dotenv
import os
import logging
import aiomqtt

# Define the UUIDs for the characteristics we are interested in
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
TEMPERATURE_LIMITS_UUID = "89b4c78f-6d5e-4cfa-8e81-4eca9738bbfd"
DATE_TIME_UUID = "b43f918a-b084-45c8-9b60-df648c4a4a1e"
DAYLIGHT_SAVING_UUID = "e4f66642-ed89-4c73-be57-2158c225bbde"

# Define the factory reset ID and others
FACTORY_RESET_ID_UUID = "f366dddb-ebe2-43ee-83c0-472ded74c8fa"
BOOST_UUID = 'ca3c0685-b708-4cd4-a049-5badd10469e7'

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# If an environment variable is not found in the .env file, load_dotenv will then search for a variable by the given name in the host environment.
# loading variables from .env file
load_dotenv()

# Define MQTT broker details, planning on using .env file (dotenv)
MQTT_BROKER = os.getenv("MQTT_BROKER")
MQTT_PORT = int(os.getenv("MQTT_PORT"))
MQTT_CLIENT_ID = os.getenv("MQTT_CLIENT_ID")
MQTT_USERNAME = os.getenv("MQTT_USERNAME")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD")
PAIRING_FILES_PATH = os.getenv("PAIRING_FILES_PATH") or "./"


def extract_mac_address(filename):
    mac_address = filename[-17:][:12].upper()
    mac_address = ':'.join(mac_address[i:i + 2] for i in range(0, len(mac_address), 2))
    return mac_address


def decode_device_name(device):
    return unicodedata.normalize('NFKD',
                          device).encode(
        'ascii', 'ignore').decode('utf-8').lower().replace(' ', '')


async def mqtt_publish(topic, msg):
    try:
        async with aiomqtt.Client(hostname=MQTT_BROKER, port=MQTT_PORT, identifier=MQTT_CLIENT_ID,
                                  username=MQTT_USERNAME, password=MQTT_PASSWORD) as client:

            await client.publish(topic, msg)
            log.info(f"published to {topic}")
            log.debug(f"msg: {msg}")

    except Exception as e:
        log.error(f"MQTT publish failed. {e}")


async def mqtt_publish_with_client(client, topic, msg):
    try:
        log.info(f"msg: {msg}")
        await client.publish(topic, msg)
        log.info(f"published to {topic}")
    except Exception as e:
        log.error(f"MQTT publish failed. {e}")


async def get_client_dict():
    client_dict = {}
    pairing_files = Path(PAIRING_FILES_PATH).glob('pairing-*.json')
    for file in pairing_files:
        # because path is object not string
        pairing_file = str(file)
        log.debug(f"Reading file={pairing_file}")
        thermostat_address = extract_mac_address(pairing_file)
        with open(file) as f:
            pairing_data = json.load(f)

        reset_key = pairing_data["resetCode"]
        device_name = device_name = decode_device_name(pairing_data["deviceName"])

        log.debug(f"reset_key:{reset_key}, device_name:{device_name}, mac:{thermostat_address}")

        client_dict[device_name] = {
            'mac': thermostat_address,
            'resetKey': reset_key,
            'deviceName': device_name
        }
    return client_dict


def calculate_crc(data):
    # Define a function to calculate the CRC
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


async def write_reset_char(client, reset_key):
    await write_gatt_char(client, FACTORY_RESET_ID_UUID, reset_key)


async def write_gatt_char(client, uuid, value):
    await client.write_gatt_char(uuid, value)


async def write_boost(client, value):
    await write_gatt_char(client, BOOST_UUID, value)


async def get_sensor_json(client):
    byte_sequence = await client.read_gatt_char(SENSOR_TYPE_UUID)

    sensor_type_dict = {
        1: "6,8kOhm",
        2: "10kOhm",
        3: "12kOhm",
        4: "15kOhm",
        5: "20kOhm",
        6: "33kOhm",
        7: "47kOhm"
    }
    sensor_type = byte_sequence[0]
    json_dict = {"sensorType": sensor_type, "sensorTypeName": sensor_type_dict[sensor_type]}
    log.debug(f"sensor: {json_dict}")
    return json_dict


async def get_boost_json(client):
    byte_sequence = await client.read_gatt_char(BOOST_UUID)
    boost_enabled = byte_sequence[0]
    boost_offset = struct.unpack('<H', byte_sequence[1:3])[0] / 100
    boost_percent = byte_sequence[3]
    boost_setpoint = struct.unpack('<H', byte_sequence[4:6])[0]
    boost_time = struct.unpack('<H', byte_sequence[6:8])[0]

    json_dict = {"boostEnabled": bool(boost_enabled),"boostOffsetDegrees":boost_offset,
            "boostSetpointMinutes": boost_setpoint, "boostPercentage":boost_percent,
            "boostTimeMinutes": boost_time}
    return json.dumps(json_dict)

async def get_real_time_json(client):
    byte_sequence = await client.read_gatt_char(DATA_UUID)
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


async def get_monitoring_json(client):
    byte_sequence = await read_gatt(client, POWER_UUID)
    log.debug("monitoring data:")
    log.debug(f"{byte_sequence}")

    # Extract header
    header_bytes = byte_sequence[0:3]

    # Extract data pairs
    data_pairs = byte_sequence[3:]

    #json_list = []

    # Calculate timestamp for the current measurement
    header = datetime(2000 + header_bytes[2], header_bytes[1], header_bytes[0], 0, 0, 0)

    json_msg = {'wallClockStamp': header.isoformat()}

    monitoring_dict = {'onOffRatio7Days': []}
    # Process each pair (last week)
    for i in range(0, 16, 2):
        delta_day = data_pairs[i]
        ratio = data_pairs[i + 1]

        # Calculate the actual timestamp
        timestamp = header - relativedelta(days=delta_day)

        json_dict = {"timestamp": timestamp.isoformat(), "ratio": ratio}

        # Append result
        monitoring_dict["onOffRatio7Days"].append(json_dict)

    #json_list.append(json_list_dict)

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

    monitoring_dict.update(json_list_dict)

    '''
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
    '''
    return monitoring_dict


async def get_realtime_power_json(client):
    byte_sequence = await read_gatt(client, REAL_TIME_POWER_UUID)
    log.debug("real time:")
    log.debug(f"{byte_sequence}")

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


async def get_calendar_day_json(client, header):
    byte_sequence = await client.read_gatt_char(CALENDAR_UUID)
    log.debug("calendar day:")
    log.debug(f"{byte_sequence}")

    # Extract data pairs
    data_pairs = byte_sequence[2:]

    # List to hold the parsed data
    json_list = []

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


async def get_vacation_json(client):
    byte_sequence = await client.read_gatt_char(VACATION_TIME_UUID)
    log.debug("vacation time:")
    log.debug(f"{byte_sequence}")

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


async def get_information_json(client, device_name, date_time):
    # Floor area
    floor_bytes = await client.read_gatt_char(FLOOR_AREA_UUID)
    area, = struct.unpack('<h', floor_bytes[0:2])
    log.debug(f"area: {area}")

    # Heating power
    heating_bytes = await client.read_gatt_char(HEATING_POWER_UUID)
    power, = struct.unpack('<h', heating_bytes[0:2])
    log.debug(f"power: {power}")

    # Sensor type
    sensor_json_dict = await get_sensor_json(client)

    # Adaptive temp control
    adaptive_bytes = await client.read_gatt_char(ADAPTIVE_CONTROL_UUID)
    adaptive = adaptive_bytes[0]
    log.debug(f"adaptive: {adaptive}")

    # Temp limits
    limits_bytes = await client.read_gatt_char(TEMPERATURE_LIMITS_UUID)
    low, = struct.unpack('<h', limits_bytes[0:2])
    high, = struct.unpack('<h', limits_bytes[2:4])
    log.debug(f"limits: low={low}, high={high}")

    return {"name": device_name, "floorArea": area, "heatingPower": power, "sensor": sensor_json_dict,
            "adaptive": adaptive, "temperatureLimits": {"low": float(low / 100), "high": float(high / 100)},
            "datetime": date_time.isoformat()}


async def get_datetime(client):
    date_time_bytes = await client.read_gatt_char(DATE_TIME_UUID)
    year = int.from_bytes(date_time_bytes[:2], byteorder='little')

    # Day-light saving configuration
    daylight_saving_bytes = await client.read_gatt_char(DAYLIGHT_SAVING_UUID)
    offset = int.from_bytes(daylight_saving_bytes[-2:], byteorder='little')
    tzlocal = tz.tzoffset('EST', offset * 60)
    return datetime(year, date_time_bytes[2],
                    date_time_bytes[3], date_time_bytes[4], date_time_bytes[5],
                    date_time_bytes[5], tzinfo=tzlocal)


async def get_calendar_mode(client):
    calendar_mode_bytes = await client.read_gatt_char(CALENDAR_MODE_UUID)
    calendar_mode = calendar_mode_bytes[0]  # Assuming uint8_t, range 0-1
    log.debug(f"Calendar mode: {bool(calendar_mode)}")
    return calendar_mode


async def get_json_msg(client, device_name):
    # Date and time
    date_time = await get_datetime(client)

    # Real Time Indication temperature and mode
    real_time_json_dict = await get_real_time_json(client)

    # Monitoring data
    monitoring_json_dict = await get_monitoring_json(client)

    # Real time indication power consumption
    real_time_power_json_dict = await get_realtime_power_json(client)

    # Calendar mode
    calendar_mode = await get_calendar_mode(client)

    # Calendar
    calendar_json_dict = await get_calendar_day_json(client, date_time)

    # Vacation time
    vacation_json_dict = await get_vacation_json(client)

    # Information
    information_json_dict = await get_information_json(client, device_name, date_time)

    # Creating message to be sent
    json_dict = {"information": information_json_dict,
                 "realtime": real_time_json_dict, "vacation": vacation_json_dict,
                 "calendar": {"mode": calendar_mode, "days": calendar_json_dict},
                 "power": real_time_power_json_dict}
    json_dict.update(monitoring_json_dict)

    return json.dumps(json_dict)


def create_boost_byte_array(boost_enabled, boost_offset_degrees, boost_offset_percentage, boost_time_set_point,
                            boost_time_minutes):
    # Pack values into a byte array according to the specification
    byte_array = bytearray()
    byte_array.append(1 if boost_enabled else 0)  # BYTE[0]: Boost enabled (1 byte)
    byte_array.extend(
        struct.pack('<h', boost_offset_degrees))  # BYTE[1-2]: Boost offset in degrees (2 bytes, little-endian short)
    byte_array.append(boost_offset_percentage)  # BYTE[3]: Boost offset percentage (1 byte)
    byte_array.extend(struct.pack('<H',
                                  boost_time_set_point))  # BYTE[4-5]: Boost time set point in minutes (2 bytes, little-endian ushort)
    byte_array.extend(
        struct.pack('<H', boost_time_minutes))  # BYTE[6-7]: Boost time in minutes (2 bytes, little-endian ushort)
    return byte_array
