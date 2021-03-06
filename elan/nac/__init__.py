import subprocess, datetime, re
import threading

from elan import session
from elan.neuron import Synapse, Dendrite

AUTHORIZATION_CHANGE_TOPIC = 'nac/authz/change'  # notify that authz changed for mac

AUTHZ_MAC_EXPIRY_PATH = 'nac:authz:expiry'  # sorted set with expiry as score, mac will be used as key of session (we only keep current sessions)
AUTHZ_SESSIONS_BY_MAC_PATH = 'nac:authz:sessions'  # hash
AUTHZ_SESSIONS_SEQUENCE_PATH = 'nac:authz:sequence'

ACCESS_CONTROLLED_IFS_PATH = 'nac:access-control:ifs'

CHECK_AUTHZ_PATH = 'check-macs-authz'

AUTHORIZATION_SESSION_TOPIC = 'session/authorization'

dendrite = Dendrite()
synapse = Synapse()

# Redis authorizations objects are set straight away, but opening of fw is async. (done by mac authz daemon)
# mac authz daemon is also responsible for de authorizing mac on expiry


def newAuthz(mac, source, no_duplicate_source=False, **auth):
    '''gets new Authorization by adding new authentication. if no_duplicate_source is true, it will first remove any auth from that source first
    Mainly a shortcut for session.add_authentication and nac.checkAuthz
    '''

    # TODO: in redis session -> use a key to lock ?
    if no_duplicate_source:
        session.remove_authentication_sessions_by_source(mac, source)

    session.add_authentication_session(mac, source=source, **auth)

    return checkAuthz(mac)


def checkAuthz(mac, remove_source=None, end_reason='overridden', **kwargs):
    '''
    Asks what Authorization should be granted to the mac, based on current authentications
    First, an authentication source can be removed using remove_source
    '''
    if remove_source is not None:
        session.remove_authentication_sessions_by_source(mac, remove_source)

    assignments = get_network_assignments(mac)

    old_authz = RedisMacAuthorization.getByMac(mac)

    if assignments:
        authz = RedisMacAuthorization(mac=mac, **assignments)

        if authz != old_authz:
            if old_authz:
                RedisMacAuthorization.deleteByMac(mac)
                notify_end_authorization_session(old_authz, reason=end_reason, **kwargs)
            authz.save()
            authzChanged(mac)
            notify_new_authorization_session(authz)

        return authz
    else:
        if old_authz:
            RedisMacAuthorization.deleteByMac(mac)
            authzChanged(mac)
            notify_end_authorization_session(old_authz, reason='expired')
        return None


def getAuthz(mac):
    ''' Returns current authz, and if none, checks if there should be one... '''
    authz = RedisMacAuthorization.getByMac(mac)
    if authz:
        return authz
    return checkAuthz(mac)


def vlan_has_access_control(vlan):
    if '.' in vlan:
        interface, vlan_id = vlan.rsplit('.', 1)
        vlan_id = int(vlan_id)
    else:
        interface = vlan
        vlan_id = 0

    for v in dendrite.get_conf('vlans') or []:
        if v['interface'] == interface and v['vlan_id'] == vlan_id:
            return v['access_control']


def authzChanged(mac):
    '''
    notify Mac Authz Manager that authz for mac has changed. Should not need to call this directly
    '''
    dendrite.publish(AUTHORIZATION_CHANGE_TOPIC, mac)


def get_network_assignments(mac, port=None, current_auth_sessions=None):
    if current_auth_sessions is None:
        current_auth_sessions = session.get_authentication_sessions(mac)
    if port is None:
        port = synapse.hget(session.MAC_PORT_PATH, mac)

    return dendrite.call('device-authorization', {'auth_sessions': current_auth_sessions, 'mac': mac, 'port': port})
    # TODO: when CC unreachable or Error, retry in a few seconds (maybe use mac authz manager daemon for that)


def tzaware_datetime_to_epoch(dt):
    return (dt - datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)).total_seconds()


def notify_new_authorization_session(authz, start=None):
    '''
        start and end are Epoch
    '''
    data = authz.serialize()

    if data.get('till', None):  # format date
        data['till'] = session.format_date(data['till'])

    dendrite.publish(AUTHORIZATION_SESSION_TOPIC, dict(start=session.format_date(start), **data))


def notify_end_authorization_session(authz, reason, end=None, **kwargs):
    ''' start is Epoch '''

    kwargs['termination_reason'] = reason
    kwargs['end'] = session.format_date(end)
    kwargs['mac'] = authz.mac
    kwargs['local_id'] = authz.local_id

    dendrite.publish(AUTHORIZATION_SESSION_TOPIC, kwargs)


class RedisMacAuthorization(object):

    def __init__(self, mac, assign_vlan, allow_on, bridge_to, till_disconnect, till=None, local_id=None, **kwargs):
        self.local_id = local_id  # local_id is mainly used for sync with CC. we find current sessions by mac
        self.mac = mac
        self.assign_vlan = assign_vlan
        self.allow_on = set(allow_on)
        self.bridge_to = set(bridge_to)
        self.till_disconnect = till_disconnect
        self.till = till

        for key in kwargs:
            setattr(self, key, kwargs[key])

    def __eq__(self, other):
        ''' Equality if all params match excluding mac and local_id. mac because we may want to compare authz of 2 macs'''
        if not isinstance(other, RedisMacAuthorization) or self.__dict__.keys() != other.__dict__.keys():
            return False

        for key in set(self.__dict__.keys()) - {'mac', 'local_id'}:
            if getattr(self, key) != getattr(other, key):
                return False

        return True

    def __ne__(self, other):
        return not self.__eq__(other)

    def save(self):
        if self.local_id is None:
            self.local_id = synapse.get_unique_id(AUTHZ_SESSIONS_SEQUENCE_PATH)

        if self.till is None:
            till = float('+inf')
        else:
            till = self.till

        pipe = synapse.pipeline()
        pipe.zadd(AUTHZ_MAC_EXPIRY_PATH, till, self.mac)

        pipe.hset(AUTHZ_SESSIONS_BY_MAC_PATH, self.mac, self.serialize())
        pipe.execute()

    def serialize(self):
        data = self.__dict__.copy()
        # sets are not serialializable, serialize them as lists
        data['allow_on'] = list(data['allow_on'])
        data['bridge_to'] = list(data['bridge_to'])

        return data

    @classmethod
    def deleteByMac(cls, mac):
        pipe = synapse.pipeline()
        pipe.hget(AUTHZ_SESSIONS_BY_MAC_PATH, mac)
        pipe.zrem(AUTHZ_MAC_EXPIRY_PATH, mac)
        pipe.hdel(AUTHZ_SESSIONS_BY_MAC_PATH, mac)
        result = pipe.execute()
        authz_session = result[0]

        if authz_session is not None:
            return cls(**authz_session)

    @classmethod
    def getByMac(cls, mac):
        auth_session = synapse.hget(AUTHZ_SESSIONS_BY_MAC_PATH, mac)

        if auth_session is not None:
            return cls(**auth_session)

