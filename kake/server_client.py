"""Routines for talking to a kake-server daemon, spawning one if necessary.

Routines in this module are meant to be used from dev-appserver.

This module defines these routines:
  start_server(): spawn a kake server as a daemon, if needed
  server_port(): returns port the current kake server is running on
  get(): given a file relative to ka-root, ask the kake server for its contents
  head(): given a file relative to ka-root, ask the kake server if it's changed
  clear_request_cache(): tells the server to check to see if something has
    changed within the request, to see if the file needs to be rebuilt.

It's possible for multiple kake-server daemons to be running on a
machine, each in a different directory.  start_server() and
server_port() are careful to only talk to the appropriate kake-server
for this dev-appserver (the one that is running in this directory).

get() and head() are superior to just talking to the kake server
manually, because they do intelligent caching and if-modified-since
processing.  Also, this way you don't have to figure out what port the
kake serve is on. :-)

Since this module is meant to be used from within a dev-appserver
context, it goes through hoops to escape the sandbox so it can
actually call subprocess.
"""

from __future__ import absolute_import

import contextlib
import logging
import os
import rfc822
import sys
import time

from google.appengine.api import urlfetch
from . import project_root
from shared import shared_globals
from shared.cache import request_cache

import subprocess_util


# Make sure we're actually running in a dev-appserver, and not in prod!
assert shared_globals.IS_DEV_SERVER, "Cannot use kake in prod!"


_DEFAULT_PORT = 5000

_PORT = None
_USE_FAKE_SERVER = False   # turned on for testing
_LASTMOD_CACHE = {}        # used to keep track of if-modified-since headers

# The number of seconds we'll wait for a response to kake by default
_DEFAULT_DEADLINE = 60


def start_server():
    """Start a kake-server daemon for us, if one is not already started.

    The "for us" means a kake-server that writes into our genfiles
    directory.  This means we look at all running kake-servers and ask
    them what directory they write their output to.  If none is found
    that writes to us, we start one up.  If one *is* found, we return
    the port the current daemon is listening on.  If we started a new
    one, we return the port it is listening on.
    """
    global _PORT
    _PORT = subprocess_util.spawn_daemon_server(
        'kake-server', _DEFAULT_PORT,
        [sys.executable,     # python
         project_root.join('kake', 'server_main.py'),
         '--port=%(port)s',
         '--verbose=%s' % os.getenv('KAKE_VERBOSE', 'info'),
         ],
        ping_url='/_api/ping?genfiles=%s' % project_root.join('genfiles'),
        # The only error we want to retry on is 98 ("port conflict").
        reject_fn=lambda p: p.wait() != 98)
    return _PORT


def start_fake_server():
    """Used for testing.

    When testing, we are not in a dev-appserver, so can build genfiles
    directly.  We do that, to avoid the overhead of starting up a
    server and talking to it.
    """
    global _USE_FAKE_SERVER, _PORT
    _USE_FAKE_SERVER = True
    _PORT = 5000   # we'll embed this fake port in urls we write
    logging.info('Faking out the kake server for tests')


def stop_fake_server():
    """Used for tests that need a real kake server.

    This undoes the effect of start_fake_server.  You can call
    start_server() after this to get a real server started up.  You
    can also call start_fake_server() again after this to go back to
    using the fake server.
    """
    global _USE_FAKE_SERVER, _PORT
    _USE_FAKE_SERVER = False
    _PORT = None


def is_fake_server():
    """True if start_fake_server was called (and not stop_fake_server)."""
    return _USE_FAKE_SERVER


def server_port():
    if not _USE_FAKE_SERVER:
        # Make sure a kake-server is running that we'll be able to talk to
        start_server()    # usually a no-op

    if _PORT is None:
        raise RuntimeError('Cannot talk to kake server: server not running')
    return _PORT


def _fake_fetch(url_path, headers):
    """'Fetches' when using the fake kake-server."""
    # We late-import here since these are not always ok to import in prod
    import kake.make
    abs_filename = project_root.join(url_path[1:])
    if url_path.startswith('/genfiles'):
        try:
            file_has_changed = kake.make.build(url_path[1:])
        except kake.make.BadRequestFailure as failure:
            logging.error(failure.message)
            return (failure.message, 400, {})
        except (IOError, kake.make.CompileFailure) as why:
            logging.error('Unable to build %s: %s' % (url_path[1:], why))
            return (None, 500, {})
        logging.info('Building %s' % url_path[1:])
    else:
        if not os.path.isfile(abs_filename):
            return (None, 404, {})

        file_has_changed = True
        ims = [v for (k, v) in headers.iteritems()
               if k.lower() == 'if-modified-since']
        if ims:
            parsed_ims = time.mktime(rfc822.parsedate(ims[0]))
            if os.path.getmtime(abs_filename) <= parsed_ims:
                file_has_changed = False

    if not file_has_changed:
        return ('', 304, {})

    with open(abs_filename) as f:
        return (f.read(), 200, {})


def _fetch(url_path, headers, deadline=_DEFAULT_DEADLINE):
    if _USE_FAKE_SERVER:
        return _fake_fetch(url_path, headers)

    r = urlfetch.fetch('http://localhost:%s%s' % (server_port(), url_path),
                       headers=headers, deadline=deadline)

    if r.status_code == 200:
        if 'Last-modified' in r.headers:
            # For next time.
            _LASTMOD_CACHE[url_path] = r.headers['Last-modified']
    elif r.status_code not in (304, 400, 404):
        raise RuntimeError('ERROR fetching %s from the kake server: (%s, %s)'
                           % (url_path, r.status_code, r.content))

    return (r.content, r.status_code, r.headers)


class NotFound(Exception):
    pass


class BadRequest(Exception):
    pass


def get(url_path, headers={}):
    """Given a url_path (/ + filename), get its content from kake.

    We send a request to the kake server, and send the response back
    to the user.  The kake server will (re)generate the file if
    necessary.

    When the response code is 200, the response body is returned as a string.
    When the response code is 400, a BadRequest exception is raised.
    When the response code is 404, a NotFound exception is raised.
    An AssertionError is raised for any other response code.
    """
    # We don't cache the results of previous get calls -- they might
    # be huge! -- so make sure we leave out the two headers that might
    # result in a 304 Not-Modified response (one via lastmod-time, one
    # via etags) rather than a content-containing 200 response.
    get_headers = {k: v for (k, v) in headers.iteritems()
                   if k.lower() not in ('if-modified-since', 'if-none-match')}
    (content, status_code, _) = _fetch(url_path, get_headers)
    assert status_code in (200, 400, 404), (status_code, url_path, get_headers)
    if status_code == 400:
        raise BadRequest(content)
    if status_code == 404:
        raise NotFound(content)
    return content


def _head_cache_key(url_path, headers):
    return 'head_%s_%s' % (url_path, headers)


def clear_request_cache(url_path, headers={}):
    """Clears the request cache for the call to head.

    This can be useful for tests, where we expect a file to change within that
    request.
    """
    cache_key = _head_cache_key(url_path, headers)
    request_cache.delete(cache_key)


def head(url_path, headers={}, deadline=_DEFAULT_DEADLINE):
    """Given a url_path (/ + filename), get its header info from kake.

    We send a request to the kake server, and send back a pair:
       (response-code, headers-dict)

    The response code will be one of: 304 (file not changed), 400
    (bad request), 404 (file not found), or 200.  We raise an exception if we
    get a response code other than those.
    """
    # We assume files don't change *within* a single request.  But
    # we can't use the normal request_cache decorator because even if
    # the head request returns 200, we want to cache a 304 (so future
    # requests see that this file hasn't changed during this request).
    cache_key = _head_cache_key(url_path, headers)
    retval = request_cache.get(cache_key)
    if retval is not None:
        return retval

    fetch_headers = headers.copy()
    if url_path in _LASTMOD_CACHE:
        fetch_headers.setdefault('If-modified-since', _LASTMOD_CACHE[url_path])

    (_, status_code, response_headers) = _fetch(url_path, fetch_headers,
                                                deadline=deadline)

    status_code_to_cache = (304 if status_code == 200 else status_code)
    request_cache.set(cache_key, (status_code_to_cache, response_headers))

    return (status_code, response_headers)


@contextlib.contextmanager
def rebuild_if_needed(url_path, headers={}, deadline=_DEFAULT_DEADLINE):
    """Rebuilds a file in a lock if necessary, yielding rebuild status.

    Example:
        with server_client.rebuild('/genfiles/foo') as rebuilt:
            if rebuilt:
                reload('genfiles/foo')

    This is semantically similar to calling head() and checking if the
    response is a 304 or not.  The main difference is that it holds a
    lock on the generated file within the context.  That way, if two
    different processes (such as is normally spawned by dev-appserver
    for two simultaenous requests) both execute this code at the same
    time, you don't have to worry about the reload() of process #1
    happening while process #2 is in the middle of regenerating foo.

    This is also semantically similar to doing:
        foo_content = server_client.get('/genfiles/foo')
        reload(foo_content)

    Using get() is simpler and should be preferred when possible.
    (However, it's not always possible, such as when reload() mustn't
    be called if foo hasn't changed.)

    Returns:
       Yields a bool in the 'as' clause, which is True if we rebuilt
       the input file, False else.  If the server returns a 404, we
       yield False.
    """
    # Sadly, it is not safe to clean up the lockfile in this function,
    # so these lockfiles may accumulate.  I purposefully put them in
    # /tmp rather than tempfile.tmpdir so they get cleaned up on
    # reboot (and also by tmpreaper, etc).  For more details on what
    # race conditions can occur if we were to unlink(lockfile) -- it
    # involves 3 processes -- see https://www.ruby-forum.com/topic/77244
    try:
        import fcntl
        lockfile = os.path.join('/tmp', 'lock.%s' % url_path.replace('/', '_'))
        with open(lockfile, 'w') as f:
            fcntl.lockf(f, fcntl.LOCK_EX)
            (rc, _) = head(url_path, headers, deadline=deadline)
            if rc == 400:
                raise BadRequest()
            if rc == 404:
                raise NotFound()
            yield rc != 304
    except (ImportError, IOError):
        # In dev-appserver, fcntl isn't available under the sandbox.
        # We could use memcache instead, but I've convinced myself we
        # don't need to do anything: the fact all our dev-appserver
        # instances are talking to the same kake server gives us all
        # the synchronization we need.
        (rc, _) = head(url_path, headers, deadline=deadline)
        if rc == 400:
            raise BadRequest()
        if rc == 404:
            raise NotFound()
        yield rc != 304
