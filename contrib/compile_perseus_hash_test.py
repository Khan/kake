# encoding: utf-8
"""Tests for compile_perseus_hash.py"""

from __future__ import absolute_import

import os

from shared.testutil import testsize

from kake import make
import kake.lib.testutil

_PERSEUS_BUILD_CONTENTS = ['abcdefg', 'hijklmn']
_PERSEUS_BUILD_HASHES = ['7ac66c', '7552a3']


class TestBase(kake.lib.testutil.KakeTestBase):
    """Sets up the filesystem."""
    def setUp(self):
        super(TestBase, self).setUp()     # sets up self.tmpdir as ka-root

        os.makedirs(self._abspath('javascript', 'perseus-package'))

        for (version, content) in enumerate(_PERSEUS_BUILD_CONTENTS):
            with open(self._abspath('javascript', 'perseus-package',
                                    'perseus-%d.js' % version), 'w') as f:
                f.write(content)


@testsize.tiny
class CompileTest(TestBase):
    def test_hash_computation(self):
        for (version, perseus_hash) in enumerate(_PERSEUS_BUILD_HASHES):
            make.build('genfiles/compiled_perseus_hash/'
                       'perseus-%d-hash.txt' % version)
            self.assertFile(
                'genfiles/compiled_perseus_hash/perseus-%d-hash.txt' % version,
                perseus_hash)
