"""Explicit official-link fetch; no searches or outbound log/query content."""
from html.parser import HTMLParser
import ipaddress
import socket
from urllib.parse import urlsplit,urljoin
import httpx

class Text(HTMLParser):
    def __init__(self):super().__init__();self.hidden=0;self.parts=[]
    def handle_starttag(self,tag,attrs):
        if tag in ('script','style','noscript'):self.hidden+=1
        if tag in ('p','div','li','pre','br','h1','h2','h3','tr'):self.parts.append('\n')
    def handle_endtag(self,tag):
        if tag in ('script','style','noscript'):self.hidden=max(0,self.hidden-1)
    def handle_data(self,data):
        if not self.hidden:self.parts.append(data)

def addresses(url,hosts,resolver=socket.getaddrinfo):
    u=urlsplit(url)
    if u.scheme!='https' or u.username or u.password or u.port not in (None,443) or u.hostname not in hosts:
        raise ValueError('仅允许官方白名单HTTPS文档链接，不含凭据或片段')
    try:addresses=resolver(u.hostname,443,type=socket.SOCK_STREAM)
    except OSError:raise ValueError('官方文档域名暂时无法解析') from None
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):raise ValueError('拒绝文档链接解析到私网或本机地址')
    return [a[4][0] for a in addresses]

def validate_url(url,hosts,resolver=socket.getaddrinfo):
    addresses(url,hosts,resolver)
    return url

def fetch(url,hosts,client=None,resolver=socket.getaddrinfo):
    url=url.split('#',1)[0]
    owned=client is None
    client=client or httpx.Client(timeout=10,trust_env=False,follow_redirects=False)
    try:
        for _ in range(4):
            resolved=addresses(url,hosts,resolver)
            host=urlsplit(url).hostname
            headers={'User-Agent':'OpsLens-Documentation/0.5','Accept':'text/html,text/plain'}
            extensions={}
            request_url=url
            if owned:
                # Pin the validated IP to prevent a second DNS lookup from rebinding
                # to a private address. TLS still authenticates the original host.
                request_url=httpx.URL(url).copy_with(host=resolved[0])
                headers['Host']=host
                extensions['sni_hostname']=host
            with client.stream('GET',request_url,headers=headers,extensions=extensions) as r:
                if r.status_code in (301,302,303,307,308):
                    url=urljoin(url,r.headers.get('location','')).split('#',1)[0];continue
                if r.status_code!=200:raise ValueError('官方文档获取失败，HTTP '+str(r.status_code))
                content=bytearray()
                for chunk in r.iter_bytes(16384):
                    content.extend(chunk)
                    if len(content)>1024*1024:raise ValueError('文档正文超过1MiB')
                text=content.decode(r.encoding or 'utf-8',errors='strict')
                if 'html' in r.headers.get('content-type',''):
                    parser=Text();parser.feed(text);text=''.join(parser.parts)
                elif not r.headers.get('content-type','').startswith('text/'):raise ValueError('链接不是可读取的文本正文')
                if not text.strip():raise ValueError('没有取得可读取的文档正文')
                return text,url
        raise ValueError('文档重定向次数过多')
    except httpx.HTTPError:raise ValueError('官方文档暂时无法访问；本机日志仍可分析') from None
    except UnicodeError:raise ValueError('文档编码暂不支持') from None
    finally:
        if owned:client.close()
