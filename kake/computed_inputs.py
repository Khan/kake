# TODO(colin): fix these lint errors (http://pep8.readthedocs.io/en/release-1.7.x/intro.html#error-codes)
# pep8-disable:E124,E127
"""Base class and useful usable-classes for managing computed inputs.

Computed inputs are an advanced feature used with complex compile
rules, where the dependencies of a rule may change over time.  For
instance, for foo.min.css, we inline all images in foo.css.  So
foo.min.css needs to change when foo.css changes, but also if any of
the images referenced by foo.css change.  So the dependencies that
foo.min.css have change whenever foo.css changes.

Consider this case:
   1) foo.css has the line 'background-image: url(baz.gif)'
   2) Thus, foo.min.css depends on foo.css and baz.gif
   3) foo.css changes the line to 'background-image: url(qux.gif)'
   4) kake notices this and rebuilds foo.min.css, correctly inlining qux.gif.
   5) Now qux.gif changes
   6) kake will not notice it needs to rebuild foo.min.css, because it
      still has the foo.min.css dependencies as (foo.css, baz.gif)!
In step 4, kake needs to have not only rebuilt foo.min.css, it needs
to have modified the compile_rule for foo.min.css.  ComputedInputsBase
is what allows this.

If a computed-input depends on the special value CURRENT_INPUTS
(e.g. triggers=['foo', 'bar', computed_inputs.CURRENT_INPUTS]), it
signifies: 'Whenever any of my dependencies changes, I not only need
to rebuild, I need to recalculate my deps.' It is useful when using
ComputedInputs to capture #include lines and the like: whenever one of
my inputs changes, I need to recalculate deps because the change might
have been to add or remove an #include line.

If a computed-input depends on the passed-in context parameter, you
should override used_context_keys() to return the list of entries from
context that you use.  This will cause us to store those
context-entries in the filemod-db, so if you try to calculate the
inputs with a different context, kake will know to re-calculate them.

This module provides:

  ComputedInputsBase: a base class that
      clients can subclass to provide specific computed-inputs
      functionality for a particular rule.

  ComputedIncludeInputs: use this class when your dependencies
      are determined via an include-like process (#include for
      C, @imports for .less, requires() for .js, etc).

"""

from __future__ import absolute_import

import os
import re

from . import compile_util
from . import filemod_db
from . import log
from . import project_root


# A pseudo-value for the trigger saying: whenever any of the current
# inputs for this rule changes, re-figure what the inputs are.  This
# value is *part* of triggers, so you'd say:
#   ComputedInputsSubclass(['foo', 'bar', computed_inputs.CURRENT_INPUT])
CURRENT_INPUTS = '//current inputs//'

# A special value for 'triggers' to force inputs to be recomputed
# every time 'make' is run.  This slows things down, so use this
# judiciously.  This value *is* the trigger, so you'd say:
#   ComputedInputsSubclass(computed_inputs.FORCE)
# NOTE: When using FORCE, we pass in None for both trigger_files and
#       changed_files to input_patterns(); those values are meaningless
#       when used with FORCE.
FORCE = object()


def _unique_extend(list1, list2):
    """Adds list2 to list1, ignoring elements already in list1."""
    list1_set = frozenset(list1)
    for element in list2:
        if element not in list1_set:
            list1.append(element)


class ComputedInputsBase(object):
    """A class you can subclass for rules that have complex dependencies.

    As compile_util.__doc__ explains, some compile rules have dependencies
    (input_patterns) that cannot be described by a simple static list.
    For such cases, you can do
        CompileRule(..., input_patterns=ComputedFooInputs([trigger, ...]))  OR
        register_compile(..., ComputedFooInputs([trigger, ...]))
    instead.  'trigger' is a file-pattern (a glob pattern with {var}
    support).  Whenever the 'trigger' file changes, ComputedFooInputs is
    called to re-compute the list of dependencies that this rule has.

    Subclasses must define input_patterns() -- see below for details
    -- which returns a list of file-patterns representing the current
    inputs (dependencies) that outfile has.  The build system will
    then use those inputs to decide whether to rebuild the output file
    or not.

    Note that using ComputedInputsBase for a rule will cause the
    creation of a file called <outfile>.deps, in the same directory
    as <outfile>.  This is like a '.d' file for 'make'.
    """
    def __init__(self, triggers, compute_crc=False):
        """triggers: list of file-patterns.  If any changes, we recompute.

        triggers can include, as a special value, CURRENT_INPUTS.
        (e.g. triggers=['foo', 'bar', computed_inputs.CURRENT_INPUTS].)
        This evaluates to the last-computed inputs for an output file,
        and is how you can say: 'Whenever any of my dependencies
        changes, I not only need to rebuild, I need to recalculate my
        deps.'  It is useful when using ComputedInputs to capture
        #include lines and the like: whenever one of my inputs
        changes, I need to recalculate deps because the change might
        have been to add or remove an #include line.
        """
        assert triggers, 'ComputedInputs needs at least one trigger-pattern'
        assert not isinstance(triggers, basestring), (
            'ComputeInputs takes a list of triggers, not a single trigger')
        self.triggers = triggers
        self.compute_crc = compute_crc

        # This caches the current input patterns when it's read.
        self._cached_current_input_patterns = {}   # outfile_name -> inputs

    def version(self):
        """An integer that can be incremented every time the class changes.

        Suppose you have a ComputedInputs object that parses #include
        lines, and you realize you want it to only parse '#include "foo"'
        lines, not '#include <foo>' lines.  The dependencies need to be
        recalculated, but there's no indication of that since the
        trigger files haven't changed.  Instead, we use the version
        number to keep track of that.  So, the idea is to update
        the version number every time the implementation of this
        computed-input rule changes in a way that affects the output.
        """
        raise NotImplementedError('Subclasses must keep track of version()!')

    def input_patterns(self, outfile_name, context, triggers, changed):
        """Return a list of input-patterns that outfile_name depends on.

        Arguments:
            outfile_name: the file that we are determining the inputs of.
            context: an arbitrary, client-provided dict, passed around to all
              compiles, augmented to also include all the {var} values.
              See CompileBase.build.__doc__ and
              compile_rule_for_outfile.var_values().
            triggers: the list of trigger-files for outfile_name.  This
              may be useful when calculating the new dependencies.
            changed: the list of trigger files that have changed since
              the last time input_patterns() was called.  This may be
              useful when calculating the new dependencies.
              NOTE: changed will have *all* the files in 'triggers' if
              the .deps file has been manually modified, or we otherwise
              can't be certain that the current deps are up-to-date.

            NOTE: both 'triggers' and 'changed' will be None if you
            registered this ComputedInputs class with triggers=FORCE.
            This is because there are no trigger files (and thus no
            changed triggers files) in FORCE mode.

        Returns:
            A list of patterns, which are like filenames but can
            include glob patterns and {var} variables.  These
            patterns are taken to be the dependencies for outfile.
        """
        raise NotImplementedError('Subclasses must implement input_patterns')

    @classmethod
    def used_context_keys(cls):
        """Return a list/set of keys from 'context' that affect input_patterns.

        If the output of input_patterns() can depend on one of the
        'context' vars, you should indicate that by returning the
        varname from this function.  That way, when you try to use
        this class with a different value for that context var, kake
        will know it needs to re-compute the input patterns.

        You only need to list context vars that you specify manually,
        not ones that the kake system adds automatically, such as
        '{lang}', '{{path}}', or '_input_map'.
        """
        return []

    def trigger_files(self, outfile_name, context):
        if self.triggers is FORCE:
            return []

        retval = compile_util.resolve_patterns(self.triggers, context)
        if CURRENT_INPUTS in retval:
            retval.remove(CURRENT_INPUTS)
            _unique_extend(retval, self._current_input_patterns(outfile_name))
        return retval

    def _depsfile_name(self, outfile_name):
        return outfile_name + '.deps'

    def _current_input_patterns(self, outfile_name):
        """Return the last-computed list of input patterns for outfile."""
        if outfile_name not in self._cached_current_input_patterns:
            log.v4('Reading current inputs from %s', outfile_name)
            try:
                depsfile = self._depsfile_name(outfile_name)
                with open(project_root.join(depsfile)) as f:
                    self._cached_current_input_patterns[outfile_name] = (
                        f.read().splitlines())
            except (IOError, OSError):
                self._cached_current_input_patterns[outfile_name] = []
        return self._cached_current_input_patterns[outfile_name]

    def _recalculate_inputs(self, depsfile, outfile_name, context,
                            trigger_files, changed):
        log.v1('Recalculating inputs for %s', outfile_name)
        inputs = self.input_patterns(outfile_name, context,
                                     trigger_files, changed)

        abs_depsfile = project_root.join(depsfile)
        try:
            os.makedirs(os.path.dirname(abs_depsfile))
        except (IOError, OSError):
            pass
        with open(abs_depsfile, 'w') as f:
            f.write('\n'.join(inputs))
        log.v1('WROTE %s', depsfile)

        # Update our cache as well.
        self._cached_current_input_patterns[outfile_name] = inputs
        log.v2('New inputs for %s: %s', outfile_name, inputs)

    def compute_and_get_input_patterns(self, outfile_name, context,
                                       force=False):
        depsfile = self._depsfile_name(outfile_name)

        if self.triggers is FORCE:
            # As we say at the top of this file, trigger_files and
            # changed are meaningless when you manually force a
            # rebuild, so we set them to None.
            self._recalculate_inputs(depsfile, outfile_name, context,
                                     None, None)
            return self._current_input_patterns(outfile_name)

        # If the triggers include the special value CURRENT_INPUTS,
        # then extend the list of triggers to include the current
        # inputs.  This is how you say "when any input changes, I
        # don't just recompile, I recompute my deps."
        #
        # Note that we don't try to build the trigger files here because they
        # should have already been by this point in the build.
        trigger_files = list(self.trigger_files(outfile_name, context))

        # Check if any of the triggers have changed since the input
        # patterns were last stored in the db.
        with filemod_db.needs_update(
                depsfile, trigger_files, self.full_version(context),
                compute_crc=self.compute_crc) as changed:
            if force or changed:
                if force or depsfile in changed:
                    # If we have force set from the command line, we are
                    # forcing rebuild of all files and are probably debugging,
                    # so we'll want to recalculate all inputs.
                    # Alternatively if our .deps file has changed from under
                    # us, we can't be certain *which* of the inputs has
                    # changed.  We just have to assume the worst.
                    changed = trigger_files
                self._recalculate_inputs(depsfile, outfile_name, context,
                                         trigger_files, changed)

        # If the current inputs has changed, and the trigger files
        # depend on the current inputs, we need to update the keys of
        # the database to match the new current inputs.
        if changed and CURRENT_INPUTS in self.triggers:
            new_trigger_files = self.trigger_files(outfile_name, context)
            with filemod_db.needs_update(depsfile, new_trigger_files,
                                         self.full_version(context)):
                # We just needed to create the updated db entry; the
                # depsfile is already correct.
                pass

        return self._current_input_patterns(outfile_name)

    def clear_caches(self):
        """Clear associated caches, if any exist.

        This is necessary to avoid tests interfering with one another.
        """
        self._cached_current_input_patterns.clear()

    @staticmethod
    def abspath(*args):
        """Return os.path.join(project_root, *args)."""
        return project_root.join(*args)

    def full_version(self, context):
        """Combine the user-defined version with class name + context vars."""
        # They are part of our version because, by definition of
        # used_context_keys(), the output differs when these context
        # keys do.
        context_vars = ['%s=%s' % (k, context.get(k))
                        for k in self.used_context_keys()]
        return '.'.join([self.__class__.__name__,
                         str(self.version())] +
                        context_vars)


class ComputedIncludeInputs(ComputedInputsBase):
    """A ComputedInputs class that determines the inputs from include-lines.

    This is useful for languages where a file indicates another file
    that it depends on in the source code, in a greppable way.  For
    instance, .c files indicate their dependencies via '#include
    <foo.h>' lines.  foo.h may have include lines of its own.  This
    class can find these lines, and follow the includes to get the
    transitive list of dependencies.  It then sets these as the inputs
    for this file, as well as the input-triggers, meaning that
    whenever any of these includes change, it recomputes the include
    dependency-graph.
    """
    def __init__(self, base_file_pattern, include_regexp_string,
                 other_inputs=[], compute_crc=False,):
        """Arguments:
            base_file_pattern: the 'base' file where we start looking
              for includes.  This is a normal kake file-pattern, so can
              include globs and {var}s.  This is always the first
              input returned by input_patterns().
            include_regexp_string: string for a regexp that finds the
              'include' lines in your input files, starting with
              base_file.  It should have exactly one group (thing in
              parentheses) that returns the filename to include.  This
              filename is *ALWAYS* taken to be relative to the input
              filename.
            other_inputs: additional files (actually, any file-
              patterns) that the outfile depends on, in addition
              to whatever we auto-discover via include-processing.
              These are always the last inputs returned by
              input_patterns().
            compute_crc: passed to ComputedInputsBase

        Example:
           ComputedIncludeInputs('file.c', r'#include "([^\"]*)"', ['gcc'])
        """
        # We don't pass in triggers here, because we override
        # trigger_files() to determine that ourselves.
        super(ComputedIncludeInputs, self).__init__(['include-inputs dummy'],
                                                    compute_crc)
        self.base_file_pattern = base_file_pattern
        self.include_regexp = re.compile(include_regexp_string, re.MULTILINE)
        self.other_inputs = other_inputs

        self.clear_caches()

    def clear_caches(self):
        super(ComputedIncludeInputs, self).clear_caches()

        # A map from source filename to (list of included filenames,
        # source filename mtime). All filenames are relative to
        # ka-root.  An entry must be manually invalidated (that is,
        # removed from the map) any time its mtime does not match the
        # mtime in the cache.
        self._include_cache = {}

        # If the version changes out from underneath us, we need to
        # invalidate this entire cache.
        self._include_cache_version = None

    def _include_cache_key(self, infile, context):
        """Return a key for use in _include_cache."""
        # TODO(jlfwong): This misses out on some caching benefit, since this
        # will store separate caches for different context values which
        # might not affect the list of included files returned. This could
        # be alleviated by a mechanism similar to
        # compile_rule.used_context_keys. Generally, resolving dependencies
        # is fast though, so I'm not sure it's worth it to be that aggressive.
        return '%(infile)s?%(context)s' % {
            'infile': infile,
            'context': '&'.join('%s=%s' % (k, v)
                                for k, v in sorted(context.iteritems()))
        }

    def _get_contents_for_analysis(self, infile):
        """Return contents for the provided infile to be used for analysis.

        This can be used to modify the contents of the file before doing the
        static analysis to extract included files. This can be used, for
        instance, to remove comments.

        By default, we just read the file contents and return it.
        """
        with open(project_root.join(infile)) as f:
            contents = f.read()

        return contents

    def included_files(self, infile, context):
        """Return a list of all files infile includes, relative to ka-root."""
        # TODO(csilvers): keep track of the direct includes of each
        # file in a db somewhere, so we don't have to re-populate and
        # manage the include-cache each time we run.
        abs_infile = project_root.join(infile)

        if self._include_cache_version != self.full_version(context):
            self.clear_caches()
            self._include_cache_version = self.full_version(context)

        cache_key = self._include_cache_key(infile, context)

        should_update_cache = False

        if cache_key not in self._include_cache:
            should_update_cache = True
            cur_file_info = filemod_db.get_file_info(
                    infile, compute_crc=self.compute_crc)
        else:
            cached_file_info = self._include_cache[cache_key][1]
            cur_file_info = filemod_db.get_file_info(
                    infile, compute_crc=self.compute_crc)
            if not filemod_db.file_info_equal(cached_file_info, cur_file_info):
                should_update_cache = True

        if should_update_cache:
            log.v3('extracting includes from %s', infile)

            contents = self._get_contents_for_analysis(infile)

            retval = []
            for m in self.include_regexp.finditer(contents):
                newfile = m.group(1)
                abs_newfile = self.resolve_includee_path(abs_infile, newfile,
                                                         context)
                retval.append(project_root.relpath(abs_newfile))
            self._include_cache[cache_key] = (retval, cur_file_info)

        log.v2('includes for %s: %s',
               infile, self._include_cache[cache_key][0])
        log.v4('cached includes for %s', cache_key)
        return self._include_cache[cache_key][0]

    def resolve_includee_path(self, abs_includer_path,
                              includee_path, context):
        """Return the absolute path of an included file.

        When file a includes file b via something like '#include "b"', we can't
        always assume that this means '#include "`dirname a`/b" because the
        include system may have include-paths and the like.  So we allow
        subclasses to override this method if they need to, to properly resolve
        an include of file b from file a.

        Arguments:
            abs_infile: absolute path to the requiring file
            newfile: requiree path extracted via regexp (usually relative)
            context: an arbitrary, client-provided dict, passed around to all
              compiles, augmented to also include all the {var} values.
              See CompileBase.build.__doc__ and
              compile_rule_for_outfile.var_values().

        Subclasses can override.
        """
        return os.path.join(os.path.dirname(abs_includer_path), includee_path)

    def version(self):
        """Subclasses can override this.

        But since this class can also be used directly, we have to
        provide a version ourself!  It will never change: since
        subclasses can override it, we can't depend on ever seeing it.
        """
        return 1

    def full_version(self, context):
        """Add our internal state to the version."""
        return '.'.join(
            [super(ComputedIncludeInputs, self).full_version(context)] +
            self.other_inputs +
            [self.include_regexp.pattern,
             # This is because of a bugfix I made to this
             # class itself.  See version.__doc__ for why
             # I can't put this version info there.
             '2',
             str(self.version()),
         ])

    def trigger_files(self, outfile_name, context):
        # We override trigger_files since we need it to be more
        # fine-grained than our superclass can do: all files reachable
        # from base_file_pattern, but not other_inputs.

        # Start with the base-file and then add from there.
        retval = compile_util.resolve_patterns([self.base_file_pattern],
                                               context)
        i = 0
        while i < len(retval):
            # We yield here to let the build system build this trigger file,
            # if necessary
            yield retval[i]
            _unique_extend(retval, self.included_files(retval[i],
                                                        context))
            i += 1

    def input_patterns(self, outfile_name, context, triggers, changed):
        # Our inputs and our trigger files are the same, except
        # our inputs also include other_inputs.
        retval = list(self.trigger_files(outfile_name, context))
        _unique_extend(retval, self.other_inputs)
        return retval
