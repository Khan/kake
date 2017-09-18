# TODO(colin): fix these lint errors (http://pep8.readthedocs.io/en/release-1.7.x/intro.html#error-codes)
# pep8-disable:E127,E128
"""Compile rule creating .bundle.js JavaScript "Bundle" files.

A bundle is a 'combined' set of .js files. In contrast to a "package" as
compiled by compile_js_css_packages.py, the files in a bundle are dictated by
the dependency graph created by following calls to require() in the code, as
opposed to the list of files listed by javascript-packages.json.

Also unlike packages, the targets aren't limited to a canned set of output
files - you can create a bundle using the dependency graph rooted at any .js
file.

To create a bundle, we need to put all the files that will live in the bundle
in one directory, and then convert the dependency graph into a list of files
within this one directory. This list of files is then passed to the same
combiner used to construct "packages" (CombineJavaScript).

To make it more natural to write JavaScript code using bundles, we have the
filename extensions reflect the unprocessed file, not the compiled file we'll
actually be putting into the bundle: foo.jsx, not foo.jsx.js.  So we rename the
files as part of the symlinking process.
"""

# TODO(jlfwong): Right now, every bundle contains the source for the entire
# dependency graph for the entry point. This is only suitable for tests, since
# in dev and especially prod, we want to cut up the bundles into several files
# in order to facilitate caching.

from __future__ import absolute_import

import os
import re

from shared import ka_root

from js_css_packages import third_party_js
import js_css_packages.analysis
import js_css_packages.require_util
import js_css_packages.util
from kake import compile_js_css_packages
from kake.lib import compile_rule
from kake.lib import computed_inputs

# List of files that are treated specially because they're entry points in test
# systems. They're special because they use information from the context to
# specify their dependencies.
_TEST_ENTRY_POINTS = frozenset([
    'javascript/testindex.js',
])


def get_required_dependencies(filename, dev, pkg_locale, top_level_only=False):
    """Return the list of files that the given file require().

    Both the argument and the list of files returned are relative to ka root.

    If top_level_only is True, we will only look at 'require' lines at
    the top level, that is not inside a function/etc.
    """
    computed_js_inputs = ComputedJavaScriptInputs('{{path}}.js',
                                                  top_level_only)
    context = {
        '{lang}': pkg_locale,
        '{env}': 'dev' if dev else 'prod',
        'testfiles': '[]'
    }

    explicit_deps = computed_js_inputs.included_files(filename, context)

    IMPLICIT_FILE_DEPS = compile_js_css_packages.IMPLICIT_JS_FILE_DEPS
    IMPLICIT_FILE_DEPS_SET = compile_js_css_packages.IMPLICIT_JS_FILE_DEPS_SET

    # Every file also implicitly depends on the module system and polyfills,
    # except those that are themselves part of the module system or polyfills.
    if filename in IMPLICIT_FILE_DEPS_SET:
        return explicit_deps
    else:
        for f in explicit_deps:
            assert f not in IMPLICIT_FILE_DEPS_SET, (
                "Files may not explicitly depend upon %s. "
                "Check dependencies of %s" % (f, filename))

        return IMPLICIT_FILE_DEPS + explicit_deps


class ComputedJavaScriptInputs(computed_inputs.ComputedIncludeInputs):
    def __init__(self, base_file_pattern, top_level_only=False):
        # NOTE: If using regex proves insufficient, then using
        # node-detective to extract require() calls using an AST is a good
        # alternative: https://github.com/substack/node-detective
        require_re = js_css_packages.analysis.REQUIRE_RE.pattern

        # If top-level-only is true, we only consider 'require' calls
        # that are unindented (and thus not in a function, etc).
        # This isn't a perfect proxy, but is surprisingly good enough
        # (as of 1 Oct 2014).
        #
        # To detect top-level requires it misses, I ran the following
        # to strip out all calls to 'require' within functions, and
        # then print out all remaining indented require's:
        #    find javascript -name '*.js' -o -name '*.jsx' | xargs -n1 perl -le '$_ = join("", <>); s,function\s*\([^)]*\)\s*(\{(?:(?>[^{}]+)|(?1))*\}),,g; @m = m,^ [^*\n]*require\(,mg; print "$ARGV:1:@m" if @m'    # @Nolint
        # If our proxy regexp were perfect, that would give no
        # results.  It does give some results, but almost all of them
        # are requiring handlebars files (via a Backbone.extend, or in
        # something like handlebars-extras.js).  Since handlebars are
        # always at the leaf of a dependency graph -- they can't have js
        # deps of their own -- we don't worry about them too much.  The
        # only 'true positive' was in notifications-package/config.js,
        # which I rewrote to match this regexp. :-)
        self.top_level_only = top_level_only
        if top_level_only:
            require_re = r'^(?:\S.*)?' + require_re

        super(ComputedJavaScriptInputs, self).__init__(
            base_file_pattern,
            require_re,
            other_inputs=compile_js_css_packages.IMPLICIT_JS_FILE_DEPS)

    def version(self):
        """Update whenever input_patterns() or trigger_files() changes."""
        return 20

    @classmethod
    def used_context_keys(cls):
        """Tell the build system our output depends on these context vars."""
        return ['testfiles']

    def input_patterns(self, outfile_name, context, triggers, changed):
        files = super(ComputedJavaScriptInputs, self).input_patterns(
                        outfile_name, context, triggers, changed)

        def file_order(f):
            # We want to load the test entry point last to allow all the
            # tests to register themselves before starting tests
            if f in _TEST_ENTRY_POINTS:
                return 1

            return 0

        assert 'js_css_packages/third_party_js.py' == files[0]
        third_party_js = files[0]
        input_files = files[1:]

        IMPLICIT_FILE_DEPS = compile_js_css_packages.IMPLICIT_JS_FILE_DEPS
        IMPLICIT_FILE_DEPS_SET = \
            compile_js_css_packages.IMPLICIT_JS_FILE_DEPS_SET

        # The ordering of files should be:
        #   1. All of the IMPLICIT_JS_FILE_DEPS
        #   2. All of our actual source trigger files (that are also inputs)
        #   3. path_to_packages_{env}.json because it can affect output
        #   4. javascript-packages.json because it can affect output
        #   5. third_party_js.py because it's a trigger file

        # Duplicate files depend on which tests are being run.  For
        # example: tests which run code that include site-infra.js
        # will end up including the shims.  This file is also included
        # in IMPLICIT_JS_FILE_DEPS. In order to prevent including the
        # same file more than once we determine any shared entries in
        # files and IMPLICIT_JS_FILE_DEPS and remove the duplicates
        # from files.
        duplicates = IMPLICIT_FILE_DEPS_SET.intersection(set(input_files))
        for dup in duplicates:
            input_files.remove(dup)

        input_files.sort(key=file_order)

        # The trigger files are specified as source files, but we want
        # to put the compiled file into our bundle.
        pkg_locale = context['{lang}']
        compiled_input_files = [
            js_css_packages.util.source_path_to_compiled_path(f, pkg_locale)
            for f in input_files]

        retfiles = (IMPLICIT_FILE_DEPS +
                    compiled_input_files +
                    # TODO(csilvers): use just path-to-packages for files[0]
                    [('genfiles/paths_and_packages/%s/js/'
                      'all_paths_to_packages_%s.json' %
                      (context['{lang}'], context['{env}'])),
                     'javascript-packages.json',
                     third_party_js])

        return retfiles

    def _get_contents_for_analysis(self, infile):
        with open(ka_root.join(infile)) as f:
            contents = f.read()

        # Strip out comments before we search for calls to require()
        return js_css_packages.analysis.strip_js_comments(contents)

    # e.g. {{#invokePartial "shared" "progress-icon-subway"
    _INVOKE_PARTIAL_RE = re.compile(r'#invokePartial\s+"([^"]*)"\s+"([^"]*)')

    # e.g. {{> shared_throbber-grid}}
    _PARTIAL_RE = re.compile(r'{{>[\s]*([\w-]+)_([\w-]+)?[\s]*}}')

    def included_handlebars_files(self, handlebars_infile, context):
        """Return a list of filepaths the given template templates on."""
        # TODO(jlfwong): This loses out on the caching made available in
        # ComputedIncludeInputs.included_files.
        deps = ['third_party/javascript-khansrc/'
                    'handlebars/handlebars.runtime.js']

        # A handlebars include of another handlebars file is never at
        # the 'top level', since {{>...}} and {{#invokePartial ...}}
        # always resolve to functions.
        if self.top_level_only:
            return deps

        with open(ka_root.join(handlebars_infile)) as f:
            contents = f.read()

        for pattern in [self._INVOKE_PARTIAL_RE, self._PARTIAL_RE]:
            for m in pattern.finditer(contents):
                package_name = m.group(1)
                template_name = m.group(2)

                deps.append('javascript/%s-package/%s.handlebars' %
                            (package_name, template_name))

        return deps

    def included_files(self, infile, context):
        infile_parts = infile.split(os.sep)

        pkg_locale = context['{lang}']
        dev = (context['{env}'] != 'prod')

        # Plugins for file x are files we want loaded after x whenever x is
        # used. e.g. jquery.timeago.{{lang}}.js is a plugin dependency of
        # jquery.timeago.js.
        plugins = third_party_js.listed_plugins(infile, pkg_locale, dev)

        if not third_party_js.can_analyze_for_dependencies(infile):
            return (plugins +
                    third_party_js.listed_dependencies(infile, pkg_locale,
                                                       dev))

        # Ensure that if we need to do analysis, we're always
        # resolving dependencies in the source tree, and not in
        # genfiles.
        assert 'genfiles' not in infile_parts, infile

        if infile in _TEST_ENTRY_POINTS:
            # To support the test system allowing us to specify a subset of
            # tests to run, we "lie" about the dependencies of testindex.js
            # here. This forces these dependencies into the dependency tree,
            # thereby adding the source for the test files into the output
            # bundle.
            assert 'testfiles' in context, (
                "context['testfiles'] must be passed and contains a comma "
                "separated list of paths relative to ka-root. Context: %r"
                % context)

            testfiles = context['testfiles'].split(',')

            return super(ComputedJavaScriptInputs, self).included_files(
                infile, context) + testfiles

        if infile.endswith('.handlebars'):
            return self.included_handlebars_files(infile, context)

        return plugins + super(ComputedJavaScriptInputs, self).included_files(
            infile, context)

    def resolve_includee_path(self, abs_includer_path,
                              includee_path, context):
        # If the includee_path has '{{lang}}'/etc in it, we need to
        # resolve it to the actual filename being included.
        if '{{' in includee_path:
            pkg_locale = context['{lang}']
            dev = (context['{env}'] != 'prod')
            includee_path = js_css_packages.util.resolve_filename_vars(
                includee_path, pkg_locale, dev,
                os.path.dirname(os.path.realpath(abs_includer_path)))

        return js_css_packages.require_util.require_to_path(
            includee_path, os.path.realpath(abs_includer_path))

    def trigger_files(self, outfile_name, context):
        triggers = list(super(ComputedJavaScriptInputs, self)
                        .trigger_files(outfile_name, context))

        # Changing third_party_js.py not only affects output (which is why it's
        # in self.other_inputs), it can also affect the dependencies
        # themselves. So if we change that file, we need to recalculate the
        # whole dep graph - hence, it is a trigger file.
        return ['js_css_packages/third_party_js.py'] + triggers


compile_rule.register_compile(
    'BUNDLES (JS)',
    ('genfiles/{compressed_or_readable}_js_bundles_{env}/'
    '{lang}/{{path}}.bundle.js'),
    ComputedJavaScriptInputs('{{path}}.js'),
    compile_js_css_packages.CombineJavaScript())

compile_rule.register_compile(
    'BUNDLES (JSX)',
    ('genfiles/{compressed_or_readable}_js_bundles_{env}/'
    '{lang}/{{path}}.bundle.jsx.js'),
    ComputedJavaScriptInputs('{{path}}.jsx'),
    compile_js_css_packages.CombineJavaScript())
