"""Tests for compile_zip.py."""
from __future__ import absolute_import

import os
import zipfile

from shared.testutil import testsize

from kake.lib import compile_rule
from kake.lib import compile_zip
import kake.lib.testutil
import kake.make


@testsize.tiny
class TestCompileZip(kake.lib.testutil.KakeTestBase):
    def setUp(self):
        super(TestCompileZip, self).setUp()   # sets up self.tmpdir
        os.makedirs(self._abspath('gdir', 'gdir2'))
        for fname in ('gdir/g1', 'gdir/gdir2/g2', 'a1', 'b1'):
            with open(self._abspath(fname), 'w') as f:
                print >>f, fname

    def test_simple(self):
        compile_rule.register_compile(
            'SIMPLE ZIP',
            'genfiles/simple.zip',
            ['a1', 'gdir/g1'],
            compile_zip.CompileZip())

        kake.make.build('genfiles/simple.zip')
        z = zipfile.ZipFile(self._abspath('genfiles/simple.zip'))
        self.assertEqual(['a1', 'gdir/g1'], z.namelist())
        self.assertEqual('a1\n', z.read('a1'))
        self.assertEqual('gdir/g1\n', z.read('gdir/g1'))

    def test_file_mapper(self):
        compile_rule.register_compile(
            'SUBDIR ZIP',
            'genfiles/subdir.zip',
            ['b1', 'gdir/gdir2/g2'],
            compile_zip.CompileZip(file_mapper=lambda f: 'subdir/' + f))

        kake.make.build('genfiles/subdir.zip')
        z = zipfile.ZipFile(self._abspath('genfiles/subdir.zip'))
        self.assertEqual(['subdir/b1', 'subdir/gdir/gdir2/g2'], z.namelist())
        self.assertEqual('b1\n', z.read('subdir/b1'))
        self.assertEqual('gdir/gdir2/g2\n', z.read('subdir/gdir/gdir2/g2'))

    def test_nix_prefix_dirs(self):
        compile_rule.register_compile(
            'GDIR ZIP',
            'genfiles/gdir.zip',
            ['gdir/g1', 'gdir/gdir2/g2'],
            compile_zip.CompileZip(
                file_mapper=compile_zip.nix_prefix_dirs(1)))

        kake.make.build('genfiles/gdir.zip')
        z = zipfile.ZipFile(self._abspath('genfiles/gdir.zip'))
        self.assertEqual(['g1', 'gdir2/g2'], z.namelist())
        self.assertEqual('gdir/g1\n', z.read('g1'))
        self.assertEqual('gdir/gdir2/g2\n', z.read('gdir2/g2'))
