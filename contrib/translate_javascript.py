"""A Compile object (see compile_rule.py): translates .js files."""

from __future__ import absolute_import

import cStringIO
import json
import re

import third_party.babel.messages.extract

import intl.english_only
from kake import translate_util
from kake.lib import compile_rule


class TranslateJavascript(translate_util.TranslateBase):
    """Class for translating natural-language text in javascript files."""
    # For backwards copmatibility, we look for $._ and $.ngettext too.
    _JS_GETTEXT_RE = re.compile(r'i18n\._|i18n\.ngettext|\$_'
                                r'|\$\._|\$\.ngettext')

    _BABEL_KW = third_party.babel.messages.extract.DEFAULT_KEYWORDS.copy()
    # <$_> in jsx expands to $_({varmap}, "string", ...), so kw-index is 2.
    _BABEL_KW['$_'] = (2,)                     # used in .jsx files as <$_>

    def version(self):
        """Update every time build() changes in a way that affects output."""
        return 1

    def extract_nltext(self, file_contents):
        # Extract the messages from the JavaScript file with the
        # appropriate start and end positions as well.  As a small
        # efficiency short-cut, we can say that if none of (i18n._,
        # i18n.ngettext, $_) are present in file_contents, there won't
        # be any translations to do.
        if not self._JS_GETTEXT_RE.search(file_contents):
            return []
        else:
            # Convert it to a StringIO buffer for pybabel to handle.
            # pybabel expects utf-8 encoded input, so that's what we give it.
            input = cStringIO.StringIO(file_contents.encode('utf-8'))
            r = third_party.babel.messages.extract.extract_javascript(
                input, self._BABEL_KW, [], {'messages_only': True})
            return list(r)    # convert from iterator if need be

    def translate_to_lang(self, babel_output, file_contents, lang, mo_entries):
        """Return file_contents after translation into lang."""
        # Keep track of if the translated file differs from the original.
        has_diff = False

        # Go through all of the matched messages in reverse (to avoid
        # having to deal with the changes in position of the messages).
        for (messages, start, end) in reversed(babel_output):
            # Figure out the lookup key.  extract_javascript returns a
            # list if a plural is found.
            key = messages
            if isinstance(key, basestring):      # singular
                key = messages
                translation = mo_entries.get_singular_translation(key)
                if not translation:
                    continue
                insert_text = json.dumps(translation, sort_keys=True)

            elif messages[0] is None:            # jsx-style $_()
                # For the jsx $_() operator, the gettext string is the
                # *second* argument (the first argument is the value-dict).
                key = messages[1]
                translation = mo_entries.get_singular_translation(key)
                if not translation:
                    continue
                insert_text = json.dumps(translation, sort_keys=True)

            else:                                # plural
                key = messages[0]
                translation_dict = mo_entries.get_plural_translation(key)
                if not translation_dict:
                    continue
                # We need the messages to be sorted by index.
                index_and_messages = sorted(translation_dict.iteritems())
                messages = [message for (_, message) in index_and_messages]
                # We store the info we need in legal javascript format.
                insert_text = json.dumps({"lang": lang, "messages": messages},
                                         sort_keys=True)

            # Insert the string at the right position.
            file_contents = ''.join((file_contents[:start],
                                     insert_text,
                                     file_contents[end:]))

            # A change was made to the file.
            has_diff = True

        if has_diff:
            return file_contents
        else:
            # signals that output == input
            return None

    def translate(self, infile_name, outfile_lang_moentries_context):
        if intl.english_only.should_not_translate_file(infile_name):
            # If we shouldn't translate it, we can just symlink it!
            for (outfile, _, _, _) in outfile_lang_moentries_context:
                self._write_output(infile_name, outfile, None)
            return

        file_contents = self._read_input(infile_name)

        babel_output = self.extract_nltext(file_contents)

        for (outfile, lang, mo_entries, _) in outfile_lang_moentries_context:
            # Get the translation, or None if output == input.
            translated_contents = self.translate_to_lang(
                babel_output, file_contents, lang, mo_entries)

            self._write_output(infile_name, outfile, translated_contents)


# These rules are only used in dev (where we don't compress js), and
# for js worker files, which we likewise translate without compressing.
compile_rule.register_compile(
    'TRANSLATED RAW JS FILES',
    'genfiles/translations/{lang}/{{path}}.js',
    ['{{path}}.js',
     'genfiles/extracted_strings/{lang}/{{path}}.js.small_mo.pickle'],
    TranslateJavascript(),
    # small_mo.pickle files are recreatd every time {lang}.po files
    # change, but their contents usually don't change, so crc's are
    # good for us.
    compute_crc=True)


# This catches files that are compiled (or transpiled) into js.
dirs_with_js = ('genfiles/compiled_{type}',
                # This is a special case (calculator.js has its own directory)
                'genfiles/khan-exercises',
                )
for d in dirs_with_js:
    translate_util.register_translatesafe_compile(
        'TRANSLATED JS FILES (%s)' % d,
        '%s/{lang}/{{path}}.js' % d,
        ['%s/en/{{path}}.js' % d,
         ('genfiles/extracted_strings/{lang}/'
          '%s/{lang}/{{path}}.js.small_mo.pickle' % d)],
        TranslateJavascript(),
        compute_crc=True)


# This is the rule used in prod, where we only translate javascript
# after it's been compressed.  The exception is handlebars files,
# which are translated before they're even converted to javascript, in
# compile_handlebars.py.  Luckily, the special-case rule for the
# handlebars files (in compress_js.py) has higher precedence than this
# rule, so we can be fully general here.
# We use 'trumped_by' to make sure this rule doesn't apply when lang=en,
# and also to make sure this rule doesn't apply when translating handlebars.
translate_util.register_translatesafe_compile(
    'TRANSLATED COMPRESSED JS FILES',
    'genfiles/compressed_javascript/{lang}/{{path}}.min.js',
    ['genfiles/compressed_javascript/en/{{path}}.min.js',
     ('genfiles/extracted_strings/{lang}/genfiles/compressed_javascript/{lang}'
      '/{{path}}.min.js.small_mo.pickle')],
    TranslateJavascript(),
    trumped_by=['COMPRESSED JS', 'COMPRESSED TRANSLATED HANDLEBARS JS'],
    compute_crc=True,
)
