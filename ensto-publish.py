import asyncio
import json
import os

from dotenv import load_dotenv
from bleak import BleakClient
from ensto import log, mqtt_publish, get_client_dict, read_gatt, get_sensor_json, get_real_time_json, \
    get_monitoring_json, get_realtime_power_json, get_calendar_day_json, get_vacation_json, get_information_json, \
    get_datetime, write_reset_char, get_calendar_mode, decode_device_name, get_json_msg

# If an environment variable is not found in the .env file, load_dotenv will then search for a variable by the given name in the host environment.
# loading variables from .env file
load_dotenv()

BASE_TOPIC = os.getenv("BASE_TOPIC") or "ECO16BT"

# Define MQTT broker details, planning on using .env file (dotenv)
PAIRING_FILES_PATH = os.getenv("PAIRING_FILES_PATH") or "./"

ADAPTER_ADDRESS = "hci0"  #Your bleuetooth controller hci address


async def connect(thermostat_address, reset_key, device):
    max_retries = 3
    retry_delay = 20  # seconds

    device_name = decode_device_name(device)

    for attempt in range(1, max_retries + 1):
        try:
            log.info(f"Connection attempt {attempt}")

            # Connect to the device
            async with BleakClient(thermostat_address, adapter=ADAPTER_ADDRESS, timeout=30) as client:
                log.debug("Connected to the device")

                if client._backend.__class__.__name__ == "BleakClientBlueZDBus":
                    await client._backend._acquire_mtu()

                log.debug(f"mtu: {client.mtu_size}")

                # Pairing (read and write factory reset ID)
                await write_reset_char(client, reset_key)
                log.debug("Paired with the device")

                json_msg = await get_json_msg(client, device_name)
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
    client_dict = await get_client_dict()

    if not client_dict:
        log.error("No devices found.")
        return

    for identifier, device_info in client_dict.items():
        reset_key = bytes(device_info['resetKey'])
        thermostat_address = device_info['mac']
        log.info(f"Connecting to {identifier}")
        await connect(thermostat_address, reset_key, identifier)


if __name__ == "__main__":
    asyncio.run(main())
