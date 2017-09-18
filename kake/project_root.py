"""Provides easy access to the project root, and easy abspath functionality.

All kake input files and output files are specified relative to the
"project root".  This file defines what that project root is.

We define it to be the first directory above us that defines a
.git/.hg/.svn directory (not file or symlink, so we don't count
submodules).  If that fails we see if a `genfiles` path exists.
But you can override this by setting the KAKE_PROJECT_ROOT
environment variable, which should be an absolute path pointing to
the project-root dir.
"""

from __future__ import absolute_import

import os
import sys
import tempfile


if 'KAKE_PROJECT_ROOT' in os.environ:
    root = os.environ['KAKE_PROJECT_ROOT']
    assert os.path.isabs(root), (
        "KAKE_PROJECT_ROOT must be an absolute path, not '%s'" % root)
else:
    root = os.path.dirname(os.path.abspath(__file__))
    # TODO(csilvers): use GENDIR instead of hard-coding "genfiles" here.
    while not any(os.path.isdir(os.path.join(root, f))
                  for f in ('.git', '.hg', '.svn', 'genfiles')):
        root = os.path.dirname(root)
        if os.path.dirname(root) == root:
            # We got to the top of the filesystem without finding a
            # match.  We'll make people specify
            sys.exit("You must specify the KAKE_PROJECT_ROOT envvar since "
                     "we cannot figure it out automatically")


# If we mock out project_root, this lets us still determine what the original
# project_root was.
real_project_root_for_tests = root


def join(*args):
    """Return an absolute path starting at project-root.

    Arguments:
       *args: arguments to os.path.join, which are taken to be
           directory paths relative to ka-app root.

    Returns:
       An absolute path.  e.g. join('foo', 'bar') might return
       '/home/csilvers/khan/webapp/foo/bar'.
    """
    return os.path.join(root, *args)


def relpath(abspath):
    """Given an absolute path under project-root, return the relative path."""
    assert abspath.startswith(root), (
        'FATAL ERROR for relpath: "%s" is not under "%s"' % (abspath, root))
    return os.path.relpath(abspath, root)


def maybe_relpath(abspath):
    """relpath() if abspath is under project-root, or abspath otherwise."""
    relpath = os.path.relpath(abspath, root)
    if relpath.startswith('..' + os.sep):     # are outside root
        return abspath
    return relpath


def realpath(rpath):
    """Path relative to project-root -> Real path relative to project-root.

    This resolves symlinks.
    """
    return relpath(os.path.realpath(join(rpath)))


def is_mocked():
    """Return True if project_root is currently mocked to point into /tmp."""
    # This assumes you always use the tempfile module when mocking
    # project_root.
    try:
        tempdir = tempfile.gettempdir()
    except NotImplementedError:
        # In appengine, we're sandboxed so that tempfile's methods aren't
        # available
        tempdir = '/tmp'
    return root.startswith(os.path.realpath(tempdir))
