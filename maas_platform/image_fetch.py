"""Bounded public image fetches with connection-time IP pinning and normal TLS."""
import asyncio,ipaddress,socket
import httpcore,httpx
from .errors import ServiceError


def public_ip(value):
    address=ipaddress.ip_address(value)
    return (address.is_global and not any((address.is_multicast,address.is_reserved,
        address.is_loopback,address.is_link_local,address.is_unspecified))
        and not (isinstance(address,ipaddress.IPv6Address) and (address.sixtofour or address.teredo)))


def image_url(value):
    try:
        url=httpx.URL(value)
        if url.scheme not in ('http','https') or not url.host or url.userinfo or url.fragment:
            raise ValueError()
        if (url.port if url.port is not None else (443 if url.scheme=='https' else 80)) not in (80,443):raise ValueError()
        if '%' in url.host:raise ValueError()
        try:address=ipaddress.ip_address(url.host)
        except ValueError:pass
        else:
            if not public_ip(address):raise ValueError()
        return url
    except (ValueError,httpx.InvalidURL):
        raise ServiceError('IMAGE_URL_NOT_ALLOWED','Use a public HTTP(S) image URL on port 80 or 443 without credentials or fragments',422) from None


class PublicNetworkBackend(httpcore.AsyncNetworkBackend):
    def __init__(self,delegate=None,resolver=None):
        self.delegate=delegate or httpcore.AnyIOBackend()
        self.resolver=resolver

    async def connect_tcp(self,host,port,timeout=None,local_address=None,socket_options=None):
        if port not in (80,443):raise ServiceError('IMAGE_URL_NOT_ALLOWED','Image URL port is not allowed',422)
        resolver=self.resolver or asyncio.get_running_loop().getaddrinfo
        # Resolve once and pass only a checked numeric IP to the network backend.
        # httpcore retains the original host for HTTP Host and TLS SNI/verification.
        rows=await resolver(host,port,type=socket.SOCK_STREAM)
        addresses=list(dict.fromkeys(row[4][0] for row in rows))
        if not addresses or any(not public_ip(ip) for ip in addresses):
            raise ServiceError('IMAGE_URL_NOT_ALLOWED','Image URL resolves to a prohibited address',422)
        return await self.delegate.connect_tcp(addresses[0],port,timeout=timeout,
            local_address=local_address,socket_options=socket_options)

    async def connect_unix_socket(self,*args,**kwargs):
        raise ServiceError('IMAGE_URL_NOT_ALLOWED','Unix sockets are not image sources',422)

    async def sleep(self,seconds):await asyncio.sleep(seconds)


class PublicImageTransport(httpx.AsyncHTTPTransport):
    def __init__(self):
        super().__init__(trust_env=False,retries=0,
            limits=httpx.Limits(max_connections=1,max_keepalive_connections=0))
        # HTTPX 0.28.1 / httpcore 1.0.9 are pinned. Keep this integration local.
        self._pool._network_backend=PublicNetworkBackend()


class PublicImageFetcher:
    def __init__(self,client_factory=None):self.client_factory=client_factory

    async def fetch(self,url,limit):
        url=image_url(url)
        try:
            factory=self.client_factory or httpx.AsyncClient
            options={'trust_env':False,'follow_redirects':False,'timeout':httpx.Timeout(8,connect=4)}
            if self.client_factory is None:options['transport']=PublicImageTransport()
            async with factory(**options) as client:
                # Never forward API keys, cookies or caller headers to the image host.
                async with client.stream('GET',url,headers={'Accept-Encoding':'identity','Accept':'image/png,image/jpeg'}) as response:
                    if 300<=response.status_code<400:
                        raise ServiceError('IMAGE_URL_REDIRECT','Image URL redirects are not supported',422)
                    if response.status_code!=200:
                        raise ServiceError('IMAGE_FETCH_FAILED','Image host did not return a successful response',422)
                    if response.headers.get('content-encoding','identity').lower()!='identity':
                        raise ServiceError('IMAGE_ENCODING','HTTP-compressed image downloads are unsupported',422)
                    length=response.headers.get('content-length')
                    if length:
                        try:size=int(length)
                        except ValueError:raise ServiceError('IMAGE_FETCH_FAILED','Invalid image length',422) from None
                        if size<0:raise ServiceError('IMAGE_FETCH_FAILED','Invalid image length',422)
                        if size>limit:raise ServiceError('INPUT_SIZE','Image exceeds the decoded byte limit',413)
                    raw=bytearray()
                    async for chunk in response.aiter_raw():
                        if len(raw)+len(chunk)>limit:raise ServiceError('INPUT_SIZE','Image exceeds the decoded byte limit',413)
                        raw.extend(chunk)
                    return bytes(raw)
        except ServiceError:raise
        except (httpx.HTTPError,OSError,TimeoutError):
            raise ServiceError('IMAGE_FETCH_FAILED','Image download failed before inference',422) from None
