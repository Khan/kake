"""Database for querying and updating last-modified information about files.

This is intended to be used by the deploy process.  Many deploy steps
can be short-circuited if the input is unchanged from the last time
deploy was run.  We use mtime + filesize to determine if a file is
unchanged (md5 or another crc would be more accurate, but it's too
slow, so we only use it if specially requested).

You use it like this:
       with filemod_db.needs_update(outfile, infiles, 'fn') as changed_files:
           if changed_files:
               _update_outfile(outfile, infile, changed_files, other_args)
           else:
               print 'Not updating %s, already up to date' % outfile

changed_files is a list of files that have changed since the last time
needs_update is called, and may be useful for incremental updates.  (If
it includes outfile, this means the output is entirely unreliable and
should be recomputed from scratch.)  It is None if nothing has changed.

The basic control flow is this:
   1) Figure out the inputs to some generated file
   2) Calculate the mtime's and file sizes of the input files and
      the output file (if it exists on disk).
   3) Look up the mtime's and file sizes of the input files the
      previous time the output file was generated.  Do the same
      for the output file.
   4) If all the mtimes/files sizes are the same, then skip
      re-generating the output file.
   5) Otherwise, generate the output file, and update the mtimes and
      file sizes of all files in the database.

The database is a key/value store, where the key is the output file,
and the values are the mtimes (and filesizes) of all the relevant
files (input files + output file), as of the last time this output
file was generated.  The database stores all filenames relative to
ka-root.

There is a lower level UI than the context manager, which maps more
directly to this control flow: you give it the filenames of a bunch of
input files, and the filename of the output file, and it tells you
whether you need to regenerate the output file or not.  (A slightly
lower-level API will tell you *why* you need to regenerate the output
file: that is, what files have changed.)

There is also a routine to update the mtimes of input files after the
user has re-generated the output file.

Like the logging class, you can create your own FilemodDb object,
which has its own associated DB, or you can use the free functions in
this file, which work on a singleton FilemodDb object.

The filemod-db class uses the kake logger for output, so if you want
verbose output, use log.set_log_level().

NOTE: the db model is that multiple threads/processes can access the
db at once, but only ONE thread/process should change a given row
(that is, information for a single output file).  If multiple
processes try to modify the information for the same output file, only
one will win.  For efficiency, all updates are stored in memory and
only flushed when the program exits.
"""

from __future__ import absolute_import

import atexit
try:
    import cPickle
except ImportError:
    import pickle      # python3
import contextlib
import fcntl
import os
import timeit
import zlib

from . import project_root
from . import log


# These are for the default, singleton db .  _DB is (will be) an InMemoryDB.
# _DB_FILENAME is relative to ka-root.
_DB_FILENAME = os.path.join('genfiles', '_filemod_db.pickle')
_DB = None

# A cache of files' current mtimes and sizes (as opposed to their
# 'creation-time' mtimes and sizes, which are stored in _DB).  We
# assume that whenever a file is modified, this module is informed
# (via set_up_to_date), so the entries in this cache are always
# accurate; that is, that we never modify a file's mtime behind this
# module's back!
# The keys to this map are filenames, relative to ka-root.
# The values are a tuple: (mtime, size, optional checksum).
# The checksum is a zlib.crc32() checksum, and may be None.
_CURRENT_FILE_INFO = {}

# A cache from mtime-and-size to crc, used whenever a crc is computed.
# We use this cache to avoid recomputing crc's in the case a file's
# mtime hasn't changed.  (We assume if a file has the same mtime and
# size as before, it will also have the same contents; the crc is for
# times when the contents are the same even though the mtime differs.)
_SIZE_AND_MTIME_TO_CRC_MAP = {}

# Maps a filename to its os.path.realpath() equivalent (with symlinks
# resolved).  realpath() is slow -- _resolve_symlinks() takes 70% of
# cpu time of a noop build -- so it makes sense to cache this.
_NORMALIZE_CACHE = {}


class InMemoryDB(object):
    """A simple db that writes itself to disk on program exit.

    This db supports a very simple (and special-purpose) transaction
    scheme.  For a given key, you can start a transaction with an
    updated value.  get() will still get the old value, but
    get_transaction() will get the new value.  update_transaction()
    can be used to modify the new value.  Once you commit the
    transaction, get() will return the new value, and
    get_transaction() will raise an exception.
    """
    def __init__(self, filename):
        if not os.path.exists(os.path.dirname(filename)):
            os.makedirs(os.path.dirname(filename))

        self.filename = filename
        try:
            with open(self.filename) as f:
                fcntl.lockf(f, fcntl.LOCK_SH)
                self.map = self._unlocked_load_and_unpickle(f)
        except (IOError, OSError):
            self.map = {}

        # keys and values currently in a transaction.
        self.transaction_map = {}

        # Those keys that have been modified since file-load time, and
        # thus need to be modified on-disk when sync() is called.
        self.keys_to_update = set()

    def __del__(self):
        # During process tear-down sync() can fail with
        #    Exception TypeError: "'NoneType' object is not callable" in
        #    <bound method InMemoryDB.__del__ of ...> ignored
        # because functions that we want to call have already been destroyed.
        try:
            self.sync()
        except TypeError:
            pass

    def __iter__(self):
        """Yields (key, value) tuples."""
        return self.map.iteritems()

    def _unlocked_load_and_unpickle(self, file_obj):
        try:
            return cPickle.load(file_obj)
        except EOFError:      # means file_obj is the empty file
            return {}

    def get(self, key):
        """Get value for key from the db, or None if not present."""
        return self.map.get(key, None)

    def put(self, key, value):
        self.map[key] = value        # for future 'get' calls
        self.keys_to_update.add(key)

    def start_transaction(self, key, new_value):
        assert key not in self.transaction_map, (
            "Cannot nest transactions: %s" % key)
        self.transaction_map[key] = new_value

    def transaction_keys(self):
        """Return list of keys in the current transaction."""
        return self.transaction_map.keys()

    def get_transaction(self, key):
        assert key in self.transaction_map, (
            "Must call start_transaction() before get_transaction()")
        return self.transaction_map[key]

    def update_transaction(self, key, new_value):
        assert key in self.transaction_map, (
            "Must call start_transaction() before update_transaction()")
        self.transaction_map[key] = new_value

    def commit_transaction(self, key):
        assert key in self.transaction_map, (
            "Must call start_transaction() before commit_transaction()")
        self.put(key, self.transaction_map[key])
        del self.transaction_map[key]

    def abandon_pending_transactions(self):
        self.transaction_map = {}

    def sync(self):
        # It is suspected that we might be OOM here sometimes, so we are adding
        # this log line to determine that for sure.
        log.v1('About to flush filemod-db "%s"', self.filename)

        # All pending transactions are discarded at sync time.
        self.abandon_pending_transactions()

        # The contents of the db may have changed on disk since we
        # loaded them, so we store the db by reading in the contents,
        # merging in our changes, and writing the new version, all
        # under a lock.
        if not self.keys_to_update:
            return

        # Even though we only open for reading, we have to use mode `a`
        # so we can acquire an exclusive lock on it.
        with open(self.filename, 'a+') as f:
            locking_start_time = timeit.default_timer()
            fcntl.lockf(f, fcntl.LOCK_EX)
            locking_total_time = timeit.default_timer() - locking_start_time

            updating_start_time = timeit.default_timer()
            f.seek(0)
            updated_map = self._unlocked_load_and_unpickle(f)
            for k in self.keys_to_update:
                updated_map[k] = self.get(k)    # doing the updating...

            with open(self.filename + '.tmp', 'w') as tmp:
                cPickle.dump(updated_map, tmp,
                             protocol=cPickle.HIGHEST_PROTOCOL)
            updating_total_time = timeit.default_timer() - updating_start_time

            os.unlink(self.filename)
            os.rename(self.filename + '.tmp', self.filename)

        self.map = updated_map
        self.keys_to_update = set()

        log.warning('Flushed filemod-db "%s" (locking took %.2f sec, updating '
                    'took %.2f sec)',
                    self.filename, locking_total_time, updating_total_time)


def _joinrealpath(path, rest):
    """A version of posixpath._joinrealpath, optimized for kake.

    Joins two paths, normalizing and eliminating any symbolic links
    encountered in the second path.
    """
    assert not os.path.isabs(rest), (
        'Symlinks for kake must be relative: %s %s' % (path, rest))
    while rest:
        name, _, rest = rest.partition(os.sep)
        newpath = os.path.normpath(os.path.join(path, name))
        assert not newpath.startswith('..' + os.sep), (
            'Symlinks must point within ka-root: %s/%s' % (path, name))
        if not os.path.islink(project_root.join(newpath)):
            path = newpath
            continue
        # Resolve the symbolic link.
        if newpath in _NORMALIZE_CACHE:
            # Already seen this path.
            path = _NORMALIZE_CACHE[newpath]
            if path is not None:
                # Use cached value.
                continue
            # The symlink is not resolved, so we must have a symlink loop.
            raise OSError('Symlink loop: %s' % os.path.join(newpath, rest))
        _NORMALIZE_CACHE[newpath] = None   # not-yet-resolved symlink
        path = _joinrealpath(path, os.readlink(project_root.join(newpath)))
        _NORMALIZE_CACHE[newpath] = path   # resolved symlink

    return path


def _resolve_symlinks(filename):
    """Return filename, relative to ka-root, resolving symlinks first."""
    # Surprisingly, this one function takes 70% of the time of a no-op
    # build.  Optimize it with some simple caching.
    if filename not in _NORMALIZE_CACHE:
        _NORMALIZE_CACHE[filename] = _joinrealpath('', filename)
    return _NORMALIZE_CACHE[filename]


def _compute_crc(file_obj):
    """To minimize memory use, compute the CRC in chunks."""
    crc = 31415            # can initialize to any value
    while True:
        content = file_obj.read(1048576)   # 1M at a time
        if not content:
            break
        crc = zlib.crc32(content, crc)
    return crc


def get_file_info(filename, bust_cache=False, compute_crc=False):
    """Return mtime and size for filename (which is relative to ka-root).

    If filename doesn't exist, return (None, None, None).  By default,
    we look in _CURRENT_FILE_INFO before looking at the filesystem;
    this can be overridden with bust_cache.

    If filename is a symlink, we return information for the symlink
    itself, not the file it's pointing to.
    """
    retval = _CURRENT_FILE_INFO.get(filename, None)
    # We need to recompute if the user asks us to, or if all the
    # information we need isn't present.
    if (retval is None) or (bust_cache) or (compute_crc and retval[2] is None):
        # For any os (filesystem) calls, we want an absolute path.
        abspath = project_root.join(filename)
        try:
            s = os.stat(abspath)
            if compute_crc:
                cache_key = (filename, s.st_size, s.st_mtime)
                crc = _SIZE_AND_MTIME_TO_CRC_MAP.get(cache_key)
                if crc is None or bust_cache:     # ah well, have to compute it
                    with open(abspath) as f:
                        crc = _compute_crc(f)
                    _SIZE_AND_MTIME_TO_CRC_MAP[cache_key] = crc
            else:
                crc = None
            _CURRENT_FILE_INFO[filename] = (s.st_mtime, s.st_size, crc)
        except OSError:
            _CURRENT_FILE_INFO[filename] = (None, None, None)
        retval = _CURRENT_FILE_INFO[filename]
    return retval


def file_info_equal(file_info_1, file_info_2):
    """Return true if the two file-infos indicate the file hasn't changed."""
    # Negative matches are never equal to each other: a file not
    # existing is not equal to another file not existing.
    if (None, None, None) in (file_info_1, file_info_2):
        return False

    # Equal if the size and the mtimes match.
    if file_info_1[:2] == file_info_2[:2]:
        return True
    # Even if mtimes don't match, they're equal if the size and the
    # crcs match.  But we have to be careful, since crcs are optional,
    # so we don't do this test if the crcs are None.
    if file_info_1[2] is not None and file_info_1[1:] == file_info_2[1:]:
        return True
    return False


class FilemodDb(object):
    def __init__(self, db_filename):
        self.db_filename = db_filename
        self._db = InMemoryDB(db_filename)

    def __del__(self):
        # During process tear-down sync() can fail with
        #    Exception TypeError: "'NoneType' object is not callable" in
        #    <bound method FilemodDb.__del__ of ...> ignored
        # because functions that we want to call have already been destroyed.
        try:
            self._db.sync()
        except TypeError:
            pass

    def can_symlink_to(self, outfile_name, symlink_candidate):
        """Return True if symlink_candidate is 'equivalent' to outfile_name.

        Sometimes, we have two output files that have the exact same
        inputs.  (This happens, for instance, with en/infile ->
        en/outfile and pt/infile -> pt/outfile, and en/infile and
        pt/infile are identical -- maybe pt/infile is even a symlink
        to en/infile!)  In that case, we don't need to do the same
        work twice, we can make outfile2 be a 'copy' of outfile1.
        That is, when we are asked to make outfile2 up to date, we can
        satisfy that by copying outfile1 to outfile2.  (In practice,
        we symlink instead of copying or hard-linking, to keep the
        relationship clearer.)

        (On the other hand, there's the bad situations in.handlebars
        -> in.js and in.handlebars -> in.py, and even though the two
        outfiles share the same infile, they should not be linked to
        each other.  This is one reason why you have to specify the
        symlink candidate yourself than trying to find it
        automatically.)

        This function checks whether symlink-candidate is an existing,
        up-to-date file that outfile can symlink to.  To determine
        this, it looks at the filemod-db entries for the two files.
        It also makes sure symlink_candidate is up to date and is not
        itself part of a filemod-db transaction (that is, the
        filemod-db data for it is up-to-date).

        NOTE: This can only be called *after* changed_files() indicates
        the file is not up to date.  And you must call set_up_to_date()
        after symlinking.

        Arguments:
            outfile_name: the outfile that we are testing if it is up
              to date, relative to ka-root.
            symlink_candidate: the file that outfile_name might be
              equivalent to, relative to ka-root.

        Returns:
            True if symlink_candidate is 'equivalent' to outfile.
        """
        if symlink_candidate == outfile_name:
            # Symlinking to ourself is *not* ok.
            return False

        if symlink_candidate in self._db.transaction_keys():
            # Don't try to symlink if our target file is being updated
            # itself; we can't tell whether we're 'equivalent' to it
            # or not if it's currently being modified!
            return False

        symlink_mtime_map = self._db.get(symlink_candidate)
        if symlink_mtime_map is None:
            # We don't have up-to-date-ness information on the candidate.
            return False

        # outfile_name is currently in a transaction (changed_files()
        # returned false), so we have to get its mtime info that way.
        outfile_mtime_map = self._db.get_transaction(outfile_name)

        # Get a copy of mtime_maps that do not include the output file.
        pruned_outfile_mtime_map = outfile_mtime_map.copy()
        pruned_outfile_mtime_map.pop(outfile_name)
        pruned_symlink_mtime_map = symlink_mtime_map.copy()
        pruned_symlink_mtime_map.pop(symlink_candidate)

        if (frozenset(pruned_outfile_mtime_map.keys()) !=
                frozenset(pruned_symlink_mtime_map.keys())):
            # This means symlink_candidate has different deps that we
            # do (common case).
            return False

        for (k, v) in pruned_outfile_mtime_map.iteritems():
            # This means symlink_candidate has the same deps as us,
            # but those deps aren't up to date.
            # This holds because infile_map has the *current* mtimes of
            # the input files, and if the (db-based) values of
            # symlink_candidate don't match, that means it's out of date.
            symlink_v = pruned_symlink_mtime_map.get(k, (None, None, None))
            if not file_info_equal(v, symlink_v):
                return False

        if not file_info_equal(get_file_info(symlink_candidate),
                               symlink_mtime_map[symlink_candidate]):
            # This finishes off the 'other_outfile is up-to-date' check.
            # We tested all the input files are up-to-date above, now we
            # need to test the output file too.  Note we never bother to
            # do the crc check here; we assume that as a generated file,
            # other_outfile will not suffer from the
            # 'git-changed-my-mtime' problem.
            return False

        return True

    def changed_files(self, outfile_name, *infile_names, **kwargs):
        """Return a set of files that have changed since the last call.

        'Last call' here means the last call to changed_files() with
        this given outfile_name.

        An infile_name is in the return set if its mtime or size is
        different from the last time changed_files(outfile_name, ...)
        was called.  This means the input has changed since last call,
        so we need to update the output.

        outfile_name is in the return set if *its* mtime or size is
        different, or if its context has changed, or if the outfile
        doesn't exist at all.  In all these cases -- for different
        reasons -- the caller should regenerate the output file from
        scratch.  (If only input files have changed, it's possible an
        in-place update to outfile would suffice, for instance if
        outfile is a .zipfile.)  In the first case, outfile changed
        'behind our back', and we can't trust the file contents
        anymore.  In the second case, the code author is telling us
        the code used to generate outfile has changed, so we can't do
        an incremental update on it for that reason.  And in the third
        case, of course, we're creating the outfile for the first
        time, so there's nothing to incrementally update.

        If an infile is added (it's in infile_names this call, but
        wasn't in the last), it is in the return set.  Likewise, if an
        infile file is deleted (it's not in infile_names now, but was
        in the last call to changed_files()), it is in the return set.
        Thus, it is possible that the output of this function is not a
        strict subset of the input.

        In the case we return some files (meaning something has
        changed), we also start a transaction in the filemod-db to
        update these files with the new mtime information.  This
        transaction is committed by a call to set_up_to_date(), so if
        changed_files returns non-empty, the caller is responsible for
        calling set_up_to_date().

        Arguments:
            outfile_name_as_given: the filename of the being created or
              updated, relative to ka-root.
            infile_names_as_given: rest of args are the filenames
              outfile depends on, relative to ka-root.
            force (kwarg): if True, create a new transaction (requiring a
               call to set_up_to_date) even if no files have changed.
               This is used if we plan to update outfile even if no
               infiles have changed (hence we're 'force'ing the
               outfile to change).
            compute_crc (kwarg): if True, look at the crc of the file
               along with the size and mtime.  Even if the mtime doesn't
               match the database, if the crc matches we'll declare
               the file unchanged.  Calculating the crc can be slow,
               especially for large files, but changing git branches
               destroys mtimes, so it can be worth it.
            context (kwarg): a string.  Required.  This is treated as
               a 'fake' dependency (a dependency that is not a file),
               and is used to encapsulate all dependencies that are
               not captured by files (for instance, a dependency on
               the python function that is used to process input to
               output).  It is used to distinguish two db entries that
               would otherwise look identical (because they have the
               same dependencies listed but differ in some implicit
               dependency).

        Returns:
           A set of files that have changed since the last call to
           changed_files(outfile_name, ...).  When possible -- that
           is, except for files removed from inputs between last
           call and this call -- we will return filenames written
           exactly the same as the input: we won't do filename
           renormalization.

           Example:
              changed_files('foo', 'bar', 'baz')
              print changed_files('foo', 'bar', 'bang')
           then we will print set(['baz', 'bang']).
        """
        retval = set()

        force = kwargs.pop('force', False)
        compute_crc = kwargs.pop('compute_crc', False)
        context = kwargs.pop('context', None)

        # For input files, we want to look at the *canonical* file
        # name, so we resolve all symlinks.  But we keep the
        # original name around so we can return it.
        name_map = {outfile_name: outfile_name}
        for infile_name in infile_names:
            name_map[_resolve_symlinks(infile_name)] = infile_name

        # Get the info from last time outfile was updated, and the
        # current info.
        old_mtime_map = self._db.get(outfile_name)
        new_mtime_map = {f: get_file_info(f, compute_crc=compute_crc)
                         for f in name_map}
        if context is not None:
            new_mtime_map['//context//'] = (context, None, None)
            name_map['//context//'] = '//context//'

        # Figure out all the ways a file can change.
        if old_mtime_map is None:
            log.v2('assuming %s not up to date: no filemod-db info',
                   outfile_name)
            retval.add(outfile_name)
        elif not file_info_equal(old_mtime_map[outfile_name],
                                 new_mtime_map[outfile_name]):
            log.v2("%s not up to date: "
                   "its timestamp doesn't match filemod-db", outfile_name)
            log.v4('   -- previous data: %s, new data: %s'
                   % (old_mtime_map[outfile_name],
                      new_mtime_map[outfile_name]))
            retval.add(outfile_name)
        elif (old_mtime_map.get('//context//') !=
              new_mtime_map.get('//context//')):
            # If the context has changed, then we act as if outfile is
            # entirely unreliable, since we have to assume the worst from
            # this 'implicit dependency' change.
            log.v2('%s not up to date: its context has changed', outfile_name)
            log.v4('   -- old context: %s, new context: %s'
                   % (old_mtime_map.get('//context//'),
                      new_mtime_map.get('//context//')))
            retval.add(outfile_name)
        else:
            for (infile_name, new_info) in new_mtime_map.iteritems():
                old_info = old_mtime_map.get(infile_name)
                if old_info is None:
                    log.v2('%s not up to date: %s not in the filemod-db',
                           outfile_name, infile_name)
                    retval.add(name_map[infile_name])
                elif not file_info_equal(old_info, new_info):
                    log.v2('%s not up to date: %s has changed',
                           outfile_name, infile_name)
                    log.v4('   -- previous data: %s, new data: %s'
                           % (old_info, new_info))
                    retval.add(name_map[infile_name])

            removed_files = set(old_mtime_map).difference(new_mtime_map)
            for removed_file in removed_files:
                log.v2('%s not up to date: %s is no longer an input',
                       outfile_name, removed_file)
                # We don't have a name-map for this file, since it's not
                # in infile_names, so we just give the normalized name.
                retval.add(removed_file)

        if force:
            # In 'force' mode, pretend that the outfile has been deleted.
            log.v2('%s not up to date: "force" was specified', outfile_name)
            retval.add(outfile_name)

        if retval:
            # If outfile is a symlink, delete it.  Otherwise, it's too
            # easy for the code that is 'fixing' outfile to write to
            # some other file instead.  We special-case executables in
            # node_modules/.bin, which are 'ok' symlinks.
            # TODO(csilvers): figure out a way to be more principled.
            if (os.path.basename(os.path.dirname(outfile_name)) != '.bin' and
                    os.path.islink(project_root.join(outfile_name))):
                os.unlink(project_root.join(outfile_name))

            # Start a transaction to update the _DB to have the new
            # mtime information.  This transaction won't be committed
            # until set_up_to_date() is called.
            self._db.start_transaction(outfile_name, new_mtime_map)

        if not retval:
            log.v2('%s is up to date', outfile_name)

        return retval

    def set_up_to_date(self, *outfile_names):
        """Update the db with new mtimes/sizes once outfile is regenerated."""
        # For efficiency, we can update many outfiles at once.
        for outfile_name in outfile_names:
            try:
                new_file_info = self._db.get_transaction(outfile_name)
            except (NameError, AssertionError):
                raise KeyError(
                    '%s: Must call changed_files()'
                    ' before calling set_up_to_date()' % outfile_name)

            # The new file info was set in changed_files, and were
            # correct at that time.  The only thing that's changed
            # since the is the output file, so we update that now.
            # If the output file had a crc before, calculate a new one again.
            compute_crc = (
                new_file_info.get(outfile_name, (None, None, None))[2]
                is not None)
            new_file_info[outfile_name] = get_file_info(
                outfile_name, bust_cache=True, compute_crc=compute_crc)

            # Now store the new mtime information in the cache.
            self._db.update_transaction(outfile_name, new_file_info)
            self._db.commit_transaction(outfile_name)

    def abandon_pending_transactions(self):
        self._db.abandon_pending_transactions()

    def sync(self):
        """Force the db to write to disk (also happens on sys.exit)."""
        self._db.sync()

    @contextlib.contextmanager
    def needs_update(self, outfile_name, infile_names, context,
                     force=False, compute_crc=False):
        """Arguments:
            outfile_name: the name of the output file to check on, relative
              to ka-root.
            infile_names: a list of dependencies of outfile_name, relative
              to ka-root.  If outfile or any of the infiles has changed
              since this function was last called, it means we should update
              outfile.
            context: a string.  This is treated as a 'fake' dependency
              (a dependency that is not a file), and is used to
              encapsulate all dependencies that are not captured by
              files (for instance, a dependency on the python function
              that is used to process input to output).  It is used to
              distinguish two db entries that would otherwise look
              identical (because they have the same dependencies
              listed but differ in some implicit dependency).
            force: if True, call update_fn() even if the file
              doesn't need to be updated.
            compute_crc: if True, look at the crc of each file along
              with the size and mtime.  Even if the mtime doesn't match the
              database, if the crc matches we'll declare the file
              unchanged.  Calculating the crc can be slow, especially for
              large files, but changing git branches destroys mtimes, so
              it can be worth it.
        """
        changed = self.changed_files(outfile_name, *infile_names,
                                     force=force,
                                     compute_crc=compute_crc,
                                     context=context)
        # Force means 'pretend the outfile has changed even if it hasn't.'
        if force:
            changed = [outfile_name]

        if changed:
            yield changed
            self.set_up_to_date(outfile_name)
        else:
            yield None


def _singleton_db():
    """Open the db file for reading and writing."""
    global _DB

    # If the filemod-db is using our old name, convert it first.
    # Since rename is atomic (inside a single fs), this is easy.
    # TODO(csilvers): remove this code after 1 June 2016
    if os.path.exists(project_root.join('genfiles', 'filemod_db.pickle')):
        try:
            os.rename(project_root.join('genfiles', 'filemod_db.pickle'),
                      project_root.join(_DB_FILENAME))
        except OSError as why:
            if why.errno == 2:      # No such file or directory
                # A concurrent process must have done the renaming for us
                pass

    # When testing, ka-root can change between one call to _singleton_db
    # and the next.  When that happens, we want a new db.
    if _DB is None or _DB.db_filename != project_root.join(_DB_FILENAME):
        _DB = FilemodDb(project_root.join(_DB_FILENAME))
    return _DB


def can_symlink_to(*args, **kwargs):
    return _singleton_db().can_symlink_to(*args, **kwargs)


def changed_files(*args, **kwargs):
    return _singleton_db().changed_files(*args, **kwargs)


def set_up_to_date(*args, **kwargs):
    return _singleton_db().set_up_to_date(*args, **kwargs)


def sync(*args, **kwargs):
    return _singleton_db().sync(*args, **kwargs)


def abandon_pending_transactions(*args, **kwargs):
    return _singleton_db().abandon_pending_transactions(*args, **kwargs)


@atexit.register
def _atexit_sync():
    # This function isn't meant to be called manually.
    #
    # Normally a db is sync'ed in __del__. However, __del__ is called
    # on the singleton instance when the interpreter exits, and this
    # may error when the global libraries that sync() relies on have
    # already been cleaned up. So, we sync before cleanup if a
    # singleton instance exists.
    if _DB is not None:
        sync()


def needs_update(*args, **kwargs):
    return _singleton_db().needs_update(*args, **kwargs)


def clear_mtime_cache():
    """For when you suspect file contents may have changed from under you."""
    _CURRENT_FILE_INFO.clear()
    # Not only the file contents may have changed, but their symlinks.
    _NORMALIZE_CACHE.clear()


def reset_for_tests():
    """For tests that use the filemod_db, reset the state between tests."""
    global _DB
    _DB = None
    _CURRENT_FILE_INFO.clear()
    _SIZE_AND_MTIME_TO_CRC_MAP.clear()
    _NORMALIZE_CACHE.clear()
