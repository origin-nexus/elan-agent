#!/usr/bin/env python3
import subprocess
from elan.ids import generate_suricata_conf
from elan.utils import reload_service 

p = subprocess.Popen('ip monitor address', shell=True, stdout=subprocess.PIPE)

while(p.returncode is None):
    p.stdout.readline() # wait for a change...
    if generate_suricata_conf():
        reload_service('suricata')