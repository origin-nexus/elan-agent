import datetime
import subprocess, threading

from elan.neuron import Synapse, Dendrite

from . import AUTHZ_MAC_EXPIRY_PATH
from . import RedisMacAuthorization, notify_end_authorization_session, checkAuthz, tzaware_datetime_to_epoch
from .. import session


class MacAuthorizationManager():
    ''' Class to manage FW authz of Macs
        It also provides a service to check Authz of Macs when something has changed (Tags, ...)
    '''

    def __init__(self, dendrite=None):
        if dendrite is None:
            self.dendrite = Dendrite()

        self.fw_mac_allowed_vlans = {}
        self.fw_mac_bridged_vlans = {}

        self.dendrite = dendrite
        self.synapse = Synapse()

        self.next_check = None
        self.check_authz_sema = threading.BoundedSemaphore()

        self.check_expired_authz()

        self.init_macs()

    def init_macs(self):
        # on startup, initialize sets
        # TODO: this should get vlans from network conf to flush nft sets and it should use fw_allow mac for each. Even if it is not in a transaction and not very efficient, it is OK as this should not restart often (TODO when flush sets works...)
        for mac in self.synapse.zmembers(AUTHZ_MAC_EXPIRY_PATH):
            authz = RedisMacAuthorization.getByMac(mac)
            if authz:
                self.fw_allow_mac(mac, on=authz.allow_on, to=authz.bridge_to)

    def removeAuthz(self, mac, reason, authz=None):
        if authz is None:
            authz = RedisMacAuthorization.getByMac(mac)
        if authz:
            RedisMacAuthorization.deleteByMac(mac)
            notify_end_authorization_session(authz, reason=reason)
        self.fw_disallow_mac(mac)

        return authz

    def fw_allowed_vlans(self, mac):
        return self.fw_mac_allowed_vlans.get(mac, set())

    def _fw_cache_allow_on_del(self, mac, vlan):
        vlans = self.fw_mac_allowed_vlans.get(mac, None)
        if vlans:
            vlans.remove(vlan)
            if not vlans:
                del self.fw_mac_allowed_vlans[mac]

    def _fw_cache_allow_on_add(self, mac, vlan):
        vlans = self.fw_mac_allowed_vlans.get(mac, None)
        if vlans:
            vlans.add(vlan)
        else:
            self.fw_mac_allowed_vlans[mac] = {vlan}

    def fw_bridged_vlans(self, mac):
        return self.fw_mac_bridged_vlans.get(mac, set())

    def _fw_cache_bridge_to_del(self, mac, vlan):
        vlans = self.fw_mac_bridged_vlans.get(mac, None)
        if vlans:
            vlans.remove(vlan)
            if not vlans:
                del self.fw_mac_bridged_vlans[mac]

    def _fw_cache_bridge_to_add(self, mac, vlan):
        vlans = self.fw_mac_bridged_vlans.get(mac, None)
        if vlans:
            vlans.add(vlan)
        else:
            self.fw_mac_bridged_vlans[mac] = {vlan}

    def fw_allow_mac(self, mac, on=None, to=None):
        "Opens access on the vlan ids specified an closes all the others, if any"
        if on is None:
            on = set()
        if to is None:
            to = set()

        # TODO: use nft from pyroute2 when ready
        with subprocess.Popen(['nft', '-i'], stdin=subprocess.PIPE, universal_newlines=True, stdout=subprocess.DEVNULL) as nft_process:

            def nft(cmd):
                print(cmd, file=nft_process.stdin, flush=True)

                if cmd == 'quit':
                    try:
                        nft_process.wait(2)
                    except subprocess.TimeoutExpired:
                        pass

            for vlan in self.fw_allowed_vlans(mac) - set(on):
                self._fw_cache_allow_on_del(mac, vlan)
                nft('delete element bridge elan mac_on_vlan {{ {mac} . {vlan} }}'.format(vlan=vlan, mac=mac))
            for vlan in set(on) - self.fw_allowed_vlans(mac):
                self._fw_cache_allow_on_add(mac, vlan)
                nft('add element bridge elan mac_on_vlan {{ {mac} . {vlan} }}'.format(vlan=vlan, mac=mac))

            for vlan in self.fw_bridged_vlans(mac) - set(to):
                self._fw_cache_bridge_to_del(mac, vlan)
                nft('delete element bridge elan mac_to_vlan {{ {mac} . {vlan} }}'.format(vlan=vlan, mac=mac))
            for vlan in set(to) - self.fw_bridged_vlans(mac):
                self._fw_cache_bridge_to_add(mac, vlan)
                nft('add element bridge elan mac_to_vlan {{ {mac} . {vlan} }}'.format(vlan=vlan, mac=mac))

            nft('quit')

    def fw_disallow_mac(self, mac):
        '''
        Disallows MAC
        '''
        self.fw_allow_mac(mac)  # on no vlans
        # TODO: Flush connections with conntrack (get IPs of MAC and conntrack -D -s <IP>)

    def authzChanged(self, mac):
        authz = RedisMacAuthorization.getByMac(mac)
        if authz:
            self.fw_allow_mac(mac, on=authz.allow_on, to=authz.bridge_to)
        else:
            self.fw_disallow_mac(mac)

    def handle_authz_changed(self, mac):
        self.authzChanged(mac)
        # Check if authz have expired and set correct timeout
        self.check_expired_authz()

    def handle_disconnection(self, mac):
        authz = RedisMacAuthorization.getByMac(mac)
        if authz and authz.till_disconnect:
            self.removeAuthz(mac, reason='disconnected', authz=authz)
            self.authzChanged(mac)

        # Check if authz have expired and set correct timeout
        self.check_expired_authz()

    def check_expired_authz(self):
        if self.next_check:
            self.next_check.cancel()

        with self.check_authz_sema:
            now = tzaware_datetime_to_epoch(datetime.datetime.now(datetime.timezone.utc))
            for mac in self.synapse.zrangebyscore(AUTHZ_MAC_EXPIRY_PATH, float('-inf'), now):
                self.removeAuthz(mac, reason='expired')
                self.check_authz([mac])

            self.schedule_next_expiry_check()

    def schedule_next_expiry_check(self):
        # get next mac to expire
        next_expiry_date = float('inf')
        for _mac, epoch_expire in self.synapse.zrange(AUTHZ_MAC_EXPIRY_PATH, 0, 0, withscores=True):  # returns first mac to expire: will iterate at most once
            if next_expiry_date > epoch_expire:
                next_expiry_date = epoch_expire

        # set timeout for next check
        if next_expiry_date != float('inf'):
            now = tzaware_datetime_to_epoch(datetime.datetime.now(datetime.timezone.utc))
            self.next_check = threading.Timer(next_expiry_date - now, self.check_expired_authz)
            self.next_check.start()

    def check_authz(self, macs):
            for mac in macs:
                if session.is_online(mac):
                    thread = threading.Thread(target=checkAuthz, args=(mac,))
                    thread.run()

