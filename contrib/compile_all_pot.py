# TODO(colin): fix these lint errors (http://pep8.readthedocs.io/en/release-1.7.x/intro.html#error-codes)
# pep8-disable:E127,E713
"""Builds the all.pot file, which lists strings that need translation.

all.pot has several inputs:
  - .py files, which mark text-to-translate with i18n._("...")
  - .js files, which mark text-to-translate with i18n._("...")
  - .handlebars files, which mark text-to-translate with {{#_}}...{{/_}}
  - .jinja2 files, which mark text-to-translate with {{ _("...") }}
  - the prod datastore, which has text-to-translate in various models
  - strings specified manually in intl/manual.json and intl/manual.csv

We read from each of them.  To aid development, we do this by
extracting strings from each file individually, then combining them.
We save each extracted string as a pickled polib.POFile, which is
faster to read and write than either a normal .po file or a .mo file.
"""

from __future__ import absolute_import

import cPickle
import collections
import json
import os
import re

from shared import ka_root
from third_party import polib

import js_css_packages.packages
import js_css_packages.util
from kake.lib import compile_rule
from kake.lib import computed_inputs
from kake.lib import log


# This is a 'fake' file we use to indicate that we need to read
# strings from the prod datastore.  It needs to exist on disk, so
# filemod_db doesn't complain.  But we decide whether to fetch from
# the datastore separately from what happens to this file.
_DATASTORE_FILE = 'intl/datastore'

# These are the files where we store strings we manually want to indicate
# need to be translated.  We support both json and csv, whichever is
# easier for people.
_MANUAL_TRANSLATION_JSON_FILE = os.path.join('intl', 'manual.json')
_MANUAL_TRANSLATION_CSV_FILE = os.path.join('intl', 'manual.csv')


def _extractor(filename):
    """Return the string form of the fn that extracts strings from filename."""
    if filename == _MANUAL_TRANSLATION_JSON_FILE:
        return 'intl.babel:babel_extract_json'

    if filename == _MANUAL_TRANSLATION_CSV_FILE:
        return 'intl.babel:babel_extract_csv'

    if filename == _DATASTORE_FILE:
        return 'content.babel:babel_extract'

    if filename.endswith('.handlebars'):
        return 'handlebars.babel:babel_extract'

    if filename.startswith('search/') and filename.endswith('.xml'):
        return 'search.babel:babel_extract'

    if filename.startswith('genfiles/labels/'):
        return 'intl.graphie_labels:babel_extract'

    if filename.startswith('templates/'):
        return 'shared_jinja:babel_extract'

    if filename.endswith('.py'):
        return 'python'           # a special function hard-coded into babel

    if filename.endswith('.js'):
        return 'javascript'       # a special function hard-coded into babel

    raise compile_rule.BadRequestFailure(
        'No rule to extract strings from %s' % filename)


def _python_files():
    """Yield all python files that might have text-to-translate.

    Returned filenames are relative to ka-root.

    We look for all python files under ka-root, ignoring files in
    third_party (which we assume handles translation on its own)
    and in genfiles/compiled_handlebars_py, which was translated
    before being compiled.
    """
    for (rootdir, dirs, files) in os.walk(ka_root.root):
        # Go backwards so we can erase as we go.
        for i in xrange(len(dirs) - 1, -1, -1):
            # If we're not a python module, no need to recurse further.
            if not os.path.exists(os.path.join(rootdir, dirs[i],
                                               '__init__.py')):
                del dirs[i]
            elif dirs[i] in ('third_party', 'compiled_handlebars_py'):
                del dirs[i]

        reldir = ka_root.relpath(rootdir)
        for f in files:
            if f.endswith('.py') and f != '__init__.py':
                yield os.path.normpath(os.path.join(reldir, f))


def _javascript_files(manifest_file):
    """Yield all .js files that might have text-to-translate.

    Returned filenames are relative to ka-root.

    We only return javascript files that are listed in packages, plus
    js worker files that are downloaded from appengine directly
    (rather than being packaged).  There is a
    english_only.should_not_translate check later in this file to
    filter out the js we don't care about.

    We return .jsx files, and other files thare are translated after
    they're compiled to .js (unlike handlebars files, which we
    translate before).
    """
    # Do this import late because it calls fix_sys_path(), which may
    # not be necessary if we never actually try to build all.pot
    from deploy import list_files_uploaded_to_appengine

    packages = js_css_packages.packages.read_package_manifest(manifest_file)
    for (_, f) in js_css_packages.util.all_files(packages):
        if f.endswith('.js'):
            yield f
        elif f.endswith('.jsx'):
            yield os.path.join('genfiles', 'compiled_jsx', 'en', f + '.js')

    # There are some files that we load directly, that aren't part of
    # any package.  We get the list of such files from app.yaml skip-files.
    uploaded_files = list_files_uploaded_to_appengine.get_uploaded_files(True)
    for f in uploaded_files:
        # I special-case MathJax here, since I happen to know that
        # it's not used via js workers, but just happens to be
        # code we can't package up for other reasons.
        if (f.endswith('.js') and not f.startswith('genfiles/') and
               not '/MathJax/' in f):
            yield f


def _handlebars_files():
    """Yield all handlebars files that might have text-to-translate.

    Returned filenames are relative to ka-root.

    We assume all .handlebars under javascript needs to be translated.  We
    have an intl.english_only.should_not_translate check later in this file to
    filter out the ones we don't care about.
    """
    root = ka_root.join('javascript')
    for (rootdir, dirs, files) in os.walk(root):
        reldir = ka_root.relpath(rootdir)
        for f in files:
            if f.endswith('.handlebars'):
                relpath = os.path.join(reldir, f)
                yield os.path.normpath(relpath)


def _jinja2_files():
    """Yield all jinja2 files that might have text-to-translate.

    Returned filenames are relative to ka-root.

    We assume all .html and .txt files under templates/ is jinja2.
    """
    root = ka_root.join('templates')
    for (rootdir, dirs, files) in os.walk(root):
        reldir = ka_root.relpath(rootdir)
        for f in files:
            if f.endswith('.html') or f.endswith('.txt'):
                relpath = os.path.join(reldir, f)
                yield os.path.normpath(relpath)


def _graphie_label_files():
    """Yield all label files that should be translated.

    Returned filenames are relative to ka-root.

    We assume that all of the files in genfiles/labels are to-translate.
    """
    with open(ka_root.join('intl', 'translations',
                           'graphie_image_shas.json')) as f:
        for sha in json.load(f).keys():
            yield os.path.join('genfiles', 'labels', 'en',
                               '%s-data.json' % sha)

    with open(ka_root.join('intl', 'translations',
                           'graphie_image_shas_in_articles.json')) as f:
        for sha in json.load(f).keys():
            yield os.path.join('genfiles', 'labels', 'en',
                               '%s-data.json' % sha)


def _cse_files():
    """Yield all custom-search-engine config files that need translation."""
    yield os.path.join('search', 'cse.xml')
    yield os.path.join('search', 'annotations.xml')


def _merge_poentry(existing, new):
    """Merge new into existing.  Sort occurrences, comments, and flags."""
    # First make sure the msgid_plural are compatible.  They are if
    # they match, or one is None (meaning a string is used singular in
    # one place and plural in another).
    if (existing.msgid_plural and new.msgid_plural and
            existing.msgid_plural != new.msgid_plural):
        raise ValueError(
            'po-entries for "%s" have conflicting plurals (%s vs %s)'
            % (new.msgid, existing.msgid_plural, new.msgid_plural))

    # Merging a plural (ngettext) form into a singular (gettext) form.
    # TODO(csilvers): if the ngettext's msgstr_plural[0] is empty, maybe
    #    take it from the gettext's msgstr.
    if not existing.msgid_plural and new.msgid_plural:
        existing.msgid_plural = new.msgid_plural
        existing.msgstr = None   # convert it to a dict (in msgstr_plural)
        existing.msgstr_plural = new.msgstr_plural

    if new.occurrences:
        existing.occurrences.extend(new.occurrences)
        existing.occurrences = sorted(set(existing.occurrences),
                                      key=lambda (f, lineno): (f, int(lineno)))

    if new.comment:
        comments = existing.comment.split('\n') + new.comment.split('\n')
        comments = sorted(set(c for c in comments if c))  # uniquify and sort
        existing.comment = '\n'.join(comments)

    if new.flags:
        existing.flags.extend(new.flags)
        existing.flags = sorted(set(existing.flags))


def _add_poentry(po_entries, filename, lineno, message, comments, context):
    """Turn the output as returned from babel.extract into a polib entry.

    Arguments:
        po_entries: a map from po_entry.msgid -> poentry
        filename: where we are extracting nltext strings from, relative
            to ka-root.
        (rest): as returned from babel.messages.extract.extract_from_file()
    """
    # babel returns a pair for ngettext entries.
    if isinstance(message, basestring):
        (msgid, msgid_plural) = (message, "")
        (msgstr, msgstr_plural) = ("", {})
    else:
        (msgid, msgid_plural) = message
        (msgstr, msgstr_plural) = ("", {0: "", 1: ""})

    flags = []
    # Store whether our string contains %(...)s.
    if '%(' in msgid or '%(' in (msgid_plural or ''):
        flags.append('python-format')

    new_poentry = polib.POEntry(
        msgid=msgid,
        msgid_plural=msgid_plural,
        msgstr=msgstr,
        msgstr_plural=msgstr_plural,
        occurrences=[(filename, str(lineno))],
        msgctxt=context,
        comment='\n'.join(sorted(set(c.strip() for c in comments
                                     if c.strip()))),
        flags=flags)

    if new_poentry.msgid in po_entries:
        old_poentry = po_entries[new_poentry.msgid]
        _merge_poentry(old_poentry, new_poentry)
    else:
        po_entries[new_poentry.msgid] = new_poentry


def _write_pofile(po_entries, filename, write_debug_file_to=None):
    """Write a polib.POFile to filename.

    The po-file format is nicely human-readable, but slow to parse.
    The mo-file format is faster to parse, but loses important
    information.  So we introduce a *third* format: pickled
    polib.POFile.  Whenever we save a pofile to disk, we save a
    pickled form of the python data structure (polib.POFile).

    We also normalize the po-entries before writing the file, to
    minimize diffs.

    Arguments:
       po_entries: a list of of POEntry objects.
       filename: an absolute path to write the pofile to.
       write_debug_file_to: if not None, a filename to write the po_entries
          as a (human-readable) po-file, rather than a po.pickle file.
    """
    from intl import polib_util

    output_pot = polib_util.pofile()
    output_pot.extend(po_entries)

    # sort the po-entries in a canonical order, to make diff-ing
    # easier, but that tries to keep content close together in the
    # file if it's close together in real life.  We sort by first
    # occurrence (alphabetically), which is good for most content,
    # but not for datastore entities, which all have the same
    # occurrence (_DATASTORE_FILE:1).  For them, we sort by first
    # url-they-appear-in.  For entities that match on all of these
    # things, we depend on the fact python's sorts are stable to
    # keep them in input order (that is, the order that we extracted
    # them from the input ifle).
    url_re = re.compile('<http[^>]*>')
    output_pot.sort(key=lambda e: (e.occurrences[0][0],
                                   int(e.occurrences[0][1]),
                                   sorted(url_re.findall(e.comment))[:1]))

    log.v2('Writing to %s', filename)
    with open(filename, 'w') as f:
        cPickle.dump(output_pot, f, protocol=cPickle.HIGHEST_PROTOCOL)

    if write_debug_file_to:
        log.v2('Also writing to %s', write_debug_file_to)
        with open(write_debug_file_to, 'w') as f:
            polib_util.write_pofile(output_pot, f)

    log.v3('Done!')


def _read_pofile(filename):
    """Read from filename, a pickled polib.POFile, and return it."""
    log.v2('Reading from %s', filename)
    try:
        with open(filename) as f:
            return cPickle.load(f)
    except (IOError, OSError):
        return None


class ExtractStrings(compile_rule.CompileBase):
    def version(self):
        """Update every time build() changes in a way that affects output."""
        return 4

    def build(self, outfile_name, infile_names, changed, context):
        # We import here so the kake system doesn't require these
        # imports unless they're actually used.
        import third_party.babel.messages.extract

        assert len(infile_names) == 1, infile_names

        keywords = third_party.babel.messages.extract.DEFAULT_KEYWORDS.copy()
        keywords['_js'] = keywords['_']           # treat _js() like _()
        # <$_> in jsx expands to $_({varmap}, "string", ...), so kw-index is 2.
        keywords['$_'] = (2,)                     # used in .jsx files as <$_>
        keywords['mark_for_translation'] = None   # used in .py files
        keywords['cached_gettext'] = keywords['gettext']  # used in .py files
        keywords['cached_ngettext'] = keywords['ngettext']  # used in .py files

        comment_tags = ['I18N:']

        options = {'newstyle_gettext': 'true',    # used by jinja/ext.py
                   'encoding': 'utf-8'}           # used by jinja/ext.py

        extractor = _extractor(infile_names[0])    # fn extracting strings
        log.v3('Extracting from %s (via %s)' % (infile_names[0], extractor))

        with open(self.abspath(infile_names[0])) as fileobj:
            nltext_data = third_party.babel.messages.extract.extract(
                extractor, fileobj,
                keywords=keywords, comment_tags=comment_tags, options=options,
                strip_comment_tags=True)

            # Create 'pseudo' polib entries, with sets instead of lists to
            # make merging easier.  We'll convert to real polib entries later.
            po_entries = collections.OrderedDict()
            for (lineno, message, comments, context) in nltext_data:
                _add_poentry(po_entries, infile_names[0],
                             lineno, message, comments, context)

        # This turns the 'pseudo' polib entries back into real polib
        # entries and writes them as a pickled pofile to disk.
        _write_pofile(po_entries.itervalues(), self.abspath(outfile_name))


class CombinePOTFiles(compile_rule.CompileBase):
    def version(self):
        """Update every time build() changes in a way that affects output."""
        return 1

    def build(self, outfile_name, infile_names, changed, context):
        # The infiles here are genfiles/extracted_string/foo.pot.pickle
        # Copy unchanged messages from the existing all.pot, if possible.
        po_entries = collections.OrderedDict()
        if outfile_name in changed or changed == infile_names:
            log.v1('Regenerating %s from scratch (it changed on us!)'
                   % outfile_name)
            changed = infile_names       # everything changed
        else:
            # Extract unchanged messages from the existing all.pot
            existing_all_pot = _read_pofile(self.abspath(outfile_name))
            if existing_all_pot:         # we found an existing file
                log.v2('Loading existing messages')

                # We don't care about deleted files: those that
                # existed in the last call to build() but don't exist
                # now.  (They'll be removed from all.pot by default.)
                # Get rid of them from 'changed' so they don't gum up
                # the code below.
                changed = [f for f in changed if f in infile_names]

                # Elements in infile_names and changed look like
                # 'genfiles/extracted_strings/en/foo.pot.pickle'. Here,
                # we want the version of infiles/changed that are just
                # 'foo'.  We use the _input_map to get that mapping.
                orig_infiles = set(context['_input_map'][f][0]
                                   for f in infile_names)
                # f might not be in _input_map if it's ben deleted.
                orig_changed = set(context['_input_map'][f][0]
                                   for f in changed)
                unchanged = orig_infiles - orig_changed
                for entry in existing_all_pot:
                    # Get rid of occurrences for files that no longer exist.
                    # TODO(csilvers): get rid of comments in the same way.
                    entry.occurrences = [occ for occ in entry.occurrences
                                         if occ[0] in unchanged]
                    # If the msgid still exists at all, let's keep it!
                    if entry.occurrences:
                        po_entries[entry.msgid] = entry
            else:
                changed = infile_names

        log.v2('Extracting new and changed messages')
        for filename in changed:
            input_pot = _read_pofile(self.abspath(filename))
            for poentry in input_pot:
                if poentry.msgid in po_entries:
                    existing_poentry = po_entries[poentry.msgid]
                    _merge_poentry(existing_poentry, poentry)
                else:
                    po_entries[poentry.msgid] = poentry

        log.v2('Writing merged output')
        _write_pofile(po_entries.itervalues(), self.abspath(outfile_name),
                      write_debug_file_to=self.abspath(outfile_name.replace(
                          '.pickle', '.txt_for_debugging')))


class NullRule(compile_rule.CompileBase):
    def version(self):
        """Update every time build() changes in a way that affects output."""
        return 1

    def build(self, outfile_name, infile_names, changed, context):
        pass


class ComputePotInputs(computed_inputs.ComputedInputsBase):
    def version(self):
        """Update if input_patterns() changes in a way that affects output."""
        return 1

    def _skip_files_from_app_yaml(self):
        return re.compile('^.*_test\..*$')

    def input_patterns(self, outfile_name, context, triggers, changed):
        import intl.english_only

        assert outfile_name.endswith('all.pot.pickle'), outfile_name

        # TODO(csilvers): look at 'changed' to only update the section
        # that changed, rather than having to call all the
        # file-listers.  (Or at least only reload the manifest when
        # foo-packages.json is in 'changed'.)
        candidates = set()
        candidates.update(_python_files())
        candidates.update(_javascript_files('javascript-packages.json'))
        candidates.update(_handlebars_files())
        candidates.update(_jinja2_files())
        candidates.update(_graphie_label_files())
        candidates.update(_cse_files())
        if _DATASTORE_FILE:                   # disabled for testing
            candidates.add(_DATASTORE_FILE)
        candidates.add(_MANUAL_TRANSLATION_JSON_FILE)
        candidates.add(_MANUAL_TRANSLATION_CSV_FILE)

        # Ignore files we know we don't care about translating: those
        # that english_only.py says not to translate, and files that
        # app.yaml says to skip even for dev_appserver.
        skip_files_re = self._skip_files_from_app_yaml()
        candidates = set(f for f in candidates
                         if (not intl.english_only.should_not_translate_file(f)
                             and not skip_files_re.match(f)))

        # Finally, we're not interested in the files themselves,
        # we're interested in the .pot files that hold the extracted
        # strings for each of them.
        return set('genfiles/extracted_strings/en/%s.pot.pickle' % f
                   for f in candidates)


# The rule to generate the per-file .pot files
compile_rule.register_compile(
    'PER-FILE POT',
    'genfiles/extracted_strings/en/{{path}}.pot.pickle',
    ['{{path}}'],
    ExtractStrings())

# The rule to combine all the per-file .pot files into all.pot.
# This also generates all.pot.txt_for_debugging, which is human-readable.
compile_rule.register_compile(
    'ALL.POT',
    'genfiles/translations/all.pot.pickle',
    ComputePotInputs(computed_inputs.FORCE),   # forces a recompute every time
    CombinePOTFiles())

# This is used only for testing and debugging: get the all.pot.pickle
# file into a more readable format.
compile_rule.register_compile(
    'ALL.POT FOR DEBUGGING',
    'genfiles/translations/all.pot.txt_for_debugging',
    ['genfiles/translations/all.pot.pickle'],
    NullRule())    # all.pot.pickle creates the debugging file automatically
