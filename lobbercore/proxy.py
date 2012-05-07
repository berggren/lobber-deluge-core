'''
Created on Nov 3, 2010

@author: leifj
'''

import re
import urlparse
from urllib import quote as urlquote
from twisted.internet import reactor, ssl
from twisted.web.resource import Resource
from twisted.web.server import NOT_DONE_YET
from twisted_web_proxy import ProxyClientFactory

class ReverseProxyTLSResource(Resource):
    """
    Resource that renders the results gotten from another server

    Put this resource in the tree to cause everything below it to be relayed
    to a different server.

    @ivar proxyClientFactoryClass: a proxy client factory class, used to create
        new connections.
    @type proxyClientFactoryClass: L{ClientFactory}

    @ivar reactor: the reactor used to create connections.
    @type reactor: object providing L{twisted.internet.interfaces.IReactorTCP}
    """

    proxyClientFactoryClass = ProxyClientFactory


    def __init__(self, host, port, path, path_rewrite=None, reactor=reactor,
                 tls=False, headers={}):
        """
        @param host: the host of the web server to proxy.
        @type host: C{str}

        @param port: the port of the web server to proxy.
        @type port: C{port}

        @param path: the base path to fetch data from. Note that you shouldn't
            put any trailing slashes in it, it will be added automatically in
            request. For example, if you put B{/foo}, a request on B{/bar} will
            be proxied to B{/foo/bar}.  Any required encoding of special
            characters (such as " " or "/") should have been done already.

        @param path_rewrite: list of lists with two regexp strings
        used for rewriting the path.

        @param tls: use tls or not

        @type path: C{str}
        """
        Resource.__init__(self)
        self.host = host
        self.port = port
        self.path = path
        self.path_rewrite = path_rewrite
        self.tls = tls
        self.reactor = reactor
        self.headers = headers

    def getChild(self, path, request):
        """
        Create and return a proxy resource with the same proxy configuration
        as this one, except that its path also contains the segment given by
        C{path} at the end.
        """
        return ReverseProxyTLSResource(
            self.host, 
            self.port, 
            self.path + '/' + urlquote(path, safe=""),
            self.path_rewrite,
            self.reactor,
            self.tls,
            self.headers)


    def render(self, request):
        """
        Render a request by forwarding it to the proxied server.
        """
        # RFC 2616 tells us that we can omit the port if it's the default port,
        # but we have to provide it otherwise
        if (self.tls and self.port == 443) or (not self.tls and self.port == 80):
            host = self.host
        else:
            host = "%s:%d" % (self.host, self.port)
        request.received_headers['host'] = host
        request.content.seek(0, 0)

        path, _, qs = urlparse.urlparse(request.uri)[2:5]
        if path and self.path_rewrite:
            for pattern, repl in self.path_rewrite:
                path = re.sub(pattern, repl, path, 1)
        if qs:
            rest = path + '?' + qs
        else:
            rest = path
            
        for key,value in self.headers.items():
            request.requestHeaders.setRawHeaders(key,[value])
        
        clientFactory = self.proxyClientFactoryClass(
            request.method, rest, request.clientproto,
            request.getAllHeaders(), request.content.read(), request)
        
        if self.tls:
            context = ssl.ClientContextFactory()
            context.method = ssl.SSL.TLSv1_METHOD
            self.reactor.connectSSL(self.host, self.port, clientFactory,
                                    context)
        else:
            self.reactor.connectTCP(self.host, self.port, clientFactory)
        return NOT_DONE_YET
