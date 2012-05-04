import logging
from deluge.plugins.pluginbase import CorePluginBase
import deluge.component as component
import deluge.configmanager
from deluge.core.rpcserver import export
from twisted.internet.task import LoopingCall
from twisted.internet.error import ConnectError, CannotListenError, ConnectionRefusedError
from twisted.web import client
from twisted.web.error import Error
import json
from urlparse import urlparse
from urllib import splitnport
from twisted.web import server
from proxy import ReverseProxyTLSResource
from twisted.internet import task, reactor

DEFAULT_PREFS = {
    'feed_url': 'https://dev.lobber.se/torrent/all.json',
    'lobber_key': '',
    'proxy_port': '7001',
    'tracker_host': 'https://dev.lobber.se',
    'minutes_delay': 1
}

log = logging.getLogger(__name__)

class Core(CorePluginBase):
    def enable(self):
        self.load_config()
        self.start_plugin()


    def disable(self):
        self.config['feed_url'] = self.feed_url
        self.config['lobber_key'] = self.lobber_key
        self.config['proxy_port'] = self.proxy_port
        self.config['tracker_host'] = self.tracker_host
        self.config.save()
        self.stop_plugin()

    def start_plugin(self):
        try:
            self.port = self.start_proxy()
        except CannotListenError:
            pass
        self.fetch_json_timer = LoopingCall(self.fetch_json)
        self.fetch_json_timer.start(self.minutes_delay*60)
        self.fetch_json_timer.start(self.minutes_delay*60)
        log.info("Lobber plugin started")

    def stop_plugin(self):
        try:
            self.fetch_json_timer.stop()
        except AssertionError:
            # Fetch loop not running
            pass
        self.port.stopListening()
        log.info("Lobber plugin stopped")

    def load_config(self):
        self.config = deluge.configmanager.ConfigManager("lobbercore.conf", DEFAULT_PREFS)
        self.feed_url = self.config['feed_url']
        self.lobber_key = self.config['lobber_key']
        self.proxy_port = self.config['proxy_port']
        self.tracker_host = self.config['tracker_host']
        self.minutes_delay = self.config['minutes_delay']

    def update(self):
        pass

    def start_proxy(self):
        log.debug('start_proxy starting')
        parse_result = urlparse(self.tracker_host)
        tracker_port = parse_result.port
        if parse_result.scheme == 'https':
            tls = True
            if not tracker_port:
                tracker_port = 443
        else:
            tls = False
            if not tracker_port:
                tracker_port = 80
        tracker_host = splitnport(parse_result.netloc, parse_result.port)[0]
        proxy = server.Site(
            ReverseProxyTLSResource(
		        tracker_host,
		        tracker_port,
		        '',
                path_rewrite=[['/tracker/announce$', '/tracker/uannounce']],
		        tls=tls,
                # str() as header can't contain unicode.
		        headers={'X_LOBBER_KEY': str(self.lobber_key), 'User-Agent': 'Lobber Storage Node/2.0'}))
        bindto_host = '127.0.0.1'
        bindto_port = int(self.proxy_port)
        log.info("Lobber proxy started")
        return reactor.listenTCP(bindto_port, proxy, interface=bindto_host)

    def process_json(self, j):
        log.debug('process_json starting')
        result = json.loads(j)
        torrent_list = component.get("TorrentManager").get_torrent_list()
        for torrent in result:
            if not torrent['info_hash'] in torrent_list:
                url = 'http://127.0.0.1:%s/torrent/%s.torrent' % (self.proxy_port, torrent['id'])
                component.get("Core").add_torrent_url(url, {}, headers=None)
                log.info("Added: %s" % torrent['label'])
            else:
                pass
        log.debug('process_json ended')

    def fetch_json_error(self, failure):
        failure.trap(Error)
        log.error('LobberCore: Error in fetch_json.')
        log.error(failure.getErrorMessage())

    def proxy_error(self, failure):
        failure.trap(ConnectionRefusedError)
        # Proxy not started, try to start it
        self.port = self.start_proxy()
            
    def fetch_json(self):
        log.debug('fetch_json starting')
        parse_result = urlparse(self.feed_url)
        # str() as url can't contain unicode.
        url = str('http://127.0.0.1:%s%s' % (self.proxy_port, parse_result.path))
        r = client.getPage(
            url,
            method='GET',
            postdata=None,
            agent='Lobber Storage Node/2.0',
            headers={'X_LOBBER_KEY': str(self.lobber_key)}) # str() as header can't contain unicode.
        r.addCallback(self.process_json)
        r.addErrback(self.fetch_json_error)
        r.addErrback(self.proxy_error)
        log.debug('fetch_json ended')
        return r

    @export
    def set_config(self, config):
        """Sets the config dictionary"""
        for key in config.keys():
            self.config[key] = config[key]
        self.config.save()

    @export
    def get_config(self):
        """Returns the config dictionary"""
        return self.config.config

    @export
    def reload(self):
        self.load_config()
        self.stop_plugin()
        self.start_plugin()