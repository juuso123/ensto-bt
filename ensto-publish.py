import asyncio
import json
import logging
import struct
import unicodedata
from pathlib import Path
from dotenv import load_dotenv, dotenv_values
import os

import aiomqtt
from bleak import BleakClient, BleakError

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# If an environment variable is not found in the .env file, load_dotenv will then search for a variable by the given name in the host environment.
# loading variables from .env file
load_dotenv()

config = dotenv_values(".env")

# Define the UUIDs for the characteristics we are interested in (update with correct UUIDs)
DEVICE_NAME_UUID = "00002a00-0000-1000-8000-00805f9b34fb"  # Example: Device Name
DATA_UUID = "66ad3e6b-3135-4ada-bb2b-8b22916b21d4"  # Example: Data
POWER_UUID = "c1686f28-fa1b-4791-9eca-35523fb3597e"  # Example: Power

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


async def connect(thermostat_address, reset_key, device_name):
    max_retries = 3
    retry_delay = 20  # seconds

    for attempt in range(1, max_retries + 1):
        try:
            log.info(f"Connection attempt {attempt}")

            # Connect to the device
            async with BleakClient(thermostat_address, adapter=ADAPTER_ADDRESS) as client:
                log.debug("Connected to the device")

                # Pairing (read and write factory reset ID)
                #factory_reset_id = await client.read_gatt_char(FACTORY_RESET_ID_UUID)
                await client.write_gatt_char(FACTORY_RESET_ID_UUID, reset_key)
                log.debug("Paired with the device")
                #print(f"reset key: {factory_reset_id}")

                # Read characteristics
                data_bytes = await client.read_gatt_char(DATA_UUID)
                #power_bytes = await client.read_gatt_char(DATA_UUID)

                device_name = unicodedata.normalize('NFKD',
                                                    device_name).encode(
                    'ascii', 'ignore').decode('utf-8').lower().replace(' ', '')

                # Decode each field according to the specified structure
                temp_setting_target = struct.unpack('<H', data_bytes[1:3])[0] * (45.0 / 450)
                temp_setting_percent = data_bytes[3]  # Assuming uint8_t, range 0-100
                room_temperature = struct.unpack('<h', data_bytes[4:6])[0] * (40.0 / 400)
                floor_temperature = struct.unpack('<h', data_bytes[6:8])[0] * (55.0 / 550)
                active_relay_state = data_bytes[8]  # Assuming 0=off, 1=on
                alarm_code = struct.unpack('<I', data_bytes[9:13])[0]  # Assuming unsigned int (32>
                active_mode = data_bytes[13]  # Assuming 1=manual, 2=calendar, 3=vacation
                active_heating_mode = data_bytes[14]  # Assuming specific characteristics for heat>
                boost_mode_enabled = data_bytes[15]  # Assuming 1=enable, 0=disable
                boost_mode_setpoint_minutes = struct.unpack('<H', data_bytes[16:18])[0]  # Assumin>
                boost_mode_remaining_minutes = struct.unpack('<H', data_bytes[18:20])[0]  # Assumi>
                potentiometer_value = data_bytes[20]  # A

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

                #log.debug(f"Power: {power_bytes}")

                json_dict = {"roomTemperature": round(room_temperature, 2),
                             "targetTemperature": round(temp_setting_target, 2),
                             "floorTemperature": round(floor_temperature, 2),
                             "heat": bool(active_relay_state),
                             "boostEnabled": bool(boost_mode_enabled),
                             "boostSetpointMinutes": boost_mode_setpoint_minutes,
                             "boostRemainingMinutes": boost_mode_remaining_minutes}

                json_msg = json.dumps(json_dict)
                topic = BASE_TOPIC + "/" + device_name
                await mqtt_publish(topic, json_msg)

                return  #exit if successful

        except Exception as e:
            log.warning(f"Attempt {attempt} failed with error: {e}",e)
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
