import logging
import json
from pprint import pformat
from stompservice import StompClientFactory

log = logging.getLogger(__name__)

class StompClient(StompClientFactory):

    def __init__(self, destinations=["/torrent/notify"]):
        self.destinations = destinations
        #self.url_handler = TransmissionURLHandler(lobber, transmission,
        #    tracker_url, proxy_addr)
        #self.lobber = lobber
        self.lobbercore = lobbercore

    def recv_connected(self, msg):
        for dst in self.destinations:
            log.msg("Subscribe to %s" % dst)
            self.subscribe(dst)

    def recv_message(self, msg):
        notice = None
        try:
            body = msg.get('body').strip()
        except Exception,err:
            log.err('recv_message: msg.get: %s' % repr(err))
        try:
            notice = json.loads(body)
        except Exception,err:
            log.err('recv_message: json.loads: %s' % repr(err))

        if not notice:
            log.err("recv_message: Got an unknown message: %s" % repr(msg))
            return

        log.err("recv_message: stomp msg: %s" % pformat(notice))
        for type, info in notice.iteritems():
            id = info[0]
            hashval = info[1].strip()
            if type == 'add':
                log.debug('STOMP ADD ID: %s, info_hash: %s' % (id, hashval))
                #self.url_handler.load_url(self.lobber.torrent_url(id), True)
            if type == 'delete':
                #t = self.transmission.hashmap.get(hashval)
                log.debug('STOMP REMOVE ID: %s, info_hash: %s' % (id, hashval))
                #if t:
                #    self.lobber.api_call(
                #        "/torrent/exists/%s" % hashval,
                #        err_handler=cb_wrapper(self.lobber, self.transmission, t).err)
                #else:
                #    log.err("recv_message: unable to delete unknown torrent %s" % hashval)