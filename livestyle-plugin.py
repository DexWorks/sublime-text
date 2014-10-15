import sys
import os.path
import logging
import imp
import threading

import sublime
import sublime_plugin

base_path = os.path.abspath(os.path.dirname(__file__))
for p in ['', 'livestyle', 'editor', 'tornado.zip', 'backports.zip']:
	p = os.path.join(base_path, p)
	if p not in sys.path:
		sys.path.append(p)

# Make sure all dependencies are reloaded on upgrade
if 'livestyle.utils.reloader' in sys.modules:
	imp.reload(sys.modules['livestyle.utils.reloader'])

import livestyle.utils.reloader
import livestyle.server as server
import livestyle.client as client
import livestyle.utils.editor as editor_utils
import livestyle.utils.file_reader as file_reader
from tornado import gen
from tornado.ioloop import IOLoop

sublime_ver = int(sublime.version()[0])

# List of supported by LiveStyle file extensions
supported_syntaxes = ['css', 'less', 'scss']

#############################
# Editor
#############################

def is_supported_view(view, strict=False):
	"Check if given view can be user for LiveStyle updates"
	return editor_utils.is_supported_view(view, supported_syntaxes, strict)

def view_syntax(view):
	"Returns LiveStyle-supported syntax for given view"
	sv = is_supported_view(view)
	return sv and sv['syntax'] or 'css'

def editor_payload(view, data=None):
	"Returns diff/patch payload for given view"
	content = editor_utils.content(view)
	result = {
		'uri':     editor_utils.file_name(view),
		'syntax':  view_syntax(view),
		'content': content,
		'hash':    editor_utils.hash(content),
		# TODO add global dependencies from current project,
		# must be filtered for current syntax
		# globalDependencies: ['/demo/global.less']
	}

	if data:
		result.update(data)

	return result

#############################
# Server
#############################

def _start():
	start_app()
	IOLoop.instance().start()

def start_app():
	logger.info('Start app')
	IOLoop.instance().add_future(client_connect(), restart_app)

def restart_app(f):
	exc = f.exception()
	if exc:
		# if app termination was caused by exception -- restart it,
		# otherwise it was a requested shutdown
		logger.info('Restarting app because %s' % exc)
		IOLoop.instance().call_later(1, start_app)

def refresh_livestyle_files():
	"Sends currenly opened files, available for live update, to all connected clients"
	client.send('editor-files', {
		'id': 'st%d' % sublime_ver,
		'files': editor_utils.supported_files(supported_syntaxes)
	})

def unload_handler():
	server.stop()
	IOLoop.instance().stop()

@client.on('open client-connect')
def identify(*args):
	client.send('editor-connect', {
		'id': 'st%d' % sublime_ver,
		'title': 'Sublime Text %d' % sublime_ver,
		'icon': 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAABu0lEQVR42q2STWsTURhG3WvdCyq4CEVBAgYCM23JjEwy+cJC41gRdTIEGyELU7BNNMJQhUBBTUjSRdRI3GThRld+gbj2JwhuRFy5cZ3Ncd5LBwZCIIIXDlzmeZ9z4d458t9WoVB4XywWCcnn89i2TSaTIZvNEuRhJvtP0e7R6XT6VYJer8dkMmE0GrHf3uPxg1s8f+TR9ncZDocq63a7SiId6YogBqiPg8FASe43d3iz7/D7rcuP1zf4NnHxfV9yQc0CSFcEeihotVo0Gg22tzbh3SbP7lq4lzTuuHlqtZrkQlSgi8AIBZVKBc/zuH5lnc7tFX4OL/L9wOTJlsbGepFyuSwzUYERCqIXhGVZJJNJbqbP0b66DC8ucO/yedLptMzMF4S3X7JXeFWJ4Zln2LZPw9NT+BuxxQTquaw1Xl47yZ/WEr92j3PgnMBc08nlcvMF1Wo1DNW7G4aBpmnouo5pmtGyzM4K+v0+4/F4ITqdzqzAdV0cxyGVSsmpc5G/s1QqzQg+N5tNdUmJRIJ4PD4XkdTrdaQTClYDlvnHFXTOqu7h5mHAx4AvC/IhYE+6IliK2IwFWT3sHPsL6BnLQ4kfGmsAAAAASUVORK5CYII='
	})
	refresh_livestyle_files()

# TODO set initial content of current view for connected patched
@client.on('patcher-connect')
def on_patcher_connect(*args):
	pass

@client.on('incoming-updates')
def apply_incoming_updates(data):
	view = editor_utils.view_for_uri(data['uri'])
	if view:
		client.send('apply-patch', editor_payload(view, {
			'patches': data['patches']
		}))

@client.on('patch')
def handle_patch_request(data):
	view = editor_utils.view_for_uri(data['uri'])
	if view:
		view.run_command('livestyle_replace_content', {'payload': data})

@client.on('request-files')
def respond_with_dependecy_list(data):
	"Returns list of requested dependecy files, with thier content"
	response = []
	for file in data.get('files', []):
		file_data = file_reader.get_file_contents(file)
		if file_data:
			response.append(file_data)

	client.send('files', {
		'token': data['token'],
		'files': response
	})


@gen.coroutine
def client_connect():
	try:
		yield client.connect()
	except Exception as e:
		print('Create own server because %s' % e)
		server.start()
		yield client.connect()

#############################
# Editor plugin
#############################

class LivestyleListener(sublime_plugin.EventListener):
	def on_new(self, view):
		refresh_livestyle_files()

	def on_load(self, view):
		refresh_livestyle_files()

	def on_close(self, view):
		refresh_livestyle_files()

	def on_modified(self, view):
		if is_supported_view(view, True) and not editor_utils.is_locked(view):
			client.send('calculate-diff', editor_payload(view))

	def on_activated(self, view):
		refresh_livestyle_files()
		if is_supported_view(view, True):
			client.send('initial-content', editor_payload(view))

	def on_post_save(self, view):
		refresh_livestyle_files()

class LivestyleReplaceContentCommand(sublime_plugin.TextCommand):
	"Internal command to correctly update view content after patching, used to retain single undo point"
	def run(self, edit, payload=None, **kwargs):
		if not payload:
			return

		editor_utils.lock(self.view)
		if payload.get('ranges') and payload.get('hash') == editor_utils.view_hash(self.view):
			# integrity check: editor content didn't changed
			# since last patch request so we can apply incremental updates
			self.view.sel().clear()
			for r in payload['ranges']:
				self.view.replace(edit, sublime.Region(r[0], r[1]), r[2])

			# select last range
			last_range = payload['ranges'][-1]
			self.view.sel().add(sublime.Region(last_range[0], last_range[0] + len(last_range[2])))
		else:
			# user changed content since last patch request:
			# replace whole content
			self.view.replace(edit, sublime.Region(0, self.view.size()), payload.get('content', ''))

		self.view.show(self.view.sel())
		editor_utils.unlock(self.view)

#############################
# Start plugin
#############################

# setup logger
logger = logging.getLogger('livestyle')
# logger.propagate = False
logger.setLevel(logging.INFO)
if not logger.handlers:
	ch = logging.StreamHandler()
	ch.setFormatter(logging.Formatter('Emmet LiveStyle: %(message)s'))
	logger.addHandler(ch)

threading.Thread(target=_start).start()