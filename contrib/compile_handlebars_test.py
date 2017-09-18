# encoding: utf-8
# TODO(colin): fix these lint errors (http://pep8.readthedocs.io/en/release-1.7.x/intro.html#error-codes)
# pep8-disable:E124,E127,E128
"""Tests for compile_handlebars.py"""

from __future__ import absolute_import

import os

from shared.testutil import testsize
from third_party import polib

import handlebars.render
from kake import make
import kake.lib.testutil


class TestBase(kake.lib.testutil.KakeTestBase):
    """Sets up the filesystem."""
    def setUp(self):
        super(TestBase, self).setUp()     # sets up self.tmpdir as ka-root

        for d in ('shared-package', 'no_nltext-package', 'no_json-package'):
            os.makedirs(self._abspath('javascript', d))
            os.makedirs(self._abspath('genfiles', 'translations', 'boxes',
                                      'javascript', d))

        os.makedirs(self._abspath('intl', 'translations', 'pofiles'))
        # Create a tiny .po-file so we can build translations.
        pofile = polib.POFile()
        pofile.append(polib.POEntry(msgid='foo', msgstr='<boxes>'))
        pofile.append(polib.POEntry(msgid='json-less',
                                    msgstr='<boxes>-<boxes>'))
        pofile.save(self._abspath('intl', 'translations', 'pofiles',
                                  'boxes.rest.po'))

        with open(self._abspath('javascript', 'shared-package',
                                'foo.handlebars'), 'w') as f:
            print >> f, ('Outside If {{#if this.isDefault}}Inside If{{/if}}'
                         '{{#noop "shared" "bar" 12 true '
                         'label="unicóde with \\"escaped quotes\\""}}'
                         '{{/noop}}'
                        )
        open(self._abspath('javascript', 'shared-package',
                           'foo.handlebars.json'), 'w').close()

        with open(self._abspath('javascript', 'no_nltext-package',
                                'en.handlebars'), 'w') as f:
            print >> f, "en"
        open(self._abspath('javascript', 'no_nltext-package',
                           'en.handlebars.json'), 'w').close()

        # This file should not be translated because there's no .json file
        with open(self._abspath('javascript', 'no_json-package',
                                'jsonless.handlebars'), 'w') as f:
            print >> f, "{{#_}}json-less{{/_}}"


@testsize.tiny
class InitTest(TestBase):
    """Verifies that we create __init__.py files appropriately."""
    def test_partials_file(self):
        make.build('genfiles/compiled_handlebars_py/'
                   'en/javascript/shared-package/foo.py')

        f = self._abspath('genfiles', 'compiled_handlebars_py', '__init__.py')
        self.assertTrue(os.path.isfile(f))
        self.assertFile(f,
            'from handlebars.render import handlebars_template\n'
            '\n'
            'handlebars_partials = {\n'
            '    "no_nltext_en": lambda params, partials=None, helpers=None: '
                'handlebars_template("no_nltext", "en", params),\n'
            '    "shared_foo": lambda params, partials=None, helpers=None: '
                'handlebars_template("shared", "foo", params),\n'
            '}\n')

    def test_per_directory_init_files_with_json(self):
        make.build('genfiles/compiled_handlebars_py/'
                   'en/javascript/shared-package/foo.py')
        make.build('genfiles/compiled_handlebars_py/'
                   'en/javascript/no_nltext-package/en.py')

        self.assertTrue(os.path.isfile(self._abspath(
            'genfiles/compiled_handlebars_py/'
            'en/javascript/shared-package/__init__.py')))
        self.assertTrue(os.path.isfile(self._abspath(
            'genfiles/compiled_handlebars_py/'
            'en/javascript/no_nltext-package/__init__.py')))

    def test_translated_directory_init_files(self):
        make.build('genfiles/compiled_handlebars_py/'
                   'boxes/javascript/shared-package/foo.py')
        make.build('genfiles/compiled_handlebars_py/'
                   'boxes/javascript/no_nltext-package/en.py')

        self.assertTrue(os.path.isfile(self._abspath(
            'genfiles/compiled_handlebars_py/boxes/__init__.py')))
        self.assertTrue(os.path.isfile(self._abspath(
            'genfiles/compiled_handlebars_py/'
            'boxes/javascript/shared-package/__init__.py')))
        self.assertTrue(os.path.isfile(self._abspath(
            'genfiles/compiled_handlebars_py/'
            'boxes/javascript/no_nltext-package/__init__.py')))

    def test_language_symlink_is_used(self):
        # Because the 'no_nltext' file contents is the same across
        # languages, we should be able to use the maybe_symlink_to
        # option to symlink to the English version.
        make.build('genfiles/compiled_handlebars_py/'
                   'en/javascript/no_nltext-package/en.py')
        make.build('genfiles/compiled_handlebars_py/'
                   'boxes/javascript/no_nltext-package/en.py')
        self.assertTrue(os.path.islink(self._abspath(
            'genfiles', 'compiled_handlebars_py',
            'boxes', 'javascript', 'no_nltext-package', 'en.py')))


@testsize.tiny
class JsonTest(TestBase):
    """Test that we don't compile when there's no .json file."""
    def test_json(self):
        make.build('genfiles/compiled_handlebars_py/'
                   'en/javascript/shared-package/foo.py')
        self.assertFileContains(('genfiles/compiled_handlebars_py/'
                                   'en/javascript/shared-package/foo.py'),
                                  ('# Begin constants\n'))

    def test_json_suddenly_appears(self):
        with self.assertRaises(AssertionError):
            make.build('genfiles/compiled_handlebars_py/'
                       'en/javascript/no_json-package/jsonless.py')

        open(self._abspath('javascript', 'no_json-package',
                           'jsonless.handlebars.json'), 'w').close()
        make.build('genfiles/compiled_handlebars_py/'
                   'en/javascript/no_json-package/jsonless.py')
        self.assertFileContains(('genfiles/compiled_handlebars_py/en/'
                                 'javascript/no_json-package/jsonless.py'),
                                ('# Begin constants\n'))


@testsize.tiny
class CompileTest(TestBase):
    def test_unicode_arguments(self):
        make.build('genfiles/compiled_handlebars_py/'
                   'en/javascript/shared-package/foo.py')
        self.assertFileContains(('genfiles/compiled_handlebars_py/'
                                 'en/javascript/shared-package/foo.py'),
                                ('u\'shared\', u\'bar\', 12, True, '
                                 'label=u\'unic\\xf3de with "escaped quotes"\''
                                ))

    def test_rendered_if(self):
        self.mock_value('intl.data._LanguageStatus._DID_LANGUAGE_CHECKS',
                        True)
        """Check that render of compiled foo.py outputs as expected."""
        make.build('genfiles/compiled_handlebars_py/'
                   'en/javascript/shared-package/foo.py')
        # Without any context defined, won't get into the If block
        output = handlebars.render.handlebars_template(
            "shared", "foo", {}).encode('utf-8') + "\n"
        self.assertEquals("Outside If \n\n", output)

        # With context define will get into the If block
        output = handlebars.render.handlebars_template(
            "shared", "foo", {"isDefault": True}).encode('utf-8') + "\n"
        self.assertEquals("Outside If Inside If\n\n", output)

