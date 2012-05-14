import logging
from deluge.plugins.pluginbase import CorePluginBase
import deluge.component as component
import deluge.configmanager
from deluge.core.rpcserver import export
from twisted.web import server, client
from twisted.internet import reactor
from twisted.internet.task import LoopingCall
from twisted.application.internet import TCPClient
from twisted.internet.error import ConnectError, CannotListenError, ConnectionRefusedError
from twisted.web.error import Error
import json
from urlparse import urlparse
from urllib import splitnport

from lobbercore.proxy import ReverseProxyTLSResource
from lobbercore.stomp_client import StompClient

DEFAULT_PREFS = {
    'feed_url': 'https://dev.lobber.se/torrent/all.json',
    'lobber_key': '',
    'proxy_port': 7001,
    'tracker_host': 'https://dev.lobber.se',
    'stomp_host': 'stomp://dev.lobber.se',
    'stomp_port': 61613,
    'minutes_delay': 1,
    # If download_dir is left blank Deluge settings will be used.
    'download_dir': '', # Ending slash important
    'unique_path': False,
    # Torrent monitoring options
    'monitor_torrents': False,
    'remove_data': False,
    'torrent_evaluator': 'total_seeders',
    'removed_torrents': [],
    # total_seeders evaluator options
    'min_seeders': 1,
    'max_seeders': 2,

}

log = logging.getLogger(__name__)

class Core(CorePluginBase):

    def enable(self):
        component.get("AlertManager").register_handler("scrape_reply_alert", self.on_scrape_reply_alert)
        self.config = deluge.configmanager.ConfigManager("lobbercore.conf", DEFAULT_PREFS)
        self.EVALUATORS = {
            'total_seeders': self.total_seeders_evaluator,
            }
        self.start_plugin()

    def disable(self):
        component.get("AlertManager").deregister_handler("scrape_reply_alert")
        self.stop_plugin()

    def start_plugin(self):
        try:
            self.proxy = self.start_proxy()
        except CannotListenError:
            pass
        self.fetch_json_timer = LoopingCall(self.fetch_json)
        self.fetch_json_timer.start(self.config['minutes_delay']*60)
        stomp_client = StompClient()
        self.stomp_service = TCPClient(self.config['stomp_host'], self.config['stomp_port'], stomp_client)
        if self.config['monitor_torrents']:
            self.monitor_torrents_timer = LoopingCall(self.monitor_torrents)
            self.monitor_torrents_timer.start(1*60)
            log.info('Monitoring torrents.')
        log.info("Lobber plugin started")

    def stop_plugin(self):
        try:
            self.fetch_json_timer.stop()
        except AssertionError:
            # Fetch loop not running
            pass
        try:
            self.monitor_torrents_timer.stop()
        except AssertionError:
            # Monitor loop not running
            pass
        self.stomp_service.looseConnection()
        self.proxy.stopListening()
        log.info("Lobber plugin stopped")

    def update(self):
        pass

    def start_proxy(self):
        parse_result = urlparse(self.config['tracker_host'])
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
		        headers={'X_LOBBER_KEY': self.config['lobber_key'], 'User-Agent': 'Lobber Storage Node/2.0'}))
        bindto_host = '127.0.0.1'
        bindto_port = int(self.config['proxy_port'])
        log.info("Lobber proxy started")
        return reactor.listenTCP(bindto_port, proxy, interface=bindto_host)

    def get_torrent_options(self, unique_path=None):
        """
        Returns a dictionary with torrent options. If a unique path is supplied the storage
        directory will be appended with it.
        """
        opts = {}
        if self.config['download_dir']:
            opts['download_location'] = self.config['download_dir']
            if unique_path:
                opts['download_location'] = '%s%s/' % (opts['download_location'], unique_path)
            log.debug('download_location: %s' % opts['download_location'])
        return opts

    def process_json(self, j):
        try:
            result = json.loads(j)
        except ValueError:
            log.error('Expected JSON, got:\n%s' % j)
            return
        log.debug('Processing JSON data:\n%s' % json.dumps(result, indent=4))
        torrent_list = component.get("TorrentManager").get_torrent_list()
        for torrent in result:
            if not torrent['info_hash'] in torrent_list and not torrent['info_hash'] in self.config['removed_torrents']:
                url = 'http://127.0.0.1:%s/torrent/%s.torrent' % (self.config['proxy_port'], torrent['id'])
                if self.config['unique_path']:
                    torrent_options = self.get_torrent_options(unique_path=torrent['info_hash'])
                else:
                    torrent_options = self.get_torrent_options()
                component.get("Core").add_torrent_url(url, torrent_options, headers=None)
                log.info("Added: %s" % torrent['label'])
            else:
                log.debug('Torrent with hash %s already added.' % torrent['info_hash'])
                pass

    def fetch_json_error(self, failure):
        failure.trap(Error, TypeError)
        log.error('LobberCore: Error in fetch_json.')
        log.error(failure.getErrorMessage())

    def proxy_error(self, failure):
        failure.trap(ConnectionRefusedError)
        # Proxy not started, try to start it
        self.proxy = self.start_proxy()
            
    def fetch_json(self):
        parse_result = urlparse(self.config['feed_url'])
        # Ensure that url does not contain unicode.
        url = str('http://127.0.0.1:%s%s' % (self.config['proxy_port'], parse_result.path))
        log.debug('Fetching JSON data from %s.' % url)
        r = client.getPage(
            url,
            method='GET',
            postdata=None,
            agent='Lobber Storage Node/2.0',
            # Ensure that headers does not contain unicode.
            headers={'X_LOBBER_KEY': str(self.config['lobber_key'])})
        r.addCallback(self.process_json)
        r.addErrback(self.fetch_json_error)
        r.addErrback(self.proxy_error)
        return r

    def on_scrape_reply_alert(self, alert):
        evaluate = self.EVALUATORS[self.config['torrent_evaluator']]
        reply = {'total_seeds': alert.complete,
                 'total_peers': alert.incomplete,
                 'info_hash': str(alert.handle.info_hash()),
        }
        evaluate(reply, scrape_reply=True)

    def monitor_torrents(self):
        """
        Loops over the list of torrents and resumes/pause/removes the torrent
        depending on the evaluator used.

        The evaluator should call monitor_torrent_execute_action with the torrent
        and 'Resume', 'Pause', 'Remove' or None.
        """
        log.debug('Running monitor_torrents.')
        evaluate = self.EVALUATORS[self.config['torrent_evaluator']]
        torrent_list = component.get("TorrentManager").get_torrent_list()
        for id in torrent_list:
            evaluate(component.get("TorrentManager")[id])

    def monitor_torrent_execute_action(self, torrent, action):
        log.debug('Monitor torrent, ID: %s, Action: %s' % (torrent.torrent_id, action))
        if action:
            if action == 'Remove':
                t_id = torrent.torrent_id
                component.get("TorrentManager").remove(t_id, remove_data=self.config['remove_data'])
                # Should removed torrents really be persistent between restarts?
                self.config['removed_torrents'].append(t_id)
                self.config.save()
            elif action == 'Pause':
                if not torrent.handle.is_paused():
                    torrent.pause()
            elif action == 'Resume':
                if torrent.handle.is_paused():
                    torrent.resume()

    def total_seeders_evaluator(self, torrent, scrape_reply=False):
        """
        Pause if seeders => min_seeders,
        Resume if seeders < min_seeders,
        Remove if seeders >= max_seeders.
        """
        if scrape_reply:
            status = torrent
            torrent = component.get("TorrentManager")[torrent['info_hash']]
        else:
            status = torrent.get_status(['total_seeds', 'state', 'is_finished'])
            if not status['is_finished']:
                return None
            if status['state'] == 'Paused':
                # Check the tracker for up to date information before evaluating.
                torrent.scrape_tracker()
                return None
        # All information gathered, evaluate torrent.
        seeders = status['total_seeds']
        if seeders >= self.config['max_seeders']:
            log.debug('Evaluator seeders >= max_seeders')
            action = 'Remove'
        elif seeders >= self.config['min_seeders']:
            log.debug('Evaluator seeders >= min_seeders')
            action = 'Pause'
        elif seeders < self.config['min_seeders']:
            log.debug('Evaluator seeders < min_seeders')
            action = 'Resume'
        else:
            log.debug('Evaluator seeders == min_seeders')
            action = None
        self.monitor_torrent_execute_action(torrent, action)


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
        self.stop_plugin()
        self.start_plugin()
