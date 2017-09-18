# TODO(colin): fix these lint errors (http://pep8.readthedocs.io/en/release-1.7.x/intro.html#error-codes)
# pep8-disable:E128
"""Test compile_js_css_manifest.py.

I use build_prod_main as the driver to run the tests, because that's
the easiest way to do so.
"""

from __future__ import absolute_import

import glob
import json
import os

from shared import ka_root
import shared.cache.util
from shared.testutil import testsize

import js_css_packages.packages
from kake import build_prod_main
import kake.compile_js_css_packages_test     # for TestBase
from kake.lib import filemod_db


def _write_to_file(filename, contents):
    with open(filename, 'w') as f:
        f.write(contents)


class ManifestTestBase(kake.compile_js_css_packages_test.TestBase):
    def setUp(self):
        super(ManifestTestBase, self).setUp()

    def _filename(self, *glob_dirparts):
        """Return the single file found in glob_dirpart1/glob_dirpart2/etc.

        Both glob_dirparts and the return value are relative to ka-root.
        """
        fullglob = os.path.join(self.tmpdir, *glob_dirparts)
        files = glob.glob(fullglob)
        self.assertEqual(1, len(files), (fullglob, files))
        return os.path.relpath(files[0], self.tmpdir)

    def assert_glob_matches_one(self, *glob_dirparts):
        """Assert that glob_dirparts resolves to exactly one file."""
        fullglob = os.path.join(self.tmpdir, *glob_dirparts)
        files = glob.glob(fullglob)
        self.assertEqual(1, len(files), (fullglob, files))

    def assert_glob_matches_zero(self, *glob_dirparts):
        """Assert that glob_dirparts resolves to 0 files."""
        fullglob = os.path.join(self.tmpdir, *glob_dirparts)
        files = glob.glob(fullglob)
        self.assertEqual([], files, (fullglob, files))

    def _build(self, build_prod_main_args, readable, languages,
               dev=False, force=False, gae_version=None):
        build_prod_main.main(build_prod_main_args, languages, {}, dev,
                             readable, force=force,
                             gae_version=gae_version)


class TestCompile(ManifestTestBase):
    def setUp(self):
        super(TestCompile, self).setUp()
        self.write_fakelang_pofile()

    def test_python_manifest(self):
        self._build(['js'], readable=True, languages=['en'], dev=False)
        fname = ('genfiles/readable_manifests_prod/en/'
                 'javascript-md5-packages.json')
        with open(ka_root.join(fname)) as f:
            manifest = json.load(f)
        self.assertIn('shared.js', manifest)

        self._build(['css'], readable=True, languages=['en'], dev=False)
        fname = ('genfiles/readable_manifests_prod/en/'
                 'stylesheets-md5-packages.json')
        with open(ka_root.join(fname)) as f:
            manifest = json.load(f)
        self.assertIn('video.css', manifest)

    def test_javascript_manifest_dev(self):
        self._build(['js_and_css'], readable=True, languages=['en'], dev=True)
        fname = 'genfiles/readable_manifests_dev/en/package-manifest.js'
        with open(ka_root.join(fname)) as f:
            contents = f.read().strip()
            # The json is inside this javascript.
            manifest = contents.split('{', 1)[1]
            manifest = manifest.rsplit('}', 1)[0]
            manifest = json.loads('{' + manifest + '}')
        self.assertEqual({'javascript', 'stylesheets'}, set(manifest.keys()))

        shared = [e for e in manifest['javascript']
                  if e['name'] == 'shared.js']
        self.assertEqual(1, len(shared), shared)
        self.assertEqual(('/_kake/genfiles/readable_js_packages_dev/en/'
                          'shared-package.js'),
                         shared[0]['url'])
        # shared.js has no dependencies.
        self.assertNotIn('dependencies', shared[0])

        video = [e for e in manifest['stylesheets']
                 if e['name'] == 'video.css']
        self.assertEqual(1, len(video), video)
        self.assertEqual(('/_kake/genfiles/readable_css_packages_dev/en/'
                          'video-package.css'),
                         video[0]['url'])
        self.assertEqual(['tiny.css'], video[0]['dependencies'])

    def test_javascript_manifest_prod(self):
        self._build(['js_and_css'], readable=False, languages=['en'],
                    dev=False)
        fname = 'genfiles/compressed_manifests_prod/en/package-manifest.js'
        with open(ka_root.join(fname)) as f:
            contents = f.read().strip()
            # The json is inside this javascript.
            manifest = contents.split('{', 1)[1]
            manifest = manifest.rsplit('}', 1)[0]
            manifest = json.loads('{' + manifest + '}')
        self.assertEqual({'javascript', 'stylesheets'}, set(manifest.keys()))

        shared = [e for e in manifest['javascript']
                  if e['name'] == 'shared.js']
        self.assertEqual(1, len(shared), shared)
        self.assertTrue(shared[0]['url'].startswith(
            '/genfiles/javascript/en/shared-package-'), shared[0])
        self.assertFileExists(shared[0]['url'][1:])

        video = [e for e in manifest['stylesheets']
                 if e['name'] == 'video.css']
        self.assertEqual(1, len(video), video)
        self.assertTrue(video[0]['url'].startswith(
            '/genfiles/stylesheets/en/video-package-'), video[0])
        self.assertFileExists(video[0]['url'][1:])

    def test_javascript_and_python_manifest_symlinks(self):
        self._build(['js_and_css'], readable=True,
                    languages=['en', 'fakelang'], dev=False)
        f1 = 'genfiles/readable_manifests_prod/en/package-manifest.js'
        f2 = self._filename('genfiles', 'manifests', 'en',
                            'package-manifest-*.js')
        self.assertEqual(os.path.realpath(ka_root.join(f1)),
                         os.path.realpath(ka_root.join(f2)))
        f3 = 'genfiles/readable_manifests_prod/fakelang/package-manifest.js'
        f4 = self._filename('genfiles', 'manifests', 'fakelang',
                            'package-manifest-*.js')
        self.assertEqual(os.path.realpath(ka_root.join(f3)),
                         os.path.realpath(ka_root.join(f4)))

        # The python manifests should have the same md5sum as the
        # javascript manifests.
        for js_or_css in ('javascript', 'stylesheets'):
            f1b = f1.replace('package-manifest.js',
                             '%s-md5-packages.json' % js_or_css)
            f2b = f2.replace('package-manifest-',
                             '%s-md5-packages-' % js_or_css) + 'on'  # js->json
            f3b = f3.replace('package-manifest.js',
                             '%s-md5-packages.json' % js_or_css)
            f4b = f4.replace('package-manifest-',
                             '%s-md5-packages-' % js_or_css) + 'on'  # js->json
            self.assertFileExists(f1b)
            self.assertFileExists(f2b)
            self.assertFileExists(f3b)
            self.assertFileExists(f4b)
            self.assertEqual(os.path.realpath(ka_root.join(f1b)),
                             os.path.realpath(ka_root.join(f2b)))
            self.assertEqual(os.path.realpath(ka_root.join(f3b)),
                             os.path.realpath(ka_root.join(f4b)))

    def test_javascript_manifest_toc(self):
        self._build(['js_and_css'], readable=True, languages=['en'],
                    dev=False, force=False, gae_version='161616-2233-hello')
        toc_filename = ka_root.join('genfiles', 'manifests',
                                    'toc-161616-2233-hello.json')
        with open(toc_filename) as f:
            toc = json.load(f)
        self.assertIn('en', toc)
        self.assertNotIn('fakelang', toc)
        en_file = ka_root.join('genfiles', 'manifests', 'en',
                               'package-manifest-%s.js' % toc['en'])
        self.assertFileExists(en_file)

        self._build(['js_and_css'], readable=True, languages=['fakelang'],
                    dev=False, force=False, gae_version='161616-2233-hello')
        with open(toc_filename) as f:
            toc = json.load(f)
        self.assertIn('en', toc)
        self.assertIn('fakelang', toc)
        self.assertFileExists(en_file)
        fake_file = ka_root.join('genfiles', 'manifests', 'fakelang',
                                 'package-manifest-%s.js' % toc['fakelang'])
        self.assertFileExists(fake_file)

    def test_no_symlinks_for_dev(self):
        self._build(['js_and_css'], readable=True, languages=['en'],
                    dev=True, force=False)
        for path in ('javascript/*',
                     'stylesheets/*',
                     'genfiles/*/package-manifest.toc.json',
                     'package-manifest-*'
                     ):
            self.assert_glob_matches_zero(ka_root.join('genfiles', path))

    def test_symlinks_update_when_args_change(self):
        self._build(['js'], readable=True, languages=['en'],
                    dev=False, force=False)
        filename = self._filename(
            'genfiles', 'javascript', 'en', 'video-package-*.js')
        self.assertIn('readable_js_packages_prod',
                      os.readlink(ka_root.join(filename)))

        self._build(['js'], readable=False, languages=['en'],
                    dev=False, force=False)
        filename = self._filename(
            'genfiles', 'javascript', 'en', 'video-package-*.js')
        self.assertIn('compressed_js_packages_prod',
            os.readlink(ka_root.join(filename)))

        # All deps are already built, but this should still change symlinks.
        self._build(['js'], readable=True, languages=['en'],
                    dev=False, force=False)
        filename = self._filename(
            'genfiles', 'javascript', 'en', 'video-package-*.js')
        self.assertIn('readable_js_packages_prod',
                      os.readlink(ka_root.join(filename)))

    def test_creates_only_necessary_symlinks(self):
        self._build(['js'], readable=False,
                    languages=['en', 'fakelang'], dev=False, force=False)
        en_outfile = self._filename(
            'genfiles', 'javascript', 'en', 'shared-package-*.js')
        self.assertFileContains(en_outfile, '"prod"')
        self.assert_glob_matches_zero('genfiles', 'javascript', 'fakelang',
                                      'shared-package-*.js')

    def test_creates_only_necessary_language_entries(self):
        self._build(['js'], readable=False,
                    languages=['en', 'fakelang'], dev=False, force=False)

        # Make sure only the 'en' file is in the manifest for shared.js.
        with open(ka_root.join('genfiles', 'compressed_manifests_prod',
                               'en', 'javascript-md5-packages.json')) as f:
            en_manifest = json.load(f)
        with open(ka_root.join('genfiles', 'compressed_manifests_prod',
                               'fakelang',
                               'javascript-md5-packages.json')) as f:
            fakelang_manifest = json.load(f)

        self.assertEqual(en_manifest['shared.js'],
                         fakelang_manifest['shared.js'])
        self.assertNotIn("genfiles/javascript/fakelang",
                         fakelang_manifest['shared.js'])

    def test_modified_files_including_delete_obsolete_files(self):
        self._build(['js'], readable=False, languages=['en'],
                    dev=False, force=False)
        orig_filename = self._filename(
            'genfiles', 'javascript', 'en', 'video-package-*.js')

        _write_to_file(ka_root.join('javascript', 'video-package', 'video.js'),
                       'var Video = {\n     youtubeId: "all new!"\n    };')
        filemod_db.clear_mtime_cache()    # since we modified a file
        self._build(['js'], readable=False, languages=['en'],
                    dev=False, force=False)
        new_filename = self._filename(
            'genfiles', 'javascript', 'en', 'video-package-*.js')
        self.assertNotEqual(orig_filename, new_filename)
        self.assertFileContains(new_filename, 'all new!')

        # Make sure we deleted the obsolete file.
        self.assert_glob_matches_zero(orig_filename)

    def test_delete_obsolete_files_many_languages(self):
        self._build(['js'], readable=False,
                    languages=['en', 'fakelang'], dev=False, force=False)

        _write_to_file(ka_root.join('javascript', 'video-package', 'video.js'),
                       'var Video = {\n     youtubeId: "all new!"\n    };')
        _write_to_file(ka_root.join('genfiles', 'translations', 'fakelang',
                                    'javascript', 'video-package', 'video.js'),
                       'var Video = {youtubeId: "fake new!"};')

        filemod_db.clear_mtime_cache()    # since we modified a file
        # We just test that this doesn't raise a FileNotFound error.
        self._build(['js'], readable=False,
                    languages=['en', 'fakelang'], dev=False, force=False)


@testsize.tiny
class TestCreateGenfilesJavascript(ManifestTestBase):
    def setUp(self):
        super(TestCreateGenfilesJavascript, self).setUp()
        self.manifest = self.write_simpler_manifest()

    def test_simple(self):
        build_prod_main.main(['js'], ['en'], {}, False, True, True)
        outdir = self._abspath('genfiles', 'readable_js_packages_prod', 'en')
        self.assertEqual(['corelibs-package.js',
                          'corelibs-package.js.deps',
                          'corelibs-package.js.map',
                          'shared-package.js',
                          'shared-package.js.deps',
                          'shared-package.js.map',
                          'third-party-package.js',
                          'third-party-package.js.deps',
                          'third-party-package.js.map',
                          'video-package.js',
                          'video-package.js.deps',
                          'video-package.js.map',
                          ],
                         sorted(os.listdir(outdir)))

        # These will die if the symlinks don't exist.
        symlinks = (self._filename('genfiles', 'javascript', 'en',
                                   'corelibs-package-*.js'),
                    self._filename('genfiles', 'javascript', 'en',
                                   'shared-package-*.js'),
                    self._filename('genfiles', 'javascript', 'en',
                                   'third-party-package-*.js'),
                    self._filename('genfiles', 'javascript', 'en',
                                   'video-package-*.js'),
                    )
        for symlink in symlinks:
            self.assertIn('readable_js_packages_prod',
                          os.readlink(ka_root.join(symlink)))

    def test_added_package(self):
        """Ensure we still make symlinks properly after a pkg is deleted."""
        build_prod_main.main(['js'], ['en'], readable=True)

        self.manifest['tiny.js'] = {'files': ['a.js', 'b.js', 'c.js']}
        with open(self._abspath('javascript-packages.json'), 'w') as f:
            json.dump(self.manifest, f)
        shared.cache.util.delete_for_function(
            js_css_packages.packages._get, ('javascript',))

        # Forces a full rebuild because javascript-packages.json has changed.
        # Make sure we see the new package.
        build_prod_main.main(['js'], ['en'], {}, False, True, True)
        outdir = self._abspath('genfiles', 'readable_js_packages_prod', 'en')
        self.assertEqual(['corelibs-package.js',
                          'corelibs-package.js.deps',
                          'corelibs-package.js.map',
                          'shared-package.js',
                          'shared-package.js.deps',
                          'shared-package.js.map',
                          'third-party-package.js',
                          'third-party-package.js.deps',
                          'third-party-package.js.map',
                          'tiny-package.js',
                          'tiny-package.js.deps',
                          'tiny-package.js.map',
                          'video-package.js',
                          'video-package.js.deps',
                          'video-package.js.map',
                          ],
                         sorted(os.listdir(outdir)))
        for pkg in ('corelibs.js', 'shared.js', 'third-party.js',
                    'tiny.js', 'video.js'):
            self.assertFileContains(
                ('genfiles/readable_manifests_prod/en/'
                 'javascript-md5-packages.json'),
                '"%s":' % pkg)

    def test_deleted_package(self):
        """Ensure we still make symlinks properly after a video is deleted."""
        build_prod_main.main(['js'], ['en'], readable=True)

        del self.manifest['video.js']     # we no longer are using videos!
        with open(self._abspath('javascript-packages.json'), 'w') as f:
            json.dump(self.manifest, f)
        shared.cache.util.delete_for_function(
            js_css_packages.packages._get, ('javascript',))

        # Forces a full rebuild because javascript-packages.json has changed.
        # Make sure this doesn't crash because video-package.js is still
        # on the filesystem, but video.js is no longer in the manifest file.
        build_prod_main.main(['js'], ['en'], readable=True)

        for pkg in ('corelibs.js', 'shared.js', 'third-party.js'):
            self.assertFileContains(
                ('genfiles/readable_manifests_prod/en/'
                 'javascript-md5-packages.json'),
                '"%s":' % pkg)
        self.assertFileLacks(
            ('genfiles/readable_manifests_prod/en/'
             'javascript-md5-packages.json'),
            '"video.js":')

    def test_language(self):
        """Ensure nothing bad happens to lang1 when we build lang2."""
        self.write_fakelang_pofile()
        build_prod_main.main(['js'], ['en'], readable=True)
        symlinks = (self._filename('genfiles', 'javascript', 'en',
                                   'corelibs-package-*.js'),
                    self._filename('genfiles', 'javascript', 'en',
                                   'shared-package-*.js'),
                    self._filename('genfiles', 'javascript', 'en',
                                   'third-party-package-*.js'),
                    self._filename('genfiles', 'javascript', 'en',
                                   'video-package-*.js'),
                    )
        for symlink in symlinks:
            self.assertIn('readable_js_packages_prod',
                          os.readlink(ka_root.join(symlink)))

        build_prod_main.main(['js'], ['fakelang'], readable=True)
        # Now check the 'en' files again -- they shouldn't have been
        # deleted just because we built fakelang.
        for symlink in symlinks:
            self.assertIn('readable_js_packages_prod',
                          os.readlink(ka_root.join(symlink)))
