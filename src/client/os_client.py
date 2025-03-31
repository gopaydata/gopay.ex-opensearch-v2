import json
# import typing as t
from typing import Iterable

from opensearchpy import OpenSearch
from opensearchpy.exceptions import OpenSearchException

DEFAULT_SIZE = 10_000
SCROLL_TIMEOUT = '15m'


class OpenSearchClientException(Exception):
    pass


class OpenSearchClient(OpenSearch):

    def __init__(self, hosts: list, scheme: str = None, http_auth: tuple = None, api_key: tuple = None):
        options = {"hosts": hosts, "timeout": 30, "retry_on_timeout": True, "max_retries": 5}

        if scheme == "https":
            options.update({"verify_certs": False, "ssl_show_warn": False})

        if http_auth:
            options.update({"http_auth": http_auth})
        elif api_key:
            options.update({"api_key": api_key})

        super().__init__(**options)

    def extract_data(self, index_name: str, query: str) -> Iterable:
        response = self.search(index=index_name, size=DEFAULT_SIZE, scroll=SCROLL_TIMEOUT, body=query)
        for r in self._process_response(response):
            yield r

        while len(response['hits']['hits']):
            response = self.scroll(scroll_id=response["_scroll_id"], scroll=SCROLL_TIMEOUT)
            for r in self._process_response(response):
                yield r

    def _process_response(self, response: dict) -> Iterable:
        results = [hit["_source"] for hit in response['hits']['hits']]
        for result in results:
            yield self.flatten_json(result)

    def ping(self, *args, **kwargs) -> bool:
        try:
            return super().ping(*args, **kwargs)
        except OpenSearchException as e:
            raise OpenSearchClientException(e)

    def flatten_json(self, x, out=None, name=''):
        if out is None:
            out = dict()
        if isinstance(x, dict):
            for a in x:
                self.flatten_json(x[a], out, name + a + '.')
        elif isinstance(x, list):
            out[name[:-1]] = json.dumps(x)
        else:
            out[name[:-1]] = x
        return out
