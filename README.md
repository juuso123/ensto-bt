Python code to read and write characteristics to Ensto ECO16BT used with home assistant.

pairing-xxxxxxxxxxxx.json file contains resetCode, which is obtained from thermostat when initiating
pairing (by pressing button) and device name you want to give it. Device name is part of mqtt topic. *Add howto here*
Content:
{"resetCode":[0,0,0,0],"deviceName": "device1"}


Insired by:
https://github.com/tuomassalo/ensto-bt-thermostat-reader/
https://github.com/jumakki/esphome-ensto
