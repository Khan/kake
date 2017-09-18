"""Tests for build.py and compile_rule.py."""


from __future__ import absolute_import

import json
import os
import resource
import shutil
import unittest

from kake import build
from kake import compile_rule
from kake import computed_inputs
import kake.filemod_db
import testutil


# Matches what make.py does.
def _build_many(outfile_names_and_contexts, num_processes=1, force=False,
                checkpoint_interval=None):
    kake.filemod_db.clear_mtime_cache()
    return build.build_with_optional_checkpoints(
        outfile_names_and_contexts, num_processes, force, checkpoint_interval)


def _build(outfile_name, context={}, num_processes=1, force=False,
           checkpoint_interval=None):
    return _build_many(
        [(outfile_name, context)], num_processes, force, checkpoint_interval)


class CopyCompile(compile_rule.CompileBase):
    """This tests having multiple input files."""
    def version(self):
        return 1

    def _build(self, output_filename, input_filenames, _, context):
        with open(self.abspath(output_filename), 'w') as fout:
            for f in input_filenames:
                with open(self.abspath(f)) as fin:
                    fout.writelines(fin)

    def build(self, output_filename, input_filenames, _, context):
        self._build(output_filename, input_filenames, _, context)

    def build_many(self, output_input_changed_context):
        for (output_filename, input_filenames, changed, context) in (
                output_input_changed_context):
            self._build(output_filename, input_filenames, changed, context)

    def num_outputs(self):
        return 2


class RevCompile(compile_rule.CompileBase):
    """This tests self.call."""
    def version(self):
        return 1

    def build(self, output_filename, input_filenames, _, context):
        assert len(input_filenames) == 1, input_filenames
        with open(self.abspath(input_filenames[0])) as inf:
            with open(self.abspath(output_filename), 'w') as outf:
                self.call(['rev'], stdin=inf, stdout=outf)


class CopyWithVarCompile(compile_rule.CompileBase):
    """This tests using context."""
    def version(self):
        return 1

    def build(self, output_filename, input_filenames, _, context):
        assert len(input_filenames) == 1, input_filenames
        infile = context['{letter}'] + context['{number}']
        assert infile == input_filenames[0], (infile, input_filenames[0])
        shutil.copy(self.abspath(infile), self.abspath(output_filename))


class WriteContext(compile_rule.CompileBase):
    def version(self):
        return 1

    def build(self, output_filename, input_filenames, _, context):
        with open(self.abspath(output_filename), 'w') as f:
            print >>f, context['content']

    @classmethod
    def used_context_keys(cls):
        return ['content']


class WriteContextInputMap(compile_rule.CompileBase):
    def version(self):
        return 1

    def build(self, output_filename, input_filenames, _, context):
        with open(self.abspath(output_filename), 'w') as f:
            json.dump(context['_input_map'], f)


class WriteBasedOnFilenameMany(compile_rule.CompileBase):
    def version(self):
        return 1

    # This needs to be build_many to properly test the ofile limit.
    def build_many(self, output_input_changed_context):
        for (output_filename, input_filenames, _, context) in (
                output_input_changed_context):
            with open(self.abspath(output_filename), 'w') as f:
                print >>f, context['{content}']

    def num_outputs(self):
        return 100000


class WriteBasedOnFilenameSplit(compile_rule.CompileBase):
    def version(self):
        return 1

    # This needs to be build_many to properly test the ofile limit.
    def build_many(self, output_input_changed_context):
        for (output_filename, input_filenames, _, context) in (
                output_input_changed_context):
            with open(self.abspath(output_filename), 'w') as f:
                print >>f, context['{content}']

    def split_outputs(self, output_input_changed_context, num_processes):
        yield output_input_changed_context


class ManuallySplit(compile_rule.CompileBase):
    def version(self):
        return 1

    def build_many(self, output_input_changed_context):
        for (output_filename, input_filenames, _, context) in (
                output_input_changed_context):
            with open(self.abspath(output_filename), 'w') as f:
                print >>f, input_filenames

    def split_outputs(self, output_input_changed_context, num_processes):
        sorted_outputs = sorted(output_input_changed_context)
        chunk_size = ((len(sorted_outputs) - 1) / num_processes) + 1
        for i in xrange(0, len(sorted_outputs), chunk_size):
            yield sorted_outputs[i:i + chunk_size]


class FailToBuild(compile_rule.CompileBase):
    def version(self):
        return 1

    def build_many(self, output_input_changed_context):
        if len(output_input_changed_context) > 1:
            raise Exception('Too many arguments for my poor head!')

        for (output_filename, input_filenames, _, context) in (
                output_input_changed_context):
            with open(self.abspath(output_filename), 'w') as f:
                print >>f, input_filenames

    def num_outputs(self):
        return 1000


class TestBase(testutil.KakeTestBase):
    def setUp(self):
        super(TestBase, self).setUp()     # sets up self.tmpdir as ka-root

        for filename in ('a1', 'a2', 'b1', 'b2', 'number3'):
            with open(self._abspath(filename), 'w') as f:
                print >>f, '%s: line 1' % filename
                print >>f, '%s: line 2' % filename

        self.copy_compile = CopyCompile()
        self.rev_compile = RevCompile()
        self.write_context = WriteContext()

        # i_number_1 depends on a1 and b1, likewise i_number_2.
        compile_rule.register_compile('NUMBER',
                                      'genfiles/i_number_{number}',
                                      ['a{number}', 'b{number}'],
                                      self.copy_compile)

        # i_letter_a depends on a1 and a2, likewise i_letter_b.
        compile_rule.register_compile('LETTER',
                                      'genfiles/i_letter_{letter}',
                                      ['{letter}1', '{letter}2'],
                                      self.copy_compile)

        # fnumber depends on i_number_1 and i_number_2
        compile_rule.register_compile('FNUMBER',
                                      'genfiles/fnumber',
                                      ['genfiles/i_number_1',
                                       'genfiles/i_number_2'],
                                      self.copy_compile)

        # fletter depends on i_letter_a and i_letter_b
        compile_rule.register_compile('FLETTER',
                                      'genfiles/fletter',
                                      ['genfiles/i_letter_a',
                                       'genfiles/i_letter_b'],
                                      self.copy_compile)

        # It uses a different instance of CopyCompile() in order to test
        # that case in TestDependencyChunking.
        compile_rule.register_compile('FMOST',
                                      'genfiles/fmost',
                                      ['genfiles/i_letter_a', 'b1'],
                                      CopyCompile())

        # bnumber depends on oletter, bletter depends on onumber
        compile_rule.register_compile('BLETTER',
                                      'genfiles/bletter',
                                      ['genfiles/fletter'],
                                      self.rev_compile)
        compile_rule.register_compile('BNUMBER',
                                      'genfiles/bnumber',
                                      ['genfiles/fnumber'],
                                      self.rev_compile,
                                      non_input_deps=['genfiles/i_letter_a'])

        # For testing the num-variables code, and crazy maybe_symlink_to.
        compile_rule.register_compile('I3',
                                      'genfiles/i_number_3',
                                      ['number3'],
                                      self.rev_compile,
                                      maybe_symlink_to='genfiles/bletter')

        # Something that uses two variables
        compile_rule.register_compile('BOTH',
                                      'genfiles/both_{letter}_{number}',
                                      ['{letter}{number}'],
                                      CopyWithVarCompile(),
                                      maybe_symlink_to='{letter}{number}')

        # Something that uses a user's context
        compile_rule.register_compile('CONTEXT',
                                      'genfiles/context_content_{number}',
                                      [],
                                      self.write_context)

        # Testing {var} vs {{var}}
        compile_rule.register_compile('DOUBLE-BRACE',
                                      'genfiles/path/{{path}}.js',
                                      ['{{path}}2'],
                                      self.rev_compile)
        compile_rule.register_compile('SINGLE-BRACE',
                                      'genfiles/dir/{path}.js',
                                      ['{path}2'],
                                      self.rev_compile)

        # Testing building a lot of files at the same level.
        # We want to test build_many() and split_files()
        compile_rule.register_compile('BASED_ON_FILENAME (MANY)',
                                      'genfiles/filename_content.m.{content}',
                                      [],
                                      WriteBasedOnFilenameMany())
        compile_rule.register_compile('BASED_ON_FILENAME (SPLIT_OUTPUTS)',
                                      'genfiles/filename_content.s.{content}',
                                      [],
                                      WriteBasedOnFilenameSplit())
        compile_rule.register_compile('200 FILES (BUILD_MANY)',
                                      'genfiles/200files.build_many',
                                      ['genfiles/filename_content.m.%d' % i
                                       for i in xrange(200)],
                                      self.copy_compile)
        compile_rule.register_compile('200 FILES (SPLIT_OUTPUTS)',
                                      'genfiles/200files.split_outputs',
                                      ['genfiles/filename_content.s.%d' % i
                                       for i in xrange(200)],
                                      self.copy_compile)

    def tearDown(self):
        super(TestBase, self).tearDown()


class TestCompileRule(TestBase):
    def test_find_compile_rule(self):
        cr = compile_rule.find_compile_rule('genfiles/i_letter_a')
        self.assertEqual('genfiles/i_letter_{letter}', cr.output_pattern)
        cr = compile_rule.find_compile_rule('genfiles/fmost')
        self.assertEqual('genfiles/fmost', cr.output_pattern)

    def test_find_compile_rule_prefers_longer_extensions(self):
        # Something that uses one vs two vs three extensions
        compile_rule.register_compile('ONE DOT',
                                      'genfiles/subdir/{{path}}.2',
                                      ['{{path}}2'],
                                      self.copy_compile)
        compile_rule.register_compile('TWO DOTS',
                                      'genfiles/{{path}}.copy.2',
                                      ['{{path}}2'],
                                      self.rev_compile)
        compile_rule.register_compile('TWO DOTS NO VARS',
                                      'genfiles/most_specific.copy.2',
                                      ['most_specific2'],
                                      self.rev_compile)

        cr = compile_rule.find_compile_rule('genfiles/subdir/make_a.copy.2')
        self.assertEqual('genfiles/{{path}}.copy.2', cr.output_pattern)
        cr = compile_rule.find_compile_rule('genfiles/subdir/make_a.dupe.2')
        self.assertEqual('genfiles/subdir/{{path}}.2', cr.output_pattern)
        cr = compile_rule.find_compile_rule('genfiles/most_specific.copy.2')
        self.assertEqual('genfiles/most_specific.copy.2', cr.output_pattern)

    def test_find_compile_rule_prefers_more_directory_parts(self):
        # Something that uses two vs three directory parts
        compile_rule.register_compile('TWO DIRS',
                                      'genfiles/{{path}}_copy_2',
                                      ['{{path}}2'],
                                      self.copy_compile)
        compile_rule.register_compile('THREE DIRS',
                                      'genfiles/subdir/{{path}}_copy_2',
                                      ['genfiles/subdir/{{path}}2'],
                                      self.rev_compile)

        cr = compile_rule.find_compile_rule('genfiles/make_a_copy_2')
        self.assertEqual('genfiles/{{path}}_copy_2', cr.output_pattern)
        cr = compile_rule.find_compile_rule('genfiles/whatever/make_a_copy_2')
        self.assertEqual('genfiles/{{path}}_copy_2', cr.output_pattern)
        cr = compile_rule.find_compile_rule('genfiles/subdir/make_a_copy_2')
        self.assertEqual('genfiles/subdir/{{path}}_copy_2', cr.output_pattern)

    def test_find_compile_rule_prefers_fewer_vars(self):
        cr = compile_rule.find_compile_rule('genfiles/i_number_1')
        self.assertEqual('genfiles/i_number_{number}', cr.output_pattern)
        cr = compile_rule.find_compile_rule('genfiles/i_number_2')
        self.assertEqual('genfiles/i_number_{number}', cr.output_pattern)
        cr = compile_rule.find_compile_rule('genfiles/i_number_3')
        self.assertEqual('genfiles/i_number_3', cr.output_pattern)
        cr = compile_rule.find_compile_rule('genfiles/both_a_1')
        self.assertEqual('genfiles/both_{letter}_{number}', cr.output_pattern)

    def test_labels_must_be_unique(self):
        compile_rule.register_compile('NON-UNIQUE LABEL',
                                      'genfiles/{{path}}_copy_2',
                                      ['{{path}}2'],
                                      self.copy_compile)
        with self.assertRaises(AssertionError):
            compile_rule.register_compile('NON-UNIQUE LABEL',
                                          'genfiles/subdir/{{path}}_copy_2',
                                          ['genfiles/subdir/{{path}}2'],
                                          self.rev_compile)

    def test_matches(self):
        cr = compile_rule.find_compile_rule('genfiles/i_number_1')
        self.assertTrue(cr.matches('genfiles/i_number_1'))
        self.assertTrue(cr.matches('genfiles/i_number_2'))
        self.assertTrue(cr.matches('genfiles/i_number_1000'))
        self.assertFalse(cr.matches('genfiles/i_letter_1'))
        self.assertFalse(cr.matches('genfiles/i_letter_a'))

        cr = compile_rule.find_compile_rule('genfiles/both_number_1')
        self.assertTrue(cr.matches('genfiles/both_number_1'))
        self.assertTrue(cr.matches('genfiles/both_number_2'))
        self.assertTrue(cr.matches('genfiles/both_whatever_2'))
        self.assertFalse(cr.matches('genfiles/both_whatever'))

    def test_single_brace(self):
        cr = compile_rule.find_compile_rule('genfiles/path/foo.js')
        self.assertNotEqual(None, cr)
        cr = compile_rule.find_compile_rule('genfiles/path/subpath/foo.js')
        self.assertNotEqual(None, cr)
        cr = compile_rule.find_compile_rule('genfiles/dir/bar.js')
        self.assertNotEqual(None, cr)
        cr = compile_rule.find_compile_rule('genfiles/dir/subdir/bar.js')
        self.assertEqual(None, cr)

    def test_var_with_underscore(self):
        # Testing {var_with_underscore}
        compile_rule.register_compile('VAR WITH UNDERSCORE',
                                      'genfiles/us/{var_with_underscore}.js',
                                      ['{var_with_underscore}2'],
                                      self.rev_compile)

        cr = compile_rule.find_compile_rule('genfiles/us/foo.js')
        self.assertNotEqual(None, cr)
        self.assertEqual({'{var_with_underscore}': 'foo'},
                         cr.var_values('genfiles/us/foo.js'))

    def test_var_values(self):
        cr = compile_rule.find_compile_rule('genfiles/i_number_1')
        self.assertEqual({'{number}': '1'},
                         cr.var_values('genfiles/i_number_1'))
        cr = compile_rule.find_compile_rule('genfiles/i_number_2')
        self.assertEqual({'{number}': '2'},
                         cr.var_values('genfiles/i_number_2'))
        cr = compile_rule.find_compile_rule('genfiles/i_number_3')
        self.assertEqual({}, cr.var_values('genfiles/i_number_3'))
        cr = compile_rule.find_compile_rule('genfiles/both_a_1')
        self.assertEqual({'{number}': '1', '{letter}': 'a'},
                         cr.var_values('genfiles/both_a_1'))

        cr = compile_rule.find_compile_rule('genfiles/path/subpath/foo.js')
        self.assertEqual({'{{path}}': 'subpath/foo'},
                         cr.var_values('genfiles/path/subpath/foo.js'))
        cr = compile_rule.find_compile_rule('genfiles/dir/bar.js')
        self.assertEqual({'{path}': 'bar'},
                         cr.var_values('genfiles/dir/bar.js'))

    def test_input_files(self):
        cr = compile_rule.find_compile_rule('genfiles/i_number_1')
        self.assertEqual(['a1', 'b1'], cr.input_files('genfiles/i_number_1'))
        cr = compile_rule.find_compile_rule('genfiles/i_number_2')
        self.assertEqual(['a2', 'b2'], cr.input_files('genfiles/i_number_2'))
        cr = compile_rule.find_compile_rule('genfiles/i_number_3')
        self.assertEqual(['number3'], cr.input_files('genfiles/i_number_3'))
        cr = compile_rule.find_compile_rule('genfiles/both_a_1')
        self.assertEqual(['a1'], cr.input_files('genfiles/both_a_1'))

    def test_input_globs(self):
        # This will match a1, a2, b1, b2
        compile_rule.register_compile('BOTH GLOB',
                                      'genfiles/both_glob',
                                      ['??'],
                                      self.copy_compile)

        cr = compile_rule.find_compile_rule('genfiles/both_glob')
        self.assertEqual(['a1', 'a2', 'b1', 'b2'],
                         cr.input_files('genfiles/both_glob'))

    def test_outfile_must_live_in_genfiles(self):
        with self.assertRaises(AssertionError):
            compile_rule.register_compile('NOT GENFILES GLOB',
                                          'not_genfiles/genfiles_glob',
                                          ['genfiles/*'],
                                          CopyCompile())

    def test_cannot_glob_over_generated_files(self):
        with self.assertRaises(AssertionError):
            compile_rule.register_compile('GENFILES GLOB',
                                          'genfiles/genfiles_glob',
                                          ['genfiles/*'],
                                          CopyCompile())

    def test_maybe_symlink_to(self):
        cr = compile_rule.find_compile_rule('genfiles/both_a_1')
        self.assertEqual('a1', cr.maybe_symlink_to('genfiles/both_a_1'))

        cr = compile_rule.find_compile_rule('genfiles/i_number_2')
        self.assertEqual(None, cr.maybe_symlink_to('genfiles/i_number_2'))

    def test_compute_crc(self):
        compile_rule.register_compile('COMPUTE CRC',
                                      'genfiles/has_crc',
                                      ['??'],
                                      self.copy_compile,
                                      compute_crc=True)

        cr = compile_rule.find_compile_rule('genfiles/has_crc')
        self.assertTrue(cr.compute_crc)

        cr = compile_rule.find_compile_rule('genfiles/both_a_1')
        self.assertFalse(cr.compute_crc)

    def test_trumped_by(self):
        compile_rule.register_compile('TRUMPED_BY',
                                      'genfiles/{{path}}.dot.js',
                                      ['??'],
                                      self.copy_compile,
                                      trumped_by=['DOUBLE-BRACE'])
        cr = compile_rule.find_compile_rule('genfiles/path/foo.dot.js')
        self.assertEqual('DOUBLE-BRACE', cr.label)


class TestCompileInstance(TestBase):
    def test_manual_split(self):
        build_args = []
        for filename in ('e', 'z', 'a', 'f', 'f2', 'g', 'h', 'j', '!', '~'):
            build_args.append((filename, [], [], {}))

        m = ManuallySplit()
        actual = list(m.split_outputs(build_args, 3))
        expected = [[('!', [], [], {}),
                     ('a', [], [], {}),
                     ('e', [], [], {}),
                     ('f', [], [], {})],
                    [('f2', [], [], {}),
                     ('g', [], [], {}),
                     ('h', [], [], {}),
                     ('j', [], [], {})],
                    [('z', [], [], {}),
                     ('~', [], [], {}),
                     ]]
        self.assertEqual(expected, actual)


class TestCompileDotBuild(TestBase):
    """Test the FooCompile.build() and build_many() methods."""
    def _build(self, outfile_name):
        cr = compile_rule.find_compile_rule(outfile_name)
        var_values = cr.var_values(outfile_name)   # the {var}'s.
        input_filenames = cr.input_files(outfile_name, var_values)
        cr.compile_instance.build(outfile_name, input_filenames,
                                  [outfile_name], var_values)

    def _build_many(self, *outfile_names):
        build_many_args = []
        compile_instance = None
        for outfile_name in outfile_names:
            cr = compile_rule.find_compile_rule(outfile_name)
            if compile_instance is None:
                compile_instance = cr.compile_instance
            else:
                # All of the outfiles must share the same compile instance
                self.assertEqual(compile_instance.__class__,
                                 cr.compile_instance.__class__)
            var_values = cr.var_values(outfile_name)
            input_filenames = cr.input_files(outfile_name, var_values)
            build_many_args.append((outfile_name, input_filenames,
                                    [outfile_name], var_values))
        compile_instance.build_many(build_many_args)

    def test_build_simple(self):
        self._build('genfiles/i_number_1')
        self.assertFile('genfiles/i_number_1',
                        'a1: line 1\na1: line 2\nb1: line 1\nb1: line 2\n')

    def test_build_missing_dep(self):
        with self.assertRaises(IOError):
            self._build('genfiles/i_number_7')

    def test_build_chain(self):
        self._build('genfiles/i_number_1')
        self._build('genfiles/i_number_2')
        self._build('genfiles/fnumber')
        self._build('genfiles/bnumber')
        self.assertFile('genfiles/i_number_1',
                        'a1: line 1\na1: line 2\nb1: line 1\nb1: line 2\n')
        self.assertFile('genfiles/i_number_2',
                        'a2: line 1\na2: line 2\nb2: line 1\nb2: line 2\n')
        self.assertFile('genfiles/fnumber',
                        'a1: line 1\na1: line 2\nb1: line 1\nb1: line 2\n'
                        'a2: line 1\na2: line 2\nb2: line 1\nb2: line 2\n')
        self.assertFile('genfiles/bnumber',
                        '1 enil :1a\n2 enil :1a\n1 enil :1b\n2 enil :1b\n'
                        '1 enil :2a\n2 enil :2a\n1 enil :2b\n2 enil :2b\n')

    def test_vars_in_context(self):
        self._build('genfiles/both_a_1')
        self.assertFile('genfiles/both_a_1',
                        'a1: line 1\na1: line 2\n')

    def test_build_many(self):
        self._build_many('genfiles/i_number_1', 'genfiles/i_number_2',
                         'genfiles/i_letter_a', 'genfiles/i_letter_b')
        self.assertFile('genfiles/i_number_1',
                        'a1: line 1\na1: line 2\nb1: line 1\nb1: line 2\n')
        self.assertFile('genfiles/i_number_2',
                        'a2: line 1\na2: line 2\nb2: line 1\nb2: line 2\n')
        self.assertFile('genfiles/i_letter_a',
                        'a1: line 1\na1: line 2\na2: line 1\na2: line 2\n')
        self.assertFile('genfiles/i_letter_b',
                        'b1: line 1\nb1: line 2\nb2: line 1\nb2: line 2\n')


class TestDependencyGraph(TestBase):
    def _add_to_dependency_graph(self, outfile, dependency_graph):
        dependency_graph.add_file(outfile, {}, set(), {}, False)

    def _files(self, dependency_graph):
        return [f for (f, _) in dependency_graph.items()]

    def test_simple(self):
        graph = build.DependencyGraph()
        self._add_to_dependency_graph('genfiles/i_letter_1', graph)
        self.assertEqual(['genfiles/i_letter_1'], self._files(graph))
        self.assertEqual(1, graph._get('genfiles/i_letter_1').level)

    def test_chained_deps(self):
        graph = build.DependencyGraph()
        self._add_to_dependency_graph('genfiles/bletter', graph)
        expected = ['genfiles/bletter', 'genfiles/fletter',
                    'genfiles/i_letter_a', 'genfiles/i_letter_b']
        self.assertItemsEqual(expected, self._files(graph))
        self.assertEqual(1, graph._get('genfiles/i_letter_a').level)
        self.assertEqual(1, graph._get('genfiles/i_letter_b').level)
        self.assertEqual(2, graph._get('genfiles/fletter').level)
        self.assertEqual(3, graph._get('genfiles/bletter').level)

    def test_non_input_deps(self):
        graph = build.DependencyGraph()
        self._add_to_dependency_graph('genfiles/bnumber', graph)
        expected = ['genfiles/bnumber', 'genfiles/fnumber',
                    'genfiles/i_number_1', 'genfiles/i_number_2',
                    'genfiles/i_letter_a']
        self.assertItemsEqual(expected, self._files(graph))
        self.assertEqual(1, graph._get('genfiles/i_number_1').level)
        self.assertEqual(1, graph._get('genfiles/i_number_2').level)
        self.assertEqual(2, graph._get('genfiles/fnumber').level)
        self.assertEqual(3, graph._get('genfiles/bnumber').level)
        self.assertEqual(1, graph._get('genfiles/i_letter_a').level)

    def test_overlapping_deps(self):
        graph = build.DependencyGraph()
        self._add_to_dependency_graph('genfiles/bletter', graph)
        self._add_to_dependency_graph('genfiles/fmost', graph)
        expected = ['genfiles/bletter', 'genfiles/fletter',
                    'genfiles/i_letter_a', 'genfiles/i_letter_b',
                    'genfiles/fmost']
        self.assertItemsEqual(expected, self._files(graph))
        self.assertEqual(1, graph._get('genfiles/i_letter_a').level)
        self.assertEqual(1, graph._get('genfiles/i_letter_b').level)
        self.assertEqual(2, graph._get('genfiles/fletter').level)
        self.assertEqual(3, graph._get('genfiles/bletter').level)
        self.assertEqual(2, graph._get('genfiles/fmost').level)

    def test_circular_dep(self):
        # testing circular deps
        compile_rule.register_compile('CIRCULAR 1',
                                      'genfiles/circular1',
                                      ['genfiles/circular2'],
                                      self.copy_compile)
        compile_rule.register_compile('CIRCULAR 2',
                                      'genfiles/circular2',
                                      ['genfiles/circular1'],
                                      self.copy_compile)

        graph = build.DependencyGraph()
        with self.assertRaises(build.CompileFailure):
            self._add_to_dependency_graph('genfiles/circular1', graph)

    def test_maybe_symlink_to_has_a_lower_level(self):
        graph = build.DependencyGraph()
        self._add_to_dependency_graph('genfiles/i_number_3', graph)
        expected = ['genfiles/bletter', 'genfiles/fletter',
                    'genfiles/i_letter_a', 'genfiles/i_letter_b',
                    'genfiles/i_number_3']
        self.assertItemsEqual(expected, self._files(graph))
        self.assertEqual(4, graph._get('genfiles/i_number_3').level)
        self.assertEqual(3, graph._get('genfiles/bletter').level)


class TestDependencyChunking(TestBase):
    def test_complex(self):
        graph = build.DependencyGraph()
        graph.add_file('genfiles/bletter', {}, set(), {}, False)
        graph.add_file('genfiles/bnumber', {}, set(), {}, False)
        graph.add_file('genfiles/fmost', {}, set(), {}, False)
        chunks = list(build._deps_to_compile_together(graph))

        expected = ['genfiles/i_number_1', 'genfiles/i_number_2',
                    'genfiles/i_letter_a', 'genfiles/i_letter_b']
        self.assertItemsEqual(expected, [f for (f, deprule) in chunks[0]])

        # fmost is at the same level as fletter and fnumber, but is
        # chunked differently because it's a different compile-rule.
        # Either one could come first.
        if chunks[1][0][0] == 'genfiles/fmost':
            expected1 = ['genfiles/fmost']
            expected2 = ['genfiles/fletter', 'genfiles/fnumber']
        else:
            expected1 = ['genfiles/fletter', 'genfiles/fnumber']
            expected2 = ['genfiles/fmost']
        self.assertItemsEqual(expected1, [f for (f, deprule) in chunks[1]])
        self.assertItemsEqual(expected2, [f for (f, deprule) in chunks[2]])

        expected = ['genfiles/bletter', 'genfiles/bnumber']
        self.assertItemsEqual(expected, [f for (f, deprule) in chunks[3]])


class TestBuild(TestBase):
    def test_build_simple(self):
        actual = _build('genfiles/i_number_1')
        self.assertEqual(['genfiles/i_number_1'], actual)
        self.assertFile('genfiles/i_number_1',
                        'a1: line 1\na1: line 2\nb1: line 1\nb1: line 2\n')

    def test_build_missing_dep(self):
        with self.assertRaises(IOError):
            _build('genfiles/i_number_7')

    def test_build_chain(self):
        _build('genfiles/bnumber')
        self.assertFile('genfiles/i_number_1',
                        'a1: line 1\na1: line 2\nb1: line 1\nb1: line 2\n')
        self.assertFile('genfiles/i_number_2',
                        'a2: line 1\na2: line 2\nb2: line 1\nb2: line 2\n')
        self.assertFile('genfiles/fnumber',
                        'a1: line 1\na1: line 2\nb1: line 1\nb1: line 2\n'
                        'a2: line 1\na2: line 2\nb2: line 1\nb2: line 2\n')
        self.assertFile('genfiles/bnumber',
                        '1 enil :1a\n2 enil :1a\n1 enil :1b\n2 enil :1b\n'
                        '1 enil :2a\n2 enil :2a\n1 enil :2b\n2 enil :2b\n')

    def test_build_many(self):
        # We can do two copies at a time, and one rev at a time.
        # We have 4 chunks:
        #   1) i_number_* and i_letter_*, 4 files, copy: 2 build_many calls
        #   2) fmost: uses a different copy_compile, not counted
        #   3) fletter, fnumber: 2 files, copy: 1 build_many call
        #   4) bletter, bnumber: 2 files, rev: 2 build calls
        with self.assertCalled(self.copy_compile.build, 0):
            with self.assertCalled(self.copy_compile.build_many, 3):
                with self.assertCalled(self.rev_compile.build, 2):
                    with self.assertCalled(self.rev_compile.build_many, 0):
                        actual = _build_many([('genfiles/bnumber', {}),
                                              ('genfiles/bletter', {}),
                                              ('genfiles/fmost', {})])
                        self.assertItemsEqual(['genfiles/bnumber',
                                               'genfiles/bletter',
                                               'genfiles/fmost'],
                                              actual)

    def test_build_many_when_not_all_need_rebuilding(self):
        _build('genfiles/bnumber')
        actual = _build_many([('genfiles/bnumber', {}),
                              ('genfiles/bletter', {}),
                              ('genfiles/fmost', {})])
        self.assertItemsEqual(['genfiles/bletter', 'genfiles/fmost'], actual)

    def test_vars_in_context(self):
        _build('genfiles/both_a_1')
        self.assertFile('genfiles/both_a_1',
                        'a1: line 1\na1: line 2\n')

    def test_version(self):
        _build('genfiles/i_number_1')
        self.assertFile('genfiles/i_number_1',
                        'a1: line 1\na1: line 2\nb1: line 1\nb1: line 2\n')

        def new_build(cls, output_filename, input_filenames, _, context):
            with open(CopyCompile.abspath(output_filename), 'w') as fout:
                print >>fout, 'Version 2 baybee!'

        old_build = CopyCompile._build
        old_version = CopyCompile.version
        try:
            CopyCompile._build = new_build
            _build('genfiles/i_number_1')
            # Shouldn't see any changes yet: filemod_db doesn't see any
            # diffs because we haven't upped the version number.
            self.assertFile('genfiles/i_number_1',
                            'a1: line 1\na1: line 2\n'
                            'b1: line 1\nb1: line 2\n')

            CopyCompile.version = lambda cls: 2
            _build('genfiles/i_number_1')
            self.assertFile('genfiles/i_number_1',
                            'Version 2 baybee!\n')
        finally:
            CopyCompile._build = old_build
            CopyCompile.version = old_version

    def test_non_input_deps(self):
        _build('genfiles/bnumber')
        # This should have built i_letter_a too, its non-input dep.
        self.assertFile('genfiles/i_letter_a',
                        'a1: line 1\na1: line 2\na2: line 1\na2: line 2\n')

        # But if we change i_letter_a, bnumber shouldn't need to be
        # rebuilt.
        os.unlink(self._abspath('genfiles', 'i_letter_a'))

        # No build calls for bnumber.
        with self.assertCalled(self.copy_compile.build, 0):
            _build('genfiles/bnumber')

        # i_letter_a should have been rebuilt.
        self.assertFile('genfiles/i_letter_a',
                        'a1: line 1\na1: line 2\na2: line 1\na2: line 2\n')

    def test_immediate_build(self):
        class ComputedInput(computed_inputs.ComputedInputsBase):
            def __init__(self, test_method, *args):
                super(ComputedInput, self).__init__(*args)
                self.test_method = test_method

            def version(self):
                return 1

            def input_patterns(self, outfile_name, context, triggers, changed):
                # Not only should our trigger have been built, so should
                # its non-input deps.
                self.test_method.assertFile('genfiles/i_letter_a',
                                            ('a1: line 1\na1: line 2\n'
                                             'a2: line 1\na2: line 2\n'))
                return ['genfiles/i_number_2']

        # By having genfiles/bnumber be a trigger-file to compute the
        # inputs to bnumber.rev, we force bnumber to be immediate-built.
        # This should also cause us to build its non-input dep, i_letter_a.
        compile_rule.register_compile('IMMEDIATE',
                                      'genfiles/i_number_2.rev',
                                      ComputedInput(self,
                                                    ['genfiles/bnumber']),
                                      self.rev_compile)
        _build('genfiles/i_number_2.rev')
        # This should also have built i_number_2, which is what the
        # ComputedInput returns.
        self.assertFile('genfiles/i_number_2',
                        'a2: line 1\na2: line 2\nb2: line 1\nb2: line 2\n')

        # Make sure that immediate builds can also raise an exception when
        # these is no matching rule
        compile_rule.register_compile('IMMEDIATE2',
                                      'genfiles/not_a_real_file.bmp',
                                      ComputedInput(
                                          self,
                                          ['genfiles/not_real_either']),
                                      self.rev_compile)

        with self.assertRaises(compile_rule.NoBuildRuleCompileFailure):
            _build('genfiles/not_a_real_file.bmp')

    def test_force_recomputes_inputs(self):
        input_filenames = ['genfiles/i_number_2']

        class ComputedInput(computed_inputs.ComputedInputsBase):
            def __init__(self, *args):
                super(ComputedInput, self).__init__(*args)

            def version(self):
                return 1

            def input_patterns(self, outfile_name, context, triggers, changed):
                return input_filenames

        computed_input = ComputedInput(['a1'])

        compile_rule.register_compile('IMMEDIATE',
                                      'genfiles/i_number_2.rev',
                                      computed_input,
                                      self.copy_compile)

        # We should be calling build twice, once for i_number_2.rev and once
        # for the input 'genfiles/i_number_2'
        with self.assertCalled(self.copy_compile._build, 2):
            _build('genfiles/i_number_2.rev')

        self.assertFileDoesNotExist('genfiles/i_number_1')
        self.assertFile('genfiles/i_number_2',
                        'a2: line 1\na2: line 2\nb2: line 1\nb2: line 2\n')

        # Now lets change the computed input file names
        input_filenames = ['genfiles/i_number_2', 'genfiles/i_number_1']

        # Rebuilding should not call _build at all because the previous inputs
        # and the trigger have not changed.
        with self.assertCalled(self.copy_compile._build, 0):
            _build('genfiles/i_number_2.rev')

        self.assertFileDoesNotExist('genfiles/i_number_1')
        self.assertFile('genfiles/i_number_2',
                        'a2: line 1\na2: line 2\nb2: line 1\nb2: line 2\n')

        # With the force flag though we should be recalculating the inputs and
        # build them both and i_number_2.rev
        with self.assertCalled(self.copy_compile._build, 3):
            _build('genfiles/i_number_2.rev', force=True)

        self.assertFile('genfiles/i_number_1',
                        'a1: line 1\na1: line 2\nb1: line 1\nb1: line 2\n')
        self.assertFile('genfiles/i_number_2',
                        'a2: line 1\na2: line 2\nb2: line 1\nb2: line 2\n')

    def test_different_contexts(self):
        _build_many([('genfiles/context_content_1', {'content': 'hello'}),
                     ('genfiles/context_content_2', {'content': 'world'})])
        self.assertFile('genfiles/context_content_1', 'hello\n')
        self.assertFile('genfiles/context_content_2', 'world\n')

    def test_maybe_symlink_to(self):
        compile_rule.register_compile('SYMLINK',
                                      'genfiles/bletter_symlink',
                                      ['genfiles/fletter'],
                                      self.rev_compile,
                                      maybe_symlink_to='genfiles/bletter')

        _build('genfiles/bletter_symlink')
        self.assertTrue(
            os.path.islink(self._abspath('genfiles', 'bletter_symlink')))
        self.assertEqual(
            os.path.join('bletter'),
            os.readlink(self._abspath('genfiles', 'bletter_symlink')))

        # Check that when we rebuild, it doesn't try to symlink again.
        with self.assertCalled(os.symlink, 0):
            _build('genfiles/bletter_symlink')

    # Since we spawn subprocesses, we have to run inside the 'main'
    # process of the test framework itself.
    def test_multiprocessing_build(self):
        # Make sure we only create two new processes for everything.
        with self.assertCalled('os.fork', 2):
            _build('genfiles/bletter', num_processes=2)

        # Make sure all the files were made.
        self.assertFile('genfiles/i_letter_a',
                        'a1: line 1\na1: line 2\na2: line 1\na2: line 2\n')
        self.assertFile('genfiles/i_letter_b',
                        'b1: line 1\nb1: line 2\nb2: line 1\nb2: line 2\n')
        self.assertFile('genfiles/fletter',
                        'a1: line 1\na1: line 2\na2: line 1\na2: line 2\n'
                        'b1: line 1\nb1: line 2\nb2: line 1\nb2: line 2\n')
        self.assertFile('genfiles/bletter',
                        '1 enil :1a\n2 enil :1a\n1 enil :2a\n2 enil :2a\n'
                        '1 enil :1b\n2 enil :1b\n1 enil :2b\n2 enil :2b\n')

    def test_multiprocessing_build_many(self):
        _build_many([('genfiles/bletter', {}), ('genfiles/bnumber', {})],
                    num_processes=2)

        self.assertFile('genfiles/i_letter_a',
                        'a1: line 1\na1: line 2\na2: line 1\na2: line 2\n')
        self.assertFile('genfiles/i_letter_b',
                        'b1: line 1\nb1: line 2\nb2: line 1\nb2: line 2\n')
        self.assertFile('genfiles/fletter',
                        'a1: line 1\na1: line 2\na2: line 1\na2: line 2\n'
                        'b1: line 1\nb1: line 2\nb2: line 1\nb2: line 2\n')
        self.assertFile('genfiles/bletter',
                        '1 enil :1a\n2 enil :1a\n1 enil :2a\n2 enil :2a\n'
                        '1 enil :1b\n2 enil :1b\n1 enil :2b\n2 enil :2b\n')

        self.assertFile('genfiles/i_number_1',
                        'a1: line 1\na1: line 2\nb1: line 1\nb1: line 2\n')
        self.assertFile('genfiles/i_number_2',
                        'a2: line 1\na2: line 2\nb2: line 1\nb2: line 2\n')
        self.assertFile('genfiles/fnumber',
                        'a1: line 1\na1: line 2\nb1: line 1\nb1: line 2\n'
                        'a2: line 1\na2: line 2\nb2: line 1\nb2: line 2\n')
        self.assertFile('genfiles/bnumber',
                        '1 enil :1a\n2 enil :1a\n1 enil :1b\n2 enil :1b\n'
                        '1 enil :2a\n2 enil :2a\n1 enil :2b\n2 enil :2b\n')

    def test_multiprocessing_build_many_for_one_rule(self):
        _build_many([('genfiles/i_letter_a', {})], num_processes=2)
        self.assertFile('genfiles/i_letter_a',
                        'a1: line 1\na1: line 2\na2: line 1\na2: line 2\n')

    def test_we_die_if_buildmany_fails_but_not_during_binary_search(self):
        compile_rule.register_compile('FAIL',
                                      'genfiles/fail*',
                                      ['a1'],
                                      FailToBuild())
        with self.assertRaises(Exception):
            _build_many([('genfiles/fail1', {}),
                         ('genfiles/fail2', {}),
                         ('genfiles/fail3', {}),
                         ('genfiles/fail4', {})])

    def test_input_map(self):
        # Something that uses a 'system var' in the context.
        compile_rule.register_compile('INPUT MAP',
                                      'genfiles/input_map',
                                      [],
                                      WriteContextInputMap())

        _build_many([('genfiles/input_map', {}),
                     ('genfiles/fletter', {})])
        with open(os.path.join(self.tmpdir, 'genfiles', 'input_map')) as f:
            input_map = json.load(f)
        expected = {'genfiles/fletter': ['genfiles/i_letter_a',
                                         'genfiles/i_letter_b'],
                    'genfiles/i_letter_a': ['a1', 'a2'],
                    'genfiles/i_letter_b': ['b1', 'b2'],
                    'genfiles/input_map': []}
        self.assertEqual(expected, input_map)

    def test_rebuild_only_when_context_changes(self):
        with self.assertCalled(self.write_context.build, 1):
            _build('genfiles/context_content_1', {'content': 'foo'})
            _build('genfiles/context_content_1', {'content': 'foo'})

        with self.assertCalled(self.write_context.build, 2):
            _build('genfiles/context_content_2', {'content': 'foo'})
            _build('genfiles/context_content_2', {'content': 'bar'})
            self.assertFile('genfiles/context_content_2', 'bar\n')

    def test_no_rebuild_when_irrelevant_context_changes(self):
        with self.assertCalled(self.write_context.build, 1):
            _build('genfiles/context_content_1',
                   {'content': 'foo', 'irrelevant': True})
            _build('genfiles/context_content_1',
                   {'content': 'foo', 'irrelevant': False})

    def test_checkpointing(self):
        # We do one sync after every 'stage', which for bletter is 3.
        # And then one sync at the end.
        with self.assertCalled(kake.filemod_db.sync, 4):
            _build('genfiles/bletter', {}, checkpoint_interval=0)

    def test_slow_checkpointing(self):
        # The whole build takes less than 100 seconds, so we don't do
        # any checkpoint syncs (except for the standard one at the
        # end of the build).
        with self.assertCalled(kake.filemod_db.sync, 1):
            _build('genfiles/bletter', {}, checkpoint_interval=100)

    @unittest.skip("Test seems flaky")
    def test_many_open_files(self):
        orig_limits = resource.getrlimit(resource.RLIMIT_NOFILE)
        try:
            # Don't mess with the hard limit, only the soft limit.
            resource.setrlimit(resource.RLIMIT_NOFILE, (100, orig_limits[1]))
            # These should not crash due to 'Too many open files'
            _build('genfiles/200files.build_many', {})
            _build('genfiles/200files.split_outputs', {})
        finally:
            resource.setrlimit(resource.RLIMIT_NOFILE, orig_limits)

    def test_no_rule(self):
        with self.assertRaises(compile_rule.NoBuildRuleCompileFailure):
            _build("genfiles/there_is_no_rule_for_this_file")


if __name__ == '__main__':
    testutil.main()
