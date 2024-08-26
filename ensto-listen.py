import datetime
import logging
import asyncio
import json
import os
import aiomqtt
import sdnotify

from bleak import BleakClient, BleakError
from dotenv import load_dotenv
from collections import deque
from ensto import mqtt_publish, mqtt_publish_with_client, get_client_dict, write_reset_char, \
    write_boost, create_boost_byte_array, get_json_msg, get_boost_json

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# If an environment variable is not found in the .env file, load_dotenv will then search for a variable by
# the given name in the host environment.
# loading variables from .env file
load_dotenv()

# Define the factory reset ID characteristic UUID
FACTORY_RESET_ID_UUID = "f366dddb-ebe2-43ee-83c0-472ded74c8fa"  # Example

BASE_TOPIC = os.getenv("BASE_TOPIC") or "ECO16BT"

# Define MQTT broker details, planning on using .env file (dotenv)
MQTT_BROKER = os.getenv("MQTT_BROKER")
MQTT_PORT = int(os.getenv("MQTT_PORT"))
MQTT_CLIENT_ID = os.getenv("MQTT_CLIENT_ID")
MQTT_USERNAME = os.getenv("MQTT_USERNAME")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD")

# Global client and loop
loop = None
client_dict = {}
n = sdnotify.SystemdNotifier()

ADAPTER_ADDRESS = "hci0"  #Your bleuetooth controller hci address

# A simple in-memory queue to store messages
message_queue = deque()


async def write_mqtt_message_to_ble(msg):
    log.info(f"Received message mid:{msg.mid}, qos:{msg.qos}, retain:{msg.retain}, "
             f"payload:'{msg.payload}', topic '{msg.topic}'")
    # Extract the device name from the topic
    parts = str(msg.topic).split('/')
    if len(parts) >= 2:
        device_name = parts[1]
        message = msg.payload.decode()

        # Read json from value
        command = json.loads(message)
        log.debug("command:")
        log.debug(f"{command}")
        command_type = command["type"]
        if command_type == 'boost':
            boost_enabled = bool(command["boostEnabled"])
            boost_offset_degrees = int(command["boostOffsetDegrees"] * 100)
            boost_offset_percentage = 0
            boost_time_set_point = int(command["boostSetpointMinutes"])
            boost_time_minutes = 0

            publish_msg = create_boost_byte_array(boost_enabled, boost_offset_degrees,
                                                  boost_offset_percentage,
                                                  boost_time_set_point, boost_time_minutes)

            for identifier, device_info in client_dict.items():
                if device_info['deviceName'] == device_name:
                    reset_key = bytes(device_info['resetKey'])
                    thermostat_address = device_info['mac']
                    log.debug(f"mac={thermostat_address}, reset_key={reset_key}")
                    # Connect to the device
                    try:
                        async with BleakClient(thermostat_address, adapter=ADAPTER_ADDRESS, timeout=15) as client:
                            log.info(f"Connected to {device_name}")

                            # Pairing (read and write factory reset ID)
                            # factory_reset_id = await client.read_gatt_char(FACTORY_RESET_ID_UUID)
                            await write_reset_char(client, reset_key)
                            log.info(f"Paired with {device_name}:{thermostat_address}")
                            await write_boost(client, publish_msg)
                            log.info(f"Wrote value '{list(publish_msg)}' to device {device_name}")
                            return True
                    except BleakError as e:
                        log.warning(f"Bleak error: {e} - retrying")
                        return False
                    except Exception as e:
                        log.error("Critical error", exc_info=e)
                        return True
        return True


async def publish_availability(client, interval=900):
    while True:
        for x in client_dict:
            topic = BASE_TOPIC + "/" + x + "/available"
            await mqtt_publish_with_client(client, topic, "online")
        await asyncio.sleep(interval)


async def listen():
    reconnect_interval = 10  # In seconds
    while True:
        try:
            async with aiomqtt.Client(hostname=MQTT_BROKER, port=MQTT_PORT, username=MQTT_USERNAME,
                                      password=MQTT_PASSWORD, logger=log,
                                      identifier=MQTT_CLIENT_ID + "-listener",
                                      keepalive=45) as client:
                # Subscribe to topics
                topics = [(BASE_TOPIC + "/" + x + "/set", 0) for x in client_dict]
                log.info(F"Listening to {topics}")

                await client.subscribe(topics)

                # Start the periodic availability publisher
                availability_task = asyncio.create_task(publish_availability(client))

                async for msg in client.messages:
                    message_queue.append(msg)

                n.notify("READY=1")

                await asyncio.sleep(2)  # Sleep briefly to prevent busy-waiting

        except aiomqtt.MqttError as error:
            client._disconnected = asyncio.Future()
            log.error(f'Error "{error}". Reconnecting in {reconnect_interval} seconds.')
            await asyncio.sleep(reconnect_interval)
        except Exception as ex:
            log.error(f'Unexpected error: {ex}')


async def process():
    while True:
        if message_queue:
            n.notify("STATUS=Processing message from queue")

            message = message_queue.popleft()
            success = await write_mqtt_message_to_ble(message)
            if not success:
                # If processing fails, put the message back in the queue for retry
                message_queue.append(message)
                log.info("Retrying message processing...")
                await asyncio.sleep(10)  # Sleep briefly to prevent busy-waiting
            else:
                # send answer state
                topic = message.topic.value[:message.topic.value.rfind('/')]
                device_name = topic.split('/')[1]

                # Connect to device and get updated
                thermostat_address = client_dict[device_name]['mac']
                reset_key = client_dict[device_name]['resetKey']
                async with BleakClient(thermostat_address, adapter=ADAPTER_ADDRESS, timeout=30) as client:
                    log.debug("Connected to the device")

                    if client._backend.__class__.__name__ == "BleakClientBlueZDBus":
                        await client._backend._acquire_mtu()

                    log.debug(f"mtu: {client.mtu_size}")

                    # Pairing (read and write factory reset ID)
                    await write_reset_char(client, reset_key)
                    log.debug("Paired with the device")

                    json_msg = await get_boost_json(client)

                    topic = BASE_TOPIC + "/" + device_name + "/boost"
                    await mqtt_publish(topic, json_msg)

        else:
            await asyncio.sleep(2)  # Sleep briefly to prevent busy-waiting


async def main():
    # Dictionary to hold clients
    global client_dict

    client_dict = await get_client_dict()

    if not client_dict:
        log.error("No devices found.")
        return

    subscriber_task = asyncio.create_task(listen())
    processor_task = asyncio.create_task(process())

    await asyncio.gather(subscriber_task, processor_task)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("Shutting down gracefully...")
