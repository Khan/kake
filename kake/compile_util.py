"""This module provides utility functions used internally by kake."""

from __future__ import absolute_import

try:
    import cPickle
except ImportError:
    import pickle      # python3
import glob
import os
import re

from . import filemod_db
from . import log
from . import project_root


# We accept either {var} or {{var}}.
VAR_RE = re.compile(r'(?:\{){1,2}([^}]+)(?:\}){1,2}')


def has_glob_metachar(s):
    """We also check for backreferences."""
    return '*' in s or '?' in s or '[' in s or '(?P=' in s


def _extended_fnmatch_compile(pattern):
    """RE-ify *, ?, etc, and replace {var}/{{var}} by a named regexp.

    This turns each {var} into a regexp named-group named
    'brace_var', which matches [^/]*, and each {{var}} into a
    group named 'bracebrace_var', which matches .*.

    It also turns '*' into [^/]*, '?' into ., etc.  This is what
    fnmatch is supposed to do, but fnmatch turns '*' into '.*' which
    is not what we want.  We support '**', which turns into '.*',
    instead.

    For both * and ** we make sure that it doesn't match directories or files
    beginning with \.
    """
    retval = ''
    i = 0
    seen_braces = set()
    while i < len(pattern):
        if pattern[i] in '*?[':
            if retval.endswith('/'):
                # If we're at the start of a directory make sure we
                # don't match a dotfile (for *, **, ?, and [...]).
                retval += r'(?!\.)'

        if pattern[i] == '*':
            if pattern.startswith('**', i):
                # Match everything as long as it doesn't have a /. in it.
                retval += r'((?!/\.).)*'
                i += 1
            else:
                retval += '[^/]*'
        elif pattern[i] == '?':
            retval += '.'
        elif pattern[i] == '[':
            # Find the end of the [...]
            j = i + 1
            if pattern.startswith('!', j):     # [!...]
                j += 1
            if pattern.startswith(']', j):     # []...]
                j += 1
            j = pattern.find(']', j)
            if j == -1:   # must have something like 'a[b'
                retval += '\\['
            else:
                match = pattern[i + 1:j].replace('\\', '\\\\')
                if match[0] == '!':
                    match = '^' + match[1:]
                elif match[0] == '^':
                    match = '\\' + match
                retval += '[%s]' % match
                i = j
        elif pattern[i] == '{':
            if pattern.startswith('{{', i):
                j = pattern.find('}}', i)
                var = pattern[i + 2:j]
                groupname = 'bracebrace_%s' % var
                if groupname in seen_braces:   # then must match previous value
                    retval += '(?P=%s)' % groupname    # back-reference
                else:
                    retval += '(?P<%s>.*)' % groupname
                    seen_braces.add(groupname)
                i = j + 1
            else:
                j = pattern.find('}', i)
                var = pattern[i + 1:j]
                groupname = 'brace_%s' % var
                if groupname in seen_braces:   # then must match previous value
                    retval += '(?P=%s)' % groupname    # back-reference
                else:
                    retval += '(?P<%s>[^/]*)' % groupname
                    seen_braces.add(groupname)
                i = j
        else:
            retval += re.escape(pattern[i])
        i += 1

    return re.compile(retval + '$')


def _extended_glob(pattern):
    """Like normal glob.glob(), but supports **.  glob must be an abspath."""
    assert os.path.isabs(pattern), 'Glob "%s" must start with /' % pattern

    # Find the directory-prefix of glob that do not have have ** or
    # backreferences in them.  We can use the 'normal' glob.glob() on
    # the prefix.
    starstar_pos = pattern.find('**')
    backref_pos = pattern.find('(?P=')
    # If the glob has no extended characters in it, normal glob.glob() is fine.
    if starstar_pos == -1 and backref_pos == -1:
        return glob.glob(pattern)

    prefix_len = pattern.rfind('/', 0, min(starstar_pos, backref_pos))
    # prefix could be a glob pattern, so expand it to a list of directories.
    prefixes = glob.glob(pattern[:prefix_len])

    # Now we convert pattern to a regexp, and then simply look at
    # every file and directory under prefix and see if the result
    # matches pattern.  If so, we return it.
    #
    # TODO(csilvers): this could probably be made much more efficient.
    # One idea is to expand '**' into '*' + '*/*' + '*/**/*', and then
    # do the os-walk only looking at directory matches for the **
    # part, and using glob.glob() on the rest.
    retval = []
    glob_re = _extended_fnmatch_compile(pattern)
    for prefix in prefixes:
        for (root, dirs, files) in os.walk(prefix):
            for basename in dirs + files:
                filename = os.path.join(root, basename)
                if glob_re.match(filename):
                    retval.append(filename)
    return retval


def resolve_patterns(patterns, var_values):
    """Resolve file-patterns, which are a glob plus '{var}' substitutions."""
    retval = []
    for pattern in patterns:
        # Expand out {var}'s.
        pattern = VAR_RE.sub(lambda m: var_values[m.group(0)], pattern)
        # Expand out other glob patterns.
        if has_glob_metachar(pattern):
            # Make an absolute path for the glob, then relativize again.
            expanded_glob = _extended_glob(project_root.join(pattern))
            expanded_glob = [project_root.relpath(path)
                             for path in expanded_glob]
            expanded_glob.sort()
            retval.extend(expanded_glob)
        else:
            retval.append(pattern)
    return retval


class CachedFile(object):
    """A wrapper around a python data structure that is stored on disk.

    This is used when we calculate a data structure via one build rule
    that we want to use in other build rules.  The way we do that in
    kake is the first build rule calculates the data structure and
    writes it to disk, just like a normal build rule.  However, it
    also saves it in a CachedFile object.

    The other build rules still depend on the first build rule -- this
    way the data structure will get rebuilt when necessary -- but
    instead of parsing the build rule's output file themselves, they
    just access the CachedFile object.  This class ensures that such
    access will always reflect the most recent disk contents.

    This class isn't necessary in single-threaded apps, where the same
    process is building the data structure as is consuming it.
    However, in multi-threaded apps, we could run into the following
    situation:
    1) process A builds the data structure, storing it both in
       memory (on process A) and on disk.
    2) processes B and C both read the data structure off disk.
    3) process C updates the data structure on disk, and in its own
       local memory.
    4) process B now has a mismatch between what it has in memory, and
       what's on disk.

    This class makes sure (4) doesn't happen: the CachedFile object will
    notice the mismatch and update B's memory.

    Note: this class uses filemod_db to determine if a file has
    changed on disk or not, so be sure to keep the filemod_db mtime-
    cache up to date if you use this class.
    """
    # This is used for tests, so we can clear all the caches easily.
    _ALL_CACHED_FILES = []

    def __init__(self, filename):
        """Where the data structure is stored, relative to ka-root.

        filename: the filename where the data is stored.  This *must*
           be a pickled object.
        """
        self._filename = filename
        # Used to make sure our copy of filename is up to date.
        self._filename_file_info = None
        self._data = None
        CachedFile._ALL_CACHED_FILES.append(self)

    def get(self):
        """Return the data structure that's backed by filename."""
        # Never bother with the CRC here: mtime is enough.
        current_file_info = filemod_db.get_file_info(self._filename)
        if self._filename_file_info != current_file_info:
            # Not up to date, need to reload from disk.
            log.v2('Re-reading cached data from %s', self._filename)
            with open(project_root.join(self._filename), 'rb') as f:
                self._data = cPickle.load(f)
            self._filename_file_info = current_file_info
        return self._data

    def put(self, data):
        """Pickle data and store it in our file."""
        log.v3('Writing data to backing file %s', self._filename)
        self._data = data
        with open(project_root.join(self._filename), 'wb') as f:
            cPickle.dump(self._data, f, cPickle.HIGHEST_PROTOCOL)
        # Make sure filemod-db knows about the new contents.
        self._filename_file_info = filemod_db.get_file_info(self._filename,
                                                            bust_cache=True)

    def filename(self):
        return self._filename

    def clear(self):
        """Reset the data; used mostly for tests."""
        self._filename_file_info = None
        self._data = None

    @classmethod
    def clear_all(cls):
        """Clears all the CachedFiles that have ever been created."""
        for cached_file in cls._ALL_CACHED_FILES:
            cached_file.clear()


def reset_for_tests():
    CachedFile.clear_all()
