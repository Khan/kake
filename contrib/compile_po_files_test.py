"""Tests compile_po_files.py."""

from __future__ import absolute_import

import os

import intl.translate
import kake.make
from kake.lib import compile_rule
from kake.lib import testutil


class TestBase(testutil.KakeTestBase):
    def setUp(self, *args, **kwargs):
        super(TestBase, self).setUp(*args, **kwargs)   # creates self.tmpdir

        os.makedirs(self._abspath('intl/translations/pofiles'))
        os.makedirs(self._abspath('intl/translations/approved_pofiles'))
        self.box = u"\u25a1".encode('utf-8')
        with open(self._abspath(
                'intl/translations/pofiles/nb.rest.po'),
                'w') as f:
            print >>f, 'msgid ""\n'
            print >>f, 'msgstr "Plural-Forms: nplurals=2; plural=(n > 1);\n"\n'
            print >>f, 'msgid "hello"\nmsgstr "%s"\n\n' % (self.box * 5)
            print >>f, 'msgid "world!"\nmsgstr "%s!"\n\n' % (self.box * 5)
            print >>f, 'msgid "volatile"\nmsgstr "%s"\n\n' % (
                self.box * 2)

        with open(self._abspath(
                'intl/translations/approved_pofiles/nb.rest.po'),
                'w') as f:
            print >>f, 'msgid ""\n'
            print >>f, 'msgstr "Plural-Forms: nplurals=2; plural=(n > 1);\n"\n'
            print >>f, 'msgid "hello"\nmsgstr "%s"\n\n' % (self.box * 5)
            print >>f, 'msgid "not in unapproved"\nmsgstr "%s"\n\n' % (
                self.box * 5)
            print >>f, 'msgid "volatile"\nmsgstr "%s"\n\n' % (
                self.box * 3)

        self.divis = u"\xf7".encode('utf-8')
        with open(self._abspath('intl/translations/pofiles/divis.po'),
                  'w') as f:
            print >>f, 'msgid ""\n'
            print >>f, 'msgstr "Plural-Forms: nplurals=2; plural=(n > 1);\n"\n'
            print >>f, 'msgid "hello"\nmsgstr "%s"\n\n' % (self.divis * 5)
        with open(self._abspath('intl/translations/pofiles/divis.po.1'),
                  'w') as f:
            print >>f, 'msgid ""\nmsgstr ""\n\n'
            print >>f, 'msgid "world!"\nmsgstr "%s!"\n\n' % (self.divis * 5)

        # We choose which rule to build the index files at import time.
        # Unfortunately we load compile_po_files before testcase.TestCase.setUp
        # mocks is_on_jenkins to True.  So we need to clear out the rules to be
        # able to see the correct one.
        compile_rule._COMPILE_RULES = {}
        compile_rule._COMPILE_RULE_LABELS = set()
        import kake.compile_po_files
        reload(kake.compile_po_files)
        self.mock_value('intl.data._LanguageStatus._DID_LANGUAGE_CHECKS',
                        True)

    def testSimple(self):
        kake.make.build('genfiles/translations/nb/index.pickle')
        # The chunk file should have the two translated strings
        # present in the order read.
        self.assertFileContains(
            'genfiles/translations/nb/chunk.0',
            (self.box * 10) + '!')

    def testCombining(self):
        kake.make.build('genfiles/translations/divis/index.pickle')
        self.assertFileContains('genfiles/translations/divis/chunk.0',
                                (self.divis * 10) + '!')

    def testApproved(self):
        kake.make.build('genfiles/translations/nb/index.pickle')
        translator = intl.translate.Translator("genfiles/translations")
        # Hello should have the approved bit set, world should not.
        self.assertTrue(translator.has_translation("nb", "hello",
                                                   approved_only=False))
        self.assertTrue(translator.has_translation("nb", "hello",
                                                   approved_only=True))

        self.assertTrue(translator.has_translation("nb", "world!",
                                                   approved_only=False))
        self.assertFalse(translator.has_translation("nb",
                                                    "world!",
                                                    approved_only=True))

        self.assertTrue(translator.has_translation(
                                "nb", "not in unapproved",
                                approved_only=False))
        self.assertTrue(translator.has_translation(
                                "nb", "not in unapproved",
                                approved_only=True))

        # The approved and unapproved entries differ here. The approved entry
        # is 3 boxes, and the unapproved entry is 2 boxes, so we assert that we
        # wind up using the approved entry instead.
        self.assertEqual(translator.gettext_for_lang("nb",
                                                     "volatile"),
                         (self.box * 3).decode('utf-8'))


class TestFetchIndexOnDevAppserver(testutil.KakeTestBase):
    def setUp(self, *args, **kwargs):
        # NOTE: creates self.tmpdir
        super(TestFetchIndexOnDevAppserver, self).setUp(*args, **kwargs)

        compile_rule._COMPILE_RULES = {}
        compile_rule._COMPILE_RULE_LABELS = set()
        self.mock_value("ka_globals.is_on_jenkins", False)
        import kake.compile_po_files
        reload(kake.compile_po_files)

    def testInvalidLocale(self):
        # Imported here due to reload() above
        import kake.compile_po_files

        def mock_call_with_output(self, params):
            if params == ['gsutil', 'ls', 'gs://ka_translations']:
                return ('gs://ka_translations/accents/\n'
                        'gs://ka_translations/boxes/\n')
            else:
                raise Exception('Unexpected call to call_with_output')

        self.mock_function(
            'kake.lib.compile_rule.CompileBase.call_with_output',
            mock_call_with_output)

        with self.assertRaises(
                kake.compile_po_files.NoSuchLocaleCompileFailure):
            kake.make.build('genfiles/translations/canadian/index.pickle')

    def testValidLocale(self):
        def mock_call_with_output(self, params):
            if params == ['gsutil', 'ls', 'gs://ka_translations']:
                return ('gs://ka_translations/accents/\n'
                        'gs://ka_translations/boxes/\n')
            elif params == ['gsutil', 'ls', 'gs://ka_translations/boxes/']:
                return 'gs://ka_translations/boxes/2017-01-01-1337-0001/'
            else:
                raise Exception('Unexpected call to call_with_output %s' %
                                params)

        def mock_call(self, params):
            if params != [
                    'gsutil', '-m', 'cp', '-r',
                    'gs://ka_translations/boxes/2017-01-01-1337-0001/*',
                    'genfiles/translations/boxes']:
                raise Exception('Unexpected call to call %s' % params)

        self.mock_function(
            'kake.lib.compile_rule.CompileBase.call_with_output',
            mock_call_with_output)

        self.mock_function(
            'kake.lib.compile_rule.CompileBase.call', mock_call)

        kake.make.build('genfiles/translations/boxes/index.pickle')


class TestFetchPOFile(testutil.KakeTestBase):
    def setUp(self, *args, **kwargs):
        # creates self.tmpdir
        super(TestFetchPOFile, self).setUp(*args, **kwargs)

        os.makedirs(self._abspath('intl/translations/pofiles'))

        with open(self._abspath('intl/translations/pofiles/boxes.rest.po'),
                  'w') as f:
            f.write("0123456789012345678901234567890123456789")

        with open(self._abspath('intl/translations/pofiles/boxes2.rest.po'),
                  'w') as f:
            f.write("0123456789012345678901234567890123456789")

        self.accentsContents = "accents file content"
        with open(self._abspath('intl/translations/pofiles/accents.rest.po'),
                  'w') as f:
            f.write(self.accentsContents)

    def testNoGitBigfileInPathRaises(self):
        self.mock_environ({'PATH': ''})
        with self.assertRaises(compile_rule.CompileFailure):
            kake.make.build('genfiles/translations/pofiles/boxes.rest.po')

    def testDownloadsFromS3(self):
        # Mock out gitbigile's transport to fake out downloading from S3
        class MockTransport(object):
            name = 'dummy_bucket'

            def get(self, sha, outfile):
                with open(outfile, "w") as f:
                    f.write("downloaded file contents")

            @property
            def bucket(self):
                return self    # just needs to be something with a .name

        def mock_transport(self):
            return MockTransport()

        # We need to run this to get gitbigile into the path so we can
        # successfully mock it out.
        kake.compile_po_files.FetchFileFromS3._munge_sys_path()

        self.mock_function(
            'gitbigfile.command.GitBigfile.transport', mock_transport)

        # Make sure it works to download two files with the same sha1.
        kake.make.build_many([('genfiles/translations/pofiles/boxes.rest.po',
                               {}),
                              ('genfiles/translations/pofiles/boxes2.rest.po',
                               {})])

        self.assertFileContains("genfiles/translations/pofiles/boxes.rest.po",
                                "downloaded file contents")
        self.assertFileContains("genfiles/translations/pofiles/boxes2.rest.po",
                                "downloaded file contents")

    def testCopiesOverFromIntlTranslation(self):
        kake.make.build('genfiles/translations/pofiles/accents.rest.po')

        self.assertFileContains(
            "genfiles/translations/pofiles/accents.rest.po",
            self.accentsContents)

