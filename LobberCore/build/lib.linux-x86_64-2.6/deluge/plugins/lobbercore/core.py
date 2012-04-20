import logging
from deluge.plugins.pluginbase import CorePluginBase
import deluge.component as component
import deluge.configmanager
from deluge.core.rpcserver import export
from twisted.internet.task import LoopingCall
from twisted.web import client
import json

from urlparse import urlparse
from urllib import splitnport
from twisted.web import server
from proxy import ReverseProxyTLSResource
from twisted.internet import task, reactor

#DEFAULT_PREFS = {
#    'feed_url':     'https://dev.lobber.se/torrent/all.json',
#    'lobber_key':   'eea076261cac1ba8e860d22bac',
#    'proxy_addr':   '127.0.0.1:7000',
#    'proxy_to':     'https://dev.lobber.se:443'
#}

DEFAULT_PREFS = {
}

log = logging.getLogger(__name__)

class Core(CorePluginBase):
    def enable(self):
        self.config = deluge.configmanager.ConfigManager("lobbercore.conf", DEFAULT_PREFS)
        self.fetch_json_timer = LoopingCall(self.fetch_json)
        self.fetch_json_timer.start(30)
        self.p = self.start_proxy()
        log.info("Lobber plugin started")

    def disable(self):
        self.fetch_json_timer.stop()
        self.port.stopListening()

    def update(self):
        pass

    def start_proxy(self):
        netloc, path = urlparse('https://beta.lobber.se:443')[1:3]
        tracker_host, tracker_port = splitnport(netloc, 443)
        proxy = server.Site(
	        ReverseProxyTLSResource(
		        tracker_host,
		        tracker_port,
		        '',
                path_rewrite=[['/tracker/announce$', '/tracker/uannounce']],
		        tls=True,   # FIXME: Base on urlparse()[0].
		        headers={'X_LOBBER_KEY': 'f4df8584bb1555fa6c794efc50', 'User-Agent': 'Lobber Storage Node/2.0'}))
        bindto = '127.0.0.1:7000'.split(':')
        bindto_host = bindto[0]
        bindto_port = int(bindto[1])
        self.port = reactor.listenTCP(bindto_port, proxy, interface=bindto_host)
        log.info("Lobber proxy started")
        return self.port

    def process_json(self, j):
        result = json.loads(j)
        torrent_list = component.get("TorrentManager").get_torrent_list()
        for torrent in result:
            if not torrent['info_hash'] in torrent_list:
                url = 'http://127.0.0.1:7000/torrent/%s.torrent' % torrent['id']
                component.get("Core").add_torrent_url(url, {}, headers=None)
                log.info("Added: %s" % torrent['label'])
            else:
                log.info("Skipped: %s" % torrent['label'])
                
    def fetch_json(self):
        r = client.getPage(
            'http://127.0.0.1:7000/torrent/all.json',
            method='GET',
            postdata=None,
            agent='Lobber Storage Node/2.0',
            headers={'X_LOBBER_KEY': 'f4df8584bb1555fa6c794efc50'})
        r.addCallback(self.process_json)
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
