"""Utility routines and a base compile class for translations.

No matter what kind of file we're translating, the process is the
same: extract the natural language text, look up the strings in the
appropriate .po/.mo file, replace the extracted text with the
translated string, and write the output, or if no translations were
done, then make the output a symlink to the input.

This file provides routines to do some of that common work.  It also
provides a base CompileUtil class that all translation classes can
inherit from, which does the work of collecting languages for a single
file.

Finally, it provides a convenience method for 'expanding' translate
compile-rules safely.
"""

from __future__ import absolute_import

import cPickle
import os
import re

from kake.lib import compile_rule
from kake.lib import log


_GENFILES_LANG_RE = re.compile(r'\bgenfiles/([^/]+)/([^/]+)/(.*)')


def register_translatesafe_compile(label, output_pattern, input_patterns,
                                   *args, **kwargs):
    """Create a 'translate-safe' compile_rule, similar to register_compile().

    A 'translate-safe' compile rule is useful in cases where you want
    to translate a files from genfiles/en/... to genfiles/<lang>/...

    The naive way to do this is something like:
        compile_rule.register_compile(
            'genfiles/compressed_javascript/{lang}/{{path}}.min.js',
            ['genfiles/compressed_javascript/en/{{path}}.min.js',
             'genfiles/extracted_strings/{lang}/{{path}}.small_mo.pickle'],
            TranslateJavascript())

    But what if {{path}} is 'genfiles/compiled_jsx/boxes/foo.jsx.js'?
    You end up with a monstrous input path like
       genfiles/compressed_javascript/en/genfiles/compiled_jsx/boxes/foo.jsx.js
    That is undesirable: we want there to be 'en's everywhere.
    (As it is, it's unclear whether the input is English, or boxes, or
    some combination of each.  And God forbid you have to tell the
    difference between that and
       genfiles/compressed_javascript/boxes/genfiles/compiled_jsx/en/foo.jsx.js
    )

    Thus we have the rule that the language should be consistent
    within a path.  This function ensures that happens by registering
    multiple compile rules for various levels of genfile nesting.  You
    end up with not only the above rule, but also
        compile_rule.register_compile(
            'genfiles/compressed_javascript/{lang}/'
            'genfiles/{dir}/{lang}/{{path}}.min.js',
            ['genfiles/compressed_javascript/en/genfiles/{dir}/en/{{path}}...',
             'genfiles/extracted_strings/{lang}/{{path}}.small_mo.pickle'],
            TranslateJavascript())

    up to 5 levels deep (which should be enough given our current
    structure).  Since kake prefers more specific rules to more
    general ones, this solvse the problem.

    We implement this by looking for 'genfiles/<stuff>/<lang var>' in
    the output-pattern, and an input pattern with a parallel
    'genfiles/<stuff>/en'.  When we see that we add more levels of
    genfiles.  If we don't see it, we raise an exception.
    """
    m = _GENFILES_LANG_RE.match(output_pattern)
    if not m:
        raise ValueError("Output pattern '%s' doesn't look like it's made "
                         "for translation" % output_pattern)
    (maindir, langvar, rest_of_output) = m.groups()

    for (en_pattern, input_pattern) in enumerate(input_patterns):
        m = _GENFILES_LANG_RE.match(input_pattern)
        if m and m.group(1) == maindir and m.group(2) == 'en':
            rest_of_input = m.group(3)
            break
    else:
        raise ValueError("No input pattern looks to be an 'English' "
                         "version of the output pattern '%s': %s'"
                         % (output_pattern, input_patterns))

    # Now we're ready to register the compile rules!
    output_genfiles_part = 'genfiles/%s/%s/' % (maindir, langvar)
    input_genfiles_part = 'genfiles/%s/en/' % maindir
    for depth in xrange(1, 6):
        new_output_pattern = output_genfiles_part + rest_of_output

        new_input_patterns = input_patterns[:]
        new_input_patterns[en_pattern] = input_genfiles_part + rest_of_input
        # Other input patterns could mirror the output pattern.  Change
        # them the same way we changed the output pattern.
        for (i, input_pattern) in enumerate(input_patterns):
            if i != en_pattern:     # we already handled this case
                new_input_patterns[i] = re.sub(
                    r'genfiles/%s/%s/' % (maindir, langvar),
                    output_genfiles_part,
                    input_pattern)

        compile_rule.register_compile(label + '_d%s' % depth,
                                      new_output_pattern, new_input_patterns,
                                      *args, **kwargs)

        output_genfiles_part += 'genfiles/{d%s}/%s/' % (depth, langvar)
        input_genfiles_part += 'genfiles/{d%s}/en/' % depth


class SmallMOFile(object):
    """Holds all translations for a single language: msgid->translation."""
    # NOTE: If you modify the source of this, you'll likely have to update both
    # SplitPOFile.version in kake/compile_small_mo.py and Accents.version in
    # kake/fake_translate.py.
    def __init__(self):
        # Map of msgid -> str (for singular) or a dict (for plural) for all
        # translations, regardless of approval status.
        self.translation_map = {}

        # Set of all msgids that have approved translations
        self.approved_msgids = set([])

    def load(self, pofile_name):
        from intl import polib_util

        self.translation_map = {}
        # TODO(csilvers): should we be resetting approved_msgids too?

        log.info('Loading translations from %s', pofile_name)
        # This just calls self.append on every POEntry in pofile.
        polib_util.streaming_pofile(
            pofile_name, callback=lambda po_entry: self.append(po_entry))

    def append(self, po_entry, approved=False):
        """Same as pofile.append: add a po_entry to this SmallMO.

        Passing in approved=True indicates that the translation of this
        po_entry has been approved.
        """
        if approved:
            self.approved_msgids.add(po_entry.msgid)

        # We store the translated output, which is stored in two
        # different places depending on if it's a gettext or
        # ngettext entry.  msgstr_plural is a dict, msgstr is a str.
        if po_entry.msgid_plural:
            # Make sure all the indices are ints, not strings.
            self.translation_map[po_entry.msgid] = {
                int(k): v for (k, v) in po_entry.msgstr_plural.iteritems()}
        else:
            self.translation_map[po_entry.msgid] = po_entry.msgstr

    def save(self, outfile_name):
        with open(outfile_name, 'w') as f:
            cPickle.dump(self, f)

    @staticmethod
    def is_plural(translation):
        """True if the value of a translation map entry is for ngettext."""
        return not isinstance(translation, basestring)    # dict or list

    def get_singular_translation(self, msgid, approved_only=True):
        """Return the singular translation of msgid.

        If msgid is for a singular (gettext) string, just return its
        translation.  If it's for a plural (ngettext) string, return
        msgstr[0], which by gettext convention is the singular
        translation.

        This is needed because sometimes a string is used in both
        ngettext and gettext contents.  For instance:

        i18n.ngettext("Do this exercise", "Do these %(num)s exercises", n);
        ...
        i18n._("Do this exercise");

        In this case 'Do this exercise' will be an ngettext entry, but
        in the second case we'll just want the singular translation,
        not the full ngettext translation.
        """
        if approved_only and msgid not in self.approved_msgids:
            return None

        translation = self.translation_map.get(msgid)
        if translation is None:
            return None
        if self.is_plural(translation):
            return translation[0]
        return translation

    def get_plural_translation(self, msgid, approved_only=True):
        """Return the plural-dict translations of msgid.

        This logs a warning if msgid is a singular (gettext) string,
        and not a plural (ngettext) string.
        """
        if approved_only and msgid not in self.approved_msgids:
            return None

        translation = self.translation_map.get(msgid)
        if translation is None:
            return None
        if self.is_plural(translation):
            return translation
        log.warning("get_plural() called on singular string %s (%s);"
                    " pretending it's untranslated" % (msgid, translation))
        return None


class TranslateBase(compile_rule.CompileBase):
    """A compile rule for translating a file from one language to another.

    Compile-rules that derive from TranslateBase define a translate()
    routine rather than a build() or build_many() routine.

    These rules *must* have the following format for their input files:
        input[0]: the English file to translate
        input[1]: the small_mo.pickle file associated with input[0]
    """
    def __init__(self, lang=None):
        """If lang is specified, you don't need {lang} in the compile rule."""
        self.lang = lang

    def translate(infile_name, outfile_lang_moentries_context):
        """Translates infile to the given outfiles using the mo-entries."""
        raise NotImplementedError('Subclasses must implement translate')

    def build_many(self, outfile_infiles_changed_context):
        """Most efficient to bundle multiple languages for the same infile."""
        # Collect multiple files with the same infile.
        infile_map = {}
        mo_entries_map = {}
        for (outfile, infiles, _, context) in outfile_infiles_changed_context:
            assert len(infiles) == 2, infiles  # file-to-translate, small .mo
            if infiles[1] not in mo_entries_map:
                with open(self.abspath(infiles[1])) as f:
                    mo_entries_map[infiles[1]] = cPickle.load(f)

            lang = self.lang or context['{lang}']
            infile_map.setdefault(infiles[0], []).append(
                (outfile, lang, mo_entries_map[infiles[1]], context))

        for (infile, outfile_lang_moentries_context) in infile_map.iteritems():
            try:
                self.translate(infile, outfile_lang_moentries_context)
            except Exception:
                log.exception('FATAL ERROR building %s',
                              outfile_lang_moentries_context[0][0])
                raise

    def split_outputs(self, outfile_infiles_changed_context, num_processes):
        """Split translations into one chunk per process."""
        if num_processes == 1:
            # Easy case: we don't split at all and let the 1 process
            # handle everything all at once.
            yield outfile_infiles_changed_context
        else:
            # We give back a list with num_processes elements in it, and
            # each element has a subset of the outfiles.  We organize it
            # so if multiple outfiles have the same infile (translating
            # infile into separate languages), we put them in the same
            # chunk, which gives us more efficiency when translating.
            infile_map = {}
            for entry in outfile_infiles_changed_context:
                infile = entry[1][0]     # infiles[0] == file to translate
                infile_map.setdefault(infile, []).append(entry)

            chunk_size = ((len(outfile_infiles_changed_context) - 1)
                          / num_processes + 1)
            chunk = []
            for entries in infile_map.itervalues():
                chunk.extend(entries)
                if len(chunk) >= chunk_size:
                    yield chunk
                    chunk = []
            if chunk:                    # get the last chunk!
                yield chunk

    # --- Utility routines that subclasses can use.

    def _read_input(self, infile_name):
        with open(self.abspath(infile_name)) as f:
            return f.read().decode('utf-8')

    def _write_output(self, infile_name, outfile_name, new_content):
        """Write output to a file, or symlink to infile if new_content=None."""
        # If a file already exists delete it first
        try:
            os.remove(outfile_name)
        except (IOError, OSError):
            pass

        # Only create a file if the file contents have changed.
        # new_content is none if outfile is identical to infile.
        if new_content is None:
            output_dir = os.path.dirname(self.abspath(outfile_name))
            symlink = os.path.relpath(self.abspath(infile_name), output_dir)
            os.symlink(symlink, self.abspath(outfile_name))
            log.info("Creating symlink for translation (content unchanged)")
        else:
            with open(self.abspath(outfile_name), 'w') as f:
                f.write(new_content.encode('utf-8'))
