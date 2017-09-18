# TODO(colin): fix these lint errors (http://pep8.readthedocs.io/en/release-1.7.x/intro.html#error-codes)
# pep8-disable:E128
"""Test for compress_css.py"""

from __future__ import absolute_import

import base64
import json
import os

import mock

from kake import compress_css
from kake import make
import kake.lib.testutil


TINY_PNG_BASE64 = ('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAACklE'
                   'QVR4nGMAAQAABQABDQottAAAAABJRU5ErkJggg==')

OTHER_PNG_BASE64 = ('iVBORw0KGgoAAAANSUhEUgAAAGUAAACrAQMAAABFUMBIAAAAA1BM'
                    'VEX///+nxBvIAAAAAXRSTlMAQObYZgAAABlJREFUeF7twDEBAAAA'
                    'wiD7p7bETlgEAIADCVoAAWOjykUAAAAASUVORK5CYII=')


_orig_input_patterns = compress_css.ComputedCssInputs.input_patterns


class TestCompressCss(kake.lib.testutil.KakeTestBase):
    def setUp(self):
        super(TestCompressCss, self).setUp()  # sets up self.tmpdir as ka-root

        os.makedirs(self._abspath('css', 'shared_package'))
        os.makedirs(self._abspath('css', 'nonce_package'))
        os.makedirs(self._abspath('css', 'onefile_package'))
        os.makedirs(self._abspath('images'))

        with open(self._abspath('css', 'shared_package', 'a.css'), 'w') as f:
            print >>f, 'background-image: url("/images/tiny.png")'
            print >>f, 'background-image:url(/images/other.png)'
            print >>f, 'background-image: url(/images/another.png)'

        # images that are mentioned 3 times should never be inlined
        with open(self._abspath('css', 'shared_package', 'b.css'), 'w') as f:
            print >>f, 'background-image: url("/images/other.png")'
            print >>f, 'background-image: url(\'/images/another.png\')'

        with open(self._abspath('css', 'nonce_package', 'c.css'), 'w') as f:
            print >>f, 'fg-image: url(/images/other.png); /*! data-uri */'
            print >>f, 'background-image: url(/images/another.png)'

        with open(self._abspath('css', 'onefile_package', 'd.css'), 'w') as f:
            print >>f, 'fg-image: url(/images/used_in_one_file.png);'
            print >>f, 'fg-image: url(/images/used_in_one_file.png);'
            print >>f, 'fg-image: url(/images/used_in_one_file.png);'
            print >>f, 'fg-image: url(/images/used_in_one_file.png);'
            print >>f, 'fg-image: url(/images/used_in_one_file.png);'
            print >>f, 'fg-image: url(/images/used_in_one_file.png);'
            print >>f, 'fg-image: url(/images/used_in_one_file.png);'
            print >>f, 'fg-image: url(/images/used_in_one_file.png);'

        with open(self._abspath('images', 'tiny.png'), 'w') as f:
            f.write(base64.b64decode(TINY_PNG_BASE64))
        with open(self._abspath('images', 'other.png'), 'w') as f:
            f.write(base64.b64decode(OTHER_PNG_BASE64))
        with open(self._abspath('images', 'another.png'), 'w') as f:
            f.write(base64.b64decode(OTHER_PNG_BASE64))
        with open(self._abspath('images', 'used_in_one_file.png'), 'w') as f:
            f.write(base64.b64decode(TINY_PNG_BASE64))

        # We need to make a tiny json file to get the image url info from.
        compress_css._IMAGE_URL_INFO.clear()
        with open(self._abspath('stylesheets-packages.json'), 'w') as f:
            json.dump({
                'shared.css': {'files': ['a.css', 'b.css'],
                               'base_path': '../css/shared_package'},
                'nonce.css': {'files': ['c.css'],
                              'base_path': '../css/nonce_package'},
                'onefile.css': {'files': ['d.css'],
                              'base_path': '../css/onefile_package'},
                }, f)

    def test_image_info_url(self):
        make.build('genfiles/css_image_url_info.pickle')
        for expected in (('tiny.png', ['a.css'], 67),
                         ('other.png', ['a.css', 'b.css', 'c.css'], 110),
                         ('another.png', ['a.css', 'b.css', 'c.css'], 110)):
            (img_name, css_files, filesize) = expected
            actual = compress_css._IMAGE_URL_INFO.get()['/images/' + img_name]

            self.assertEqual(len(css_files), len(actual[0]), actual)

            for expected_file in css_files:
                self.assertTrue(any(expected_file in f for f in actual[0]))

            self.assertEqual(os.path.join('images', img_name), actual[1])

            self.assertEqual(filesize, actual[2])

    def test_loading_image_info_url(self):
        make.build('genfiles/css_image_url_info.pickle')
        expected = compress_css._IMAGE_URL_INFO.get()

        # We should get this info off disk, not recalculate it
        compress_css._IMAGE_URL_INFO.clear()
        with self.assertCalled(compress_css._update_image_url_info, 0):
            make.build('genfiles/css_image_url_info.pickle')
            self.assertEqual(expected, compress_css._IMAGE_URL_INFO.get())

    def test_image_info_url_after_build(self):
        # The build modifies the _IMAGE_URL_INFO array.
        make.build(
            'genfiles/compressed_stylesheets/en/css/shared_package/a.min.css')
        self.test_image_info_url()

    def test_rebuild_image_info_url_after_delete(self):
        make.build('genfiles/css_image_url_info.pickle')

        # Remove a.css from all packages containing it
        os.unlink(self._abspath('genfiles', 'compiled_autoprefixed_css', 'en',
                                'css', 'shared_package', 'a.css'))
        with open(self._abspath('stylesheets-packages.json')) as f:
            packages = json.load(f)
        for name, pkg in packages.iteritems():
            pkg['files'] = [filename for filename in pkg['files']
                            if filename != 'a.css']
        with open(self._abspath('stylesheets-packages.json'), 'w') as f:
            json.dump(packages, f)

        make.build('genfiles/css_image_url_info.pickle')
        for expected in (('other.png', ['b.css', 'c.css'], 110),
                         ('another.png', ['b.css', 'c.css'], 110)):
            (img_name, css_files, filesize) = expected
            actual = compress_css._IMAGE_URL_INFO.get()['/images/' + img_name]
            # Make sure the a.css entries are gone.
            self.assertEqual(len(css_files), len(actual[0]), actual)

    def test_inline_image_that_occurs_once_but_not_3_times(self):
        make.build(
            'genfiles/compressed_stylesheets/en/css/shared_package/a.min.css')
        self.assertFile(
            'genfiles/compressed_stylesheets/en/css/shared_package/a.min.css',
            ('background-image: url("data:image/png;base64,%s")'
             'background-image:url(/images/other.png)'
             'background-image: url(/images/another.png)'
             % TINY_PNG_BASE64))

    def test_inline_image_with_data_uri(self):
        make.build(
            'genfiles/compressed_stylesheets/en/css/nonce_package/c.min.css')
        self.assertFile(
            'genfiles/compressed_stylesheets/en/css/nonce_package/c.min.css',
            ('fg-image: url(data:image/png;base64,%s);'
             'background-image: url(/images/another.png)'
             % OTHER_PNG_BASE64))

    def test_rebuild_when_image_file_changes(self):
        make.build(
            'genfiles/compressed_stylesheets/en/css/shared_package/a.min.css')
        self.assertFileContains(
            'genfiles/compressed_stylesheets/en/css/shared_package/a.min.css',
            TINY_PNG_BASE64)

        # Now change tiny.png to hold other.png's contents.
        with open(self._abspath('images', 'tiny.png'), 'w') as f:
            f.write(base64.b64decode(OTHER_PNG_BASE64))

        make.build(
            'genfiles/compressed_stylesheets/en/css/shared_package/a.min.css')
        self.assertFileContains(
            'genfiles/compressed_stylesheets/en/css/shared_package/a.min.css',
            OTHER_PNG_BASE64)

    def test_rebuild_when_image_reference_changes(self):
        make.build(
            'genfiles/compressed_stylesheets/en/css/shared_package/b.min.css')

        # Now change b.css to point to tiny.png
        with open(self._abspath('css', 'shared_package', 'b.css'), 'w') as f:
            print >>f, 'background-image: url(/images/tiny.png)'

        make.build(
            'genfiles/compressed_stylesheets/en/css/shared_package/b.min.css')
        self.assertFileContains(
            'genfiles/compressed_stylesheets/en/css/shared_package/b.min.css',
            TINY_PNG_BASE64)

        # Now change tiny.png to hold other.png's contents.  This
        # should force yet another rebuild.
        with open(self._abspath('images', 'tiny.png'), 'w') as f:
            f.write(base64.b64decode(OTHER_PNG_BASE64))

        make.build(
            'genfiles/compressed_stylesheets/en/css/shared_package/b.min.css')
        self.assertFileContains(
            'genfiles/compressed_stylesheets/en/css/shared_package/b.min.css',
            OTHER_PNG_BASE64)

    def test_rebuild_when_image_url_info_changes(self):
        make.build(
            'genfiles/compressed_stylesheets/en/css/shared_package/a.min.css')
        self.assertFileContains(
            'genfiles/compressed_stylesheets/en/css/shared_package/a.min.css',
            TINY_PNG_BASE64)

        # Now add tiny.png to b.css.  We should still inline it since
        # it's so tiny.
        with open(self._abspath('css', 'shared_package', 'b.css'), 'a') as f:
            print >>f, 'background-image: url("/images/tiny.png")'

        make.build(
            'genfiles/compressed_stylesheets/en/css/shared_package/a.min.css')
        self.assertFileContains(
            'genfiles/compressed_stylesheets/en/css/shared_package/a.min.css',
            TINY_PNG_BASE64)

        # Now add tiny.png to d.css as well.  Now it's in enough places
        # we should not be inlining it.
        with open(self._abspath('css', 'onefile_package', 'd.css'), 'a') as f:
            print >>f, 'background-image: url("/images/tiny.png")'

        make.build(
            'genfiles/compressed_stylesheets/en/css/shared_package/a.min.css')
        self.assertFileLacks(
            'genfiles/compressed_stylesheets/en/css/shared_package/a.min.css',
            TINY_PNG_BASE64)

    @mock.patch('kake.compress_css._MAX_INLINE_SIZE', 4)
    def test_max_inline_size(self):
        # With _MAX_INLINE_SIZE == 4, tiny.png is too big to inline!
        make.build(
            'genfiles/compressed_stylesheets/en/css/shared_package/a.min.css')
        # (Our mock cssmin just removes newlines.)
        self.assertFile(
            'genfiles/compressed_stylesheets/en/css/shared_package/a.min.css',
            ('background-image: url("/images/tiny.png")'
             'background-image:url(/images/other.png)'
             'background-image: url(/images/another.png)'))

    @mock.patch('kake.compress_css._MAX_INLINE_SIZE', 4)
    def test_max_inline_size_does_not_affect_manual_inlining(self):
        self.test_inline_image_with_data_uri()

    def test_no_inline_when_image_used_many_times_in_only_one_file(self):
        make.build(
            'genfiles/compressed_stylesheets/en/css/onefile_package/d.min.css')
        self.assertFile(
            'genfiles/compressed_stylesheets/en/css/onefile_package/d.min.css',
            ('fg-image: url(/images/used_in_one_file.png);'
             'fg-image: url(/images/used_in_one_file.png);'
             'fg-image: url(/images/used_in_one_file.png);'
             'fg-image: url(/images/used_in_one_file.png);'
             'fg-image: url(/images/used_in_one_file.png);'
             'fg-image: url(/images/used_in_one_file.png);'
             'fg-image: url(/images/used_in_one_file.png);'
             'fg-image: url(/images/used_in_one_file.png);'))
