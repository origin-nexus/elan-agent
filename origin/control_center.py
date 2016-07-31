from origin.neuron import Dendrite, Synapse, TimeoutException
from origin import utils
from mako.template import Template
from uuid import uuid4

CC_IPv4 = ['87.98.150.15'] # Control center IPs to be used in NGINX conf: indeed, when no resolver available, NGINX fails if we use fqdn
CC_IPv6 = ['2001:41d0:2:ba47::1:10']

ACCOUNT_ID_PATH = 'account:id'
AGENT_ID_PATH = 'agent:id'
AGENT_UUID_PATH = 'agent:uuid'
AGENT_LOCATION_PATH = 'agent:location'

synapse = Synapse()

class AxonMapper:
    def __init__(self):
        self.dendrite = Dendrite()
        
    def once_registered(self, agent):
        self.agent_id = agent.id
        
        self.dendrite.subscribe("control-center/conf/authentication", self.parse_authentications)
        
    def run(self):
        self.dendrite.subscribe_conf("agent", self.once_registered)
        self.dendrite.provide('register', self.register)
        self.dendrite.provide('registration-available', self.registration_available)
        self.dendrite.provide('check-connectivity', self.check_connectivity)
        
        self.dendrite.wait_complete()

    def parse_authentications(self, conf):
        agent_provided_authentications = []
        for auth in conf:
            if 'agents' in auth and self.agent_id in auth['agents']:
                agent_provided_authentications.append(auth)
        
        self.dendrite.publish_conf("authentication", agent_provided_authentications)
    
    def check_connectivity(self, data):
        # check control-center connectivity
        try:
            connected = bool(int( self.dendrite.get('$SYS/broker/connection/{uuid}/state'.format(uuid=synapse.get(AGENT_UUID_PATH) ))))
        except TimeoutException:
            connected = False
        
        if not connected:
            return {'status': 'disconnected', 'error': 'Connection to Control Center down'}

    def register(self, data):
        # check control-center connectivity
        connectivity = self.check_connectivity(data)
        
        if 'error' in connectivity:
            return connectivity
        
        if not data:
            return {'status': 'available'}
        
        result = self.dendrite.call('control-center/register', data)
        
        synapse.set(ACCOUNT_ID_PATH, result['account'])
        synapse.set(AGENT_ID_PATH, result['id'])
        synapse.set(AGENT_UUID_PATH, result['uuid'])
        
        configure_axon()
        
        if 'error' in result: 
            return result
        return {'status': 'registered'}


def configure_axon(reload=True):
    try:
        uuid = synapse.get(AGENT_UUID_PATH)
        agent_id = synapse.get(AGENT_ID_PATH)
        account_id = synapse.get(ACCOUNT_ID_PATH)
    except:
        agent_id = None
        account_id = None
        uuid = uuid4()
        try:
            synapse.set(AGENT_UUID_PATH, uuid)
        except:
            pass 
    
    
    axon_template = Template(filename="/origin/control_center/nginx/axon")
    with open ("/etc/nginx/sites-available/axon", "w") as axon_file:
        axon_file.write( axon_template.render(
                                  uuid       = uuid,
                                  cc_ipv4    = CC_IPv4,
                                  cc_ipv6    = CC_IPv6,
                       ) )

    # Reload Nginx
    if reload:
        utils.reload_service('nginx')
    
    mosquitto_template = Template(filename="/origin/control_center/mosquitto/axon")
    with open ("/etc/mosquitto/conf.d/axon.conf", "w") as mosquitto_file:
        mosquitto_file.write( mosquitto_template.render(
                                  uuid       = uuid,
                                  agent_id   = agent_id,
                                  account_id = account_id
                            ) )

    # Reload Nginx
    if reload:
        utils.restart_service('mosquitto')

    