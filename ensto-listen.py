import logging
import asyncio
import struct
import json
import os
import aiomqtt
from pathlib import Path
from bleak import BleakScanner, BleakClient, BleakError
from dotenv import load_dotenv
from collections import deque

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# Define the UUIDs for the characteristics we are interested in (update with correct UUIDs)
BOOST_UUID = 'ca3c0685-b708-4cd4-a049-5badd10469e7'

# Define the factory reset ID characteristic UUID
FACTORY_RESET_ID_UUID = "f366dddb-ebe2-43ee-83c0-472ded74c8fa"  # Example

BASE_TOPIC = "ECO16BT"

# Define MQTT broker details, planning on using .env file (dotenv)
MQTT_BROKER = os.getenv("MQTT_BROKER")
MQTT_PORT = os.getenv("MQTT_PORT")
MQTT_CLIENT_ID = os.getenv("MQTT_CLIENT_ID")
MQTT_USERNAME = os.getenv("MQTT_USERNAME")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD")

# Global client and loop
loop = None
client_dict = {}

ADAPTER_ADDRESS = "hci1" #Your bleuetooth controller hci address

# A simple in-memory queue to store messages
message_queue = deque()


def extract_mac_address(filename):
    mac_address = filename[-17:][:12].upper()
    mac_address = ':'.join(mac_address[i:i + 2] for i in range(0, len(mac_address), 2))
    return mac_address


async def test(msg):
    try:
        log.info("test")
        return True
    except Exception as e:
        log.error(f"Error handling message: {e}")
        return False


async def write_value_to_ble(msg):
    log.info(f"Received message mid:{msg.mid}, qos:{msg.qos}, retain:{msg.retain}, "
             f"payload:'{msg.payload}', topic '{msg.topic}'")
    # Extract the device name from the topic
    parts = str(msg.topic).split('/')
    if len(parts) >= 3:
        device_name = parts[1]
        message = msg.payload.decode()

        for identifier, device_info in client_dict.items():
            if device_info['deviceName'] == device_name:
                reset_key = bytes(device_info['resetKey'])
                thermostat_address = device_info['mac']
                log.debug(f"mac={thermostat_address}, reset_key={reset_key}")
                # Connect to the device
                try:
                    async with BleakClient(thermostat_address, adapter=ADAPTER_ADDRESS) as client:
                        log.info(f"Connected to {device_name}")

                        # Pairing (read and write factory reset ID)
                        # factory_reset_id = await client.read_gatt_char(FACTORY_RESET_ID_UUID)
                        await client.write_gatt_char(FACTORY_RESET_ID_UUID, reset_key)
                        log.info(f"Paired with {device_name}:{thermostat_address}")

                        #Read json from value
                        boost = json.loads(message)
                        log.debug(f"boost {boost}")
                        boost_enabled = bool(boost["boost_enabled"])
                        boost_offset_degrees = boost["boost_offset_degrees"] * 100
                        boost_offset_percentage = 0
                        boost_time_set_point = boost["boost_time_set_point"]
                        boost_time_minutes = 0

                        publish_msg = create_boost_byte_array(boost_enabled, boost_offset_degrees,
                                                              boost_offset_percentage,
                                                              boost_time_set_point, boost_time_minutes)
                        await client.write_gatt_char(BOOST_UUID, publish_msg)
                        log.info(f"Wrote value '{publish_msg}' to device {device_name}")
                        return True
                except BleakError as e:
                    log.warning(f"Bleak error: {e} - retrying")
                    return False
                except Exception as e:
                    log.error("Critical error", exc_info=e)
                    return True


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


async def listen():
    reconnect_interval = 10  # In seconds
    while True:
        try:
            async with aiomqtt.Client(hostname=MQTT_BROKER, port=MQTT_PORT, username=MQTT_USERNAME,
                                      password=MQTT_PASSWORD, logger=log,
                                      identifier=MQTT_CLIENT_ID + "-listener", protocol=aiomqtt.ProtocolVersion.V5,
                                      keepalive=20) as client:
                # Subscribe to base topic ECO16BT/#
                await client.subscribe(BASE_TOPIC + "/+/boost/command")
                async for msg in client.messages:
                    message_queue.append(msg)
        except aiomqtt.MqttError as error:
            client._disconnected = asyncio.Future()
            log.error(f'Error "{error}". Reconnecting in {reconnect_interval} seconds.')
            await asyncio.sleep(reconnect_interval)


async def process():
    while True:
        if message_queue:
            message = message_queue.popleft()
            success = await write_value_to_ble(message)
            if not success:
                # If processing fails, put the message back in the queue for retry
                message_queue.append(message)
                print("Retrying message processing...")
                await asyncio.sleep(10)  # Sleep briefly to prevent busy-waiting
        else:
            await asyncio.sleep(2)  # Sleep briefly to prevent busy-waiting


async def main():
    # Dictionary to hold clients
    global client_dict

    directory_in_str = "./" # change where you have your pairing files
    pathlist = Path(directory_in_str).rglob('pairing-*.json')
    for path in pathlist:
        # because path is object not string
        pairing_file = str(path)
        log.info(f"Reading file={pairing_file}")
        thermostat_address = extract_mac_address(pairing_file)
        with open(path) as f:
            pairing_data = json.load(f)

        reset_key = pairing_data["resetCode"]
        device_name = pairing_data["deviceName"]

        log.info(f"reset_key:{reset_key}, device_name:{device_name}, mac:{thermostat_address}")

        client_dict[device_name] = {
            'mac': thermostat_address,
            'resetKey': reset_key,
            'deviceName': device_name
        }

    if not client_dict:
        log.error("No devices found.")
        return

    subscriber_task = asyncio.create_task(listen())
    processor_task = asyncio.create_task(process())

    await asyncio.gather(subscriber_task, processor_task)


if __name__ == "__main__":
    asyncio.run(main())
