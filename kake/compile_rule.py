"""This module provides the base class for 'compile' (build) commands.

There are two parts of this file that are generally useful:

-- CompileBase:

All compile commands subclass from this class to provide the function
that does the actual building.  The subclass must define a 'build()'
function which takes the output filename, the input filenames, the
changed files (since the last time this target was built), and a
'context' dict which is passed around the build system.  (See the
docstring for CompileBase.build() for more description of 'context'.)

Instead of providing build(), subclasses may provide build_many() +
num_outputs(), which allows creating many output files at once.  This
can be used when the build command has a large start-up cost, so it's
worth it to bundle several output files together (at the cost of less
flexibility for the build scheduler).  num_outputs() should return an
integer, the maximum number of outputs that should be passed to
build_many() in one call.  NOTE: the kake scheduler can schedule
build() calls more efficiently than build_many() calls, so only use
build_many() when there's a performance advantage to doing so.

A third option is to provide build_many() + split_outputs(), which
is an advanced feature when you want finer-grained control over
how your inputs are divvied up across multiple build sub-processes.
split_outputs() takes the same inputs as build_many(), plus the
number of subprocesses, and given an input that is a list of
build-inputs (each build-input a 4-tuple as given to build_many),
yields a bunch of lists, each yielded-list being a subset of
build-inputs, so that together they partition build-inputs.
As an example, if you call
   split_outputs([tuple1, tuple2, tuple3, tuple4, tuple5], 3)
it might do
   yield [tuple1, tuple5]
   yield [tuple3]
   yield [tuple4, tuple2]

If the output of your build() or build_many() call depends on the
passed-in context parameter, you should override used_context_keys()
to return the list of entries from context that you use.  This will
cause us to store those context-entries in the filemod-db, so if you
try to build the file with a different context, kake will know to
re-make it.


-- register_compile():

You must call this function for every build rule that you want to
register.  A build rule is a way of producing an output file from an
input file.

See the docstring for register_compile() for details on the arguments
to this function, including the use of {variable}s in the build specs.

The build system decides what compile rule to use for a particular
output file based only on the output filename.  When multiple rules
match a given filename, the 'most specific' rule is chosen.
Basically, the 'most specific' rule is the one with the most extension
parts, followed by the most directory parts, followed by the fewest
{variables}.  See the docstring from find_compile_rule() for details.
"""

from __future__ import absolute_import

import os
import re
import subprocess
import sys

from . import compile_util
from . import log
from . import project_root


# Subdirectory of ka-root that holds generated files.  All other
# subdirs of ka-root are assumed to hold static files.  We check,
# here, that all compile_rules put their output in GENDIR.
GENDIR = 'genfiles' + os.sep


class CompileFailure(Exception):
    pass


class GracefulCompileFailure(CompileFailure):
    """When using the kake server, serve a more useful error message.

    For example, when JSX compilation fails, this can be used to return
    JavaScript that will log a helpful message to the console instead of just
    500'ing on the JS request.

    Because this subclasses CompileFailure, when compiling from
    kake/build_prod_main.py, this will act like a regular CompileFailure.
    """
    def __init__(self, message, graceful_response):
        super(GracefulCompileFailure, self).__init__(message)
        self.graceful_response = graceful_response


class BadRequestFailure(CompileFailure):
    """When using the kake server, serve a 400 instead of a 500.

    Indicates a compilation failure that will NOT be resolved by retrying.
    """
    pass


class NoBuildRuleCompileFailure(BadRequestFailure):
    """Raised when there is no rule to generate the requested file."""
    def __init__(self, requested_file):
        super(NoBuildRuleCompileFailure, self).__init__(
            "No rule found to generate '%s'" % (requested_file))


class CompileBase(object):
    def version(self):
        """An integer that can be incremented every time the compiler changes.

        Suppose you have a Compile object that concatentes two files
        with a ';' in between, and you decide to change it to use a
        '\n' in between instead.  All output files now need to be
        re-created, but there's no indication of that since the
        input files haven't changed.  Instead, we use the version
        number to keep track of that.  So, the idea is to update
        the version number every time the implementation of this
        compile rule has changed in a way that affects the output.
        """
        raise NotImplementedError('Subclasses must keep track of version()!')

    def build(self, output_filename, input_filenames, changed_filenames,
              context):
        """Does a 'compile' to create output_filename based on the inputs.

        Arguments:
           output_filename: the filename to create, relative to project_root.
           input_filenames: a list of filenames that output_filename depends
               on, relative to project_root.
           changed_filenames: a subset of input_filename + output_filename
               that were changed since the last time this build command was
               called on this output.  This may be useful for incremental
               rebuilds.
           context: an arbitrary dict, passed around to all compiles.  It
               can be used to store global state, such as whether we
               should be stripping comments, etc.  context['{var}']
               holds the '{var}' variables from the compile rule this
               build was registered with (cf. register_compile()).
               There are also some 'system' context vars that are set
               for your use, such as context['_input_map'], a map from
               outfile_name -> [list_of_infile_names] for all files
               currently being built.

        Raises:
            CompileFailure if the build failed for some reason.
        """
        raise NotImplementedError(
            '%s must implement either build() or build_many() + num_outputs().'
            % self.__class__.__name__)

    def build_many(self, output_inputs_changed_context):
        """Does a 'compile' to create several outputs based on inputs.

        Arguments:
            output_inputs_changed_context: a list of 4-tuples:
                (output_filename, input_filenames, changed_filenames, context)
                which are the same as the arguments to build().

        Raises:
            CompileFailure if the build failed for some reason.  In this
            case, it is assumed that no output files were successfully
            created, even if possibly some of them ere.
        """
        raise NotImplementedError(
            '%s must implement either build() or build_many() + num_outputs().'
            % self.__class__.__name__)

    def num_outputs(self):
        """When implementing build_many() how big 'many' should be.

        The size of the output_inputs_changed list passed to
        build_many() will be capped at num_outputs().  This needn't be
        defined for subclasses that implement build() rather than
        build_many().
        """
        return 0

    @classmethod
    def used_context_keys(cls):
        """Return a list/set of keys from 'context' that affect build output.

        If the output of a build can depend on one of the 'context'
        vars (from the passed-in context arg to build/build_many), you
        should indicate that by returning the varname from this
        function.  That way, when you try to use this rule with a
        different value for that context var, kake will know it needs
        to re-build the output file.

        You only need to list context vars that you specify manually,
        not ones that the kake system adds automatically, such as
        '{lang}', '{{path}}', or '_input_map'.
        """
        return []

    # -- These are all utility routines that subclasses might find useful.

    def try_call_with_output(self, *args, **kwargs):
        """Execute command and return (retcode, stdout, stderr)."""
        log.v3('Calling %s', args)
        log.v4('   -- with kwargs %s', kwargs)
        popen_kwargs = {
            'cwd': project_root.root,
            'stderr': subprocess.PIPE,
            'stdout': subprocess.PIPE
        }
        popen_kwargs.update(kwargs)

        proc = subprocess.Popen(*args, **popen_kwargs)
        (stdout, stderr) = proc.communicate()
        retcode = proc.wait()
        return (retcode, stdout, stderr)

    def call(self, *args, **kwargs):
        """subprocess.check_call(), but raises CompileFailure on error."""
        log.v3('Calling %s', args)
        log.v4('   -- with kwargs %s', kwargs)
        popen_kwargs = {'cwd': project_root.root}
        popen_kwargs.update(kwargs)
        try:
            subprocess.check_call(*args, **popen_kwargs)
        except (subprocess.CalledProcessError, OSError) as why:
            raise CompileFailure('Call to %s failed: %s' % (args[0], why))

    def call_with_output(self, *args, **kwargs):
        """subprocess.check_output(), but raises CompileFailure on error."""
        log.v3('Calling %s', args)
        log.v4('   -- with kwargs %s', kwargs)
        popen_kwargs = {'cwd': project_root.root}
        popen_kwargs.update(kwargs)
        try:
            return subprocess.check_output(*args, **popen_kwargs)
        except (subprocess.CalledProcessError, OSError) as why:
            raise CompileFailure('Call to %s failed: %s' % (args[0], why))

    def try_call_with_input(self, path, input, **kwargs):
        """Execute command with input and return (retcode, stdout, stderr)."""
        log.v3('Calling %s', path)
        log.v4('   -- with kwargs %s', kwargs)
        assert 'stdin' not in kwargs, 'call_with_input: Cannot specify stdin'
        assert 'stdout' not in kwargs, 'call_with_input: Cannot specify stdout'
        assert 'stderr' not in kwargs, 'call_with_input: Cannot specify stderr'

        popen_kwargs = {'cwd': project_root.root}
        popen_kwargs.update(kwargs)

        sub_stdout = sub_stderr = subprocess.PIPE
        if sys.stdout == sys.stderr:
            sub_stderr = subprocess.STDOUT
        p = subprocess.Popen(path,
                             stdin=subprocess.PIPE,
                             stdout=sub_stdout, stderr=sub_stderr,
                             **popen_kwargs)

        (p_stdout, p_stderr) = p.communicate(input=input)
        return (p.returncode, p_stdout, p_stderr)

    def call_with_input(self, path, input, **kwargs):
        """subprocess.Popen(path).communicate(input), but handles weirdness.

        In particular, we properly handle stdout/stderr being a StringIO
        object.  If they are, we correctly append stdout/stderr to the
        StringIO object, otherwise they stay with whatever they were before.

        kwargs should not include 'stdin', 'stdout', or 'stderr'.
        """
        retcode, stdout, stderr = self.try_call_with_input(path, input,
                                                           **kwargs)

        sys.stdout.write(stdout)

        # stderr will be None if we used subprocess.STDOUT in
        # try_call_with_input
        if stderr:
            sys.stderr.write(stderr)

        if retcode != 0:
            raise CompileFailure("Command FAILED (rc %d): echo '%s' | %s"
                                 % (retcode, input, ' '.join(path)))

    @staticmethod
    def abspath(*args):
        """Return os.path.join(project_root, *args)."""
        return project_root.join(*args)

    # -- These are for internal use.

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

    def should_call_build_many(self):
        """True: self supports build_many(), False: it supports build()."""
        return (self.num_outputs() > 0 or
                hasattr(self, 'split_outputs'))


_COMPILE_RULES = {}
_COMPILE_RULE_LABELS = set()
# Counts dots, but only in the file's basename, and only after the last {var}
_LITERAL_EXTENSION_RE = re.compile(r'[^\\/{}]*$')


class CompileRule(object):
    """Object that is stored in _COMPILE_RULES."""
    def __init__(self, label, output_pattern, input_patterns,
                 compile_instance, non_input_deps_patterns=[],
                 maybe_symlink_to_pattern=None, compute_crc=False,
                 trumped_by=[]):
        """See register_compile() for an explanation of these parameters."""
        self.label = label
        self.output_pattern = output_pattern
        self.input_patterns = input_patterns
        self.compile_instance = compile_instance
        self.non_input_deps_patterns = non_input_deps_patterns
        self.maybe_symlink_to_pattern = maybe_symlink_to_pattern
        self.compute_crc = compute_crc
        self.trumped_by = trumped_by

        # This turns each {var} into a regexp named-group named 'brace_var',
        # and each {{var}} into a group named bracebrace_var.
        self.output_re = compile_util._extended_fnmatch_compile(
            self.output_pattern)
        self.num_vars_in_output_pattern = output_pattern.count('{')
        self.num_dirparts_in_output_pattern = output_pattern.count(os.sep)
        literal_extension = (
            _LITERAL_EXTENSION_RE.search(output_pattern).group().strip('.'))
        self.num_extensions_in_output_pattern = (
            len(literal_extension.split('.')))

        # Verify our constraints: the output file must live in
        # genfiles, and we can't do globs over generated files.
        assert self.output_pattern.startswith(GENDIR), (
            '%s violates the rule that generated files must live in %s'
            % (self.output_pattern, GENDIR))

        # input_patterns can be a ComputedInputsBase, in which case we
        # can't sanity-check.  Alas, we'll have to hope for the best.
        if not self._has_computed_inputs():
            for ip in self.input_patterns:
                if ip.startswith(GENDIR):
                    assert not compile_util.has_glob_metachar(ip), (
                        '%s: We do not support globbing over generated files'
                        % ip)

    def matches(self, output_filename):
        """True if filename could be produced by this output rule."""
        return self.output_re.match(output_filename) is not None

    def var_values(self, output_filename):
        """Given an output filename, return a dict of all var values.

        For instance, if output_pattern is '{{dir}}/{file}.{type}.js'
        and output_filename is 'foo/bar/baz.handlebars.js', this
        would return
           {'{{dir}}': 'foo/bar', '{file}': 'baz', '{type}': 'handlebars'}

        Raises an AssertionError if called on an string for which
        self.matches() is False.
        """
        m = self.output_re.match(output_filename)
        assert m, (self.output_re.pattern, output_filename)
        # groupdict returns {'brace_var': 'value'} when we want
        # {'{var}': 'value'}, and {'bracebrace_var': 'value'} when we
        # want {'{{var}}': 'value'}.
        var_values = m.groupdict()
        retval = {}
        for (k, v) in var_values.iteritems():
            if k.startswith('bracebrace_'):
                retval['{{%s}}' % k[len('bracebrace_'):]] = v
            else:
                assert k.startswith('brace_'), (k, v)
                retval['{%s}' % k[len('brace_'):]] = v
        return retval

    def _has_computed_inputs(self):
        return hasattr(self.input_patterns, 'compute_and_get_input_patterns')

    def input_trigger_files(self, output_filename, context=None):
        """Return the list of files needed to compute the input files.

        This will normally be [], but if input_pattern is a
        ComputedInputBase, where the inputs are determined at runtime,
        then it may not be.  In that case, the input-figurer-outer has
        depedencies itself, called 'triggers', that are used to
        determine what the input files should be for a rule.  This
        returns the list of filenames that are input-file triggers.
        """
        if self._has_computed_inputs():
            if context is None:
                context = self.var_values(output_filename)
            return self.input_patterns.trigger_files(output_filename, context)
        return []

    def input_files(self, output_filename, context=None, force=False):
        """Return the list of input files needed for output_filename.

        This matches output_filename to output_pattern to fill the
        list of var values, and then applies them to each input file.
        If you already have var_values handy, you can pass it in to
        save a bit of time.
        """
        if context is None:
            context = self.var_values(output_filename)

        if self._has_computed_inputs():
            ips = self.input_patterns.compute_and_get_input_patterns(
                output_filename, context, force)
        else:
            ips = self.input_patterns

        return compile_util.resolve_patterns(ips, context)

    def non_input_deps_files(self, output_filename, var_values=None):
        if var_values is None:
            var_values = self.var_values(output_filename)

        return compile_util.resolve_patterns(self.non_input_deps_patterns,
                                             var_values)

    def maybe_symlink_to(self, output_filename, var_values=None):
        """Return the maybe-symlink-to file for output_filename, or None.

        See filemod-db for more information about maybe-symlink-to;
        basically, we call this to get a file that output_filename
        might be equivalent to.  If that file exists and is up to
        date, we can use that in lieu of remaking output_filename.
        """
        if self.maybe_symlink_to_pattern is None:
            return None
        if var_values is None:
            var_values = self.var_values(output_filename)

        retval = compile_util.resolve_patterns([self.maybe_symlink_to_pattern],
                                               var_values)

        # Make sure that the pattern resolves to a single file, not a
        # list (which it could if it contained a glob pattern like '*').
        assert len(retval) == 1, "Ambiguous glob pattern for maybe_symlink_to."
        return retval[0]


def reset_for_tests():
    """Called automatically by TestCase in between tests."""
    for cr_set in _COMPILE_RULES.values():
        for cr in cr_set:
            # TODO(benkraft): Don't access internals here.
            if cr._has_computed_inputs():
                cr.input_patterns.clear_caches()


def find_compile_rule(filename):
    """Find the best compile rule for (re)generating filename.

    Returns:
        If there is only one compile rule in _COMPILE_RULES whose
        output_pattern matches filename, return that.  Otherwise,
        return the 'most specific' compile rule that matches,
        using the following algorithm: when comparing two rules,
        it looks at the following properties in order, stopping
        when the two rules are no longer tied:

        1) A LONGER LITERAL EXTENSION.  This means that the
           'basename' of the rule (after the last slash), has more
           dotted-components in it.  Only components after the last
           variable are considered.  So 'a/b/c/{foo}.js.o' is longer
           than 'a.b.c.d/e.f.d.{foo}.handlebars', and
           'a/b/c/file.js.o' is the longest of them all.
        2) MORE DIRECTORY COMPONENTS.  This lets us prefer
           'genfiles/{{path}}.foo' to '{{path}}.foo'.
        3) FEWER {VAR}S.  This lets us prefer '{name}.en.foo'
           to '{name}.{lang}.foo'.

        If there is a tie for 'best' rule even after all of this,
        then we raise an error.  If on the other hand, no matching
        compile rule is found at all, return None.

        A rule may have 'trumped_by=[label1, label2, ...]'.  In
        that case, if a rule labeled label1 (or label2, etc) is
        found in our candidate set, than our rule is removed from
        consideration.
    """
    if not filename.startswith(GENDIR):
        return None

    # We check for this in register_compile but re-check here:
    # register_compile will miss outfiles like 'genfiles/{foo}'
    # where 'foo' starts with an underscore.
    assert not filename.startswith(GENDIR + '_'), (
        'Output files in the top-level %s directory cannot start with _. '
        'Use a subdirectory instead.' % GENDIR)

    # _COMPILE_RULES is bucketed by the second dir (after genfiles/).
    second_dir = filename.split('/', 2)[1]
    matches = [cr for cr in _COMPILE_RULES.get(second_dir, [])
               if cr.matches(filename)]
    # Also look at _COMPILE_RULES[None], which holds all rules where
    # the second dir is not a constant string.
    matches.extend([cr for cr in _COMPILE_RULES.get(None, [])
                    if cr.matches(filename)])

    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]

    # Get rid of matches that are trumped by other matches.
    if any(m.trumped_by for m in matches):      # speed optimization
        candidates_labels = frozenset(m.label for m in matches)
        matches = [m for m in matches
                   if not any(l in candidates_labels for l in m.trumped_by)]

    matches.sort(key=lambda cr: cr.num_extensions_in_output_pattern,
                 reverse=True)
    matches = [m for m in matches
               if (m.num_extensions_in_output_pattern ==
                   matches[0].num_extensions_in_output_pattern)]
    if len(matches) == 1:
        return matches[0]

    matches.sort(key=lambda cr: cr.num_dirparts_in_output_pattern,
                 reverse=True)
    matches = [m for m in matches
               if (m.num_dirparts_in_output_pattern ==
                   matches[0].num_dirparts_in_output_pattern)]
    if len(matches) == 1:
        return matches[0]

    matches.sort(key=lambda cr: cr.num_vars_in_output_pattern)
    matches = [m for m in matches
               if (m.num_vars_in_output_pattern ==
                   matches[0].num_vars_in_output_pattern)]
    if len(matches) == 1:
        return matches[0]

    raise CompileFailure('Cannot find a unique compile rule for %s: %s'
                         % (filename,
                            ' '.join(m.output_pattern for m in matches)))


def register_compile(label, output_pattern, input_patterns, compile_instance,
                     non_input_deps=[],
                     maybe_symlink_to=None,
                     compute_crc=False,
                     trumped_by=[]):
    """Tell the build system how to build files matching 'output_pattern'.

    When asked to compile a file that matches output_pattern, the make
    system will know to call compile_instance.build() to do so.

    The input and output are specified as 'patterns', which are
    filenames (relative to ka-root) that can have meta-variables
    in them of the form '{variable-name}'.  These are equivalent to
    a '*' glob pattern, but are named so a {variable} in the output
    can match a {variable} in an input.  For instance:
       output_pattern = genfiles/translations/{lang}/{pkg}/foo.js
       input_patterns = ['translations/{lang}.mo', 'javascript/{pkg}/foo.js']

    {{variable}} is exactly the same as {variable}, except that it
    is equivalent to a '**' glob pattern.  For instance:
       output_pattern = genfiles/translations/{lang}/{{path}}.js
       input_patterns = ['translations/{lang}.mo', 'javascript/{{path}}.js']

    If a variable occurs twice in a rule, then they must all have the
    same value: "genfiles/translations/{lang}/{lang}/{{path.js}}"
    matches "genfiles/translations/en/en/foo/bar.js" but not
    "genfiles/translations/en/es-ES/foo/bar.js".

    Other glob metavars ('*', '?', '[...]', '[!...]') are also supported.
    When used on an input pattern, beware that they only match
    *static* files, not generated files.  (If you even try to do
    something like input_patterns=['genfiles/compiled_*/{{path}}.js'],
    we will raise an exception.)

    This indicates that 'genfiles/translations/es/shared.js' depends on
    'translations/es.mo' and 'javascript/shared.js'.

    Registrations are global.  If two registrations cover the same
    output file, then the 'more specific' registration wins -- that
    is, the one with fewer {variable}s.  If both registrations are
    equally specific, we will raise an exception when trying to
    build that output file.

    Arguments:
        label: a string used to identify this build rule when logging.
          It is also used by 'trumped_by'.
        output_pattern: a filename relative to ka-root, but with {var}
          variables, as described above.  output_pattern *must*
          start with 'genfiles/' and *must not* start with 'genfiles/_'
          (top-level underscore files are reserved for use by kake).
        input_patterns: a list of filenames relative to ka-root, but
          with {var} variables, as described above, such that we
          should regenerate the output file whenever an input has
          changed.  May be the empty list, to mean that this rule
          should only be executed if the output file does not already
          exist.  An example of the varname_to_value_map:
              {'{{path}}': 'javascript/shared-package', '{ext}': 'js'}
        compile_instance: an instance of some subclass of CompileBase.
        non_input_deps: if specified as a list of patterns, then these
           files are made up-to-date before the output file is.  These
           are similar to input patterns, except that the output file
           doesn't depend on their contents, so will not need to be
           re-created whenever one of these non-input deps changes.
           An example of a non-input dep might be an __init__.py
           file, which must be created whenever a foo.py file is.
        maybe_symlink_to: if specified, then evaulate the pattern the
           same as for input_patterns, and use the resulting filename
           (which should be relative to ka-root) as a maybe_symlink_to
           argument to filemod-db.  Basically, this should be a
           filename that you might be equivalent to.
        compute_crc: passed to filemod_db.  If True, we compute CRCs
           on the input files to tell if they've changed, rather than
           mtime.  Useful if the output is expensive to compute.
        trumped_by: a list of compile-rule labels.  If
           rule_x.trumped_by = [rule_y], then when determining
           while compile-rule matches a given filename, we will
           never choose rule_x over rule_y, even if otherwise it
           would be considered a better match.
    """
    # Guard against adding the exact same rule twice.  This can happen
    # while we're transitioning to using kake everywhere, and some of
    # the compile_*/compress_*/translate_* scripts have their own
    # __main__.  In that case, the register_compile() in those scripts
    # can be called twice, once when the module is imported as
    # __main__, and again when the module is imported from make.py If
    # that happens, we use the __main__ version (which is always
    # registered first, the way we have the transition code set up),
    # and ignore the second.

    log.v4('Registering compile rule for %s', output_pattern)

    assert output_pattern.startswith(GENDIR), (
        '%s violates the rule that generated files must live in %s'
        % (output_pattern, GENDIR))
    assert not output_pattern.startswith(GENDIR + '_'), (
        '%s emits to %s_<something>, which is reserved for use by kake'
        % (output_pattern, GENDIR))

    # It turns out matching output-files against _COMPILE_RULES is
    # a (relatively minor) perf bottleneck.  To speed things up,
    # we partition the compile-rules by the directory after 'genfiles/',
    # which is almost always a constant string.
    second_dir = output_pattern.split('/', 2)[1]
    if compile_util.has_glob_metachar(second_dir) or '{' in second_dir:
        # 'catch-all' location for when the second-dir *isn't* a constant.
        second_dir = None

    # Make sure the label is unique
    assert label not in _COMPILE_RULE_LABELS, (
        'Label "%s" found on more than one compile-rule' % label)
    _COMPILE_RULE_LABELS.add(label)

    _COMPILE_RULES.setdefault(second_dir, set())
    _COMPILE_RULES[second_dir].add(CompileRule(label,
                                               output_pattern,
                                               input_patterns,
                                               compile_instance,
                                               non_input_deps,
                                               maybe_symlink_to,
                                               compute_crc,
                                               trumped_by))
