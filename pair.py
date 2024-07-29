import asyncio
import json
import logging
import os
import unicodedata

import aiofiles
from bleak import BleakScanner, BleakClient
from dotenv import load_dotenv
from aiofiles import os as async_os

# If an environment variable is not found in the .env file,
# load_dotenv will then search for a variable by the given name in the host environment.
# loading variables from .env file
load_dotenv()

# Define the UUIDs for the characteristics we are interested in (update with correct UUIDs)
DEVICE_NAME_UUID = "00002a00-0000-1000-8000-00805f9b34fb"  # Example: Device Name

# Define the factory reset ID characteristic UUID
FACTORY_RESET_ID_UUID = "f366dddb-ebe2-43ee-83c0-472ded74c8fa"

PAIRING_FILES_PATH = os.getenv("PAIRING_FILES_PATH") or "./"

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


def decode(data):
    data_null_index = data.find(b'\x00')

    # Slice the byte sequence up to the first null byte
    return (unicodedata.normalize('NFKD',
                                  data[:data_null_index].decode('utf-8', errors='ignore'))
            .replace(' ', ''))


def decode_reset_key(factory_reset_id):
    # Convert to a list of integers
    int_list = list(factory_reset_id)

    # Extract the first four values
    return int_list[:4]


async def save_pairing_file(address, reset_key, device_name):
    file_path = PAIRING_FILES_PATH + "pairing-" + address.replace(":", "").lower() + ".json"
    if not await async_os.path.exists(file_path):
        json_dict = {"resetKey": reset_key, "deviceName": device_name}
        log.info(f"{json_dict}")
        async with aiofiles.open(file_path, mode='w') as f:
            await f.write(json.dumps(json_dict))


async def connect(device_address):
    async with BleakClient(device_address) as client:
        # Pairing (read and write factory reset ID)
        factory_reset_id = await client.read_gatt_char(FACTORY_RESET_ID_UUID)
        await client.write_gatt_char(FACTORY_RESET_ID_UUID, factory_reset_id)
        log.info("Paired with the device")
        reset_key = decode_reset_key(factory_reset_id)
        log.debug(f"reset key: {reset_key}")

        # Read device name
        device_name = decode(await client.read_gatt_char(DEVICE_NAME_UUID))
        log.info(f"device_name={device_name}")
        # save to pairing file
        await save_pairing_file(device_address, reset_key, device_name)


async def main():
    devices = await BleakScanner.discover(timeout=15,return_adv=True)
    for d in devices:
        device = devices[d]
        try:
            device_name = str(device[1].local_name)
            if "ECO16BT" in device_name:
                #   when the button is pushed, manufacturerData looks like
                #   'ECO16BT;1;0;0;' where "1" is PAIRING.

                log.debug(f"{device[1].manufacturer_data}")
                pairing = next(iter(device[1].manufacturer_data.items()))[1][8:9].decode()

                log.debug(f"Device: {device_name} pairing:{pairing}")
                if pairing == "1":
                    device_address = d
                    log.info(f"Device: {device_name}, Address: {device_address} is in pairing mode")

                    # connect
                    log.info("connect")
                    await connect(device_address)
                    continue
        except Exception as e:
            log.error(f"Error in callback: {e}", e)


if __name__ == "__main__":
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    loop.run_until_complete(main())
