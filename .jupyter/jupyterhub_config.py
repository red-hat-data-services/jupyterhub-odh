c.KubeSpawner.http_timeout = 60 * 10 #Images are big, take time to pull, make it 10 mins for now because of storage issue
c.KubeSpawner.start_timeout = 60 * 10 #Images are big, take time to pull, make it 10 mins for now because of storage issue

import os
import distutils
from distutils import util

#c.JupyterHub.log_level = 'DEBUG'
#c.Spawner.debug = True
# Do not shut down singleuser servers on restart
c.JupyterHub.cleanup_servers = False

custom_notebook_namespace = os.environ.get('NOTEBOOK_NAMESPACE')

import uuid
jsp_api_dict = {
    'KUBERNETES_SERVICE_HOST': os.environ['KUBERNETES_SERVICE_HOST'],
    'KUBERNETES_SERVICE_PORT': os.environ['KUBERNETES_SERVICE_PORT'],
    'JUPYTERHUB_LOGIN_URL': None,
    'NOTEBOOK_NAMESPACE': custom_notebook_namespace
}

from jupyterhub_singleuser_profiles.openshift import OpenShift

openshift = OpenShift(namespace=namespace)
culler_secret = 'jupyterhub-idle-culler'

def get_culler_secret():

    secret = openshift.read_secret(culler_secret)
    if secret == {}:
        return set_culler_secret()
    else:
        return secret['token']

def set_culler_secret():

    secret_data = str(uuid.uuid4())
    openshift.write_secret(culler_secret, {'token': secret_data})
    return secret_data

idle_culler_api_token = get_culler_secret()
c.JupyterHub.services = [
                            {
                                'name': 'jsp-api',
                                'url': 'http://jupyterhub:8181',
                                'admin': True,
                                'command': ['jupyterhub-singleuser-profiles-api'],
                                'environment': jsp_api_dict
                            },
                            {
                                'name': 'idle-culler', 
                                'api_token': idle_culler_api_token,
                                'admin': True
                            },
                        ]

if "PROMETHEUS_API_TOKEN" in os.environ:
    c.JupyterHub.services.append(dict(name='prometheus', api_token=os.environ.get("PROMETHEUS_API_TOKEN")))

DEFAULT_MOUNT_PATH = '/opt/app-root/src'

# Work out the public server address for the OpenShift REST API. Don't
# know how to get this via the REST API client so do a raw request to
# get it. Make sure request is done in a session so connection is closed
# and later calls against REST API don't attempt to reuse it. This is
# just to avoid potential for any problems with connection reuse.

# Enable the OpenShift authenticator.

from oauthenticator.openshift import OpenShiftOAuthenticator
c.JupyterHub.authenticator_class = OpenShiftOAuthenticator
c.Authenticator.auto_login = True
c.Authenticator.enable_auth_state = True
c.OpenShiftOAuthenticator.auth_refresh_age = 300
c.OpenShiftOAuthenticator.refresh_pre_spawn = True

# Override scope as oauthenticator code doesn't set it correctly.
# Need to lodge a PR against oauthenticator to have this fixed.

#OpenShiftOAuthenticator.scope = ['user:info']

# Setup authenticator configuration using details from environment.

service_name = os.environ['JUPYTERHUB_SERVICE_NAME']

service_account_name = '%s-hub' %  service_name
service_account_path = '/var/run/secrets/kubernetes.io/serviceaccount'

with open(os.path.join(service_account_path, 'namespace')) as fp:
    namespace = fp.read().strip()

client_id = 'system:serviceaccount:%s:%s' % (namespace, service_account_name)

c.OpenShiftOAuthenticator.client_id = client_id

with open(os.path.join(service_account_path, 'token')) as fp:
    client_secret = fp.read().strip()

c.OpenShiftOAuthenticator.client_secret = client_secret

def parse_groups(groups_str):
    group_set = set()
    for group in groups_str.split(','):
        group_set.add(group.strip())
    return group_set

groups_default_denied = bool(distutils.util.strtobool(os.environ.get('JUPYTERHUB_GROUPS_DEFAULT_DENIED', "false")))
allowed_groups = os.environ.get('JUPYTERHUB_ALLOWED_GROUPS', "")
admin_groups = os.environ.get('JUPYTERHUB_ADMIN_GROUPS', "")
if allowed_groups or groups_default_denied:
    c.OpenShiftOAuthenticator.allowed_groups = parse_groups(allowed_groups)
if admin_groups or groups_default_denied:
    c.OpenShiftOAuthenticator.admin_groups = parse_groups(admin_groups)

# Work out hostname for the exposed route of the JupyterHub server. This
# is tricky as we need to use the REST API to query it.

verify_ssl = False

from kubernetes import client, config
from openshift.dynamic import DynamicClient

config.load_incluster_config()

configuration = client.Configuration()
configuration.verify_ssl = verify_ssl

oapi_client = DynamicClient(
    client.ApiClient(configuration=configuration)
)

routes = oapi_client.resources.get(kind='Route', api_version='route.openshift.io/v1')

route_list = routes.get(namespace=namespace)

host = None

for route in route_list.items:
    if route.metadata.name == service_name:
        host = route.spec.host

if not host:
    raise RuntimeError('Cannot calculate external host name for JupyterHub.')

c.OpenShiftOAuthenticator.oauth_callback_url = 'https://%s/hub/oauth_callback' % host
jsp_api_dict['JUPYTERHUB_LOGIN_URL'] = 'https://%s/hub/login' % host

from html.parser import HTMLParser

class UILinkParser(HTMLParser):
    def __init__(self):
        HTMLParser.__init__(self)
        self.data = None
        self.tag = None
        self.attrs = None
        self.result = []
        self.html_tag = []

    def handle_starttag(self, tag, attrs):
        self.tag = tag
        self.attrs = attrs
        if self.tag == 'link':
            self.generate_link()
        if self.tag == 'html':
            self.html_tag.append(self.getpos()[1])
    
    def handle_endtag(self, tag):
        if tag == 'html':
            self.html_tag.append(self.getpos()[1])

    def generate_link(self):
        attr_strings = []
        for attr in self.attrs:
            attr_strings.append('{0[0]}={0[1]} '.format(attr))
        string = '<%s ' % self.tag
        for attr in attr_strings:
            string += attr
        self.result.append(string+' />')

parser = UILinkParser()
index = None
html_string = None
with open("/opt/app-root/share/jupyterhub/static/jsp-ui/index.html", "r") as f:
    html_string = f.read()
    parser.feed(html_string)
    index = html_string.find('<body>')
links = parser.result
for link in links:
    html_string = html_string[:index+6]+link+html_string[index+6:]
for tag in parser.html_tag:
    html_string = html_string[:tag] + html_string[tag:]

from jupyterhub_singleuser_profiles.profiles import SingleuserProfiles
from jupyterhub_singleuser_profiles.utils import escape

from kubespawner import KubeSpawner
class OpenShiftSpawner(KubeSpawner):
  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)
    self.single_user_services = []
    self.single_user_profiles = SingleuserProfiles(gpu_mode=os.environ.get('GPU_MODE'), notebook_namespace=custom_notebook_namespace, verify_ssl=verify_ssl)
    self.gpu_mode = self.single_user_profiles.gpu_mode
    self.gpu_count = 0
    self.deployment_size = None
    self.uid = None
    self.fs_gid = None

  def _options_form_default(self):
    response = html_string
    return response

  def options_from_form(self, formdata):
    options = {}
    cm_data = self.single_user_profiles.user.get(self.user.name)
    if custom_notebook_namespace:
        options['custom_image'] = f'image-registry.openshift-image-registry.svc:5000/{namespace}/%s' % cm_data['last_selected_image']
    else:
        options['custom_image'] = cm_data['last_selected_image']
    options['size'] = cm_data['last_selected_size']
    self.gpu_count = cm_data['gpu']
    self.image = options['custom_image']
    self.deployment_size = cm_data['last_selected_size']

    return options

  def get_env(self):
    env = super(OpenShiftSpawner, self).get_env()

    if custom_notebook_namespace:
      env['JUPYTERHUB_API_URL'] = f'http://{service_name}.{namespace}:8081/hub/api'
      env['JUPYTERHUB_ACTIVITY_URL'] = f'http://{service_name}.{namespace}:8081/hub/api/users/{self.user.name}/activity'

    return env

  async def get_url(self):
    pods = oapi_client.resources.get(kind='Pod', api_version='v1')
    pod = pods.get(name="jupyterhub-nb-%s" % escape(self.user.name), namespace=custom_notebook_namespace if custom_notebook_namespace else namespace)

    url = None
    try:
        url = "http://%s:%d/user/%s/" % (pod["status"]["podIP"], pod['spec']['containers'][0]['ports'][0]['containerPort'], self.user.name)
    except Exception as e:
        self.log.error("Failed to produce pod url")
        raise

    return url

  def get_image(self):
    return self.single_user_profiles.user.get(self.user.name)['last_selected_image']

def apply_pod_profile(spawner, pod):
  spawner.single_user_profiles.load_profiles(username=spawner.user.name)
  profile = spawner.single_user_profiles.get_merged_profile(spawner.image, user=spawner.user.name, size=spawner.deployment_size)
  gpu_types = spawner.single_user_profiles.get_gpu_types()
  return SingleuserProfiles.apply_pod_profile(spawner.user.name, pod, profile, gpu_types, DEFAULT_MOUNT_PATH, spawner.gpu_mode)

def setup_environment(spawner):
    spawner.image = spawner.get_image()
    spawner.single_user_profiles.load_profiles(username=spawner.user.name)
    spawner.single_user_profiles.setup_services(spawner, spawner.image, spawner.user.name)

def clean_environment(spawner):
    spawner.single_user_profiles.clean_services(spawner, spawner.user.name)

c.JupyterHub.spawner_class = OpenShiftSpawner

c.OpenShiftSpawner.pre_spawn_hook = setup_environment
c.OpenShiftSpawner.post_stop_hook = clean_environment
c.OpenShiftSpawner.modify_pod_hook = apply_pod_profile
c.OpenShiftSpawner.cpu_limit = float(os.environ.get("SINGLEUSER_CPU_LIMIT", "1"))
c.OpenShiftSpawner.mem_limit = os.environ.get("SINGLEUSER_MEM_LIMIT", "1G")
c.OpenShiftSpawner.storage_pvc_ensure = True

if custom_notebook_namespace:
    c.KubeSpawner.namespace = custom_notebook_namespace

c.KubeSpawner.storage_capacity = os.environ.get('SINGLEUSER_PVC_SIZE', '2Gi')
c.KubeSpawner.pvc_name_template = '%s-nb-{username}-pvc' % os.environ['JUPYTERHUB_SERVICE_NAME']
c.KubeSpawner.volumes = [dict(name='data', persistentVolumeClaim=dict(claimName=c.KubeSpawner.pvc_name_template))]
c.KubeSpawner.volume_mounts = [dict(name='data', mountPath=DEFAULT_MOUNT_PATH)]
c.KubeSpawner.user_storage_class = os.environ.get("JUPYTERHUB_STORAGE_CLASS", c.KubeSpawner.user_storage_class)
admin_users = os.environ.get('JUPYTERHUB_ADMIN_USERS')
if admin_users:
    c.Authenticator.admin_users = set(admin_users.split(','))


#Enable Traefik Proxy instead of the configurableHTTPPRoxy
from jupyterhub_traefik_proxy import TraefikTomlConfigmapProxy

c.JupyterHub.proxy_class = TraefikTomlConfigmapProxy

c.TraefikTomlConfigmapProxy.traefik_api_url = "http://traefik-proxy:8099"

# traefik api endpoint login username
c.TraefikTomlConfigmapProxy.traefik_api_username = os.environ['TRAEFIK_API_USERNAME']
# traefik api endpoint login password
c.TraefikTomlConfigmapProxy.traefik_api_password = os.environ['TRAEFIK_API_PASSWORD']

c.TraefikTomlConfigmapProxy.cm_namespace = os.environ['NAMESPACE']
c.TraefikTomlConfigmapProxy.cm_name = "traefik-rules"
c.TraefikTomlConfigmapProxy.traefik_svc_namespace = os.environ['NAMESPACE']
