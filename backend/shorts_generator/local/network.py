"""Keep platform requests and redirects on public HTTP(S) addresses."""

import http.client
import ipaddress
import socket
from urllib.parse import urlsplit
from urllib.request import BaseHandler

from yt_dlp import YoutubeDL
from yt_dlp.networking._urllib import HTTPHandler, UrllibRH
from yt_dlp.utils import DownloadError


def connect_public(address, timeout=None, source_address=None):
    host, port = address
    if port not in (80, 443):
        raise OSError("The platform requested an unsupported port.")
    candidates = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
    error = OSError("The platform requested a non-public destination.")
    for family, kind, protocol, _, target in candidates:
        ip = ipaddress.ip_address(target[0])
        if isinstance(ip, ipaddress.IPv6Address):
            ip = ip.ipv4_mapped or ip
        if not ip.is_global or ip.is_multicast or ip.is_reserved:
            continue
        sock = socket.socket(family, kind, protocol)
        try:
            sock.settimeout(timeout)
            if source_address:
                sock.bind(source_address)
            sock.connect(target)
            return sock
        except OSError as exc:
            sock.close()
            error = exc
    raise error


class PublicConnection:
    def connect(self):
        self._create_connection = connect_public
        super().connect()


class PublicHTTPConnection(PublicConnection, http.client.HTTPConnection):
    pass


class PublicHTTPSConnection(PublicConnection, http.client.HTTPSConnection):
    pass


class PublicHTTPHandler(HTTPHandler):
    @staticmethod
    def _make_conn_class(base, req):
        return PublicHTTPSConnection if base is http.client.HTTPSConnection else PublicHTTPConnection


class NoOtherSchemes(BaseHandler):
    handler_order = 0

    def default_open(self, request):
        if urlsplit(request.full_url).scheme not in {"http", "https"}:
            raise OSError("The platform redirected to an unsupported URL.")


class PublicUrllibRH(UrllibRH):
    def _create_instance(self, *args, **kwargs):
        opener = super()._create_instance(*args, **kwargs)
        for handler in opener.handlers:
            if type(handler) is HTTPHandler:
                # OpenerDirector stores bound methods on the handler instance.
                handler.__class__ = PublicHTTPHandler
                break
        opener.add_handler(NoOtherSchemes())
        return opener


class PlatformDownloader(YoutubeDL):
    def __init__(self, options, extractors):
        self.platform_extractors = frozenset(extractors)
        super().__init__({**options, "allowed_extractors": ["^" + name.replace(".", r"\.") + "$" for name in extractors]})

    def build_request_director(self, handlers, preferences=None):
        return super().build_request_director([PublicUrllibRH])

    def get_info_extractor(self, ie_key):
        extractor = self._ies.get(ie_key)
        if extractor is None or extractor.IE_NAME.lower() not in self.platform_extractors:
            raise DownloadError("The platform did not return an individual video it hosts.")
        return super().get_info_extractor(ie_key)
