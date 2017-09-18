"""While most of compile_rule is tested in build_test, some tests are here.

Basically, tests that test whether we can actually create a
compile-rule or not -- independent of what it actually does -- are
tested here.
"""

from __future__ import absolute_import

import kake.build
from kake import compile_rule
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


class CompileRuleTest(testutil.KakeTestBase):
    def test_must_be_in_genfiles(self):
        compile_rule.register_compile(
            'OUTFILE IN GENFILES',
            'genfiles/outfile',
            ['foo/infile'],
            CopyCompile())

        with self.assertRaises(AssertionError):
            compile_rule.register_compile(
                'OUTFILE NOT IN GENFILES',
                'foo/outfile',
                ['foo/infile'],
                CopyCompile())

    def test_must_not_start_with_underscore(self):
        compile_rule.register_compile(
            'UNDERSCORE IN GENFILES SUBDIR',
            'genfiles/dir/_underscore_ok_here',
            ['foo/infile'],
            CopyCompile())

        with self.assertRaises(AssertionError):
            compile_rule.register_compile(
                'UNDERSCORE IN GENFILES MAINDIR',
                'genfiles/_underscore_not_ok_here',
                ['foo/infile'],
                CopyCompile())

    def test_sneaky_underscore(self):
        compile_rule.register_compile(
            'UNDERSCORE VIA VAR',
            'genfiles/{{path}}',
            ['{{path}}'],
            CopyCompile())

        with self.assertRaises(AssertionError):
            kake.build.build('genfiles/_bad_underscore')


if __name__ == '__main__':
    testutil.main()
