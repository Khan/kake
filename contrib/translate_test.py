"""Tests the translate_* files."""

from __future__ import absolute_import

import cPickle
import os
import shutil

from shared.testutil import testsize
from third_party import polib

from kake import compile_all_pot
from kake import compile_small_mo
from kake import translate_handlebars
from kake import translate_javascript
from kake import translate_util
import kake.lib.compile_rule
import kake.lib.testutil
import kake.make


class TranslateString(translate_util.TranslateBase):
    """Treats the input file as a single nltext string."""
    def translate(self, infile_name, outfile_lang_moentries_context):
        file_contents = self._read_input(infile_name)
        for (outfile, lang, mo_entries, _) in outfile_lang_moentries_context:
            translated_contents = mo_entries.get_singular_translation(
                file_contents.strip())
            if translated_contents == file_contents:
                translated_contents = None
            self._write_output(infile_name, outfile, translated_contents)


class TestBase(kake.lib.testutil.KakeTestBase):
    def setUp(self, make_small_mo_file=True):
        super(TestBase, self).setUp()

        os.makedirs(self._abspath('javascript'))
        os.makedirs(self._abspath('caps'))
        os.makedirs(self._abspath('intl', 'translations', 'pofiles'))
        os.makedirs(self._abspath('intl', 'translations', 'approved_pofiles'))
        os.makedirs(self._abspath('genfiles', 'translations', 'caps'))
        os.makedirs(self._abspath('genfiles', 'extracted_strings', 'caps'))
        os.makedirs(self._abspath('kake'))

        shutil.copyfile(os.path.join(self.real_ka_root,
                                     'kake', 'compile_js.js'),
                        os.path.join(self.tmpdir,
                                     'kake', 'compile_js.js'))

        with open(self._abspath('f1'), 'w') as f:
            print >>f, 'Graphing linear equations'

        with open(self._abspath('javascript', 'f1.js'), 'w') as f:
            print >>f, 'a = i18n._("Graphing linear equations");'
            print >>f, 'b = i18n._("Hello %(where)s", {where: "world"});'

        with open(self._abspath('javascript', 'f1.jsx'), 'w') as f:
            print >>f, 'a = i18n._("Graphing linear equations");'
            print >>f, 'b = i18n._("Hello %(where)s", {where: "world"});'
            # The actual jsx would be: <$_ where="world">Hello %(where)s</$_>
            # But our fake jsx-compiler won't correctly 'compile' this, so
            # I cheat and put in the post-compiled value.
            print >>f, 'c = $_({where: "world"}, "Hello %(where)s", etc, etc);'

        with open(self._abspath('javascript', 'f1.handlebars'), 'w') as f:
            print >>f, '{{#_}}Graphing linear equations{{/_}}'

        # Also test plural functionality
        with open(self._abspath('javascript', 'f2.js'), 'w') as f:
            print >>f, 'a = $.ngettext("1 second", "%(num)s seconds");'

        with open(self._abspath('javascript', 'f2.handlebars'), 'w') as f:
            print >>f, ('{{#ngettext num}}1 minute{{else}}'
                        '{{num}} minutes{{/ngettext}}')

        # A plural used in a singular context.
        with open(self._abspath('f.html'), 'w') as f:
            print >>f, '<div title="1 second">1 minute</div>'
        with open(self._abspath('f.js'), 'w') as f:
            print >>f, 'a = i18n._("1 minute");'
        with open(self._abspath('f.handlebars'), 'w') as f:
            print >>f, '{{#_}}1 minute{{/_}}'

        # An exercise with no translations.
        with open(self._abspath('f_no.html'), 'w') as f:
            print >>f, '<script>alert(i18n._("Apple"));</script>'
            print >>f, ('<span data-if="alert(i18n._(\'Banana\'));">'
                        'Canteloupe'
                        '</span>')
            print >>f, '<input type="text" value="Durian" />'
            print >>f, '<var>alert(i18n._("Eggplant"));</var>'
            print >>f, ('<span data-if="isSingular(A)"><var>A</var> Fig</span>'
                        '<span data-else=""><var>A</var> Figs</span>')

        # Exercise files with partial translations in diferent kinds of nltext
        # positions.
        with open(self._abspath('f_p1.html'), 'w') as f:
            print >>f, '<script>alert(i18n._("Apple"));</script>'
            print >>f, '<script>alert(i18n._("Addition 1"));</script>'

        with open(self._abspath('f_p2.html'), 'w') as f:
            print >>f, '<script>alert(i18n._("Apple"));</script>'
            print >>f, '<span data-if="alert(i18n._(\'Addition 1\'));"></span>'

        with open(self._abspath('f_p3.html'), 'w') as f:
            print >>f, '<script>alert(i18n._("Apple"));</script>'
            print >>f, '<span>Addition 1</span>'

        with open(self._abspath('f_p4.html'), 'w') as f:
            print >>f, '<script>alert(i18n._("Apple"));</script>'
            print >>f, '<input type="text" value="Addition 1" />'

        with open(self._abspath('f_p5.html'), 'w') as f:
            print >>f, '<script>alert(i18n._("Apple"));</script>'
            print >>f, '<var>alert(i18n._("Addition 1"));</var>'

        with open(self._abspath('f_p6.html'), 'w') as f:
            print >>f, '<script>alert(i18n._("Apple"));</script>'
            print >>f, ('<span data-if="isSingular(n)">1 hour</span>'
                        '<span data-else=""><var>n</var> hours</span>')

        with open(self._abspath('f_p7.html'), 'w') as f:
            print >>f, ('<script>'
                        'alert(i18n._("Apple")); alert(i18n._("Addition 1"));'
                        '</script>')

        with open(self._abspath('f_p8.html'), 'w') as f:
            print >>f, ('<script>'
                        'alert(i18n._("Apple")); '
                        'alert(i18n._("Subtraction 1"));'
                        '</script>')

        # A file without a translation
        with open(self._abspath('f_no'), 'w') as f:
            print >>f, 'Hello, world'

        # Make the .po file.  We don't need 'occurences' fields for
        # our tests, but _write_pofile() wants them, so we make up
        # some fake ones.
        e1 = polib.POEntry(msgid='Hello %(where)s',
                           msgstr='HELLO %(where)s',
                           occurrences=[('a', 1)])
        e2 = polib.POEntry(msgid='Graphing linear equations',
                           msgstr='GRAPHING LINEAR EQUATIONS',
                           occurrences=[('a', 1)])
        e3 = polib.POEntry(msgid='Addition 1',
                           msgstr='ADDITION 1',
                           occurrences=[('a', 1)])
        e4 = polib.POEntry(msgid='1 second',
                           msgid_plural='%(num)s seconds',
                           msgstr_plural={'0': '1 SECOND',
                                          '1': '%(num)s SECONDS',
                                          '2': '%(num)s SECS'},
                           occurrences=[('a', 1)])
        e5 = polib.POEntry(msgid='1 minute',
                           msgid_plural='{{num}} minutes',
                           msgstr_plural={'0': '1 MINUTE',
                                          '1': '{{num}} MINUTES',
                                          '2': '{{num}} MINS'},
                           occurrences=[('a', 1)])
        e6 = polib.POEntry(msgid='1 hour',
                           msgid_plural='<var>n</var> hours',
                           msgstr_plural={'0': '1 HOUR',
                                          '1': '<var>n</var> HOURS',
                                          '2': '<var>n</var> H'},
                           occurrences=[('a', 1)])

        # This entry differs between the approved pofiles and the unapproved
        # pofiles
        e3_unapproved = polib.POEntry(msgid='Addition 1',
                                      msgstr='ADDITION ONE',
                                      occurrences=[('a', 1)])

        # These entries only exists in the unapproved pofile
        e7_unapproved = polib.POEntry(msgid='Subtraction 1',
                                      msgstr='SUBTRACTION ONE',
                                      occurrences=[('a', 1)])
        e8_unapproved = polib.POEntry(msgid='1 fortnight',
                                      msgid_plural='{{num}} fortnights',
                                      msgstr_plural={'0': '1 FORTNIGHT',
                                                     '1': '{{num}} FORTNIGHTS',
                                                     '2': '{{num}} FORTNS'},
                                      occurrences=[('a', 1)])

        def save_po_file(entries, outpath):
            po_file = polib.POFile()
            po_file.extend(entries)
            po_file.save(outpath)

        save_po_file((e1, e2, e3_unapproved, e4, e5, e6, e7_unapproved,
                      e8_unapproved),
                     self._abspath('intl', 'translations',
                                   'pofiles', 'caps.rest.po'))
        save_po_file((e1, e2, e3, e4, e5, e6),
                     self._abspath('intl', 'translations',
                                   'approved_pofiles', 'caps.rest.po'))

        # Also make the .pot.pickle files.
        po_entry_map = {
            'f1': [e2],
            'javascript/f1.js': [e2, e1],
            'javascript/f1.jsx': [e2, e1],
            'javascript/f1.handlebars': [e2],
            'javascript/f2.js': [e4],
            'javascript/f2.handlebars': [e5],
            'f.html': [e4, e5, e8_unapproved],
            'f.js': [e5],
            'f.handlebars': [e5],
            'f_no': [],
            'f_no.html': [],
            'f_p1.html': [e3],
            'f_p2.html': [e3],
            'f_p3.html': [e3],
            'f_p4.html': [e3],
            'f_p5.html': [e3],
            'f_p6.html': [e6],
            'f_p7.html': [e3],
            'f_p8.html': [e7_unapproved],
        }
        for (fname, po_entries) in po_entry_map.iteritems():
            fname = 'genfiles/extracted_strings/en/%s.pot.pickle' % fname
            if not os.path.isdir(os.path.dirname(self._abspath(fname))):
                os.makedirs(os.path.dirname(self._abspath(fname)))
            compile_all_pot._write_pofile(po_entries, self._abspath(fname))

        if make_small_mo_file:
            for f in po_entry_map:
                fout = 'genfiles/extracted_strings/caps/%s.small_mo.pickle' % f
                if not os.path.isdir(os.path.dirname(self._abspath(fout))):
                    os.makedirs(os.path.dirname(self._abspath(fout)))
                compile_small_mo.SplitPOFile().build_many([
                    (fout,
                     ['genfiles/extracted_strings/en/%s.pot.pickle' % f,
                      'intl/translations/pofiles/caps.rest.po',
                      'intl/translations/approved_pofiles/caps.rest.po'],
                     ['intl/translations/pofiles/caps.rest.po'],
                     {})])

    def build(self, translator, infile, outfile):
        translator.build_many([(
            outfile,
            [infile,
             'genfiles/extracted_strings/caps/%s.small_mo.pickle' % infile],
            [outfile],
            {'{lang}': 'caps'}
            )])


@testsize.tiny
class TestSmallMo(TestBase):
    def test_approval_flag(self):
        with open(self._abspath('genfiles/extracted_strings/caps/'
                                'f.html.small_mo.pickle')) as f:
            small_mo = cPickle.load(f)

        # We have translations for both "1 second", and "1 fortnight"
        self.assertIsNotNone(small_mo.get_plural_translation(
                                "1 second", approved_only=False))
        self.assertIsNotNone(small_mo.get_singular_translation(
                                "1 second", approved_only=False))
        self.assertIsNotNone(small_mo.get_plural_translation(
                                "1 fortnight", approved_only=False))
        self.assertIsNotNone(small_mo.get_singular_translation(
                                "1 fortnight", approved_only=False))

        # ...but the translation for "1 fortnight" is not approved.
        self.assertIsNotNone(small_mo.get_plural_translation(
                                "1 second", approved_only=True))
        self.assertIsNotNone(small_mo.get_singular_translation(
                                "1 second", approved_only=True))
        self.assertIsNone(small_mo.get_plural_translation(
                                "1 fortnight", approved_only=True))
        self.assertIsNone(small_mo.get_singular_translation(
                                "1 fortnight", approved_only=True))


@testsize.tiny
class TestTranslations(TestBase):
    def test_simple(self):
        translator = TranslateString()
        self.build(translator, 'f1', 'f1_caps')
        self.assertFile('f1_caps', 'GRAPHING LINEAR EQUATIONS')

    def test_symlink_when_there_is_no_translation(self):
        translator = TranslateString()
        self.build(translator, 'f_no', 'caps/f1_symlink')
        self.assertFile('caps/f1_symlink', 'Hello, world\n')
        self.assertTrue(os.path.islink(self._abspath('caps', 'f1_symlink')))
        self.assertEqual(os.path.join('..', 'f_no'),
                         os.readlink(self._abspath('caps', 'f1_symlink')))


@testsize.tiny
class TestJavascript(TestBase):
    def test_singular(self):
        translator = translate_javascript.TranslateJavascript()
        self.build(translator, 'javascript/f1.js', 'caps/f1.js')
        self.assertFile('caps/f1.js',
                        'a = i18n._("GRAPHING LINEAR EQUATIONS");\n'
                        'b = i18n._("HELLO %(where)s", {where: "world"});\n')

    def test_plural(self):
        translator = translate_javascript.TranslateJavascript()
        self.build(translator, 'javascript/f2.js', 'caps/f2.js')
        self.assertFile('caps/f2.js',
                        'a = $.ngettext({"lang": "caps", '
                        '"messages": ["1 SECOND", "%(num)s SECONDS", '
                        '"%(num)s SECS"]});\n')

    def test_ngettext_entry_used_in_singular_context(self):
        translator = translate_javascript.TranslateJavascript()
        self.build(translator, 'f.js', 'caps/f.js')
        self.assertFile('caps/f.js',
                        'a = i18n._("1 MINUTE");\n')

    def test_should_not_translate_file(self):
        self.mock_function('intl.english_only.should_not_translate_file',
                           lambda f: f == 'javascript/f1.js')
        translator = translate_javascript.TranslateJavascript()

        # caps/f1.js should be a symlink since it's in do-not-translate
        self.build(translator, 'javascript/f1.js', 'caps/f1.js')
        self.assertTrue(os.path.islink(self._abspath('caps', 'f1.js')))
        self.assertEqual('../javascript/f1.js',
                         os.readlink(self._abspath('caps', 'f1.js')))

        # But f2.js is a different story...
        self.build(translator, 'javascript/f2.js', 'caps/f2.js')
        self.assertFile('caps/f2.js',
                        'a = $.ngettext({"lang": "caps", '
                        '"messages": ["1 SECOND", "%(num)s SECONDS", '
                        '"%(num)s SECS"]});\n')


@testsize.tiny
class TestHandlebars(TestBase):
    def test_singular(self):
        translator = translate_handlebars.TranslateHandlebars()
        self.build(translator, 'javascript/f1.handlebars', 'caps/f1.hbars')
        self.assertFile('caps/f1.hbars',
                        'GRAPHING LINEAR EQUATIONS\n')

    def test_plural(self):
        translator = translate_handlebars.TranslateHandlebars()
        self.build(translator, 'javascript/f2.handlebars', 'caps/f2.hbars')
        self.assertFile('caps/f2.hbars',
                        '{{#ngettext  num "caps" 0}}1 MINUTE{{else}}'
                        '{{#ngettext  num "caps" 1}}{{num}} MINUTES{{else}}'
                        '{{num}} MINS{{/ngettext}}{{/ngettext}}\n')

    def test_ngettext_entry_used_in_singular_context(self):
        translator = translate_handlebars.TranslateHandlebars()
        self.build(translator, 'f.handlebars', 'caps/f.hbars')
        self.assertFile('caps/f.hbars',
                        '1 MINUTE\n')

    def test_gettext_entry_used_in_plural_context(self):
        with open(self._abspath('f.handlebars'), 'w') as f:
            print >>f, ('{{#ngettext num}}Addition 1{{else}}Additions 1'
                        '{{/ngettext}}')

        translator = translate_handlebars.TranslateHandlebars()
        self.build(translator, 'f.handlebars', 'caps/f.hbars')
        # Shouldn't translate our string since it's a singular string
        # used in a plural context, and it doesn't know how to
        # translate the plural.
        self.assertFile('caps/f.hbars',
                        '{{#ngettext num}}Addition 1{{else}}Additions 1'
                        '{{/ngettext}}\n')


@testsize.tiny
class TestBuild(TestBase):
    """Test make.build() on translate targets."""
    def setUp(self):
        # make.build should make the small-mo file for us.
        super(TestBuild, self).setUp(make_small_mo_file=False)

    def test_javascript(self):
        kake.make.build('genfiles/translations/caps/javascript/f1.js')
        self.assertFile('genfiles/translations/caps/javascript/f1.js',
                        'a = i18n._("GRAPHING LINEAR EQUATIONS");\n'
                        'b = i18n._("HELLO %(where)s", {where: "world"});\n')

    def test_handlebars(self):
        kake.make.build('genfiles/translations/caps/javascript/f1.handlebars')
        self.assertFile('genfiles/translations/caps/javascript/f1.handlebars',
                        'GRAPHING LINEAR EQUATIONS\n')

    def test_incremental_rebuilds(self):
        """Test we don't re-translate when irrelevant translations change."""
        kake.make.build('genfiles/translations/caps/javascript/f1.handlebars')
        kake.make.build('genfiles/translations/caps/javascript/f2.handlebars')

        po_path = self._abspath('intl', 'translations', 'approved_pofiles',
                                'caps.rest.po')
        with open(po_path) as f:
            old_po = f.read()
        new_po = old_po.replace('MINUTE', 'MINUUUUTE')   # used in f2, not f1
        with open(po_path, 'w') as f:
            print >>f, new_po

        self.assertFileLacks(
            'genfiles/translations/caps/javascript/f2.handlebars',
            'MINUUUUTE')

        # Now rebuilding f1 should be a noop.
        cr = kake.lib.compile_rule.find_compile_rule(
            'genfiles/translations/caps/javascript/f1.handlebars')

        with self.assertCalled(cr.compile_instance.translate, 0):
            kake.make.build(
                'genfiles/translations/caps/javascript/f1.handlebars')

        # While rebuilding f2 should not be.
        with self.assertCalled(cr.compile_instance.translate, 1):
            kake.make.build(
                'genfiles/translations/caps/javascript/f2.handlebars')

        self.assertFileContains(
            'genfiles/translations/caps/javascript/f2.handlebars',
            'MINUUUUTE')


class TestBuildForFakeLang(TestBase):
    """Test make.build() using the special codepath for fake languages."""

    # Note we don't make any fake boxes.po file at all.  kake
    # automatically extracts the strings from the input file,
    # fake-translates them, and inserts them into the translated file,
    # all on the fly.

    _BOX = u'\u25a1'.encode('utf-8')
    _UTF8_GRAPHING_LINEAR_EQUATIONS = '%s %s %s' % (_BOX * len('GRAPHING'),
                                                    _BOX * len('LINEAR'),
                                                    _BOX * len('EQUATIONS'))
    _S_GRAPHING_LINEAR_EQUATIONS = '%s %s %s' % (r'\u25a1' * len('GRAPHING'),
                                                 r'\u25a1' * len('LINEAR'),
                                                 r'\u25a1' * len('EQUATIONS'))
    _S_HELLO_WORLD = '%s %%(where)s' % (r'\u25a1' * len('HELLO'))
    _S_ADDITION_1 = '%s %s' % (r'\u25a1' * len('ADDITION'),
                               r'\u25a1' * len('1'))

    def test_javascript(self):
        kake.make.build('genfiles/translations/boxes/javascript/f1.js')
        self.assertFile('genfiles/translations/boxes/javascript/f1.js',
                        'a = i18n._("%s");\n'
                        'b = i18n._("%s", {where: "world"});\n'
                        % (self._S_GRAPHING_LINEAR_EQUATIONS,
                           self._S_HELLO_WORLD))

    def test_jsx(self):
        kake.make.build('genfiles/compiled_jsx/boxes/javascript/f1.jsx.js')
        self.assertFile('genfiles/compiled_jsx/boxes/javascript/f1.jsx.js',
                        'a = i18n._("%s");\n'
                        'b = i18n._("%s", {where: "world"});\n'
                        'c = $_({where: "world"}, "%s", etc, etc);\n'
                        % (self._S_GRAPHING_LINEAR_EQUATIONS,
                           self._S_HELLO_WORLD,
                           self._S_HELLO_WORLD))

    def test_handlebars(self):
        kake.make.build('genfiles/translations/boxes/javascript/f1.handlebars')
        self.assertFile('genfiles/translations/boxes/javascript/f1.handlebars',
                        '%s\n' % self._UTF8_GRAPHING_LINEAR_EQUATIONS)
