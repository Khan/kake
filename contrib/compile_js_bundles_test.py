# TODO(colin): fix these lint errors (http://pep8.readthedocs.io/en/release-1.7.x/intro.html#error-codes)
# pep8-disable:E128
"""Test compile_js_bundles.py."""

from __future__ import absolute_import

import os
import shutil

from shared import ka_root
from shared.testutil import testcase
from third_party import polib

import intl.data
from kake import make
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


def _fake_build(self, outfile_name, infile_names, _, context):
    """Does a fake compilation (just copies first file instead)."""
    with open(ka_root.join(outfile_name), 'w') as fout:
        with open(ka_root.join(infile_names[0])) as fin:
            fout.writelines(fin)


def _fake_build_many(self, output_inputs_changed_context):
    """Does a fake compilation (just copies instead)."""
    for (output, inputs, changed, context) in output_inputs_changed_context:
        _fake_build(self, output, inputs, changed, context)


class TestBuildBundleBase(kake.lib.testutil.KakeTestBase):

    def assertFileContentsMatch(self, filename, expected):
        # overridden from shared/testutil/testcase.py
        # to conditionally regenerate expected output
        # (see comments on _RECREATE_EXPECTED_RESULTS)
        if not _RECREATE_EXPECTED_RESULTS:
            return super(TestBuildBundleBase, self).assertFileContentsMatch(
                filename, expected)

        target_path = os.path.join(self.real_ka_root,
                                   'kake',
                                   'compile_js_bundles-testfiles',
                                   expected)
        util.mkdir_p(os.path.dirname(target_path))
        with open(target_path, 'w') as fout:
            fout.write(self._file_contents(filename))

    def setUp(self):
        super(TestBuildBundleBase, self).setUp()
        self._copy_to_test_tmpdir(os.path.join('kake',
                                               'compile_js_bundles-testfiles'))

        os.makedirs(os.path.join(self.tmpdir, 'kake'))
        # We need this file from the main repo for building.
        #
        # NOTE: We intentionally make a copy instead of symlinking here because
        # node resolves dependencies based on the where the real file is. We
        # want compile_handlebars.js to look inside the sandbox for
        # dependencies, not the real ka-root.
        shutil.copyfile(os.path.join(self.real_ka_root,
                                     'kake', 'compile_handlebars.js'),
                        os.path.join(self.tmpdir,
                                     'kake', 'compile_handlebars.js'))
        shutil.copyfile(os.path.join(self.real_ka_root,
                                     'kake', 'compile_js.js'),
                        os.path.join(self.tmpdir,
                                     'kake', 'compile_js.js'))

        self.write_pofiles()

        self.mock_value('intl.data._LanguageStatus._LANGUAGES',
                        {
                            'es': intl.data._LanguageStatus.ROCK_STAR
                        })

    def write_pofiles(self):
        # TODO(jlfwong): The tests should use ka-locales for their test locales
        # (or fake locales). Right now it's using es-ES and es-MX, which we
        # don't actually differentiate between when we're doing builds, so it's
        # a little misleading.

        # Create a few tiny .mo-files so we can build translations.
        # (I could have just checked this in to compile_i18n_stats-testfiles/,
        # but this way it's easier to edit.)
        #
        # We'll just use the same pofile for all the languages we care about,
        # since we don't actually care about what the translations *are* for
        # these tests
        pofile = polib.POFile()
        pofile.append(polib.POEntry(msgid='en', msgstr='fake'))
        pofile.append(polib.POEntry(msgid='nail', msgstr='<not a nail>'))
        os.makedirs(self._abspath('intl', 'translations', 'pofiles'))
        pofile.save(self._abspath('intl', 'translations', 'pofiles',
                                  'es-ES.rest.po'))
        pofile.save(self._abspath('intl', 'translations', 'pofiles',
                                  'es-MX.rest.po'))

        # ...and we'll just say that all the translations are also approved
        os.makedirs(self._abspath('intl', 'translations', 'approved_pofiles'))
        pofile.save(self._abspath('intl', 'translations', 'approved_pofiles',
                                  'es-ES.rest.po'))
        pofile.save(self._abspath('intl', 'translations', 'approved_pofiles',
                                  'es-MX.rest.po'))


class TestBuildBundle(TestBuildBundleBase):
    def test_build_js_only_deps(self):
        bundle_path = os.path.join(
            'genfiles', 'readable_js_bundles_test', 'en', 'nom_test.bundle.js')
        make.build(bundle_path)
        self.assertFileContentsMatch(bundle_path,
                'expected/js_only_deps.bundle.js')

    def test_build_with_jsx_deps(self):
        bundle_path = os.path.join(
            'genfiles', 'readable_js_bundles_test', 'en', 'cola.bundle.js')
        make.build(bundle_path)
        self.assertFileContentsMatch(bundle_path,
                'expected/jsx_deps.bundle.js')

    def test_build_with_handlebars_deps(self):
        bundle_path = os.path.join(
            'genfiles', 'readable_js_bundles_test', 'en',
            'javascript', 'lookma-package', 'lookma.bundle.js')
        make.build(bundle_path)
        self.assertFileContentsMatch(bundle_path,
                'expected/handlebars_deps.bundle.js')

    def test_build_with_third_party_deps_with_requires(self):
        self.mock_value('js_css_packages.third_party_js._EXPOSE_REQUIRE',
                        {'third_party.js': False})
        self.mock_value('js_css_packages.third_party_js._FILE_TO_EXPORTS',
                        {'third_party.js': []})
        bundle_path = os.path.join(
            'genfiles', 'readable_js_bundles_test', 'en',
            'javascript', 'dashboard-package', 'first_party.bundle.js')
        make.build(bundle_path)
        self.assertFileContentsMatch(bundle_path,
                'expected/third_party_deps.bundle.js')

    def test_node_module_require_file(self):
        bundle_path = os.path.join(
            'genfiles', 'readable_js_bundles_test', 'en',
            'javascript', 'shared-package', 'ui.bundle.js')
        make.build(bundle_path)
        self.assertFileContentsMatch(bundle_path,
                'expected/node_module_require.bundle.js')


class TestRequireWithVars(TestBuildBundleBase):
    def test_dev_or_prod__dev(self):
        bundle_path = os.path.join(
            'genfiles', 'readable_js_bundles_dev', 'en', 'cola.bundle.js')
        make.build(bundle_path)
        self.assertFileContains(bundle_path,
            'KAdefine.updateFilenameRewriteMap({'
                '"javascript/node_modules/underscore/index.js": '
                    '"../../../third_party/third_party.js", '
                '"trademark.{{dev_or_prod}}.js": "trademark.dev.js"});\n')

    def test_dev_or_prod__prod(self):
        bundle_path = os.path.join(
            'genfiles', 'readable_js_bundles_prod', 'en', 'cola.bundle.js')
        make.build(bundle_path)
        self.assertFileContains(bundle_path,
            'KAdefine.updateFilenameRewriteMap({'
                '"javascript/node_modules/underscore/index.js": '
                    '"../../../third_party/third_party.js", '
                '"trademark.{{dev_or_prod}}.js": "trademark.prod.js"});\n')

    def test_dev_or_prod__test(self):
        bundle_path = os.path.join(
            'genfiles', 'readable_js_bundles_test', 'en', 'cola.bundle.js')
        make.build(bundle_path)
        self.assertFileContains(bundle_path,
            'KAdefine.updateFilenameRewriteMap({'
                '"javascript/node_modules/underscore/index.js": '
                    '"../../../third_party/third_party.js", '
                '"trademark.{{dev_or_prod}}.js": "trademark.dev.js"});\n')

    def test_lang_en(self):
        bundle_path = os.path.join('genfiles', 'readable_js_bundles_test',
                                   'en', 'instructions.bundle.js')
        make.build(bundle_path)
        self.assertFileContentsMatch(bundle_path,
                'expected/with_vars_lang_en.bundle.js')

    def test_lang_es_ES(self):
        bundle_path = os.path.join('genfiles', 'readable_js_bundles_test',
                                   'es-ES', 'instructions.bundle.js')
        make.build(bundle_path)
        self.assertFileContentsMatch(bundle_path,
                'expected/with_vars_lang_es_es.bundle.js')

    def test_lang_es_MX(self):
        bundle_path = os.path.join('genfiles', 'readable_js_bundles_test',
                                   'es-MX', 'instructions.bundle.js')
        make.build(bundle_path)
        self.assertFileContentsMatch(bundle_path,
                'expected/with_vars_lang_es_mx.bundle.js')

    def test_testfiles_include_override(self):
        bundle_path = os.path.join('genfiles', 'readable_js_bundles_test',
                                   'en', 'javascript', 'testindex.bundle.js')
        make.build(bundle_path, {'testfiles': 'nom_test.js'})
        self.assertFileContentsMatch(bundle_path,
                'expected/testfiles_include_override1.bundle.js')

        make.build(bundle_path, {'testfiles': 'lib/eat.js'})
        self.assertFileContentsMatch(bundle_path,
                'expected/testfiles_include_override2.bundle.js')

    def test_ignore_requires_in_comments(self):
        bundle_path = os.path.join(
            'genfiles', 'readable_js_bundles_test', 'en',
            'comments1.bundle.js')
        make.build(bundle_path)
        self.assertFileContentsMatch(bundle_path,
                'expected/ignore_requires_in_comments1.bundle.js')

        bundle_path = os.path.join(
            'genfiles', 'readable_js_bundles_test', 'en',
            'comments2.bundle.js')
        make.build(bundle_path)
        self.assertFileContentsMatch(bundle_path,
                'expected/ignore_requires_in_comments2.bundle.js')

    def test_doesnt_strip_requires_following_urls(self):
        bundle_path = os.path.join(
            'genfiles', 'readable_js_bundles_test', 'en',
            'url.bundle.js')
        make.build(bundle_path)
        self.assertFileContentsMatch(bundle_path,
                'expected/no_strip_require_after_url.bundle.js')

    def test_third_party_files_require_not_exposed(self):
        bundle_path = os.path.join(
            'genfiles', 'readable_js_bundles_test', 'en',
            'third_party', 'third_party.bundle.js')
        make.build(bundle_path)
        self.assertFileContentsMatch(bundle_path,
                'expected/third_party_files_not_wrapped.bundle.js')

    def test_third_party_files_require_exposed_override(self):
        self.mock_value('js_css_packages.third_party_js._EXPOSE_REQUIRE',
                        {'third_party/third_party.js': True})
        bundle_path = os.path.join(
            'genfiles', 'readable_js_bundles_test', 'en',
            'third_party', 'third_party.bundle.js')
        make.build(bundle_path)
        self.assertFileContentsMatch(bundle_path,
                'expected/third_party_file_wrap_override.bundle.js')

    def test_third_party_files_analysis_disabled_by_default(self):
        bundle_path = os.path.join(
            'genfiles', 'readable_js_bundles_test', 'en',
            'third_party', 'analyze-me.bundle.js')
        make.build(bundle_path)
        self.assertFileContentsMatch(bundle_path,
                'expected/third_party_no_analyze.bundle.js')

    def test_third_party_files_analyze_override(self):
        self.mock_value('js_css_packages.third_party_js._EXPOSE_REQUIRE',
                        {'third_party/analyze-me.js': True})
        self.mock_value('js_css_packages.third_party_js._CAN_ANALYZE_FOR_DEPS',
                        {'third_party/analyze-me.js': True})
        bundle_path = os.path.join(
            'genfiles', 'readable_js_bundles_test', 'en',
            'third_party', 'analyze-me.bundle.js')
        make.build(bundle_path)
        self.assertFileContentsMatch(bundle_path,
                'expected/third_party_analyze.bundle.js')

    def test_listed_dependencies(self):
        self.mock_value('js_css_packages.third_party_js._DEPENDENCIES',
                        {'a.js': ['b.js']})
        self.mock_value('js_css_packages.third_party_js._CAN_ANALYZE_FOR_DEPS',
                        {'a.js': False,
                         'b.js': False})
        self.mock_value('js_css_packages.third_party_js._EXPOSE_REQUIRE',
                        {'a.js': False,
                         'b.js': False})
        bundle_path = os.path.join(
            'genfiles', 'readable_js_bundles_test', 'en',
            'a.bundle.js')
        make.build(bundle_path)
        self.assertFileContentsMatch(bundle_path,
                'expected/listed_dependencies.bundle.js')

    def test_listed_plugins(self):
        self.mock_value('js_css_packages.third_party_js._PLUGINS',
                        {'a.js': ['b.js']})
        self.mock_value('js_css_packages.third_party_js._CAN_ANALYZE_FOR_DEPS',
                        {'a.js': False,
                         'b.js': False})
        self.mock_value('js_css_packages.third_party_js._EXPOSE_REQUIRE',
                        {'a.js': False,
                         'b.js': False})
        bundle_path = os.path.join(
            'genfiles', 'readable_js_bundles_test', 'en',
            'a.bundle.js')
        make.build(bundle_path)
        self.assertFileContentsMatch(bundle_path,
                'expected/listed_plugins.bundle.js')

    def test_build_with_config_and_hidden_require(self):
        self.mock_value('js_css_packages.third_party_js._DEPENDENCIES',
                        {'a.js': ['b.js']})
        self.mock_value('js_css_packages.third_party_js._CAN_ANALYZE_FOR_DEPS',
                        {'a.js': False,
                         'b.js': False})
        self.mock_value('js_css_packages.third_party_js._EXPOSE_REQUIRE',
                        {'a.js': False,
                         'b.js': False})
        self.mock_value('js_css_packages.third_party_js._FILE_TO_EXPORTS',
                        {'a.js': 'A',
                         'b.js': ['B']})
        self.mock_value('js_css_packages.third_party_js._FILE_TO_GLOBALS',
                        {'a.js': ['A'],
                         'b.js': ['B']})
        bundle_path = os.path.join(
            'genfiles', 'readable_js_bundles_test', 'en',
            'a.bundle.js')
        make.build(bundle_path)
        self.assertFileContentsMatch(bundle_path,
                'expected/config_and_hidden_require.bundle.js')

    def test_build_with_config_and_exposed_require(self):
        self.mock_value('js_css_packages.third_party_js._DEPENDENCIES',
                        {'a.js': ['b.js']})
        self.mock_value('js_css_packages.third_party_js._CAN_ANALYZE_FOR_DEPS',
                        {'a.js': False,
                         'b.js': False})
        self.mock_value('js_css_packages.third_party_js._EXPOSE_REQUIRE',
                        {'a.js': True,
                         'b.js': True})
        self.mock_value('js_css_packages.third_party_js._FILE_TO_EXPORTS',
                        {'a.js': 'A',
                         'b.js': ['B']})
        self.mock_value('js_css_packages.third_party_js._FILE_TO_GLOBALS',
                        {'a.js': ['A'],
                         'b.js': ['B']})
        bundle_path = os.path.join(
            'genfiles', 'readable_js_bundles_test', 'en',
            'a.bundle.js')
        make.build(bundle_path)
        self.assertFileContentsMatch(bundle_path,
                'expected/config_and_exposed_require.bundle.js')
