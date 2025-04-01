import json
import logging
import os
import shutil
import gc

from keboola.component.base import ComponentBase
from keboola.component.exceptions import UserException
from keboola.csvwriter import ElasticDictWriter

from client.os_client import OpenSearchClient
from legacy_client.legacy_es_client import LegacyClient
from client.ssh_utils import SomeSSHException, get_private_key
from sshtunnel import SSHTunnelForwarder, BaseSSHTunnelForwarderError

# Configuration keys
KEY_GROUP_DB = 'db'
KEY_DB_HOSTNAME = 'hostname'
KEY_DB_PORT = 'port'
KEY_QUERY = 'request_body'
KEY_INDEX_NAME = 'index_name'
KEY_STORAGE_TABLE = 'storage_table'
KEY_PRIMARY_KEYS = 'primary_keys'
KEY_INCREMENTAL = 'incremental'
KEY_GROUP_AUTH = 'authentication'
KEY_AUTH_TYPE = 'auth_type'
KEY_USERNAME = 'username'
KEY_PASSWORD = '#password'
KEY_API_KEY_ID = 'api_key_id'
KEY_API_KEY = '#api_key'
KEY_BEARER = '#bearer'
KEY_SCHEME = 'scheme'
KEY_TIME_WINDOW = 'time_window_minutes'
KEY_SIZE = 'size'

KEY_GROUP_DATE = 'date'
KEY_DATE_APPEND = 'append_date'
KEY_DATE_FORMAT = 'format'
KEY_DATE_SHIFT = 'shift'
KEY_DATE_TZ = 'time_zone'
DATE_PLACEHOLDER = '{{date}}'
DEFAULT_DATE = '5 minutes'
DEFAULT_DATE_FORMAT = '%Y-%m-%d'
DEFAULT_TZ = 'UTC'

KEY_SSH = "ssh_options"
KEY_USE_SSH = "enabled"
KEY_SSH_KEYS = "keys"
KEY_SSH_PRIVATE_KEY = "#private"
KEY_SSH_USERNAME = "user"
KEY_SSH_TUNNEL_HOST = "sshHost"
KEY_SSH_TUNNEL_PORT = "sshPort"

LOCAL_BIND_ADDRESS = "127.0.0.1"
RSA_HEADER = "-----BEGIN RSA PRIVATE KEY-----"

REQUIRED_PARAMETERS = [KEY_GROUP_DB]


class Component(ComponentBase):
    def __init__(self):
        super().__init__()
        self.ssh_tunnel = None

    def build_query(self, params):
        if KEY_QUERY in params and params[KEY_QUERY]:
            logging.info("Custom query provided in config, using it directly.")
            query = params[KEY_QUERY]
            query.setdefault("size", params.get(KEY_SIZE, 1000))
            return query

        minutes = params.get(KEY_TIME_WINDOW, 5)
        size = params.get(KEY_SIZE, 1000)
        logging.info(f"Generating query for the last {minutes} minutes (now-{minutes}m to now) with size={size}")
        return {
            "size": size,
            "query": {
                "range": {
                    "@timestamp": {
                        "gte": f"now-{minutes}m",
                        "lte": "now"
                    }
                }
            }
        }

    def get_client(self, params: dict) -> OpenSearchClient:
        auth_params = params.get(KEY_GROUP_AUTH)
        if not auth_params:
            return self.get_client_legacy(params)

        db_params = params.get(KEY_GROUP_DB)
        db_hostname = db_params.get(KEY_DB_HOSTNAME)
        db_port = db_params.get(KEY_DB_PORT)
        scheme = params.get(KEY_SCHEME, "http")

        if self.ssh_tunnel is not None and self.ssh_tunnel.is_active:
            local_host, local_port = self.ssh_tunnel.local_bind_address
            logging.info(f"SSH Tunnel is active. Using local_bind_address: {local_host}:{local_port}")
            db_hostname, db_port = local_host, local_port
        else:
            logging.info(f"SSH Tunnel is inactive or not configured, "
                         f"using direct connection to {db_hostname}:{db_port}")

        auth_type = auth_params.get(KEY_AUTH_TYPE, False)
        if auth_type not in ["basic", "api_key", "bearer", "no_auth"]:
            raise UserException(f"Invalid auth_type: {auth_type}")

        setup = {"host": db_hostname, "port": db_port, "scheme": scheme}

        logging.info(f"The component will use {auth_type} type authorization.")

        if auth_type == "basic":
            username = auth_params.get(KEY_USERNAME)
            password = auth_params.get(KEY_PASSWORD)
            if not (username and password):
                raise UserException("You must specify both username and password for basic type authorization")
            auth = (username, password)
            client = OpenSearchClient([setup], scheme, http_auth=auth)

        elif auth_type == "api_key":
            api_key_id = auth_params.get(KEY_API_KEY_ID)
            api_key = auth_params.get(KEY_API_KEY)
            api_key = (api_key_id, api_key)
            client = OpenSearchClient([setup], scheme, api_key=api_key)

        elif auth_type == "no_auth":
            client = OpenSearchClient([setup], scheme)

        else:
            raise UserException(f"Unsupported auth_type: {auth_type}")

        try:
            if not client.ping(error_trace=True):
                raise UserException(f"Connection to OpenSearch instance {db_hostname}:{db_port} failed.")
        except Exception as e:
            raise UserException(f"Connection to OpenSearch instance {db_hostname}:{db_port} failed. {str(e)}")

        return client

    def run(self):
        self.validate_configuration_parameters(REQUIRED_PARAMETERS)
        params = self.configuration.parameters
        scroll_size = params.get("scroll_size", 1000)

        out_table_name = params.get(KEY_STORAGE_TABLE, "").strip() or "ex-opensearch-result"
        logging.info(f"Using output table name: {out_table_name}")

        user_defined_pk = params.get(KEY_PRIMARY_KEYS, [])
        incremental = params.get(KEY_INCREMENTAL, False)

        index_name = params.get(KEY_INDEX_NAME)
        query = self.build_query(params)
        logging.info(f"Extracting data from index: {index_name}")

        range_query = query.get('query', {}).get('range', {})
        if range_query:
            for field, condition in range_query.items():
                gte = condition.get('gte', 'not specified')
                lte = condition.get('lte', 'not specified')
                logging.info(f"Time range on field '{field}': from {gte} to {lte}")

        logging.info(f"Full query: {json.dumps(query)}")

        statefile = self.get_state_file()

        ssh_options = params.get(KEY_SSH)
        if ssh_options and ssh_options.get(KEY_USE_SSH, False):
            self._create_and_start_ssh_tunnel(ssh_options, params)

        if self.ssh_tunnel is not None and self.ssh_tunnel.is_active:
            logging.info(f"SSH Tunnel is active. Local bind address: {self.ssh_tunnel.local_bind_address}")
        else:
            logging.info("SSH Tunnel is not active or not configured, using direct connection.")

        client = self.get_client(params)

        temp_folder = os.path.join(self.data_folder_path, "temp")
        os.makedirs(temp_folder, exist_ok=True)

        columns = statefile.get(out_table_name, [])
        out_table = self.create_out_table_definition(out_table_name, primary_key=user_defined_pk,
                                                     incremental=incremental)

        doc_count = 0
        try:
            with ElasticDictWriter(out_table.full_path, columns) as wr:
                for result in client.extract_data(index_name, query, scroll_size):
                    wr.writerow(result)
                    doc_count += 1
                    if doc_count % 1000 == 0:
                        logging.info(f"Downloaded {doc_count} documents so far...")
                        gc.collect()
                wr.writeheader()
        except Exception as e:
            raise UserException(f"Error occurred while extracting data from OpenSearch: {e}")
        finally:
            if self.ssh_tunnel is not None and self.ssh_tunnel.is_active:
                logging.info("Stopping SSH Tunnel.")
                self.ssh_tunnel.stop()

        logging.info(f"Total downloaded documents: {doc_count}")

        self.write_manifest(out_table)
        statefile[out_table_name] = wr.fieldnames
        self.write_state_file(statefile)
        self.cleanup(temp_folder)

    @staticmethod
    def is_valid_rsa(rsa_key):
        if not rsa_key.startswith(RSA_HEADER):
            return False, "RSA key does not start with the correct header."
        if "\n" not in rsa_key:
            return False, "RSA key does not contain newline characters."
        return True, ""

    def _create_and_start_ssh_tunnel(self, ssh_options, params):
        ssh_username = ssh_options.get(KEY_SSH_USERNAME)
        private_key = ssh_options.get(KEY_SSH_KEYS, {}).get(KEY_SSH_PRIVATE_KEY)
        ssh_tunnel_host = ssh_options.get(KEY_SSH_TUNNEL_HOST)
        ssh_tunnel_port = ssh_options.get(KEY_SSH_TUNNEL_PORT, 22)

        db_params = params.get(KEY_GROUP_DB)
        db_hostname = db_params.get(KEY_DB_HOSTNAME)
        db_port = int(db_params.get(KEY_DB_PORT))

        if not private_key.startswith(RSA_HEADER) or "\n" not in private_key:
            raise UserException("Invalid RSA private key.")

        try:
            private_key = get_private_key(private_key, None)
        except SomeSSHException as e:
            raise UserException(e) from e

        self.ssh_tunnel = SSHTunnelForwarder(
            ssh_address_or_host=(ssh_tunnel_host, ssh_tunnel_port),
            ssh_username=ssh_username,
            ssh_pkey=private_key,
            remote_bind_address=(db_hostname, db_port),
            local_bind_address=(LOCAL_BIND_ADDRESS, 0),
            allow_agent=False
        )

        try:
            self.ssh_tunnel.start()
        except BaseSSHTunnelForwarderError as e:
            raise UserException("Failed to establish SSH connection. Recheck all SSH configuration parameters") from e

        logging.info("SSH tunnel is enabled and started.")

    @staticmethod
    def run_legacy_client():
        client = LegacyClient()
        client.run()

    @staticmethod
    def cleanup(temp_folder):
        shutil.rmtree(temp_folder)


if __name__ == "__main__":
    try:
        comp = Component()
        comp.execute_action()
    except UserException as exc:
        logging.exception(exc)
        exit(1)
    except Exception as exc:
        logging.exception(exc)
        exit(2)
