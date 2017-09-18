# TODO(colin): fix these lint errors (http://pep8.readthedocs.io/en/release-1.7.x/intro.html#error-codes)
# pep8-disable:E127,E128
"""Tests compile_all_pot.py."""

from __future__ import absolute_import

import json
import os

import mock
from shared.testutil import testsize

from kake import compile_all_pot
from kake.lib import compile_rule
from kake.lib import testutil
import kake.make


_GRAPHIE_DATA = r'''svgData110f132aaa8a4e2aed4655088a99552715a1177f(
{"range":[[-1.15,1.15],[-1,1]],"labels":[
{"content":"\\blue{\\text{bluetext}}","coordinates":[-1.25,0.53],
    "alignment":"center", "typesetAsMath":true,"style":{"color":"black"}},
{"content":"\\pink{\\text{pinktext}}","coordinates":[-0.125,0.53],
    "alignment":"center","typesetAsMath":true,"style":{"color":"black"}}]});'''


class TestBase(testutil.KakeTestBase):
    def setUp(self, *args, **kwargs):
        super(TestBase, self).setUp(*args, **kwargs)   # creates self.tmpdir

        # Disable trying to fetch from the prod datastore.
        # TODO(csilvers): figure out how to test this functionality too.
        self.mock_value('kake.compile_all_pot._DATASTORE_FILE', None)

        # Copy over all the files from compile_all_pot-testfiles/,
        # to create our new mini-webapp repo.
        self._copy_to_test_tmpdir(os.path.join('kake',
                                               'compile_all_pot-testfiles'))

        # Write a graphie image sha list with just one item
        os.makedirs(self._abspath('intl', 'translations'))
        with open(self._abspath('intl', 'translations',
                                'graphie_image_shas.json'), 'w') as f:
            json.dump({
                "110f132aaa8a4e2aed4655088a99552715a1177f": {
                    "itemId": "xc1b25120",
                    "exerciseSlug": "number-opposites"}
            }, f)

        # Write a graphie image sha for articles list with just one item
        with open(self._abspath('intl', 'translations',
                'graphie_image_shas_in_articles.json'), 'w') as f:
            json.dump({
                "110f132aaa8a4e2aed4655088a99552715a1177f": "number-article"
            }, f)

        # Write the CSE config files.
        os.makedirs(self._abspath('search'))
        with open(self._abspath('search', 'cse.xml'), 'w') as f:
            print >>f, '<xml><cse></cse></xml>'
        with open(self._abspath('search', 'annotations.xml'), 'w') as f:
            print >>f, '<xml><annotations></annotations></xml>'

        def mock_url_retrieve(url, outfile_name):
            with open(self._abspath(outfile_name), 'w') as f:
                f.write(_GRAPHIE_DATA)

        self.mock_function('urllib.urlretrieve', mock_url_retrieve)

        # Also copy app.yaml, so we can do proper ignoring
        self._copy_to_test_tmpdir('app.yaml')

        os.makedirs(self._abspath('genfiles', 'translations'))
        # A convenience var
        self.all_pot = self._abspath('genfiles', 'translations',
                                     'all.pot.txt_for_debugging')

    def _build(self, files_to_include, changed_files=None):
        """Changed-files of none means *all* files to include are changed."""
        # First we have to build the individual .pot files.
        pot_files_to_include = [
            'genfiles/extracted_strings/en/%s.pot.pickle' % f
            for f in files_to_include]
        input_map = {'genfiles/extracted_strings/en/%s.pot.pickle' % f: [f]
                     for f in files_to_include}
        kake.make.build_many([(f, {'_input_map': input_map})
                              for f in pot_files_to_include])

        pickle_compiler = compile_all_pot.CombinePOTFiles()
        if changed_files is None:
            changed_files = files_to_include
        pickle_file = 'genfiles/translations/all.pot.pickle'
        pot_changed_files = ['genfiles/extracted_strings/en/%s.pot.pickle' % f
                                for f in changed_files]
        pickle_compiler.build(pickle_file, pot_files_to_include,
                              pot_changed_files, {'_input_map': input_map})


class TestIncludingAndIgnoring(TestBase):
    def test_make(self):
        kake.make.build('genfiles/translations/all.pot.txt_for_debugging')
        # We just make sure the file contents are plausible.
        self.assertFileContains(
            'genfiles/translations/all.pot.txt_for_debugging',
            'msgid "Single line"')
        self.assertFileContains(
            'genfiles/translations/all.pot.txt_for_debugging',
            'msgid_plural "I am %(num)s plural!"')
        self.assertFileContains(
            'genfiles/translations/all.pot.txt_for_debugging',
            '#: javascript/j1.js:')
        self.assertFileContains(
            'genfiles/translations/all.pot.txt_for_debugging',
            '#, python-format')

    def test_comment_is_not_wrapped(self):
        kake.make.build('genfiles/translations/all.pot.txt_for_debugging')
        self.assertFileContains(
                'genfiles/translations/all.pot.txt_for_debugging',
                ('https://www.khanacademy.org/translations/edit/redirect/e/'
                 'number-opposites?lang=xc1b25120#en-pt'))

    def test_all_files_are_included_that_should_be(self):
        kake.make.build('genfiles/translations/all.pot.txt_for_debugging')
        for f in ('templates/j1.html', 'templates/foo/j2.html',
                  'templates/j1.txt',
                  'javascript/j1.js', 'javascript/j1.handlebars',
                  'javascript/foo/j2.js', 'javascript/foo/j2.handlebars',
                  'genfiles/labels/en/'
                  '110f132aaa8a4e2aed4655088a99552715a1177f-data.json'):
            self.assertFileContains(
                'genfiles/translations/all.pot.txt_for_debugging',
                ' %s:' % f)

    def test_ignores_js_file_not_in_a_package(self):
        kake.make.build('genfiles/translations/all.pot.txt_for_debugging')
        self.assertFileLacks('genfiles/translations/all.pot.txt_for_debugging',
                             'jignore.js')

    def test_ignores_test_file(self):
        """We ignore this directory because it's in app.yaml skip-files."""
        kake.make.build('genfiles/translations/all.pot.txt_for_debugging')
        self.assertFileLacks('genfiles/translations/all.pot.txt_for_debugging',
                             'j1_test.html')


class TestAddingAndRemoving(TestBase):
    def test_removing_last_occurrence_deletes_poentry(self):
        self._build(['javascript/j1.js', 'javascript/foo/j2.js',
                     'templates/foo/j2.html'])
        self._build(['javascript/j1.js', 'javascript/foo/j2.js'],
                     changed_files=['templates/foo/j2.html'])
        self.assertFileLacks('genfiles/translations/all.pot.txt_for_debugging',
                             'I am so happy!')   # only in j2.html

    def test_adding_new_occurrence_adds_poentry(self):
        self._build(['javascript/j1.js', 'javascript/foo/j2.js'])
        self._build(['javascript/j1.js', 'javascript/foo/j2.js',
                     'templates/foo/j2.html'],
                     changed_files=['templates/foo/j2.html'])
        self.assertFileContains(
            'genfiles/translations/all.pot.txt_for_debugging',
            'I am so happy!')   # only in j2.html

    def test_specifying_the_same_file_twice_is_a_noop(self):
        self._build(['javascript/j1.js'])
        with open(self.all_pot) as f:
            once_contents = f.read()

        os.unlink(self.all_pot)
        self._build(['javascript/j1.js', 'javascript/j1.js'])
        with open(self.all_pot) as f:
            twice_contents = f.read()

        self.assertEqual(once_contents, twice_contents)

    def test_adding_compatible_plural__singular_first(self):
        self._build(['javascript/j1.js'])
        self.assertFileContains(
            'genfiles/translations/all.pot.txt_for_debugging',
            'msgid "Am I singular or plural?"\n'
            'msgstr ""')

        self._build(['javascript/j1.js', 'javascript/foo/j2.js'],
                    changed_files=['javascript/foo/j2.js'])
        self.assertFileContains(
            'genfiles/translations/all.pot.txt_for_debugging',
            'msgid "Am I singular or plural?"\n'
            'msgid_plural "I am %(num)s plural!"\n'
            'msgstr[0] ""')

    def test_adding_compatible_plural__plural_first(self):
        self._build(['javascript/foo/j2.js'])
        self._build(['javascript/j1.js', 'javascript/foo/j2.js'],
                    changed_files=['javascript/j1.js'])

    def test_adding_incompatible_plural(self):
        self._build(['javascript/j1.js', 'javascript/foo/j2.js'])
        with self.assertRaises(ValueError):
            self._build(['javascript/j1.js', 'javascript/foo/j2.js',
                         'javascript/foo/jplural.js'],
                        changed_files=['javascript/foo/jplural.js'])


class TestOccurrences(TestBase):
    def assert_has_occurrences(self, occurrences_string):
        """assert there's a po-entry with *exactly* this occurrences line."""
        if not occurrences_string.endswith('\n'):
            occurrences_string += '\n'
        self.assertFileContains(
            'genfiles/translations/all.pot.txt_for_debugging',
            occurrences_string)

    def test_sort_order(self):
        self._build(['javascript/j1.js', 'javascript/foo/j2.js'])
        # j2 is first in sort order, even though it was second in build order.
        self.assert_has_occurrences(
            '#: javascript/foo/j2.js:4 javascript/j1.js:4')

    def test_added_occurrence(self):
        self._build(['javascript/j1.js', 'javascript/foo/j2.js'])
        self._build(['javascript/j1.js', 'javascript/foo/j2.js',
                     'templates/foo/j2.html'],
                     changed_files=['templates/foo/j2.html'])
        self.assert_has_occurrences(
            '#: javascript/foo/j2.js:4 javascript/j1.js:4'
            ' templates/foo/j2.html:4')

    def test_deleted_occurrence(self):
        self._build(['javascript/j1.js', 'javascript/foo/j2.js',
                     'templates/foo/j2.html'])
        self._build(['javascript/j1.js', 'javascript/foo/j2.js'],
                     changed_files=[])
        self.assert_has_occurrences(
            '#: javascript/foo/j2.js:4 javascript/j1.js:4')


class TestComments(TestBase):
    def assert_has_comment(self, comment_string):
        """Assert there's a po-entry with *exactly* these comment lines."""
        # polib always puts comments first for a msgid, followed by
        # occurrences, so we can just do a string match.
        if not comment_string.endswith('\n'):
            comment_string += '\n'
        self.assertFileContains(
            'genfiles/translations/all.pot.txt_for_debugging',
            '\n\n%s#:' % comment_string)

    def test_comment_order_matches_build_order(self):
        self._build(['javascript/j1.js', 'templates/foo/j2.html'])
        self.assert_has_comment(
            '#. This is a different comment than in j1.html\n'
            '#. This is a single line comment\n')

    def test_identical_comments_are_merged(self):
        self._build(['javascript/j1.handlebars', 'templates/j1.html'])
        self.assert_has_comment(
            '#. This is a single line comment')

    def test_multiline_comments_are_space_joined(self):
        self._build(['javascript/j1.js', 'javascript/foo/j2.js'])
        self.assert_has_comment(
            '#. This is a multi-line comment. But line two differs from line '
            'two in j1.js.\n'
            '#. This is a multi-line comment. But line two differs from line '
            'two in j2.js.\n'
            )

    def test_comment_repeated_for_single_string(self):
        """When a string has two comments above it, which are the same."""
        self._build(['javascript/j1.js'])
        # "this content occurs in no other file" is listed there
        # twice, but we should only include it once.
        self.assert_has_comment(
            '#. but this comment occurs a lot.  I repeat myself:\n'
            '#. this content occurs in no other file.\n')

    def test_multi_line_jinja2_comments(self):
        self._build(['templates/j1.html'])
        self.assert_has_comment(
            '#. This is a multi-line comment.\n'
            '#.     Babel can deal with these as well\n')

    def test_merge_comments_old_only(self):
        # j1 has a comment for "Single line", j2 does not.
        self._build(['javascript/j1.js'])
        self._build(['javascript/j1.js', 'javascript/foo/j2.js'],
                    changed_files=['javascript/foo/j2.js'])
        self.assert_has_comment('#. This is a single line comment')

    def test_merge_comments_new_only(self):
        # j1 has a comment for "Single line", j2 does not.
        self._build(['javascript/foo/j2.js'])
        self._build(['javascript/j1.js', 'javascript/foo/j2.js'],
                    changed_files=['javascript/j1.js'])
        self.assert_has_comment('#. This is a single line comment')

    def test_merge_comments_both(self):
        self._build(['templates/j1.html'])
        self._build(['templates/j1.html', 'templates/foo/j2.html'],
                    changed_files=['templates/foo/j2.html'])
        self.assert_has_comment(
            '#. This is a different comment than in j1.html\n'
            '#. This is a single line comment\n')


class TestPOFileSorting(TestBase):
    def test_entries_on_same_line(self):
        self._build(['templates/j1.html'])
        with open(self._abspath('genfiles', 'translations',
                                'all.pot.txt_for_debugging')) as f:
            po_contents = f.read()
        position = {}
        for s in ('a', 'b', 'c', 'x', 'y', 'z'):
            position[s] = po_contents.find('msgid "%s"' % s)
        self.assertLess(position['a'], position['b'], position)
        self.assertLess(position['b'], position['c'], position)
        self.assertLess(position['c'], position['x'], position)
        self.assertLess(position['x'], position['y'], position)
        self.assertLess(position['y'], position['z'], position)


class TestFlags(TestBase):
    def test_python_format_yes(self):
        self._build(['javascript/j1.js', 'javascript/j1.handlebars'])
        self.assertFileContains(
            'genfiles/translations/all.pot.txt_for_debugging',
            '#, python-format\n'
            'msgid "This line has %(vbl)s"\n')

    def test_python_format_no(self):
        self._build(['javascript/j1.js', 'javascript/j1.handlebars'])
        self.assertFileLacks(
            'genfiles/translations/all.pot.txt_for_debugging',
            '#, python-format\n'
            'msgid "This line has {{variables}}"\n')

    def test_added_python_format(self):
        # In j1.js, "Am I singular" is gettext and has no python-format.
        self._build(['javascript/j1.js'])
        self.assertFileLacks(
            'genfiles/translations/all.pot.txt_for_debugging',
            '#, python-format\n'
            'msgid "Am I singular or plural?"')

        self._build(['javascript/j1.js', 'javascript/foo/j2.js'],
                    changed_files=['javascript/foo/j2.js'])
        self.assertFileContains(
            'genfiles/translations/all.pot.txt_for_debugging',
            '#, python-format\n'
            'msgid "Am I singular or plural?"')


@testsize.tiny
class TestManualJson(TestBase):
    def test_entries_and_comments(self):
        self._build(['intl/manual.json'])
        self.assertFile(
            'genfiles/translations/all.pot.txt_for_debugging',
            '# \n'
            'msgid ""\n'
            'msgstr ""\n'
            '\n'
            '#. This comment goes with this manual string\n'
            '#: intl/manual.json:2\n'
            'msgid "A manual string"\n'
            'msgstr ""\n'
            '\n'
            '#: intl/manual.json:3\n'
            'msgid "A manual string without comments"\n'
            'msgstr ""\n'
            '\n'
            "#. Don't think manual.json wasn't getting into this action!\n"
            '#: intl/manual.json:4\n'
            'msgid "Single line"\n'
            'msgstr ""\n')


@testsize.tiny
class TestManualCsv(TestBase):
    def test_entries_and_comments(self):
        self._build(['intl/manual.csv'])
        self.assertFile(
            'genfiles/translations/all.pot.txt_for_debugging',
            '# \n'
            'msgid ""\n'
            'msgstr ""\n'
            '\n'
            '#. This comment goes with this manual string\n'
            '#: intl/manual.csv:2\n'
            'msgid "A manual string"\n'
            'msgstr ""\n'
            '\n'
            '#: intl/manual.csv:3\n'
            'msgid "A manual string without comments"\n'
            'msgstr ""\n'
            '\n'
            "#. Don't think manual.csv wasn't getting into this action!\n"
            '#: intl/manual.csv:4\n'
            'msgid "Single line"\n'
            'msgstr ""\n')


@testsize.tiny
class TestLoadingAndSaving(TestBase):
    def setUp(self, *args, **kwargs):
        super(TestLoadingAndSaving, self).setUp(*args, **kwargs)
        self.all_pot_pickle = self.all_pot.replace('.txt_for_debugging',
                                                   '.pickle')

    def test_load_non_existing_all_pot(self):
        # We expect no logline because we're regenerating from scratch.
        with mock.patch('kake.lib.log.v2') as logger:
            self._build(['intl/manual.json'])
            self.assertNotIn(
                mock.call('Reading from %s', self.all_pot_pickle),
                logger.call_args_list)
            self.assertIn(
                mock.call('Writing to %s', self.all_pot_pickle),
                logger.call_args_list)

    def test_load_all_files_changed(self):
        # We expect no logline because we're doing a total rebuild.
        self._build(['intl/manual.json', 'javascript/j1.js'])
        with mock.patch('kake.lib.log.v2') as logger:
            self._build(['intl/manual.json', 'javascript/j1.js'])
            self.assertNotIn(
                mock.call('Reading from %s', self.all_pot_pickle),
                logger.call_args_list)
            self.assertIn(
                mock.call('Writing to %s', self.all_pot_pickle),
                logger.call_args_list)

    def test_load_pickle(self):
        self._build(['intl/manual.json', 'javascript/j1.js'])
        with mock.patch('kake.lib.log.v2') as logger:
            self._build(['intl/manual.json', 'javascript/j1.js'],
                        changed_files=['intl/manual.json'])
            self.assertIn(
                mock.call('Reading from %s', self.all_pot_pickle),
                logger.call_args_list)


@testsize.tiny
class TestInvalidFileType(TestBase):
    def test_invalid_file_type(self):
        with self.assertRaises(compile_rule.BadRequestFailure):
            self._build(['webapp/main.rs'])
