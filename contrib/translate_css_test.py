"""Test for translate_css.py"""

from __future__ import absolute_import

import json
import os

from kake import make
import kake.lib.testutil
import intl.data


def mirror_css_mock(original_css):
    """Mock out the mirroring to simply reverse the whole CSS."""
    return [l[::-1] for l in original_css[::-1]]


class TestTranslateAndMirrorCss(kake.lib.testutil.KakeTestBase):
    def setUp(self):
        # sets up self.tmpdir as ka-root
        super(TestTranslateAndMirrorCss, self).setUp()

        os.makedirs(self._abspath('css', 'shared_package'))

        with open(self._abspath('css', 'shared_package', 'a.css'), 'w') as f:
            print >>f, '.foo {'
            print >>f, '    margin-left: 10px'
            print >>f, '    margin-right: 20px'
            print >>f, '}'

        with open(self._abspath('css', 'shared_package', 'b.less'), 'w') as f:
            print >>f, '.foo {'
            print >>f, '    .bar {'
            print >>f, '      margin-left: 10px'
            print >>f, '      margin-right: 20px'
            print >>f, '    }'
            print >>f, '}'

        with open(self._abspath('stylesheets-packages.json'), 'w') as f:
            json.dump({
                'shared.css': {'files': ['a.css', 'b.less'],
                               'base_path': '../css/shared_package'},
                }, f)

        self.mock_function('kake.translate_css.mirror_css', mirror_css_mock)

    def test_rtl_mirroring(self):
        """Tests that generated files for right-to-left languages are mirrored.
        """
        lang = intl.data.right_to_left_languages()[0]  # Should be 'he'

        make.build_many([
            ('genfiles/compressed_stylesheets/%s/css/shared_package/a.min.css'
             % lang, {}),
            ('genfiles/compressed_stylesheets/%s/'
             'css/shared_package/b.less.min.css' % lang, {})
        ])
        self.assertFile(
            'genfiles/compressed_stylesheets/%s/css/shared_package/a.min.css'
            % lang,
            '}xp02 :thgir-nigram    xp01 :tfel-nigram    { oof.')

        self.assertFile(
            ('genfiles/compressed_stylesheets/%s/'
             'css/shared_package/b.less.min.css')
            % lang,
            ('}}    '
             'xp02 :thgir-nigram      xp01 :tfel-nigram      '
             '{ rab.    { oof.'))

    def test_rtl_symlinking(self):
        """Tests that all right-to-left languages symlink to the first one."""
        lang1 = intl.data.right_to_left_languages()[0]  # Should be 'he'
        lang2 = intl.data.right_to_left_languages()[1]  # Should be 'ar'

        make.build_many([
            ('genfiles/compressed_stylesheets/%s/css/shared_package/a.min.css'
             % lang1, {}),
            ('genfiles/compressed_stylesheets/%s/css/shared_package/a.min.css'
             % lang2, {})
        ])

        # Check that translated static CSS symlinks correctly
        self.assertEqual(
                '../../../%s/css/shared_package/a.css' % lang1,
                os.readlink(self._abspath(
                    'genfiles', 'compiled_autoprefixed_css', lang2, 'css',
                    'shared_package', 'a.css')))

        # Check that translated & minified static CSS symlinks correctly
        self.assertEqual(
                '../../../%s/css/shared_package/a.min.css' % lang1,
                os.readlink(self._abspath(
                    'genfiles', 'compressed_stylesheets', lang2, 'css',
                    'shared_package', 'a.min.css')))

        make.build_many([
            ('genfiles/compressed_stylesheets/%s/'
             'css/shared_package/b.less.min.css'
             % lang1, {}),
            ('genfiles/compressed_stylesheets/%s/'
             'css/shared_package/b.less.min.css'
             % lang2, {})
        ])

        # Check that translated compiled LESS symlinks correctly
        self.assertEqual(
                '../../../%s/css/shared_package/b.less.css' % lang1,
                os.readlink(self._abspath(
                    'genfiles', 'compiled_autoprefixed_css', lang2,
                    'css', 'shared_package', 'b.less.css')))

        # Check that translated & minified compiled LESS symlinks correctly
        self.assertEqual(
                '../../../%s/css/shared_package/b.less.min.css' % lang1,
                os.readlink(self._abspath(
                    'genfiles', 'compressed_stylesheets', lang2,
                    'css', 'shared_package', 'b.less.min.css')))

        make.build_many([
            ('genfiles/compressed_css_packages_dev/%s/shared-package.css'
             % lang1, {}),
            ('genfiles/compressed_css_packages_dev/%s/shared-package.css'
             % lang2, {})
        ])

        # Check that the entire package links symlinks correctly
        self.assertEqual(
                '../%s/shared-package.css' % lang1,
                os.readlink(self._abspath(
                    'genfiles', 'compressed_css_packages_dev', lang2,
                    'shared-package.css')))
