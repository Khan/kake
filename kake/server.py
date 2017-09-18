#!/usr/bin/env python
# TODO(colin): fix these lint errors (http://pep8.readthedocs.io/en/release-1.7.x/intro.html#error-codes)
# pep8-disable:E128

"""A simple python server for serving generated files from genfiles/.

You give it a request which is just a filename, and we build the
file if necessary and then send it back over the wire.

TODO(csilvers): Add support for sending logs to a tty on a per-request
basis.  That way, the client can do
   GET /genfiles/...?_log_to=/dev/ttyX

and our logs will go to our client's stdout.  This is particularly
useful if the client is dev-appserver; then it can see what kake is
doing.  Here are my ideas for how to do this:
1) Set up a thread-local var that holds the output-stream fd:
   http://stackoverflow.com/questions/1408171/thread-local-storage-in-python
2) Use a log-stream rewriter like we have in deploy_to_gae.py, and set all
   the streams to a new ThreadStream() class that has:
       def write(self, s): os.write(getattr(threadlocal, 'stream', 1), s)
3) Have middleware that checks for _log_to in the QUERY_STRING, and
   if present, set threadlocal.stream = os.open(_log_to, O_CREAT|O_APPEND)
   and do an os.close at the end.  If the open fails, or _log_to is not
   present, set threadlocal.stream to 1 (stdout).
4) server_client can then set _log_to appropriately.  To get the filename,
   try '/proc/self/fd/1' (this works in linux).  If that fails, try
   'lsof -p os.getpid() -d1 -a -Fn' and look at output.splitlines()[-1][1:]
   (this works on OS X, though not when the output is to a pipe).

The return code can be one of the following 3 values:
* If we are unable to bind to the given port, exit with code 2
* Any other exception, exit with code 1
* Quit normally due to /_ah/quit, exit with code 247 (I have no idea why)
"""
from __future__ import absolute_import

import datetime
import errno
import logging
import mimetypes
import os
import socket
import sys
import threading
import time
import traceback
import urllib


try:
    from third_party import flask
except ImportError:
    # We need the appengine dir for jinja2.  This requires setting up
    # the appengine path.
    sys.path.insert(0,
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from tools import appengine_tool_setup
    appengine_tool_setup.fix_sys_path()
    from third_party import flask

from . import project_root
from shared.cache import request_cache
import shared.util.local

from . import compile_rule
from . import filemod_db
from . import log
import kake.make
import thread_util


# Set up for request-locals, needed for RequestCacheMiddleware
shared.util.local.patch_start_new_thread()


app = flask.Flask('genfiles_server')


# A map of filename to mtime, which we use for if-modified-since
_LASTMOD_TIMES = {}


# A lock to keep us only building one thing at a time.  Otherwise, if
# two requests try to build the same file simultaneously, they could
# step on each others' toes since the filemod_db isn't updated until
# after a request has finished.
#
# You may wonder, given this lock, why make the server multi-threaded
# at all?  The reason is chrome will sometimes open multiple
# connections but not even bother sending any data on one of them.
# multithreading lets us process the other connections while the
# first one times out.  cf.
#    http://stackoverflow.com/questions/4893353/why-is-dev-appserver-py-app-engine-dev-server-hanging-waiting-for-a-request
_BUILD_LOCK = thread_util.Lock()


# The last time we synced the filemod-db in maybe_sync_filemod_db. This is
# protected by _BUILD_LOCK. Make sure to hold it when interacting with this.
last_filemod_db_sync = datetime.datetime.now()

# The minimum amount of time that needs to pass since the last filemod-db sync
# before we sync again.
FILEMOD_DB_SYNC_INTERVAL = datetime.timedelta(minutes=5)


def maybe_sync_filemod_db():
    """Sync filemod-db if enough time has passed.

    This should only ever be called when _BUILD_LOCK is held.
    """
    global last_filemod_db_sync

    time_since_last_sync = datetime.datetime.now() - last_filemod_db_sync
    should_sync = time_since_last_sync >= FILEMOD_DB_SYNC_INTERVAL

    if should_sync:
        filemod_db.sync()
        last_filemod_db_sync = datetime.datetime.now()


def _error_response(traceback_string):
    """Create a flask response for an error."""
    response = flask.Response(traceback_string, status=500,
                              mimetype='text/plain')
    response.headers.add('Access-Control-Allow-Origin', '*')
    return response


def _add_caching_headers(response, last_modified_time):
    """Add headers to a flask response controlling caching and access."""
    # This trio allows browser caching but forces revalidation every time:
    # http://stackoverflow.com/questions/5017454/make-ie-to-cache-resources-but-always-revalidate
    if last_modified_time:
        response.headers.add('Last-modified', last_modified_time)
    response.headers.add('Expires', '-1')
    response.headers.add('Cache-Control', 'must-revalidate, private')

    # This allows the dev-appserver to use our content.
    response.headers.add('Access-Control-Allow-Origin', '*')


def _maybe_add_sourcemap_header(response, filename, user_context):
    """If a sourcemap file exists, send a header telling about it."""
    if os.path.exists(project_root.join('genfiles', filename + '.map')):
        map_url = '/genfiles/%s.map?%s' % (filename,
                                           urllib.urlencode(user_context))
        # The standards say to use 'SourceMap' but older browsers only
        # recognize 'X-SourceMap'.  What the heck, send both.
        response.headers.add('SourceMap', map_url)
        response.headers.add('X-SourceMap', map_url)


# This must come before serve_genfile().
@app.route('/genfiles/<path:filename>.map')
def serve_sourcemap(filename):
    """The sourcemap is automatically created along with its file."""
    # This forces the .map file to get made too, if filename has one.
    non_map_response = serve_genfile(filename)
    # We'll say the .map file was last modified when its corresponding
    # .js/.css file was.
    last_modified_time = non_map_response.headers['Last-modified']

    abspath = project_root.join('genfiles', filename + '.map')
    try:
        with open(abspath) as f:
            content = f.read()
    except (IOError, OSError):
        flask.abort(404)

    # The consensus is that sourcemap files should have type json:
    #    http://stackoverflow.com/questions/18809567/what-is-the-correct-mime-type-for-min-map-javascript-source-files
    response = flask.Response(content, mimetype='application/json')
    _add_caching_headers(response, last_modified_time)

    # TODO(jlfwong): We always return a 200 for sourcemap files, when really we
    # should be returning a 304 sometimes based on the If-Modified-Since
    # header, like we do for non-sourcemap files.
    return response


@app.route('/genfiles/<path:filename>')
def serve_genfile(filename):
    """Serve a file from genfiles/, building it first if necessary.

    Arguments:
        filename: the filename to serve, relative to ka-root/genfiles/
        _force: force re-build of filename even if it's up-to-date
        (query parameters): all other parameters a=b, where a does not
           start with an underscore, are added to the kake
           context-dict (as {a: b}).  Parameters starting with _
           are *not* added to the context dict.
    """
    abspath = project_root.join('genfiles', filename)

    # This converts a werkzeug MultiDict to a normal dict.
    context = dict((k, v) for (k, v) in flask.request.args.iteritems()
                   if not k.startswith('_'))
    force = flask.request.args.get('_force', False)

    # The call to build below will modify the context, but we want
    # the original context to pass through to the SourceMap header.
    user_context = context.copy()

    # TODO(csilvers): use a file-watcher to remove files from
    #    _LASTMOD_TIMES as they change.  Then we don't need to call
    #    kake.make.build() at all if filename is in _LASTMOD_TIMES.
    #    To do this, we'd need to keep a map from filename to all
    #    files that depend on it.  We could also update filemod_db's
    #    mtime-cache via this file-watcher.
    try:
        with _BUILD_LOCK:
            # We pass in None for the checkpoint interval to prevent automatic
            # syncing of the filemod db, since we do that ourselves.
            file_changed = kake.make.build(os.path.join('genfiles', filename),
                                           context, force=force,
                                           checkpoint_interval=None)
            maybe_sync_filemod_db()
    except compile_rule.GracefulCompileFailure as e:
        # If the page is requested directly, just let the regular werkzeug
        # debugger display the error, otherwise serve the graceful response.
        if flask.request.accept_mimetypes.best == 'text/html':
            raise

        mimetype = mimetypes.guess_type(filename)[0]
        response = flask.Response(e.graceful_response, mimetype=mimetype)
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response
    except compile_rule.BadRequestFailure as failure:
        return flask.Response(
            "BAD REQUEST: %s\n" % failure.message,
            mimetype="text/*",
            status=400)
    except Exception:
        # If it's a normal http request, re-raise and get the cool
        # werkzeug debugger output.  If it's a javascript (XHR)
        # request, give our own output which is less cool but safer
        # to use with Access-Control-Allow-Origin.
        if flask.request.headers.get('Origin'):
            return _error_response(traceback.format_exc())
        raise

    if file_changed or filename not in _LASTMOD_TIMES:
        mtime = os.path.getmtime(abspath)
        dtime = datetime.datetime.fromtimestamp(mtime)
        _LASTMOD_TIMES[filename] = dtime.strftime("%a, %d %b %Y %H:%M:%S GMT")   # @Nolint(API expected English date-names)

    # If the file hasn't changed, and the etag matches, return a 304.
    client_mtime = flask.request.headers.get("If-Modified-Since")
    if client_mtime == _LASTMOD_TIMES[filename]:
        response = flask.Response(status=304)
        _add_caching_headers(response, _LASTMOD_TIMES[filename])
        _maybe_add_sourcemap_header(response, filename, user_context)
        return response

    with open(abspath) as f:
        content = f.read()

    response = flask.Response(content,
                              mimetype=mimetypes.guess_type(filename)[0])
    _add_caching_headers(response, _LASTMOD_TIMES[filename])
    # If we have a sourcemap, tell the client.
    _maybe_add_sourcemap_header(response, filename, user_context)

    return response


@app.route('/_api/outdir')
def outdir():
    """Simple handler to report the directory where we write our files.

    See: kake.server_client.start_server
    """
    return flask.Response(project_root.join('genfiles'), mimetype='text/plain')


# The /ping route is kept for backwards compatibility, but new users
# should use /_api/ping.
@app.route('/_api/ping')
@app.route('/ping')
def ping():
    """Simple handler used to check if the server is running.

    If 'genfiles' arg is specified, only respond with a 200 if our
    genfiles dir is the same as the specified one.

    See: kake.server_client.start_server
    """
    if flask.request.args.get('genfiles'):
        if project_root.join('genfiles') != flask.request.args.get('genfiles'):
            flask.abort(400)
            return

    return flask.Response('pong', mimetype='text/plain')


@app.route('/_api/quit')
def quit():
    # Taken from http://flask.pocoo.org/snippets/67/
    shutdown_func = flask.request.environ.get('werkzeug.server.shutdown')
    if shutdown_func is None:
        raise RuntimeError('Not running with the Werkzeug Server: %s'
                           % flask.request.environ)
    shutdown_func()
    return flask.Response('Server shutting down...', mimetype='text/plain')


# This route must come last!  Better would be to have an
# @app.route('/dir/<path>') for every dir under ka-app.root (or better
# yet, every dir with static content in it), but it's simpler to just
# have this as a catch-all rule at the end.  We'll give a 404 if the
# input isn't actually a file on our filesystem.
#
# NOTE: If we do want to limit what directories we serve, this command:
#    grep -oh 'url([^)]*' -r stylesheets | tr -d \'\" | grep -v data: | sort -u
# indicates that it would be enough to enable:
#    images/ fonts/ stylesheets/jqueryui-package/images/
# This content was also experimentally seen to be needed:
#    gae_mini_profiler/static/css gae_mini_profiler/static/js
# There's undoubtedly a lot more, hence the catch-all rule.

@app.route('/<path:filename>')
def serve_static_file(filename):
    """Serve static content (images, fonts, etc) referred to in .css/.js."""
    # Protect against exposing /etc/passwd!
    if '..' in filename or filename.startswith('/'):
        flask.abort(404)
    abspath = project_root.join(filename)

    try:
        response = flask.send_file(abspath, add_etags=False)

        # Let's add last-modified here.
        response.last_modified = os.path.getmtime(abspath)
        # Apparently make_conditional() has to happen after last-modified
        # is set?  So we can't just pass conditional=True to send_file.
        # cf. https://github.com/mitsuhiko/flask/issues/637
        response.make_conditional(flask.request)

        # Firefox refuses to load fonts if we don't set a CORS header.
        # Let's do that.  In fact, let's set it for all content that
        # it makes sense for: javascript and fonts (for firefox).
        cors_extensions = ('.otf', '.woff', 'woff2', '.eot', '.ttf',  # fonts
                           '.svg', '.js', '.css')
        if filename.endswith(cors_extensions):
            response.headers.add('Access-Control-Allow-Origin', '*')

        return response
    except IOError:
        flask.abort(404)


def _poll_to_die(port):
    """Poll periodically, and die when we're useful anymore.

    kake-server runs as a daemon, which means that it runs forever.
    Furthermore, a different kake-server must run for each genfiles
    directory you want to write to.  This opens up the possibility
    that you could end up with a zillion kake-servers on your machine,
    if you have a zillion genfiles/ dirs.

    Except you don't have a zillion genfiles/ dirs; you just have one.
    *Except* tests can create a new genfiles dir in /tmp.  We want
    the kakes that reside there to go away when that tmpdir is deleted.
    This is the routine that does that.  It polls periodically, and
    if the directory it's supposed to write to is gone, it dies.

    TODO(csilvers): also die if the kake-server has been idle for
    a certain amount of time (use middleware to keep track).
    """
    # Store a local copy of all global vars, since we may run after
    # shutdown.
    _time = time
    _os = os
    _os_path = os.path
    _log = log
    _project_root = project_root
    _urllib = urllib

    while True:
        _time.sleep(1)
        if not _os_path.exists(_project_root.root):
            reason = 'its directory %s is gone' % _project_root.root
            break

    # This will properly kill the child process (which exists when
    # we're running with the reloader) too.  This is hacky but it
    # works!
    _urllib.urlopen('http://localhost:%s/_api/quit' % port)
    _log.info('kake-server (pid %s): dying because %s'
              % (_os.getpid(), reason))


# By default, listen on all interface for both IPv6 and IPv4 requests
DEFAULT_HOST = '::'


# Apply middleware.
# TODO(benkraft): Should we apply other webapp middleware?
app.wsgi_app = shared.util.local.RequestLocalMiddleware(
    request_cache.RequestCacheMiddleware(app.wsgi_app))


def main(port=None, debug=False, host=DEFAULT_HOST):
    # We always set debug=True in the flask app, so we get good
    # exception info on error.  *Our* debug flag controls the
    # use_evalex param, which is what controls pdb-style debugging.
    use_reloader = True
    run_args = {'port': port,
                'threaded': True,
                'use_reloader': use_reloader,
                'debug': True,
                'host': host}
    if not debug:
        # We turn off pdb-style debugging, and allow connecting from anywhere.
        run_args.update({'use_evalex': False})

    # If running with the reloader, run in the reloader process but not child.
    if not use_reloader or not os.environ.get('WERKZEUG_RUN_MAIN'):
        t = threading.Thread(target=_poll_to_die, args=(port,))
        t.daemon = True
        t.start()

    try:
        app.run(**run_args)
    except socket.error as why:
        # EADDRINUSE is 48 on Mac OS X, but 98 elsewhere, like Ubuntu
        if why.errno == errno.EADDRINUSE:
            logging.exception('Cannot start server')
            # Convert to be 98 always, so upstream processes know about it
            # (namely server_client.py).
            sys.exit(98)
        raise


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', '-p', type=int, default=5000,
                        help=("What port to run the server on "
                              "(default %(default)s)"))
    parser.add_argument('--debug', '-d', action='store_true',
                        help=("Allow pdb-style debugging on exception. For "
                              "security, your browser must run on the same "
                              "machine as this server when using --debug."))
    parser.add_argument('--host', default=DEFAULT_HOST,
                        help="The address to bind to (default %(default)s)")
    log.add_verbose_flag(parser)
    args = parser.parse_args()

    # When server_main is run from tests (via dev_appserver_utils.py)
    # the test rootdir has a bunch of symlinks in it
    # (e.g. /tmp/testdir/javsacript -> khan/webapp/javascript).
    # Normally kake would complain about these 'out of tree' symlinks,
    # but for tests -- where the genfiles/ dir is torn down as soon as
    # the test is complete -- we don't care, so we just patch them
    # out.
    if os.environ.get('KAKE_FOR_TESTS'):
        project_root.relpath = lambda f: os.path.relpath(f, project_root.root)
        filemod_db._resolve_symlinks = lambda f: project_root.realpath(f)

    main(port=args.port, debug=args.debug, host=args.host)
