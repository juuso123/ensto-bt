import asyncio
import json
import logging
import struct
import unicodedata
from pathlib import Path

import aiomqtt
from bleak import BleakClient, BleakError

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# Define the UUIDs for the characteristics we are interested in (update with correct UUIDs)
DEVICE_NAME_UUID = "00002a00-0000-1000-8000-00805f9b34fb"  # Example: Device Name
DATA_UUID = "66ad3e6b-3135-4ada-bb2b-8b22916b21d4"  # Example: Data
POWER_UUID = "c1686f28-fa1b-4791-9eca-35523fb3597e"  # Example: Power

# Define the factory reset ID characteristic UUID
FACTORY_RESET_ID_UUID = "f366dddb-ebe2-43ee-83c0-472ded74c8fa"  # Example

# Define MQTT broker details, planning on using .env file
MQTT_BROKER = "127.0.0.1"
MQTT_PORT = 1883
MQTT_CLIENT_ID = "ensto"
BASE_TOPIC = "ECO16BT"
MQTT_USERNAME = "user"
MQTT_PASSWORD = "pass"

ADAPTER_ADDRESS = "hci1" #Your bleuetooth controller hci address

def extract_mac_address(filename):
    mac_address = filename[-17:][:12].upper()
    mac_address = ':'.join(mac_address[i:i + 2] for i in range(0, len(mac_address), 2))
    return mac_address


def get_reset_key(filename):
    with open(filename) as f:
        pairing_data = json.load(f)
        return bytes(pairing_data["resetCode"])


async def mqtt_publish(msgs):
    try:
        async with aiomqtt.Client(hostname=MQTT_BROKER, port=MQTT_PORT, identifier=MQTT_CLIENT_ID,
                                  username=MQTT_USERNAME, password=MQTT_PASSWORD) as client:
            for msg in msgs:
                await client.publish(topic=msg[0], payload=msg[1])
                #log.info(f"published to {msg[0]}, payload:{msg[1]}")

            log.info("published")
    except BleakError as e:
        log.error(f"Bleak error. {e}")
    except Exception as e:
        log.error(f"MQTT publish failed. {e}")


async def connect(thermostat_address, reset_key):
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
                device_name = await client.read_gatt_char(DEVICE_NAME_UUID)
                data_bytes = await client.read_gatt_char(DATA_UUID)
                #power_bytes = await client.read_gatt_char(DATA_UUID)

                #device_name_trimmed = bytes(device_name.rstrip(b'\x00'))
                #print(f"Device Name trimmed: {device_name_trimmed}")
                # Find the index of the first null byte
                device_name_null_index = device_name.find(b'\x00')

                # Slice the byte sequence up to the first null byte
                device_name_utf8_bytes = device_name[:device_name_null_index]
                device_name = unicodedata.normalize('NFKD',
                                                    device_name_utf8_bytes.decode('utf-8', errors='ignore')).encode(
                    'ascii', 'ignore').decode('utf-8').lower().replace(' ', '')
                log.info(f"Device Name: {device_name_utf8_bytes.decode('utf-8', errors='ignore')}")

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
                log.info(f"Temp setting (target): {temp_setting_target:.2f}   C")
                log.info(f"Temp setting %: {temp_setting_percent}%")
                log.info(f"Room temperature: {room_temperature:.2f}   C")
                log.info(f"Floor temperature: {floor_temperature:.2f}   C")
                log.info(f"Active relay state: {'On' if active_relay_state == 1 else 'Off'}")
                log.info(f"Alarm code: {alarm_code}")
                log.info(f"Active mode: {active_mode}")
                log.info(f"Active heating mode: {active_heating_mode}")
                log.info(f"Boost mode enabled: {'Enabled' if boost_mode_enabled == 1 else 'Disabled'}")
                log.info(f"Boost mode setpoint: {boost_mode_setpoint_minutes} minutes")
                log.info(f"Boost mode remaining time: {boost_mode_remaining_minutes} minutes")
                log.info(f"Potentiometer value: {potentiometer_value}%")

                #log.debug(f"Power: {power_bytes}")

                msgs = [(BASE_TOPIC + device_name + "/room/temperature", round(room_temperature, 2)),
                        (BASE_TOPIC + device_name + "/target/temperature", round(temp_setting_target, 2)),
                        (BASE_TOPIC + device_name + "/floor/temperature", round(floor_temperature, 2)),
                        (BASE_TOPIC + device_name + "/heat", active_relay_state),
                        (BASE_TOPIC + device_name + "/boost/enabled", boost_mode_enabled),
                        (BASE_TOPIC + device_name + "/boost/setpoint", boost_mode_setpoint_minutes),
                        (BASE_TOPIC + device_name + "/boost/remaining", boost_mode_remaining_minutes)]

                await mqtt_publish(msgs)

                log.debug(f"msgs: {msgs}")

                return  #exit if successful

        except Exception as e:
            log.warning(f"Attempt {attempt} failed with error: {e}")
            if attempt < max_retries:
                log.info(f"Retrying in {retry_delay} seconds...")
                await asyncio.sleep(retry_delay)
            else:
                log.error("Max retries reached, exiting.")
                break


async def main():
    directory_in_str = "./" # change where you have your pairing files
    pathlist = Path(directory_in_str).rglob('pairing-*.json')
    for path in pathlist:
        # because path is object not string
        pairing_file = str(path)
        thermostat_address = extract_mac_address(pairing_file)
        log.info(f"Connecting to {thermostat_address}")
        reset_key = get_reset_key(path)
        log.debug(f"reset_key:{reset_key}")

        await connect(thermostat_address, reset_key)


if __name__ == "__main__":
    asyncio.run(main())
