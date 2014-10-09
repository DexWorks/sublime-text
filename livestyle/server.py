# A Tornado-based LiveStyle server implementation
# 
import json
import logging
from event_dispatcher import EventDispatcher

# don't know why, but tornado's IOLoop cannot
# properly load platform modules during runtime, 
# so we pre-import them
try:
	import select

	if hasattr(select, "epoll"):
		import tornado.platform.epoll
	elif hasattr(select, "kqueue"):
		import tornado.platform.kqueue
	else:
		import tornado.platform.select
except ImportError:
	pass

import tornado.process
import tornado.ioloop
import tornado.web
import tornado.websocket
import tornado.httpserver

# Tornado server instance
httpserver = None
logger = logging.getLogger('livestyle')
dispatcher = EventDispatcher()

clients  = set()  # all connected clients
patchers = set()  # clients identified as 'patcher'
editors  = dict() # clients identified as 'editor'

class MainHandler(tornado.web.RequestHandler):
	def get(self):
		self.write('LiveStyle websockets server is up and running')

class WebsocketHandler(tornado.websocket.WebSocketHandler):
	def open(self):
		logger.debug('Client connected')
		dispatcher.emit('open', self)
		clients.add(self)
	
	def on_message(self, message):
		handle_message(message, self)		

	def on_close(self):
		logger.debug('Client disconnected')
		dispatcher.emit('close', self)
		remove_client(self)

application = tornado.web.Application([
	(r'/livestyle', WebsocketHandler),
	(r'/', MainHandler),
])

try:
	isinstance("", basestring)
	def isstr(s):
		return isinstance(s, basestring)
except NameError:
	def isstr(s):
		return isinstance(s, str)

def remove_client(client):
	"Removes given client from all collections"
	clients.discard(client)
	patchers.discard(client)

	for editor_id, editor in editors.items():
		if editor is client:
			send(clients, {
				'name': 'editor-disconnect',
				'data': {'id': editor_id}
			})
			del editors[editor_id]

def handle_message(message, client):
	"Perform a special processing of incoming messages"
	payload = json.loads(message)
	receivers = clients

	logger.debug('Received message: %s' % payload['name'])

	if payload['name'] == 'editor-connect':
		editors[payload['data']['id']] = client
	elif payload['name'] == 'patcher-connect':
		patchers.add(client)
	elif payload['name'] in ('calculate-diff', 'apply-patch'):
		# These are very heavy and intensive messages
		# that can be only handled by special clients 
		# called 'patchers'. To save some resources and
		# bandwidth it's recommended to send these
		# messages to patchers only
		receivers = patchers

	# Send all incoming messages to all connected clients
	# except current one
	send(receivers, message, client)

def send(receivers, message, exclude=None):
	"Sends message to given receivers"

	if exclude:
		receivers = [client for client in receivers if c is not exclude]

	if not receivers:
		logger.debug('Cannot send message, client list empty')
	else:
		dispatcher.emit('send-message', message)
		if not isstr(message):
			message = json.dumps(message)
		for client in receivers:
			client.write_message(message)

def on(name, callback):
	dispatcher.on(name, callback)

def off(name, callback=None):
	dispatcher.off(name, callback)

def one(name, callback):
	dispatcher.one(name, callback)

def start(port=54000, address='127.0.0.1'):
	"Starts LiveStyle server on given port"
	global httpserver
	logger.info('Starting LiveStyle server on port %s' % port)
	httpserver = tornado.httpserver.HTTPServer(application)
	httpserver.listen(port, address=address)

def stop():
	global httpserver
	for c in clients:
		c.close()
	clients.clear()

	if httpserver:
		logger.info('Stopping server')
		httpserver.stop()