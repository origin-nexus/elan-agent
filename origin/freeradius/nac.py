#! /usr/bin/env python3

import radiusd
from origin import neuron, snmp, session, nac
import re

# TODO: maybe put this in instanciate ?
dendrite = neuron.Dendrite('freeradius')
synapse = dendrite.synapse
snmp_manager = snmp.DeviceSnmpManager() 

def extract_mac(string):
    # extract mac from beginning of string
    if not string:
        return
    
    m = re.match('([a-f0-9]{2})[.:-]?([a-f0-9]{2})[.:-]?([a-f0-9]{2})[.:-]?([a-f0-9]{2})[.:-]?([a-f0-9]{2})[.:-]?([a-f0-9]{2})', string, flags=re.IGNORECASE)
    if m:
        return ':'.join(m.groups()).lower()

def seen(request):
    request_hash = request_as_hash_of_values(request)

    mac = extract_mac(request_hash.get('Calling-Station-Id', None))

    port = find_port(request_hash)
    if port:# don't bother if we do not have port info
        session.seen(mac, port=port)

def find_port(request_hash):
    nas_ip_address = request_hash.get('NAS-IP-Address', None)
    radius_client_ip = request_hash.get('Packet-Src-IP-Address', request_hash.get('Packet-Src-IPv6-Address', None))

    switch_polled = False
    switch = None
    # Retrieve switch info
    if nas_ip_address:
        switch = snmp_manager.get_device_by_ip(nas_ip_address)
        if not switch:
            switch = snmp_manager.poll(nas_ip_address)
    if switch:
        switch_ip = nas_ip_address
        switch_polled = True
    else:
        # If not found with NAS-IP-Address, try with Radius client IP
        switch = snmp_manager.get_device_by_ip(radius_client_ip)
        if not switch:
            switch = snmp_manager.poll(radius_client_ip)
        if switch:
            switch_ip = radius_client_ip
            switch_polled = True
        else:
            # if switch not found, nothing we can do
            return
    
    called_station_id = extract_mac(request_hash.get('Called-Station-Id', None))
    found_ports_by_mac = set()
    if called_station_id:
        for port in switch[u'ports']:
            if port[u'mac'] == called_station_id:
                found_ports_by_mac.add(port[u'interface'])
        if len(found_ports_by_mac) == 1:
            port_interface = list(found_ports_by_mac)[0]
            return { 'local_id': str(switch[u'local_id']), 'interface': str(port_interface) } 

    # Try to find SSID
    ssid = None
    for k,v in request_hash:
        if '-avpair' in k.lower():
            if not isinstance(v, list):
                v=[v]
            for val in v:
                if val.lower().startswith('ssid='):
                    ssid = val.partition('=')[2]
                    break
            if ssid:
                break

    found_ports_by_ssid = set()
    if ssid:
        for port in switch[u'ports']:
            for ssid_obj in port.get(u'ssids', []):
                if ssid_obj[u'ssid'] == ssid:
                    found_ports_by_ssid.add(port[u'interface'])
                    break
        if len(found_ports_by_ssid) == 1:
            port_interface = list(found_ports_by_ssid)[0]
            return { 'local_id': str(switch[u'local_id']), 'interface': str(port_interface) } 
        


    # try to find by nas port id
    nas_port_id = request_hash.get('NAS-Port-ID', None)
    found_ports_by_nas_port_id = set()
    if nas_port_id:
        for port in switch[u'ports']:
            if nas_port_id in (port[u'interface'], port[u'name'], port[u'description']):
                found_ports_by_nas_port_id.add(port[u'interface'])
        if len(found_ports_by_nas_port_id) == 1:
            port_interface = list(found_ports_by_nas_port_id)[0]
            return { 'local_id': str(switch[u'local_id']), 'interface': str(port_interface) } 
    
    
    # If still, try nasport to ifindex
    nas_port = request_hash.get('NAS-Port', None)
    found_ports_by_nas_port = set()
    if nas_port:
        ifIndexes = snmp_manager.nasPort2IfIndexes(switch_ip, nas_port)
        force_poll = not switch_polled # force poll if not already polled
        for if_index in ifIndexes:
            port = snmp_manager.getPortFromIndex(switch_ip, if_index, force_poll=force_poll, no_poll=(not force_poll))
            force_poll = False # Polled once, no need to poll any more
            if port:
                found_ports_by_nas_port.add(port['interface'])
        if len(found_ports_by_nas_port) == 1:
            port_interface = list(found_ports_by_nas_port)[0]
            return { 'local_id': str(switch[u'local_id']), 'interface': str(port_interface) } 
    
    # try to find a common one between the 3
    intersection = found_ports_by_mac | found_ports_by_ssid | found_ports_by_nas_port_id | found_ports_by_nas_port
    if found_ports_by_mac:
        intersection &= found_ports_by_mac
    if found_ports_by_ssid:
        intersection &= found_ports_by_ssid
    if found_ports_by_nas_port_id:
        intersection &= found_ports_by_nas_port_id
    if found_ports_by_nas_port:
        intersection &= found_ports_by_nas_port
    if len(intersection) == 1:
        port_interface = intersection[0]
        return { 'local_id': str(switch[u'local_id']), 'interface': str(port_interface) }
    
    # port not found...
    # TODO: alert with logs to ON...
    return

def request_as_hash_of_values(request):
    class MultiDict(dict):
        'Dictionary that returns only last value when get is used and value is a list'
        def get(self, *args, **kwargs):
            v = super(MultiDict, self).get(*args, **kwargs)
            if isinstance(v, list):
                return v[-1]
            return v
            
    ret = MultiDict()
    
    for key, value in request:
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        if key in ret:
            if isinstance(ret[key], list):
                ret[key].append(value)
            else:
                ret[key] = [ ret[key], value ]
        else:
            ret[key] = value

    return ret


def get_assignments(request):
    ''' Will create new session for Mac and allow it on VLAN and on the net if authorized'''
    request_hash = request_as_hash_of_values(request)
        
    mac = extract_mac(request_hash.get('Calling-Station-Id', None))

    port = find_port(request_hash)
    if port:
        session.seen(mac, port=port)

    #TODO: Send an alert top CC ON when vlan is None and we get here: should not happen
    auth_type = request_hash.get('Origin-Auth-Type', None)
    
    extra_kwargs = {}
    if auth_type == 'dot1x':
        extra_kwargs = dict(
                authentication_provider = request_hash.get('Origin-Auth-Provider'),
                login = request_hash.get('Origin-Login', request_hash.get('User-Name'))
        )
    
    session.remove_till_disconnect_authentication_session(mac)
    session.add_authentication_session(mac, source=auth_type, till_disconnect=True, **extra_kwargs)

    assignments = session.get_network_assignments(mac, port)
    
    if not assignments:
        # TODO: log no assignment rule matched....
        return radiusd.RLM_MODULE_REJECT
    
    if assignments['bridge']:
        nac.allowMAC(mac, assignments['vlan'], till_disconnect=True)
    session.notify_new_authorization_session(mac, assignments['vlan'], type=auth_type, till_disconnect=True, authorized=assignments['bridge'], **extra_kwargs)
    return radiusd.RLM_MODULE_UPDATED, ( ('Origin-Vlan-Id', str(assignments['vlan'])), ), ()
