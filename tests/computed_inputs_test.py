"""Tests for computed_inputs.py."""

from __future__ import absolute_import

import os
import re

try:
    from unittest import mock   # python3
except ImportError:
    import mock

from kake import build
from kake import compile_rule
from kake import computed_inputs
from kake import filemod_db
from kake import project_root
import testutil


class CopyCompile(compile_rule.CompileBase):
    """This tests having multiple input files."""
    def version(self):
        return 1

    def build(self, output_filename, input_filenames, _, context):
        with open(self.abspath(output_filename), 'w') as fout:
            for f in input_filenames:
                with open(self.abspath(f)) as fin:
                    fout.writelines(fin)


class DowncaseCompile(compile_rule.CompileBase):
    def version(self):
        return 1

    def build(self, output_filename, input_filenames, _, context):
        with open(self.abspath(output_filename), 'w') as fout:
            for f in input_filenames:
                with open(self.abspath(f)) as fin:
                    fout.writelines([x.lower() for x in fin])


class ComputedStaticInputs(computed_inputs.ComputedInputsBase):
    def version(self):
        return 1

    def input_patterns(self, outfile_name, context, triggers, changed):
        return ['a1', 'a2']


class ComputedInputsFromVar(computed_inputs.ComputedInputsBase):
    def version(self):
        return 1

    def input_patterns(self, outfile_name, context, triggers, changed):
        return ['a{number}']


class ComputedInputsFromFileContents(computed_inputs.ComputedInputsBase):
    def version(self):
        return 1

    def input_patterns(self, outfile_name, context, triggers, changed):
        with open(project_root.join(triggers[0])) as f:
            content = f.read().strip()
            return content.split(',') if content else []


class ComputedInputsFromChangedContents(computed_inputs.ComputedInputsBase):
    def version(self):
        return 1

    def input_patterns(self, outfile_name, context, triggers, changed):
        retval = []
        for basename in changed:
            with open(project_root.join(basename)) as f:
                content = f.read().strip()
                if content:
                    retval.extend(content.split(','))
        return retval


class ComputedInputsFromContext(computed_inputs.ComputedInputsBase):
    def version(self):
        return 1

    def input_patterns(self, outfile_name, context, triggers, changed):
        return context["usethesefiles"]

    @classmethod
    def used_context_keys(cls):
        return ["usethesefiles"]


class ComputedIncludeInputsSubclass(computed_inputs.ComputedIncludeInputs):
    def version(self):
        return 1


class VarDependentComputedIncludeInputs(computed_inputs.ComputedIncludeInputs):
    def version(self):
        return 1

    def resolve_includee_path(self, abs_include_path, includee_path, context):
        if includee_path != '?.h':
            return (super(VarDependentComputedIncludeInputs, self)
                    .resolve_includee_path(abs_include_path,
                                           includee_path, context))

        if context['{{path}}'].startswith('a'):
            relpath = 'a.h'
        else:
            relpath = os.path.join('includes', 'd.h')

        return os.path.join(os.path.dirname(abs_include_path), relpath)


class NoCommentComputedIncludeInputs(computed_inputs.ComputedIncludeInputs):
    STRIP_COMMENT_RE = re.compile(r'/\*.*?\*/', re.DOTALL)

    def version(self):
        return 1

    def _get_contents_for_analysis(self, infile):
        with open(project_root.join(infile)) as f:
            contents = f.read()

        return re.sub(NoCommentComputedIncludeInputs.STRIP_COMMENT_RE, '',
                      contents)


class TestBase(testutil.KakeTestBase):
    def __init__(self, *args, **kwargs):
        super(TestBase, self).__init__(*args, **kwargs)
        # Whenever we touch a file, we modify its mtime to be a unique
        # number, because sometimes our tests run so fast that if we
        # create a file and then modify it, they end up with the same
        # mtime each time.
        self.mtime = 10000

    def setUp(self):
        super(TestBase, self).setUp()     # sets up self.tmpdir as project-root

        os.makedirs(os.path.join(self.tmpdir, 'genfiles', 'computed_inputs'))

        for filename in ('a1', 'a2', 'b1', 'b2', 'number3'):
            with open(self._abspath(filename), 'w') as f:
                print >>f, '%s: line 1' % filename
                print >>f, '%s: line 2' % filename

        compile_rule.register_compile(
            'STATIC',
            'genfiles/computed_inputs/static',
            ComputedStaticInputs(['a1', 'a2']),
            CopyCompile())

        compile_rule.register_compile(
            'A',
            'genfiles/computed_inputs/a{number}',
            ComputedInputsFromVar(['number3']),
            CopyCompile())

        compile_rule.register_compile(
            'B',
            'genfiles/computed_inputs/b{number}',
            ComputedInputsFromVar(['b{number}'], compute_crc=True),
            CopyCompile())

        compile_rule.register_compile(
            'B TAKE 2',
            'genfiles/computed_2/b{number}',
            ComputedInputsFromVar(['genfiles/computed_inputs/b{number}']),
            CopyCompile())

        compile_rule.register_compile(
            'FROM CONTEXT',
            'genfiles/computed_fromcontext/index',
            ComputedInputsFromContext(['genfiles/computed_inputs/b1']),
            CopyCompile())

        compile_rule.register_compile(
            'COMPUTED',
            'genfiles/computed_inputs/content',
            ComputedInputsFromFileContents(['a1'], compute_crc=True),
            CopyCompile())

        compile_rule.register_compile(
            'CURR',
            'genfiles/computed_inputs/curr',
            ComputedStaticInputs([computed_inputs.CURRENT_INPUTS]),
            CopyCompile())

        compile_rule.register_compile(
            'CURR2',
            'genfiles/computed_inputs/curr2',
            ComputedInputsFromChangedContents(
                ['a1', computed_inputs.CURRENT_INPUTS]),
            CopyCompile())

        compile_rule.register_compile(
            'FORCE',
            'genfiles/computed_inputs/force',
            ComputedStaticInputs(computed_inputs.FORCE),
            CopyCompile())

    def tearDown(self):
        super(TestBase, self).tearDown()

    def _write_to(self, filename, contents):
        """filename is relative to project-root."""
        with open(self._abspath(filename), 'w') as f:
            f.write(contents)

        # Whenever we touch a file, we modify its mtime to be a unique
        # number, because sometimes our tests run so fast that if we
        # create a file and then modify it, they end up with the same
        # mtime each time.  This matters because we have a cache where
        # entries are invalidated when a file's mtime changes, so we
        # need to make sure every content change has an associated
        # mtime change.
        os.utime(self._abspath(filename), (self.mtime, self.mtime))
        self.mtime += 1


class TestCompileRule(TestBase):
    def test_input_trigger_files(self):
        cr = compile_rule.find_compile_rule('genfiles/computed_inputs/static')
        self.assertEqual(['a1', 'a2'], cr.input_trigger_files(
            'genfiles/computed_inputs/static'))
        cr = compile_rule.find_compile_rule('genfiles/computed_inputs/b2')
        self.assertEqual(['b2'], cr.input_trigger_files(
            'genfiles/computed_inputs/b2'))

    def test_current_inputs(self):
        cr = compile_rule.find_compile_rule('genfiles/computed_inputs/curr')
        # Should be empty because CURRENT_INPUTS is empty.
        self.assertEqual([], cr.input_trigger_files(
            'genfiles/computed_inputs/curr'))


class TestComputedInputs(TestBase):
    """Test the ComputeInputsBase subclasses from compile_rule.py"""
    def test_computes_inputs(self):
        cr = compile_rule.find_compile_rule('genfiles/computed_inputs/static')
        self.assertEqual(['a1', 'a2'], cr.input_files(
            'genfiles/computed_inputs/static'))

    def test_no_trigger_changes(self):
        cr = compile_rule.find_compile_rule('genfiles/computed_inputs/static')
        with self.assertCalled(cr.input_patterns.input_patterns, 1):
            self.assertEqual(['a1', 'a2'], cr.input_files(
                'genfiles/computed_inputs/static'))

        # num_calls should be 0 this time: no need to recompute the inputs.
        with self.assertCalled(cr.input_patterns.input_patterns, 0):
            self.assertEqual(['a1', 'a2'], cr.input_files(
                'genfiles/computed_inputs/static'))

    def test_force(self):
        cr = compile_rule.find_compile_rule('genfiles/computed_inputs/static')
        with self.assertCalled(cr.input_patterns.input_patterns, 1):
            self.assertEqual(['a1', 'a2'], cr.input_files(
                'genfiles/computed_inputs/static'))

        # num_calls still be 1 this time because we are forcing it
        with self.assertCalled(cr.input_patterns.input_patterns, 0):
            self.assertEqual(['a1', 'a2'], cr.input_files(
                'genfiles/computed_inputs/static'))

        # num_calls still be 1 this time because we are forcing it
        with self.assertCalled(cr.input_patterns.input_patterns, 1):
            self.assertEqual(['a1', 'a2'], cr.input_files(
                'genfiles/computed_inputs/static', force=True))

    def test_trigger_changes(self):
        cr = compile_rule.find_compile_rule('genfiles/computed_inputs/static')
        with self.assertCalled(cr.input_patterns.input_patterns, 1):
            self.assertEqual(['a1', 'a2'], cr.input_files(
                'genfiles/computed_inputs/static'))

        # a1 is one of the trigger files for this rule.
        self._write_to('a1', '')
        filemod_db.clear_mtime_cache()

        with self.assertCalled(cr.input_patterns.input_patterns, 1):
            self.assertEqual(['a1', 'a2'], cr.input_files(
                'genfiles/computed_inputs/static'))

    def test_trigger_changes_on_genfiles(self):
        cr = compile_rule.find_compile_rule('genfiles/computed_inputs/a2')
        with self.assertCalled(cr.input_patterns.input_patterns, 1):
            self.assertEqual(['a2'],
                             cr.input_files('genfiles/computed_inputs/a2'))

        self._write_to('number3', '')
        filemod_db.clear_mtime_cache()

        with self.assertCalled(cr.input_patterns.input_patterns, 1):
            self.assertEqual(['a2'],
                             cr.input_files('genfiles/computed_inputs/a2'))

    def test_trigger_depends_on_output(self):
        cr = compile_rule.find_compile_rule('genfiles/computed_inputs/b2')
        with self.assertCalled(cr.input_patterns.input_patterns, 1):
            self.assertEqual(['a2'],
                             cr.input_files('genfiles/computed_inputs/b2'))

        self._write_to('b2', '')
        filemod_db.clear_mtime_cache()
        with self.assertCalled(cr.input_patterns.input_patterns, 1):
            self.assertEqual(['a2'],
                             cr.input_files('genfiles/computed_inputs/b2'))

        # But if we touch b1, it doesn't cause a new call
        self._write_to('b1', '')
        filemod_db.clear_mtime_cache()
        with self.assertCalled(cr.input_patterns.input_patterns, 0):
            self.assertEqual(['a2'],
                             cr.input_files('genfiles/computed_inputs/b2'))

    def test_computed_inputs_from_var(self):
        cr = compile_rule.find_compile_rule('genfiles/computed_inputs/a1')
        self.assertEqual(['a1'], cr.input_files(
            'genfiles/computed_inputs/a1'))

        cr = compile_rule.find_compile_rule('genfiles/computed_inputs/a2')
        self.assertEqual(['a2'], cr.input_files(
            'genfiles/computed_inputs/a2'))

        # Make sure that a2 didn't overwrite any info about a1, and
        # didn't cause an unnecessary re-compute.
        cr = compile_rule.find_compile_rule('genfiles/computed_inputs/a1')
        with self.assertCalled(cr.input_patterns.input_patterns, 0):
            self.assertEqual(['a1'], cr.input_files(
                'genfiles/computed_inputs/a1'))

    def test_computed_inputs_from_file(self):
        self._write_to('a1', 'a1,a2,genfiles/fnumber')
        cr = compile_rule.find_compile_rule('genfiles/computed_inputs/content')
        self.assertEqual(['a1', 'a2', 'genfiles/fnumber'], cr.input_files(
            'genfiles/computed_inputs/content'))

        self._write_to('a1', 'b1,genfiles/fletter')
        filemod_db.clear_mtime_cache()

        with self.assertCalled(cr.input_patterns.input_patterns, 1):
            self.assertEqual(
                ['b1', 'genfiles/fletter'],
                cr.input_files('genfiles/computed_inputs/content'))

    def test_current_inputs(self):
        cr = compile_rule.find_compile_rule('genfiles/computed_inputs/curr')
        self.assertEqual(['a1', 'a2'], cr.input_files(
            'genfiles/computed_inputs/curr'))
        self.assertEqual(['a1', 'a2'], cr.input_trigger_files(
            'genfiles/computed_inputs/curr'))

        self._write_to('a1', '')
        filemod_db.clear_mtime_cache()

        with self.assertCalled(cr.input_patterns.input_patterns, 1):
            self.assertEqual(['a1', 'a2'],
                             cr.input_files('genfiles/computed_inputs/curr'))

    def test_growing_current_inputs(self):
        cr = compile_rule.find_compile_rule('genfiles/computed_inputs/curr2')
        self._write_to('a1', '')
        self.assertEqual([], cr.input_files(
            'genfiles/computed_inputs/curr2'))
        self.assertEqual(['a1'], cr.input_trigger_files(
            'genfiles/computed_inputs/curr2'))

        self._write_to('a1', 'b1,number3')
        filemod_db.clear_mtime_cache()

        with self.assertCalled(cr.input_patterns.input_patterns, 1):
            self.assertEqual(['b1', 'number3'], cr.input_files(
                'genfiles/computed_inputs/curr2'))
            self.assertEqual(['a1', 'b1', 'number3'], cr.input_trigger_files(
                'genfiles/computed_inputs/curr2'))

        # Let's keep going!
        self._write_to('b1', 'b2')
        filemod_db.clear_mtime_cache()
        # Since a1 and number3 didn't change, we should only judge based on b1.
        self.assertEqual(['b2'], cr.input_files(
            'genfiles/computed_inputs/curr2'))
        self.assertEqual(['a1', 'b2'], cr.input_trigger_files(
            'genfiles/computed_inputs/curr2'))

    def test_compute_crc(self):
        self._write_to('a1', 'a1,a2,genfiles/fnumber')
        cr = compile_rule.find_compile_rule('genfiles/computed_inputs/content')
        self.assertEqual(['a1', 'a2', 'genfiles/fnumber'], cr.input_files(
            'genfiles/computed_inputs/content'))

        # Adjust the mtime of this file to sometime in the distant past.
        os.utime(os.path.join(self.tmpdir, 'a1'), (1, 1))
        filemod_db.clear_mtime_cache()

        # This should not cause a recompute even though the mtime has
        # changed, because we used compute_crc.
        cr = compile_rule.find_compile_rule('genfiles/computed_inputs/content')
        with self.assertCalled(cr.input_patterns.input_patterns, 0):
            self.assertEqual(['a1', 'a2', 'genfiles/fnumber'], cr.input_files(
                'genfiles/computed_inputs/content'))

    def test_version_change_forces_rebuild(self):
        cr = compile_rule.find_compile_rule('genfiles/computed_inputs/static')
        with self.assertCalled(cr.input_patterns.input_patterns, 1):
            self.assertEqual(['a1', 'a2'], cr.input_files(
                'genfiles/computed_inputs/static'))

        with mock.patch.object(ComputedStaticInputs, 'version', lambda cls: 2):
            with self.assertCalled(cr.input_patterns.input_patterns, 1):
                self.assertEqual(['a1', 'a2'], cr.input_files(
                    'genfiles/computed_inputs/static'))


class TestBuild(TestBase):
    def test_compile_dot_build(self):
        self._write_to('a1', 'b1,b2')

        outfile_name = 'genfiles/computed_inputs/content'
        cr = compile_rule.find_compile_rule(outfile_name)
        var_values = cr.var_values(outfile_name)   # the {var}'s.
        input_filenames = cr.input_files(outfile_name, var_values)
        cr.compile_instance.build(outfile_name, input_filenames,
                                  [outfile_name], var_values)

        self.assertFile(outfile_name,
                        'b1: line 1\nb1: line 2\n'
                        'b2: line 1\nb2: line 2\n')

    def test_build(self):
        self._write_to('a1', 'b2,b1')
        build.build('genfiles/computed_inputs/content')
        self.assertFile('genfiles/computed_inputs/content',
                        'b2: line 1\nb2: line 2\n'
                        'b1: line 1\nb1: line 2\n')

    def test_build_with_context(self):
        cr = compile_rule.find_compile_rule(
            'genfiles/computed_fromcontext/index')

        build.build('genfiles/computed_fromcontext/index', {
            'usethesefiles': ['b2', 'b1']})
        self.assertFile('genfiles/computed_fromcontext/index',
                        'b2: line 1\nb2: line 2\n'
                        'b1: line 1\nb1: line 2\n')

        with self.assertCalled(cr.input_patterns.input_patterns, 1):
            build.build('genfiles/computed_fromcontext/index', {
                'usethesefiles': ['a2', 'a1']})
        self.assertFile('genfiles/computed_fromcontext/index',
                        'a2: line 1\na2: line 2\n'
                        'a1: line 1\na1: line 2\n')

        # But if the context stays the same, we shouldn't re-call.
        with self.assertCalled(cr.input_patterns.input_patterns, 0):
            build.build('genfiles/computed_fromcontext/index', {
                'usethesefiles': ['a2', 'a1']})

        # And also if some unrelated context gets added and/or changed.
        with self.assertCalled(cr.input_patterns.input_patterns, 0):
            build.build('genfiles/computed_fromcontext/index', {
                'usethesefiles': ['a2', 'a1'], 'foo': 1})

        with self.assertCalled(cr.input_patterns.input_patterns, 0):
            build.build('genfiles/computed_fromcontext/index', {
                'usethesefiles': ['a2', 'a1'], 'foo': 2})

    def test_genfile_trigger(self):
        build.build('genfiles/computed_2/b2')
        self.assertFile('genfiles/computed_2/b2',
                        'a2: line 1\na2: line 2\n')

        cr = compile_rule.find_compile_rule('genfiles/computed_2/b2')
        self.assertEqual(['genfiles/computed_inputs/b2'],
                         cr.input_trigger_files('genfiles/computed_2/b2'))
        self.assertEqual(['a2'], cr.input_files('genfiles/computed_2/b2'))

    def test_removed_includes_forces_rebuild(self):
        build.build('genfiles/computed_inputs/static')
        self.assertFile('genfiles/computed_inputs/static',
                        'a1: line 1\na1: line 2\n'
                        'a2: line 1\na2: line 2\n')

        # We have to mock the version too, to force the includes to be
        # recalculated.
        with mock.patch.object(ComputedStaticInputs, 'version', lambda cls: 2):
            with mock.patch.object(ComputedStaticInputs, 'input_patterns',
                                   lambda *args: ['a1']):
                build.build('genfiles/computed_inputs/static')
        self.assertFile('genfiles/computed_inputs/static',
                        'a1: line 1\na1: line 2\n')

    def test_force_rebuild(self):
        cr = compile_rule.find_compile_rule('genfiles/computed_inputs/force')
        with self.assertCalled(cr.input_patterns.input_patterns, 2):
            build.build('genfiles/computed_inputs/force')
            build.build('genfiles/computed_inputs/force')

        # Just to be sure, show that a normal, non-FORCE rule only
        # calls input_patterns once.
        cr = compile_rule.find_compile_rule('genfiles/computed_inputs/static')
        with self.assertCalled(cr.input_patterns.input_patterns, 1):
            build.build('genfiles/computed_inputs/static')
            build.build('genfiles/computed_inputs/static')


class TestComputedIncludes(TestBase):
    def setUp(self):
        super(TestComputedIncludes, self).setUp()

        os.makedirs(self._abspath('includes'))

        with open(self._abspath('a.c'), 'w') as f:
            print >>f, '#include <stdio.h>'
            print >>f, '#include "a.h"'
            print >>f, 'int main() { return 0; }'

        with open(self._abspath('commented.c'), 'w') as f:
            print >>f, '/*'
            print >>f, '#include "a.h"'
            print >>f, '*/'
            print >>f, '#include "includes/d.h"'

        with open(self._abspath('a.h'), 'w') as f:
            print >>f, '#include "includes/b.h"'

        with open(self._abspath(os.path.join('includes', 'b.h')), 'w') as f:
            print >>f, '#include "c.h"'

        with open(self._abspath(os.path.join('includes', 'c.h')), 'w') as f:
            print >>f, '#define AVOID_CIRCULAR_INCLUDE 1'
            print >>f, '#include "b.h"'
            print >>f, '#include "../a.h"'

        with open(self._abspath(os.path.join('includes', 'd.h')), 'w') as f:
            print >>f, '#define MY_USE "hello, world"'

        with open(self._abspath('b.c'), 'w') as f:
            print >>f, '#include "includes/b.h"'

        with open(self._abspath('yelling.loudc'), 'w') as f:
            print >>f, '#INCLUDE "VUVUZELA.C"'
            print >>f, '#INCLUDE "GODZILLA.C"'
            print >>f, 'INT MAIN() { RETURN 0; }'

        with open(self._abspath('vuvuzela.loudc'), 'w') as f:
            print >>f, '#INCLUDE "GODZILLA.C"'

        with open(self._abspath('godzilla.loudc'), 'w') as f:
            print >>f, '#INCLUDE "VUVUZELA.C"'
            print >>f, '#DEFINE LOCALE "ja-JP"'

        with open(self._abspath('magic.c'), 'w') as f:
            print >>f, '#include "?.h"'

        self.includer = computed_inputs.ComputedIncludeInputs(
            '{{path}}.c', r'^#include\s+"(.*?)"', other_inputs=['a1'])

        compile_rule.register_compile('II', 'genfiles/{{path}}.ii',
                                      self.includer, CopyCompile())

        self.genfiles_includer = ComputedIncludeInputsSubclass(
            'genfiles/{{path}}.c', r'^#include\s+"(.*?)"')

        compile_rule.register_compile('GENII', 'genfiles/{{path}}.genii',
                                      self.genfiles_includer, CopyCompile())

        self.var_dep_includer = VarDependentComputedIncludeInputs(
            'magic.c', r'^#include\s+"(.*?)"')

        compile_rule.register_compile('VARII', 'genfiles/{{path}}.varii',
                                      self.var_dep_includer, CopyCompile())

        self.no_comment_dep_includer = NoCommentComputedIncludeInputs(
            '{{path}}.c', r'^#include\s+"(.*?)"')

        compile_rule.register_compile('NCII', 'genfiles/{{path}}.ncii',
                                      self.no_comment_dep_includer,
                                      CopyCompile())

        compile_rule.register_compile('C', 'genfiles/{{path}}.c',
                                      ['{{path}}.loudc'],
                                      DowncaseCompile())

    def test_simple(self):
        cr = compile_rule.find_compile_rule('genfiles/a.ii')
        self.assertEqual(['a.c', 'a.h', 'includes/b.h', 'includes/c.h', 'a1'],
                         cr.input_files('genfiles/a.ii'))

    def test_no_recompute_inputs_if_unchanged(self):
        cr = compile_rule.find_compile_rule('genfiles/a.ii')
        expected = ['a.c', 'a.h', 'includes/b.h', 'includes/c.h', 'a1']

        with self.assertCalled(cr.input_patterns.input_patterns, 1):
            self.assertEqual(expected, cr.input_files('genfiles/a.ii'))

        with self.assertCalled(cr.input_patterns.input_patterns, 0):
            self.assertEqual(expected, cr.input_files('genfiles/a.ii'))

    def test_recompute_inputs_if_changed(self):
        cr = compile_rule.find_compile_rule('genfiles/a.ii')
        self.assertEqual(['a.c', 'a.h', 'includes/b.h', 'includes/c.h', 'a1'],
                         cr.input_files('genfiles/a.ii'))

        self._write_to('a.h', '#include "includes/d.h"  /* new include */ \n')
        filemod_db.clear_mtime_cache()

        with self.assertCalled(cr.input_patterns.input_patterns, 1):
            self.assertEqual(['a.c', 'a.h', 'includes/d.h', 'a1'],
                             cr.input_files('genfiles/a.ii'))

    def test_include_cache(self):
        cr = compile_rule.find_compile_rule('genfiles/a.ii')
        self.assertEqual(['a.c', 'a.h', 'includes/b.h', 'includes/c.h', 'a1'],
                         cr.input_files('genfiles/a.ii'))

        self._write_to('includes/b.h', '#include "d.h"  /* new include */ \n')
        filemod_db.clear_mtime_cache()

        with mock.patch('kake.log.v3') as logger:
            actual = cr.input_files('genfiles/a.ii')
            self.assertEqual(
                [mock.call('extracting includes from %s', 'includes/b.h'),
                 mock.call('extracting includes from %s', 'includes/d.h')],
                logger.call_args_list)
        self.assertEqual(['a.c', 'a.h', 'includes/b.h', 'includes/d.h', 'a1'],
                         actual)

    def test_version_change_forces_full_rebuild(self):
        cr = compile_rule.find_compile_rule('genfiles/a.ii')
        self.assertEqual(['a.c', 'a.h', 'includes/b.h', 'includes/c.h', 'a1'],
                         cr.input_files('genfiles/a.ii'))

        with mock.patch.object(self.includer, 'version', lambda: 2):
            with mock.patch('kake.log.v3') as logger:
                actual = cr.input_files('genfiles/a.ii')
                self.assertEqual(
                    [mock.call('extracting includes from %s', 'a.c'),
                     mock.call('extracting includes from %s', 'a.h'),
                     mock.call('extracting includes from %s', 'includes/b.h'),
                     mock.call('extracting includes from %s', 'includes/c.h')],
                    logger.call_args_list)

        self.assertEqual(['a.c', 'a.h', 'includes/b.h', 'includes/c.h', 'a1'],
                         actual)

    def test_other_inputs_not_in_triggers(self):
        cr = compile_rule.find_compile_rule('genfiles/a.ii')
        self.assertEqual(['a.c', 'a.h', 'includes/b.h', 'includes/c.h', 'a1'],
                         cr.input_files('genfiles/a.ii'))
        self.assertEqual(['a.c', 'a.h', 'includes/b.h', 'includes/c.h'],
                         list(cr.input_trigger_files('genfiles/a.ii')))

    def test_reading_from_depsfile(self):
        cr = compile_rule.find_compile_rule('genfiles/a.ii')
        self.assertEqual(['a.c', 'a.h', 'includes/b.h', 'includes/c.h', 'a1'],
                         cr.input_files('genfiles/a.ii'))

        # Clearing this cache will force re-reading from the depsfile.
        cr.input_patterns._cached_current_input_patterns = {}
        self.assertEqual(['a.c', 'a.h', 'includes/b.h', 'includes/c.h', 'a1'],
                         cr.input_files('genfiles/a.ii'))

    def test_recompute_inputs_if_regexp_changes(self):
        cr = compile_rule.find_compile_rule('genfiles/a.ii')
        self.assertEqual(['a.c', 'a.h', 'includes/b.h', 'includes/c.h', 'a1'],
                         cr.input_files('genfiles/a.ii'))

        # Now change the compile rule to have a different includer.
        bad_includer = computed_inputs.ComputedIncludeInputs(
            '{{path}}.c', '#bad_include "(.*?)"', other_inputs=['a1'])
        with mock.patch.object(cr, 'input_patterns', bad_includer):
            self.assertEqual(['a.c', 'a1'],
                             cr.input_files('genfiles/a.ii'))

    def test_recompute_inputs_if_other_includes_changes(self):
        cr = compile_rule.find_compile_rule('genfiles/a.ii')
        self.assertEqual(['a.c', 'a.h', 'includes/b.h', 'includes/c.h', 'a1'],
                         cr.input_files('genfiles/a.ii'))

        # Now change the compile rule to have a different includer.
        other_includer = computed_inputs.ComputedIncludeInputs(
            '{{path}}.c', '#include "(.*?)"', other_inputs=['a2'])
        with mock.patch.object(cr, 'input_patterns', other_includer):
            self.assertEqual(['a.c', 'a.h', 'includes/b.h', 'includes/c.h',
                              'a2'],
                             cr.input_files('genfiles/a.ii'))

    def test_recompute_inputs_when_a_bad_include_is_fixed(self):
        # Have a.c have a bad include.
        self._write_to('a.c',
                       '#include <stdio.h>\n'
                       '#include "includes/non_existent_file.h"\n'
                       'int main() { return 0; }\n')

        cr = compile_rule.find_compile_rule('genfiles/a.ii')
        with self.assertRaises(IOError):          # 'file not found'
            cr.input_files('genfiles/a.ii')

        # Now fix up a.c.
        self._write_to('a.c',
                       '#include <stdio.h>\n'
                       '#include "a.h"\n'
                       'int main() { return 0; }\n')
        filemod_db.clear_mtime_cache()

        # We should be able to calculate the new trigger files, even
        # though the last, failed request cached a non-existent file.
        self.assertEqual(['a.c', 'a.h', 'includes/b.h', 'includes/c.h'],
                         list(cr.input_trigger_files('genfiles/a.ii')))

    def test_generated_trigger_files(self):
        # To avoid having build() calls in computed_inputs, it assumes that all
        # the trigger files are built as part of a build call. We're interested
        # in inspecting the results of the input_files() call here, so we need
        # to make sure all the trigger files are already built
        build.build('genfiles/yelling.genii')

        cr = compile_rule.find_compile_rule('genfiles/yelling.genii')
        self.assertEqual(['genfiles/yelling.c',
                          'genfiles/vuvuzela.c',
                          'genfiles/godzilla.c'],
                         cr.input_files('genfiles/yelling.genii'))

    def test_generated_trigger_files_immediate(self):
        # To avoid having build() calls in computed_inputs, it assumes that all
        # the trigger files are built as part of a build call. We're interested
        # in inspecting the results of the input_files() call here, so we need
        # to make sure all the trigger files are already built
        build._immediate_build(['genfiles/yelling.genii'],
                               context={},
                               caller=None,
                               already_built=set(),
                               timing_map={},
                               force=True)

        cr = compile_rule.find_compile_rule('genfiles/yelling.genii')
        self.assertEqual(['genfiles/yelling.c',
                          'genfiles/vuvuzela.c',
                          'genfiles/godzilla.c'],
                         cr.input_files('genfiles/yelling.genii'))

    def test_version_change_on_subclass_forces_complete_rebuild(self):
        build.build('genfiles/yelling.genii')

        with mock.patch.object(self.genfiles_includer, 'version', lambda: 2):
            with mock.patch('kake.log.v3') as logger:
                build.build('genfiles/yelling.genii')
                self.assertIn(mock.call('extracting includes from %s',
                                        'genfiles/vuvuzela.c'),
                              logger.call_args_list)

    def test_recompute_inputs_even_if_mtime_doesnt_change(self):
        cr = compile_rule.find_compile_rule('genfiles/a.ii')
        self.assertEqual(['a.c', 'a.h', 'includes/b.h', 'includes/c.h', 'a1'],
                         cr.input_files('genfiles/a.ii'))

        # modify includes/a.h without modifying its mtime
        mtime = os.path.getmtime(self._abspath('a.h'))
        self._write_to('a.h', '#include "includes/d.h" // change the include')
        os.utime(self._abspath('a.h'), (mtime, mtime))
        filemod_db.clear_mtime_cache()

        self.assertEqual(['a.c', 'a.h', 'includes/d.h', 'a1'],
                         cr.input_files('genfiles/a.ii'))

    def test_recompute_inputs_when_var_changes(self):
        cr = compile_rule.find_compile_rule('genfiles/a.varii')
        with self.assertCalled(cr.input_patterns.resolve_includee_path, 5):
            self.assertEqual(['magic.c',
                              'a.h',
                              'includes/b.h',
                              'includes/c.h'],
                             list(cr.input_trigger_files('genfiles/a.varii')))

        with self.assertCalled(cr.input_patterns.resolve_includee_path, 1):
            self.assertEqual(['magic.c',
                              'includes/d.h'],
                             list(cr.input_trigger_files('genfiles/d.varii')))

        # Both sets of contexts should be in the include cache now
        with self.assertCalled(cr.input_patterns.resolve_includee_path, 0):
            self.assertEqual(['magic.c',
                              'a.h',
                              'includes/b.h',
                              'includes/c.h'],
                             list(cr.input_trigger_files('genfiles/a.varii')))

        with self.assertCalled(cr.input_patterns.resolve_includee_path, 0):
            self.assertEqual(['magic.c',
                              'includes/d.h'],
                             list(cr.input_trigger_files('genfiles/d.varii')))

    def test_modifying_content_pre_analysis(self):
        cr = compile_rule.find_compile_rule('genfiles/a.ii')
        nccr = compile_rule.find_compile_rule('genfiles/a.ncii')

        self.assertEqual(
            ['commented.c',
             'a.h',
             'includes/d.h',
             'includes/b.h',
             'includes/c.h'],
            list(cr.input_trigger_files('genfiles/commented.ii')))
        self.assertEqual(
            ['commented.c',
             'includes/d.h'],
            list(nccr.input_trigger_files('genfiles/commented.ncii')))


if __name__ == '__main__':
    testutil.main()
