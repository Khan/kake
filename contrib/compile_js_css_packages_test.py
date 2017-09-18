# TODO(colin): fix these lint errors (http://pep8.readthedocs.io/en/release-1.7.x/intro.html#error-codes)
# pep8-disable:E124,E128,E131
"""Test compile_js_css_packages.py.

I use build_prod_main as the driver to run the tests, because that's
the easiest way to do so.
"""

from __future__ import absolute_import

import base64
import codecs
import contextlib
import json
import os
import shutil
import unittest

from shared import ka_root
from shared.testutil import testcase
from shared.testutil import testsize
from third_party import polib

import js_css_packages.packages
from kake import build_prod_main
from kake import compress_css
from kake.lib import filemod_db
import kake.lib.compile_rule
import kake.lib.testutil
import util


# If you make some change that changes the output for ALL tests, then you
# can update all the test outputs by setting this flag to True.
# This will cause the "tests" to write the expected output to files
# instead of diffing it against those files.
# Be sure to examine the diff afterwards to make sure your change made sense!
# Also, make sure to set the flag to False again once you're done!
_RECREATE_EXPECTED_RESULTS = False


class TestNotRecreatingResults(testcase.TestCase):

    def test_not_recreating_results(self):
        """Make sure the recreate-results flag isn't left on by mistake."""
        self.assertFalse(_RECREATE_EXPECTED_RESULTS)


class TestBase(kake.lib.testutil.KakeTestBase):

    def assertFileContentsMatch(self, filename, expected):
        # overridden from shared/testutil/testcase.py
        # to conditionally regenerate expected output
        # (see comments on _RECREATE_EXPECTED_RESULTS)
        if not _RECREATE_EXPECTED_RESULTS:
            return super(TestBase, self).assertFileContentsMatch(
                filename, expected)

        target_path = os.path.join(self.real_ka_root,
                                   'kake',
                                   'compile_js_css_packages-testfiles',
                                   expected)
        util.mkdir_p(os.path.dirname(target_path))
        with open(target_path, 'w') as fout:
            fout.write(self._file_contents(filename))

    def setUp(self):
        super(TestBase, self).setUp()
        self._copy_to_test_tmpdir(os.path.join(
            'kake', 'compile_js_css_packages-testfiles'))

        # We need the kake directory to read compile_handlebars.js, etc.
        # NOTE: We intentionally make a copy instead of symlinking here because
        # node resolves dependencies based on the where the real file is. We
        # want compile_handlebars.js to look inside the sandbox for
        # dependencies, not the real ka-root.
        shutil.copytree(os.path.join(self.real_ka_root, 'kake'),
                        self._abspath('kake'))

        os.makedirs(ka_root.join('js_css_packages'))
        # We copy here because filemod-db doesn't like out-of-tree symlinks.
        f = 'js_css_packages/third_party_js.py'
        shutil.copyfile(os.path.join(self.real_ka_root, f),
                        self._abspath(f))
        self.mock_value('intl.data._LanguageStatus._DID_LANGUAGE_CHECKS',
                        True)

    def write_fakelang_pofile(self):
        # Create a tiny .mo-file so we can build translations.  (I could
        # have just checked this in to compile_js_css_packages-testfiles/,
        # but this way it's easier to edit.)
        pofile = polib.POFile()
        pofile.append(polib.POEntry(msgid='en', msgstr='fake'))
        pofile.append(polib.POEntry(msgid='nail', msgstr='<not a nail>'))
        os.makedirs(self._abspath('intl', 'translations', 'pofiles'))
        pofile.save(self._abspath('intl', 'translations', 'pofiles',
                                 'fakelang.rest.po'))

        # ...and we'll just say that all the translations are also approved
        os.makedirs(self._abspath('intl', 'translations', 'approved_pofiles'))
        pofile.save(self._abspath('intl', 'translations', 'approved_pofiles',
                                  'fakelang.rest.po'))

    def write_simpler_manifest(self):
        # Use our own, simpler, javascript-packages file.
        manifest = {'video.js': {'files': ['video.js', 'modal.js',
                                           't.handlebars']},
                    'shared.js': {'files': ['dev.js']},
                    'third-party.js': {'files': ['jakeweery.js']},
                    'corelibs.js': {'files': ['ka-define.js',
                                              'leading-paren.js']},
                }
        with open(self._abspath('javascript-packages.json'), 'w') as f:
            json.dump(manifest, f)
        return manifest

    @contextlib.contextmanager
    def modify_manifest(self):
        manifest = js_css_packages.packages.read_package_manifest(
            'javascript-packages.json')
        yield manifest     # must modify manifest in-place!
        with open(self._abspath('javascript-packages.json'), 'w') as f:
            json.dump(manifest, f)
        filemod_db.clear_mtime_cache()    # since we modified a file


@testsize.tiny
class TestComputeJsCssInputs(TestBase):
    def setUp(self):
        super(TestComputeJsCssInputs, self).setUp()
        # Use our own, simpler, javascript-packages file.
        self.write_simpler_manifest()

    def test_modifying_manifest(self):
        build_prod_main.main(['video.js'], ['en'], readable=True)
        outfile = self._abspath('genfiles', 'readable_js_packages_prod', 'en',
                                'video-package.js')
        self.assertFileContains(outfile + '.deps', 'video.js')
        self.assertFileContains(outfile, 'youtubeId')   # in video.js

        with self.modify_manifest() as manifest:
            manifest['video.js']['files'] = ['modal.js']  # nix video.js
        build_prod_main.main(['video.js'], ['en'], readable=True)
        # Now the video.js content should be gone.
        self.assertFileLacks(outfile + '.deps', 'video.js')
        self.assertFileLacks(outfile, 'youtubeId')


class TestCompile(TestBase):
    def setUp(self):
        super(TestCompile, self).setUp()

        self.write_fakelang_pofile()

        # It is helpful to know the base64 values of our image files.
        with open(self._abspath('images', 'tiny.png')) as f:
            self.tiny_png_base64 = base64.b64encode(f.read())
        with open(self._abspath('images', 'other.png')) as f:
            self.other_png_base64 = base64.b64encode(f.read())
        self.mock_value('intl.data._LanguageStatus._DID_LANGUAGE_CHECKS',
                        True)

    def _build(self, package_names, readable, languages,
               dev=False, force=False):
        build_prod_main.main(package_names, languages, {}, dev, readable,
                             force=force)

    def test_compress(self):
        # Our mocked-out 'compressor' just gets rid of newlines
        self._build(['shared.js', 'video.css'],
                    readable=False, languages=['en'],
                    dev=True, force=False)
        self.assertFileContentsMatch(
            ka_root.join('genfiles', 'compressed_javascript',
                         'en', 'genfiles', 'compiled_es6', 'en',
                         'javascript', 'shared-package',
                         'dev.min.js'),
             'expected/test_compress/dev.min.js')
        self.assertFileContentsMatch(
            ka_root.join('genfiles', 'compressed_stylesheets',
                         'en', 'stylesheets', 'video-package',
                         'video.less.min.css'),
            'expected/test_compress/video.less.min.css')

    def test_compress_and_combine(self):
        self._build(['js'], readable=False, languages=['en'],
                    dev=True, force=False)
        outfile = 'genfiles/compressed_js_packages_dev/en/video-package.js'
        # Our mocked-out 'compressor' just gets rid of newlines
        self.assertFileContains(
            outfile, 'var Video = {     youtubeId: "en"    };')
        self.assertFileContains(
            outfile, 'var ModalVideo = {t: require("./t.handlebars"); };')
        # A string taken from the compiled handlebars template.
        self.assertFileContains(
            outfile, 'var template = Handlebars.template(function')

    def test_produces_only_requested_files(self):
        # If we only ask for video.js, we should only get video.js
        self._build(['video.js'], readable=True, languages=['en'],
                    dev=True, force=False)
        self.assertFileExists(
            'genfiles/readable_js_packages_dev/en/video-package.js')
        self.assertFileDoesNotExist(
            'genfiles/readable_js_packages_dev/en/shared-package.js')

        self._build(['js'], readable=True, languages=['en'],
                    dev=True, force=False)
        self.assertFileExists(
            'genfiles/readable_js_packages_dev/en/video-package.js')
        self.assertFileExists(
            'genfiles/readable_js_packages_dev/en/shared-package.js')

    def test_dev_false(self):
        self._build(['shared.js'], readable=False, languages=['en'],
                    dev=False, force=False)
        # Our mocked-out 'compressor' just gets rid of newlines
        self.assertFileContentsMatch(
            'genfiles/compressed_js_packages_prod/en/shared-package.js',
            'expected/test_dev_false/shared-package.js')

    def test_dev_true(self):
        self._build(['shared.js'], readable=False, languages=['en'],
                    dev=True, force=False)
        # Our mocked-out 'compressor' just gets rid of newlines
        self.assertFileContentsMatch(
            'genfiles/compressed_js_packages_dev/en/shared-package.js',
            'expected/test_dev_true/shared-package.js')

    def test_combine_only(self):
        self._build(['js'], readable=True, languages=['en'],
                    dev=True, force=False)
        outfile = ka_root.join('genfiles', 'readable_js_packages_dev', 'en',
                               'video-package.js')
        self.assertFileContains(
            outfile, 'var Video = {\n     youtubeId: "en"\n    };')
        self.assertFileContains(
            outfile, 'var ModalVideo = {t: require("./t.handlebars"); };')
        # A string taken from the compiled handlebars template.
        self.assertFileContains(
            outfile, 'var template = Handlebars.template(function')

    def test_another_language(self):
        self._build(['video.js'], readable=False,
                    languages=['fakelang'], dev=True, force=False)
        outfile = ('genfiles/compressed_js_packages_dev/fakelang/'
                   'video-package.js')
        # Our mocked-out 'compressor' just gets rid of newlines
        self.assertFileContains(
            outfile, 'var Video = {     youtubeId: "en"    };')
        self.assertFileContains(
            outfile, 'var ModalVideo = {t: require("./t.handlebars"); };')
        # A string taken from the compiled handlebars template.
        self.assertFileContains(
            outfile, 'var template = Handlebars.template(function')
        # This is the part that should be translated!
        self.assertFileContains(
            outfile, '<not a nail>')

    def test_dev_false_in_another_language(self):
        self._build(['mishmash.js'], readable=False,
                    languages=['fakelang'], dev=False, force=False)
        outfile = ('genfiles/compressed_js_packages_prod/fakelang/'
                   'mishmash-package.js')
        self.assertFileContains(
            outfile, 'var context = function() {     return "prod";    };')

    def test_css(self):
        self._build(['css'], readable=False, languages=['en'],
                    dev=True, force=False)
        # Our mocked-out 'compressor' just gets rid of newlines.
        # But it should also inline our image.
        self.assertFileContentsMatch(
            'genfiles/compressed_css_packages_dev/en/video-package.css',
            'expected/test_css/video-package.css')

    def test_max_inline_size(self):
        old_max_inline_size = compress_css._MAX_INLINE_SIZE
        try:
            compress_css._MAX_INLINE_SIZE = 4   # so now tiny.png is too big!
            self._build(['video.css'], readable=False, languages=['en'],
                        dev=True, force=False)
            self.assertFileContentsMatch(
                'genfiles/compressed_css_packages_dev/en/video-package.css',
                'expected/test_max_inline_size/video-package.css')
        finally:
            compress_css._MAX_INLINE_SIZE = old_max_inline_size

    def test_depends_on_image_file(self):
        self._build(['video.css'], readable=False,
                    languages=['en'], dev=True, force=False)
        # Now replace tiny.png with other.png
        shutil.copy(ka_root.join('images', 'other.png'),
                    ka_root.join('images', 'tiny.png'))
        filemod_db.clear_mtime_cache()    # since we modified a file

        # Now we should rebuild because of the changed image file.
        self._build(['video.css'], readable=False, languages=['en'],
                    dev=True, force=False)
        outfile = 'genfiles/compressed_css_packages_dev/en/video-package.css'
        self.assertFileContains(outfile, self.other_png_base64)

    def test_third_party_no_exports(self):
        self.mock_value('js_css_packages.third_party_js._EXPOSE_REQUIRE',
            {'javascript/third-party-package/jakeweery.js': False})
        self.mock_value('js_css_packages.third_party_js._FILE_TO_EXPORTS',
            {'javascript/third-party-package/jakeweery.js': []})

        self._build(['third-party.js'], readable=True,
                    languages=['en'], dev=True, force=False)
        self.assertFileContentsMatch(
            'genfiles/readable_js_packages_dev/en/third-party-package.js',
            'expected/test_third_party_no_exports/third-party-package.js')

    def test_third_party_one_export(self):
        self.mock_value('js_css_packages.third_party_js._EXPOSE_REQUIRE',
            {'javascript/third-party-package/jakeweery.js': False})
        self.mock_value('js_css_packages.third_party_js._FILE_TO_EXPORTS',
            {'javascript/third-party-package/jakeweery.js': '$'})

        self._build(['third-party.js'], readable=True,
                    languages=['en'], dev=True, force=False)
        self.assertFileContentsMatch(
            'genfiles/readable_js_packages_dev/en/third-party-package.js',
            'expected/test_third_party_one_export/third-party-package.js')

    def test_third_party_two_exports(self):
        self.mock_value('js_css_packages.third_party_js._EXPOSE_REQUIRE',
            {'javascript/third-party-package/jakeweery.js': False})
        self.mock_value('js_css_packages.third_party_js._FILE_TO_EXPORTS',
            {'javascript/third-party-package/jakeweery.js': ['$', 'jQuery']})

        self._build(['third-party.js'], readable=True,
                    languages=['en'], dev=True, force=False)
        self.assertFileContentsMatch(
            'genfiles/readable_js_packages_dev/en/third-party-package.js',
            'expected/test_third_party_two_exports/third-party-package.js')

    def test_evil(self):
        # If this test is failing, make sure no-trailing-newline.js and bom.js
        # aren't modified in any way. Getting rid of the BOM or adding
        # a trailing newline will make this test fail.
        self.mock_value('js_css_packages.third_party_js._EXPOSE_REQUIRE',
            {'javascript/evil-package/no-trailing-newline.js': False,
             'javascript/evil-package/bom.js': False})
        self.mock_value('js_css_packages.third_party_js._FILE_TO_EXPORTS',
            {'javascript/evil-package/no-trailing-newline.js': [],
             'javascript/evil-package/bom.js': []})
        self._build(['evil.js'], readable=True,
                    languages=['en'], dev=True, force=False)
        outfile = 'genfiles/readable_js_packages_dev/en/evil-package.js'
        utf8out = codecs.open(os.path.join(self.tmpdir, outfile), 'r', 'utf-8')
        self.maxDiff = None
        self.assertMultiLineEqual(utf8out.read(),
            u'KAdefine("javascript/evil-package/no-trailing-newline.js", '
                u'function(__KA_require, __KA_module, '
                u'__KA_exports, __KA_persistentData) {\n'
            u'var boop={}\n'
            u'});\n'
            u'KAdefine("javascript/evil-package/bom.js", '
                u'function(__KA_require, __KA_module, '
                u'__KA_exports, __KA_persistentData) {\n'
            # The Byte Order Mark (BOM), if present, must start on its own line
            # otherwise browsers will complain when the source is loaded
            u'\uFEFF// This file starts with a utf-8 Byte Order Mark.\n'
            u'var bomGoesTheDynamite;\n'
            u'});\n')

    def test_ka_define_readable(self):
        self.mock_value('js_css_packages.third_party_js._EXPOSE_REQUIRE',
            {'javascript/corelibs-package/leading-paren.js': False})
        self.mock_value('js_css_packages.third_party_js._FILE_TO_EXPORTS',
            {'javascript/corelibs-package/leading-paren.js': []})

        self._build(['corelibs.js'], readable=True,
                    languages=['en'], dev=True, force=False)
        self.assertFileContentsMatch(
            'genfiles/readable_js_packages_dev/en/corelibs-package.js',
            'expected/test_ka_define_readable/corelibs-package.js')

    def test_with_jsx(self):
        self.mock_value('js_css_packages.third_party_js._EXPOSE_REQUIRE',
            {'javascript/third-party-package/jakeweery.js': False})
        self.mock_value('js_css_packages.third_party_js._FILE_TO_EXPORTS',
            {'javascript/third-party-package/jakeweery.js': '$'})

        self._build(['with-jsx.js'], readable=True,
                    languages=['en'], dev=True, force=False)
        self.assertFileContentsMatch(
            'genfiles/readable_js_packages_dev/en/with-jsx-package.js',
            'expected/test_with_jsx/with-jsx-package.js')

    def test_with_async(self):
        self._build(['with-async.js'], readable=True, languages=['en'],
                    dev=True, force=False)
        outfile = 'genfiles/readable_js_packages_dev/en/with-async-package.js'
        expected = (
            'KAdefine.updatePathToPackageMap({'
            '"javascript/third-party-package/jakeweery.js": "third-party.js", '
            '"javascript/tiny-package/a.js": "tiny.js", '
            '"javascript/tiny-package/b.js": "tiny.js", '
            '"javascript/with-jsx-package/widejet.jsx": "with-jsx.js"});')
        self.assertFileContains(outfile, expected)

        # Also do this with prod to make sure comment-stripping works right.
        self._build(['with-async.js'], readable=False, languages=['en'],
                    dev=False, force=False)
        outfile = (
            'genfiles/compressed_js_packages_prod/en/with-async-package.js')
        self.assertFileContains(outfile, expected)

    def test_changed_manifest_that_does_affect_us(self):
        cr = kake.lib.compile_rule.find_compile_rule(
            'genfiles/readable_js_packages_dev/en/with-jsx-package.js')
        with self.assertCalled(cr.compile_instance.build, 2):
            self._build(['with-jsx.js'], readable=True, languages=['en'],
                        dev=True, force=False)
            outfile = (
                'genfiles/readable_js_packages_dev/en/with-jsx-package.js')
            self.assertFileContains(outfile, "Antonov An-225 Mriya")

            # Now remove main.js from the manifest.
            with self.modify_manifest() as manifest:
                manifest['with-jsx.js']['files'].remove('widejet.jsx')

            self._build(['with-jsx.js'], readable=True, languages=['en'],
                        dev=True, force=False)
            self.assertFileLacks(outfile, "Antonov An-225 Mriya")

    # TODO(csilvers): this will pass when we change ComputeJsCssInputs
    # to not try to re-combine everything when js-packages.json changes.
    @unittest.expectedFailure
    def test_changed_manifest_that_does_not_affect_us(self):
        cr = kake.lib.compile_rule.find_compile_rule(
            'genfiles/readable_js_packages_dev/en/tiny-package.js')
        with self.assertCalled(cr.compile_instance.build, 1):
            self._build(['tiny.js'], readable=True, languages=['en'],
                        dev=True, force=False)

            # Now remove main.js from the manifest for with-jsx.js.
            with self.modify_manifest() as manifest:
                manifest['with-jsx.js']['files'].remove('main.js')

            self._build(['tiny.js'], readable=True, languages=['en'],
                        dev=True, force=False)

    def test_changed_path_to_package(self):
        self._build(['with-async.js'], readable=True, languages=['en'],
                    dev=True, force=False)
        outfile = 'genfiles/readable_js_packages_dev/en/with-async-package.js'
        self.assertFileContains(outfile,
            'KAdefine.updatePathToPackageMap({'
            '"javascript/third-party-package/jakeweery.js": "third-party.js", '
            '"javascript/tiny-package/a.js": "tiny.js", '
            '"javascript/tiny-package/b.js": "tiny.js", '
            '"javascript/with-jsx-package/widejet.jsx": "with-jsx.js"});')

        # Now modify things so "javascript/tiny-package/a.js" is in
        # evil.js instead.
        with self.modify_manifest() as manifest:
            manifest['tiny.js']['files'].remove('a.js')
            manifest['evil.js']['files'].append(
                '../tiny-package/a.js')
        self._build(['with-async.js'], readable=True, languages=['en'],
                    dev=True, force=False)
        self.assertFileContains(outfile,
            'KAdefine.updatePathToPackageMap({'
            '"javascript/third-party-package/jakeweery.js": "third-party.js", '
            '"javascript/tiny-package/a.js": "evil.js", '
            '"javascript/tiny-package/b.js": "tiny.js", '
            '"javascript/with-jsx-package/widejet.jsx": "with-jsx.js"});')

    def test_sourcemap_js(self):
        self.mock_value('js_css_packages.third_party_js._EXPOSE_REQUIRE',
                        {'javascript/tiny-package/b.js': False,
                         'javascript/tiny-package/c.js': False})
        self.mock_value('js_css_packages.third_party_js._FILE_TO_EXPORTS',
                        {'javascript/tiny-package/b.js': ['b1', 'b2'],
                         'javascript/tiny-package/c.js': 'c'})

        self._build(['tiny.js'], readable=True, languages=['en'], dev=True,
                    force=False)

        self.assertFileContentsMatch(
            'genfiles/readable_js_packages_dev/en/tiny-package.js',
            'expected/test_sourcemap_js/tiny-package.js')

        f_map = 'genfiles/readable_js_packages_dev/en/tiny-package.js.map'
        actual_sourcemap = json.load(open(self._abspath(f_map)))
        # If you need to update this source map, uncomment this:
        #
        # with open('/tmp/foo.py', 'w') as x:
        #     x.write('import pprint; pprint.pprint(%s, width=65)'
        #         % open(self._abspath(f_map)).read())
        #
        # then run
        #
        #     python /tmp/foo.py | pbcopy
        #
        # and replace what you see below. Oh, and make sure it makes sense :)
        self.assertDictEqual(actual_sourcemap,
            {'file': 'genfiles/readable_js_packages_dev/en/tiny-package.js',
            'sections': [{'map': {'mappings': 'A',
                                'names': [],
                                'sourceRoot': '/',
                                'sources': [],
                                'version': 3},
                        'offset': {'column': 0, 'line': 0}},
                        {'map': {'file': 'javascript/tiny-package/a.js',
                                'mappings': 'AAAA',
                                'names': [],
                                'sourceRoot': '/',
                                'sources': ['javascript/tiny-package/a.js'],
                                'version': 3},
                        'offset': {'column': 0, 'line': 1}},
                        {'map': {'mappings': '',
                                'names': [],
                                'sourceRoot': '/',
                                'sources': [],
                                'version': 3},
                        'offset': {'column': 0, 'line': 2}},
                        {'map': {'mappings': 'A',
                                'names': [],
                                'sourceRoot': '/',
                                'sources': [],
                                'version': 3},
                        'offset': {'column': 0, 'line': 2}},
                        {'map': {'mappings': 'A',
                                'names': [],
                                'sourceRoot': '/',
                                'sources': [],
                                'version': 3},
                        'offset': {'column': 0, 'line': 3}},
                        {'map': {'file': 'javascript/tiny-package/b.js',
                                'mappings': 'AAAA;AACA',
                                'names': [],
                                'sourceRoot': '/',
                                'sources': ['javascript/tiny-package/b.js'],
                                'version': 3},
                        'offset': {'column': 0, 'line': 4}},
                        {'map': {'mappings': 'A;A',
                                'names': [],
                                'sourceRoot': '/',
                                'sources': [],
                                'version': 3},
                        'offset': {'column': 0, 'line': 6}},
                        {'map': {'mappings': '',
                                'names': [],
                                'sourceRoot': '/',
                                'sources': [],
                                'version': 3},
                        'offset': {'column': 0, 'line': 8}},
                        {'map': {'mappings': 'A',
                                'names': [],
                                'sourceRoot': '/',
                                'sources': [],
                                'version': 3},
                        'offset': {'column': 0, 'line': 8}},
                        {'map': {'mappings': 'A',
                                'names': [],
                                'sourceRoot': '/',
                                'sources': [],
                                'version': 3},
                        'offset': {'column': 0, 'line': 9}},
                        {'map': {'file': 'javascript/tiny-package/c.js',
                                'mappings': 'AAAA',
                                'names': [],
                                'sourceRoot': '/',
                                'sources': ['javascript/tiny-package/c.js'],
                                'version': 3},
                        'offset': {'column': 0, 'line': 10}},
                        {'map': {'mappings': 'A',
                                'names': [],
                                'sourceRoot': '/',
                                'sources': [],
                                'version': 3},
                        'offset': {'column': 0, 'line': 11}},
                        {'map': {'mappings': '',
                                'names': [],
                                'sourceRoot': '/',
                                'sources': [],
                                'version': 3},
                        'offset': {'column': 0, 'line': 12}},
                        {'map': {'mappings': 'A',
                                'names': [],
                                'sourceRoot': '/',
                                'sources': [],
                                'version': 3},
                        'offset': {'column': 0, 'line': 12}}],
            'version': 3})

    def test_sourcemap_css(self):
        self._build(['video.css'], readable=True, languages=['en'],
                    dev=True, force=False)
        # Line number comments included to make sense of the soucemap below
        self.assertFileContentsMatch(
            'genfiles/readable_css_packages_dev/en/video-package.css',
            'expected/test_sourcemap_css/video-package.css')

        f_map = 'genfiles/readable_css_packages_dev/en/video-package.css.map'
        actual_sourcemap = json.load(open(self._abspath(f_map)))
        self.assertDictEqual(actual_sourcemap,
            {'file': 'genfiles/readable_css_packages_dev/en/video-package.css',
             'sections': [{'map': {'file': 'genfiles/'
                                           'compiled_autoprefixed_css/en/'
                                           'stylesheets/video-package/'
                                           'modal.css',
                                   'mappings': 'AAAA;AACA;AACA',
                                   'names': [],
                                   'sourceRoot': '/',
                                   'sources': ['genfiles/'
                                               'compiled_autoprefixed_css/en/'
                                               'stylesheets/video-package/'
                                               'modal.css'],
                                   'version': 3},
                           'offset': {'column': 0, 'line': 0}},
                          {'map': {'mappings': 'A',
                                   'names': [],
                                   'sourceRoot': '/',
                                   'sources': [],
                                   'version': 3},
                           'offset': {'column': 1, 'line': 2}},
                          {'map': {'file': 'genfiles/'
                                           'compiled_autoprefixed_css/en/'
                                           'stylesheets/video-package/'
                                           'video.less.css',
                                   'mappings': 'AAAA;AACA;AACA',
                                   'names': [],
                                   'sourceRoot': '/',
                                   'sources': ['genfiles/'
                                               'compiled_autoprefixed_css/en/'
                                               'stylesheets/video-package/'
                                               'video.less.css'],
                                   'version': 3},
                           'offset': {'column': 0, 'line': 3}},
                          {'map': {'mappings': 'A',
                                   'names': [],
                                   'sourceRoot': '/',
                                   'sources': [],
                                   'version': 3},
                           'offset': {'column': 0, 'line': 6}},
                          {'map': {'file': 'genfiles/'
                                           'compiled_autoprefixed_css/en/'
                                           'stylesheets/video-package/'
                                           'amara.less.css',
                                   'mappings': 'AAAA;AACA;AACA',
                                   'names': [],
                                   'sourceRoot': '/',
                                   'sources': ['genfiles/'
                                               'compiled_autoprefixed_css/en/'
                                               'stylesheets/video-package/'
                                               'amara.less.css'],
                                   'version': 3},
                           'offset': {'column': 0, 'line': 7}}],
             'version': 3})
