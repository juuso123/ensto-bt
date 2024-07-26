# ensto-bt
Python code to read and write characteristics to Ensto ECO16BT used with home assistant.

## Usage
pairing-xxxxxxxxxxxx.json file contains resetCode, which is obtained from thermostat when initiating
pairing (by pressing button) and device name you want to give it. The xxxxxxxxxxxx is the mac address without ":" characters.

*Add howto get pairing code here*

Content:
{"resetCode":[0,0,0,0],"deviceName": "device1"}

Device name is part of mqtt topic. 

## Insired by:
https://github.com/tuomassalo/ensto-bt-thermostat-reader/ and 
https://github.com/jumakki/esphome-ensto
