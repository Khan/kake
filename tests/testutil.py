"""Utilities for running tests in the kake directory.

In particular, we provide a test-class that makes it easy to create a
'fake' filesystem to run compiles in.  (This is in kake, rather than
at the top level, because it involves filemod-db, which is a kake-only
concept.  That said, it may make sense to move this if needed.)
"""

from __future__ import absolute_import

import contextlib
import os
import shutil
import sys
import tempfile
import types
import unittest

try:
    from unittest import mock   # python3
except ImportError:
    import mock

from kake import compile_rule
from kake import compile_util
from kake import filemod_db
from kake import project_root


main = unittest.main


# A small python script that just prints lines it sees, except for
# @import lines which cause it to include the reader.
_RECURSIVE_PY_CAT = """\
#!/usr/bin/env python
import os, re, sys          # @Nolint(multiple imports on one line)
args = [a for a in sys.argv[1:] if not a.startswith('--')]
with open(args[1], 'w') as outfile:
    def cp(infilename):
        with open(infilename) as infile:
            for line in infile:
                match = re.match(r'^\@import "(.*?)"', line)
                if match:
                    relpath = os.path.normpath(os.path.join(
                        os.path.dirname(infilename), match.group(1)))
                    cp(relpath)
                else:
                    outfile.write(line)

    cp(args[0])

    # And we'll add a sourcemap line like lessc does, too.
    print >>outfile, ('/*# sourceMappingURL=%s.map */'
                      % os.path.basename(args[1]))
"""

_FAKE_AUTOPREFIXER = """\
#!/usr/bin/env python
import sys
assert sys.argv[1] == '-o'
with open(sys.argv[2], 'w') as outfile:
    with open(sys.argv[-1]) as infile:
        for line in infile:
            outfile.write(line)
"""

_FAKE_HANDLEBARS_COMPILER = """\
exports.precompile = function(x) {
    return (
        'function() { return "' +
        x.replace(/"/g, '\\\\"').replace(/\\n/g, '\\\\n') +
        '"; }'
    );
};
"""

_FAKE_BABELJS = """\
var fs = require("fs");
exports.transformFileSync = function(file) {
    return { code: fs.readFileSync(file, { encoding: "utf-8" }) };
};
"""


def _fake_npm_build(self, outfile_infiles_changed_context):
    """Does a fake 'build' that creates simpler versions of npm scripts."""
    node_modules_path = project_root.join('node_modules')
    if not os.path.exists(node_modules_path):
        os.symlink(os.path.join('genfiles', 'node_modules'), node_modules_path)

    for (outfile_name, infile_names, _, _) in outfile_infiles_changed_context:
        if os.path.basename(outfile_name) == 'handlebars.js':
            # kake/compile_handlebars.js does require("handlebars"), so we need
            # to make sure it requires the version in the test sandbox, and not
            # the global version that you might have installed on your system.
            with open(project_root.join('genfiles', 'node_modules',
                                        'handlebars', 'index.js'), 'w') as f:
                print >>f, _FAKE_HANDLEBARS_COMPILER
            # compile_handlebars.py also has a dep on
            # handlebars/lib/handlebars.js (though the fake handlebars
            # compiler never uses it), so create a fake file to make
            # that dep happy.
            open(project_root.join('genfiles', 'node_modules', 'handlebars',
                                   'lib', 'handlebars.js'), 'w').close()
            continue
        elif 'babel-core' in outfile_name.split(os.sep):
            index_file = project_root.join('genfiles', 'node_modules',
                                           'babel-core', 'index.js')
            with open(index_file, 'w') as f:
                print >>f, _FAKE_BABELJS
            with open(project_root.join('genfiles', 'node_modules',
                                        'babel-core', 'package.json'),
                      'w') as f:
                print >>f, '{}'
            continue
        with open(project_root.join(outfile_name), 'w') as f:
            if os.path.basename(outfile_name) == 'lessc':
                # format is lessc --flags <infile> <outfile>.  We
                # follow @import's
                print >>f, _RECURSIVE_PY_CAT
            if os.path.basename(outfile_name) == 'autoprefixer':
                # format is autoprefixer -o <outfile> --map <infile>.  We
                # follow @import's
                print >>f, _FAKE_AUTOPREFIXER
            elif os.path.basename(outfile_name) in ('cssmin', 'uglifyjs'):
                # We'll just have the compressors remove newlines and
                # comments.  We have to run perl from a shell script so
                # we can ignore all the args to cssmin/uglifyjs.
                # Note that in cssmin, /*! ... */ is a directive, not a
                # comment, so we leave it alone.
                print >>f, '#!/bin/sh'
                print >>f, ('perl -e \'$_ = join("", <>);'
                            ' s,\n,,g;'              # newlines
                            ' s,/\*[^!].*?\*/,,g;'   # /* comments */
                            ' s,//.*,,g;'            # // comments
                            'print;\'')
            else:
                # Our script just copies from stdin/argv[1] to stdout.
                # -p does 'cat'.  -s ignores flags (all args starting with -).
                print >>f, '#!/usr/bin/perl -ps'
        os.chmod(project_root.join(outfile_name), o0755)


class KakeTestBase(unittest.TestCase):
    def setUp(self):
        super(KakeTestBase, self).setUp()

        self.create_tmpdir()   # sets self.tmpdir and self.real_project_root
        os.mkdir(os.path.join(self.tmpdir, 'genfiles'))

        # We need a fake app.yaml because the handlebars babel-extract
        # calls route-map which reads app.yaml.
        with open(self._abspath('app.yaml'), 'w') as f:
            f.write("""\
application: khan-academy
runtime: python27
api_version: 1
threadsafe: no
handlers:
- url: .*
  script: ping.application
""")

        filemod_db.reset_for_tests()   # ensure the old _DB isn't lying around
        compile_rule.reset_for_tests()
        compile_util.reset_for_tests()

        # Make sure we reset the compile-rules after each test.
        self.mock_value(
            'kake.compile_rule._COMPILE_RULES',
            {k: v.copy()
             for (k, v) in compile_rule._COMPILE_RULES.iteritems()})
        self.mock_value(
            'kake.compile_rule._COMPILE_RULE_LABELS',
            compile_rule._COMPILE_RULE_LABELS.copy())

        # Useful for, e.g. test_starstar().
        open(self._abspath('package.json'), 'w').close()

    def tearDown(self):
        shutil.rmtree(self.tmpdir)
        self.tmpdir = None
        super(KakeTestBase, self).tearDown()

    def create_tmpdir(self):
        """Create a new directory and set project_root.root to point to it."""
        self.tmpdir = os.path.realpath(
            tempfile.mkdtemp(prefix=(self.__class__.__name__ + '.')))
        self.real_project_root = project_root.root
        self.mock_value('kake.project_root.root', self.tmpdir)
        return self.tmpdir

    def _abspath(self, *args):
        return os.path.join(self.tmpdir, *args)

    def _clean_for_mock_project_root(self):
        """Do extra cleanups to make sure it's safe to mock project-root.

        Lots of things are cached with the current value of project-root:
        sometimes by us, and sometimes by other systems.  We make
        sure that *all* of them are cleaned up whenever we mock
        project-root, both before and after.
        """
        # handlebars/render.py may have done some __import__'s while
        # project-root was mocked, meaning the old location is cached in
        # sys.modules.  To fix that, we just uncache everything in
        # the 'genfiles' directory (where render.py reads from).
        for k in sys.modules.keys():   # make a copy because we mutate
            if k == 'genfiles' or k.startswith('genfiles.'):
                del sys.modules[k]

    def mock_value(self, var_string, value):
        """Mocks var_string to have value value for the remainder of this test.

        var_string can be a global variable, like 'os.path.sep', or it
        can be a class property.

        For a variable like a dict, string, or set, consider:
           mock_value('varname', varname.copy())
        to keep the initial value of varname for the test, but to let
        you add to it for the duration of this test only.

        Arguments:
          var_string: the name of the variable or class property to
             fake out: e.g. 'os.path.sep' or 'os.environ'.
          value: the value for the variable to have for this test.
             You can further modify the value in the test, and at
             the end of the test it will be restored to the value it
             had when mock_value was called.
        """
        patcher = mock.patch(var_string, value)
        # This is better than calling stop() in tearDown(), because it
        # will fire even if an exception is raised in setUp().
        self.addCleanup(patcher.stop)
        retval = patcher.start()

        # If we're mocking project_root.root, we need to do extra cleanup.
        # We do it both before and after the test.
        if var_string == 'kake.project_root.root':
            self._clean_for_mock_project_root()
        self.addCleanup(self._clean_for_mock_project_root)

        return retval

    def mock_function(self, fn_name_string, fn_body):
        """Mocks fn_name_string to be fn_body for the remainder of this test.

        fn_name_string is like 'users.current_user.get_cached'.  It can
        be a free function or a static method of a class.

        Arguments:
          fn_name_string: the name of the function or static method to
             fake out: e.g., 'users.current_user.get_cached'.
          fn_body: the body to execute when this function is called,
             rather than the real body.  Can be a function or lambda.
        """
        # The implementation is the same as for mock_value -- function
        # definitions are just their 'values', after all -- but I give
        # it a different name since the semantics are somewhat
        # different.
        return self.mock_value(fn_name_string, fn_body)

    def _file_contents(self, filename):
        """filename is taken to be relative to project-root."""
        abs_filename = project_root.join(filename)
        self.assertTrue(os.path.exists(abs_filename),
                        '%s not found in %s: %s'
                        % (filename, os.path.dirname(filename),
                           os.listdir(os.path.dirname(abs_filename))))

        with open(abs_filename) as f:
            return f.read()

    def _truncate_value(self, a):
        max_length = 100
        str_a = str(a)
        if len(str_a) <= max_length:
            return str_a
        else:
            return "%s(%i): '%s...%s'" % (
                a.__class__.__name__,
                len(a),
                str_a[:max_length / 2],
                str_a[-max_length / 2:])

    def assertEqualTruncateError(self, a, b):
        """AssertEqual, but limit the size of the error message on failure."""
        assert a == b, "%s != %s" % (self._truncate_value(a),
                                     self._truncate_value(b))

    def assertFile(self, filename, expected):
        """Assert that the contents of 'filename' are exactly 'expected.'"""
        # Don't cap how big of a diff to display
        self.maxDiff = None
        self.assertMultiLineEqual(expected, self._file_contents(filename))

    def assertFileExists(self, filename):
        """Assert that filename, relative to project-root, exists."""
        self.assertTrue(os.path.exists(project_root.join(filename)), filename)

    def assertFileDoesNotExist(self, filename):
        """Assert that filename, relative to project-root, does not exist."""
        self.assertFalse(os.path.exists(project_root.join(filename)), filename)

    @contextlib.contextmanager
    def assertCalled(self, fn, expected_times):
        """Assert than fn is called 'expected_times' times inside this context.

        Arguments:
            fn: a function, a method, or a string representing a fn/method.
              Examples: os.getpid, myclass.meth, 'os.getpid', 'MyClass.meth'.
              If you pass in a method directly (rather than as a
              string), it must be a *bound* method:
              self.object.method, not ObjectClass.method.
            expected_times: how many times you expect fn to be called
              while inside this contextmanager.
        """

        # Sadly, we have to jump through hoops to figure out what to
        # patch with the mock: we can't just say patch(fn, mock) :-(
        # But if the user passed in the function as a string (e.g.:
        # 'os.getpid'), we can definitely make use of that.
        if isinstance(fn, basestring):
            (module_name, fn_name) = fn.rsplit('.', 1)
            __import__(module_name)
            actual_fn = getattr(sys.modules[module_name], fn_name)
            fn_mock = mock.Mock(wraps=actual_fn)
            patcher = mock.patch(fn, fn_mock)

        elif isinstance(fn, (types.FunctionType, types.BuiltinFunctionType)):
            fn_mock = mock.Mock(wraps=fn)
            patcher = mock.patch.object(sys.modules[fn.__module__],
                                        fn.__name__, fn_mock)

        elif isinstance(fn, (types.MethodType, types.BuiltinMethodType)):
            if not fn.im_self:
                raise ValueError('Must use assertCalled with a bound method: '
                                 'foo.method(), not FooClass.method()')
            fn_mock = mock.Mock(wraps=fn)
            patcher = mock.patch.object(fn.im_self, fn.__name__, fn_mock)

        else:
            raise ValueError('Must implement assertCalled for %s' % type(fn))

        try:
            patcher.start()
            yield
            self.assertEqual(expected_times, fn_mock.call_count,
                             '%s: Expected %s calls, found %s'
                             % (fn, expected_times, fn_mock.call_count))
        finally:
            patcher.stop()

