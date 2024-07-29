# ensto-bt
Python code to read and write characteristics to Ensto ECO16BT used with home assistant.

## Usage
pairing-xxxxxxxxxxxx.json file contains resetCode, which is obtained from thermostat when initiating
pairing (by pressing button) and running pair.py. The xxxxxxxxxxxx in the filename is the mac address 
without ":" characters. Pairing file is saved by default to same directory where script is. 

Content of file:
{"resetCode":[0,0,0,0],"deviceName": "device1"}

Device name is part of mqtt topic and is converted to ascii and spaces replaced with empty string.
For example a device with name "Kylpylä spa" will be renamed to "kylpylaspa" to be used as part of topic.

## Insired by:
https://github.com/tuomassalo/ensto-bt-thermostat-reader/ and 
https://github.com/jumakki/esphome-ensto
