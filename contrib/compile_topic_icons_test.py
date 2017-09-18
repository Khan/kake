# TODO(colin): fix these lint errors (http://pep8.readthedocs.io/en/release-1.7.x/intro.html#error-codes)
# pep8-disable:E128
"""Tests for compile_topic_icons.py"""

from __future__ import absolute_import

import cStringIO
import json
import os
import shutil

import PIL.Image
from shared.testutil import testsize

from kake import make
from kake.lib import compile_rule
import kake.lib.testutil
from topic_icons import icon_util


FAKE_HEAD = 'a4fe8ce585e1ddfdf179a77641fa6a2709eff22b'

icon_manifest = {
    'sizes': ['128c', '416', '608', '800', '1200'],
    'formats': {
        'png': ['png'],
        'jpg': ['jpeg'],
        'jpeg': ['jpeg'],
    },
    'inherited_icons': {
        'a': 'foo.png',
        'c': 'baz.jpg',
        'b': 'bar.png',
        'd': 'foo.png',
    },
    'non_inherited_icons': {
        'e': 'wop.png',
    },
    'md5sums': {
        'foo.png': '3f5aaa',
        'baz.jpg': '43e0e1',
        'wop.png': '19e37f',
        'bar.png': '31a6b7',
    },
    'base_url': 'https://cdn.kastatic.org/genfiles/topic-icons/icons/',
    'webapp_commit': FAKE_HEAD,
}

_MAX_WIDTH = max([w for (w, _) in icon_util.SUPPORTED_SIZE_CONFIGS])
_MAX_HEIGHT = ((_MAX_WIDTH * icon_util.ASPECT_RATIO[1]) /
    icon_util.ASPECT_RATIO[0])


def _img_to_string(image, format='png'):
    string_io = cStringIO.StringIO()
    image.save(string_io, format=format)
    return string_io.getvalue()


class TopicIconsTest(kake.lib.testutil.KakeTestBase):

    """Sets up the filesystem."""
    def setUp(self):
        super(TopicIconsTest, self).setUp()

        self._copy_to_test_tmpdir(os.path.join(
            'kake', 'compile_topic_icons-testfiles'))

        # Mock out the compression pipeline with a no-op so as to avoid making
        # all the compression binaries hard dependencies of these tests.
        self.mock_function('deploy.pngcrush.main',
                           lambda f: None)

        # Mock out the resize step with a pre-resized image, to avoid making
        # ImageMagick a hard dependency of these tests.
        def _replace_with_resized(input_path, output_path, width, height,
                                  quality):
            resized_input_filename = '%s-%s-%s' % (
                os.path.basename(input_path), width, height)
            resized_input_path = self._abspath(
                os.path.join('resized', resized_input_filename))

            shutil.copyfile(resized_input_path, self._abspath(output_path))

        self.mock_function('topic_icons.icon_util._resize_raster_image',
                           _replace_with_resized)


@testsize.medium
class CompileTopicIconsTest(TopicIconsTest):

    """Sets up the filesystem."""
    def setUp(self):
        super(CompileTopicIconsTest, self).setUp()

    def _build_icon_with_name(self, filename, size_config=(_MAX_WIDTH, False),
                              format='png'):
        """Build an icon through Kake and return the resulting image."""
        size_config_str = icon_util.serialize_size_config(size_config)
        outfile = 'genfiles/topic-icons/icons-src/%s.%s.%s' % (
            filename, size_config_str, format)
        make.build(outfile)
        return PIL.Image.open(self._abspath(outfile))

    def test_validate_icon(self):
        # Try to build an icon that's too narrow.
        with self.assertRaises(icon_util.TopicIconDimensionException):
            self._build_icon_with_name('narrow.png')

        # Try to build an icon that's too short.
        with self.assertRaises(icon_util.TopicIconDimensionException):
            self._build_icon_with_name('short.png')

        # Make an icon that's sufficiently large, but misshapen.
        with self.assertRaises(icon_util.TopicIconDimensionException):
            self._build_icon_with_name('misshapen.png')

        # Make an icon that's sufficiently large and has the appropriate aspect
        # ratio.
        self._build_icon_with_name('large.png')

    def test_resizing(self):
        target_width = _MAX_WIDTH / 2

        # Verify that images are properly square-cropped.
        output_image = self._build_icon_with_name(
            'bar.png', size_config=(target_width, True))
        self.assertEqual(output_image.size, (target_width, target_width))

        # Verify that images are properly shrunk.
        output_image = self._build_icon_with_name(
            'bar.png', size_config=(target_width, False))
        target_height = ((target_width * icon_util.ASPECT_RATIO[1]) /
            icon_util.ASPECT_RATIO[0])
        self.assertEqual(output_image.size, (target_width, target_height))

    def test_formats(self):
        # Validate that a PNG icon can be built as a PNG.
        self._build_icon_with_name('bar.png', format='png')

        # Validate that a JPG icon can be built as a JPEG.
        self._build_icon_with_name('baz.jpg', format='jpeg')

        # Validate that a PNG icon cannot be built as a JPEG.
        with self.assertRaises(compile_rule.CompileFailure):
            self._build_icon_with_name('bar.png', format='jpeg')

        # Validate that a JPG icon cannot be built as a PNG.
        with self.assertRaises(compile_rule.CompileFailure):
            self._build_icon_with_name('baz.jpg', format='png')

    def test_compression(self):
        # Mock out the compression call to swap out the image with a different
        # but valid built icon. It doesn't really matter what image we use here
        # as the 'compressed' output; we we just want to verify that the
        # compressed image is what's output by Kake.
        compressed_image = self._build_icon_with_name('foo.png')

        def compression_mock(filenames):
            self.assertEqual(len(filenames), 1,
                "Each icon should be compressed individually.")

            with open(filenames[0], 'w') as f:
                compressed_image.save(f, format='png')

        self.mock_function('deploy.pngcrush.main', compression_mock)

        output_image = self._build_icon_with_name('bar.png')

        self.assertEqual(
            _img_to_string(compressed_image), _img_to_string(output_image))


@testsize.medium
class CompileTopicIconManifestTest(TopicIconsTest):

    """Sets up the filesystem."""
    def setUp(self):
        super(CompileTopicIconManifestTest, self).setUp()

        # Mock out the Git object.
        self.mock_function('deploy.git_util.Git.current_version',
                           lambda self: FAKE_HEAD)

    def test_create_manifest(self):
        make.build('genfiles/topic-icons/icon-manifest.json')

        self.assertFile('genfiles/topic-icons/icon-manifest.json',
                        json.dumps(icon_manifest, sort_keys=True))
