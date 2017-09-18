# TODO(colin): fix these lint errors (http://pep8.readthedocs.io/en/release-1.7.x/intro.html#error-codes)
# pep8-disable:E128
"""Converts translation (.po) files to the format we use internally.

Most people convert .po files to .mo files and use the standard
library gettext module to use the translation data.  But not us.  For
reasons of efficiency -- both time and space -- we use our own file
format which is basically a pickled dict.  This converts the .po files
to our new file format.
"""

from __future__ import absolute_import

import md5
import os
import shutil
import sys

import shared.util.thread

import ka_globals
from kake.lib import compile_rule
from kake.lib import computed_inputs
from kake.lib import log


class NoSuchLocaleCompileFailure(compile_rule.BadRequestFailure):
    """Raised when GCS does not contain translations for the requested locale.

    When Google Cloud Storage (GCS) does not contain translations for a
    requested locale, this failure should be negatively cached to avoid
    re-requesting the same index over and over again.

    This failure is only raised on the dev-appserver, NOT on Jenkins.
    """

    def __init__(self, locale):
        super(NoSuchLocaleCompileFailure, self).__init__(
            "The index for the '%s' locale is not present on "
            "Google Cloud Storage." % (locale))


class FetchFileFromS3(compile_rule.CompileBase):
    """If the po-file is stored on S3, retrieve it from there.

    PO files have traditionally been checked into source control
    (github.com:Khan/webapp-i18n).  But a more modern approach has
    been to store those files remotely on S3, and only store the
    s3-file name in source control.  We use the 'git bigfile'
    extension to control this. This compile rule deals with both
    cases: just copying the file in the first case and downloading
    from S3 in the second.

    """
    def version(self):
        """Update every time build() changes in a way that affects output."""
        return 1

    @staticmethod
    def _munge_sys_path():
        """Modify sys.path so we can load the git-bigfile library."""
        # First, find out where git-bigfile lives.  It lives on the
        # path, so we can just look for that.
        for pathdir in os.environ['PATH'].split(':'):
            if os.path.exists(os.path.join(pathdir, 'git-bigfile')):
                sys.path.append(os.path.dirname(pathdir))
                return
        raise compile_rule.CompileFailure(
            "Can't find git-bigfile in %s" % os.environ['PATH'])

    @staticmethod
    def _download_from_s3(gitbigfile_module, outfile_abspath, sha):
        s3_fetcher = gitbigfile_module.GitBigfile().transport()
        log.v2('Downloading s3://%s/%s to %s' % (
            s3_fetcher.bucket.name, sha, outfile_abspath + '.tmp'))
        s3_fetcher.get(sha, outfile_abspath + '.tmp')
        # Make sure we don't create the 'real' file until it's fully
        # downloaded.
        try:
            os.unlink(outfile_abspath)
        except (IOError, OSError):
            pass    # probably "file not found"
        try:
            os.rename(outfile_abspath + '.tmp', outfile_abspath)
        except OSError:
            log.v1('Error fetching %s' % outfile_abspath)
            raise

    def build_many(self, outfile_infiles_changed_context):
        from shared.testutil import fake_datetime

        sha_to_files = {}            # for the files we need to get from S3
        for (outfile, infiles, _, context) in outfile_infiles_changed_context:
            assert len(infiles) == 1, infiles
            assert infiles[0].startswith('intl/translations/')

            with open(self.abspath(infiles[0])) as f:
                head = f.read(64).strip()

            # Does the head look like a sha1?  (sha1's are only 40 bytes.)
            # If so, store it for later.  If not, take care of it now.
            if head.strip('0123456789abcdefABCDEF') == '':
                sha_to_files.setdefault(head, []).append(outfile)
            else:
                # Nope, not a sha1.  NOTE: We could also use a hard-link,
                # but that could fail if genfiles is on a different
                # filesystem from the source.  Copying is more expensive
                # but safer.  Symlinks are right out.
                shutil.copyfile(self.abspath(infiles[0]),
                                self.abspath(outfile))

        if not sha_to_files:
            return

        # We could just call 'git bigfile pull' but we purposefully
        # don't so as to leave untouched the file-contents in
        # intl/translations.  This works better with kake, which
        # doesn't like it when input contents change as part of a kake
        # rule.
        self._munge_sys_path()     # so the following import succeeds
        import gitbigfile.command

        # Download all our files from S3 in parallel.  We store these
        # files under a 'permanent' name based on the sha1.  (Later
        # we'll copy these files to outfile_name.)  That way even if
        # you check out a different branch and come back to this one
        # again, you can get the old contents without needing to
        # revisit S3.
        # GitBigfile() (in _download_from_s3) runs 'git' commands in a
        # subprocess, so we need to be in the right repository for that.
        old_cwd = os.getcwd()
        os.chdir(self.abspath('intl/translations'))
        try:
            # This will actually try to download translation files via
            # bigfile.  This requires a real datetime for making the
            # api requests to S3 (S3 complains about weird dates).
            with fake_datetime.suspend_fake_datetime():
                arglists = []
                for (sha, outfiles) in sha_to_files.iteritems():
                    # Typically a given sha will have only one outfile,
                    # but for some shas (an empty po-file, e.g.), many
                    # outfiles may share the same sha!
                    log.v1('Fetching %s from S3' % ' '.join(outfiles))
                    # We just need to put this in a directory we know we
                    # can write to: take one of the outfile dirs arbitrarily.
                    sha_name = os.path.join(os.path.dirname(outfiles[0]), sha)
                    arglists.append(
                        (gitbigfile.command, self.abspath(sha_name), sha))
                shared.util.thread.run_many_threads(
                    self._download_from_s3, arglists)
        except RuntimeError as why:
            log.error(why)    # probably misleading, but maybe helpful
            # TODO(csilvers): check whether git-bigfile *is* set up
            # correctly, and give a more precise failure message if so.
            raise compile_rule.CompileFailure(
                "Failed to download translation file for %s from S3. "
                "Make sure you have git-bigfile set up as per the "
                "configs in the khan-dotfiles repo: namely, the "
                "'bigfile' section in .gitconfig.khan, and the "
                "update_credentials() section in setup.sh." % outfile)
        finally:
            os.chdir(old_cwd)

        # Now copy from the sha-name to the actual output filename.
        for (sha, outfiles) in sha_to_files.iteritems():
            sha_name = os.path.join(os.path.dirname(outfiles[0]), sha)
            for outfile in outfiles:
                log.v2('Copying from %s to %s' % (sha_name, outfile))
                try:
                    os.unlink(self.abspath(outfile))
                except OSError:
                    pass     # probably file not found
                os.link(self.abspath(sha_name), self.abspath(outfile))

    def num_outputs(self):
        """We limit how many parallel fetches we do so we don't overload S3."""
        return 50


# This is only used on the dev-appserver, NOT on Jenkins (or else we'd never
# update the indices!)
class DownloadIndex(compile_rule.CompileBase):
    def __init__(self):
        super(DownloadIndex, self).__init__()
        self._locale_paths = None

    def version(self):
        """Update every time build() changes in a way that affects output."""
        import datetime
        # Force redownloading once a month.
        return datetime.datetime.now().strftime("%Y-%m")

    def build(self, outfile_name, infile_names, changed, context):
        """Download .index and .chunk files from prod.

        CompilePOFile takes a long time to compute.  So when not on jenkins we
        call this rule instead to fetch from prod what is there.
        """
        if self._locale_paths is None:
            self._init_locale_paths()

        log.v2("Determining latest prod translation files for %s" %
               context['{lang}'])

        locale = context['{lang}']
        locale_path = 'gs://ka_translations/%s/' % locale
        if locale_path not in self.locale_paths:
            raise NoSuchLocaleCompileFailure(locale)

        try:
            stdout = self.call_with_output(['gsutil', 'ls', locale_path])
        except compile_rule.CompileFailure, e:
            # TODO(james): make sure we download gcloud and gsutil as part
            # of the khan-dotfiles setup.
            raise compile_rule.CompileFailure(
                "%s.\nFailed to download translations from gcs. Make sure "
                "that you have gsutil installed via gcloud." % e)
        dirs = stdout.split()

        if dirs:
            most_recent_dir = dirs[-1]
            log.v2("Downloading latest prod files from %s" %
                   most_recent_dir)
            self.call(
                ['gsutil', '-m', 'cp', '-r', "%s*" % most_recent_dir,
                 os.path.dirname(outfile_name)])

            return

        # No translation files found on gcs ... lets complain
        raise compile_rule.CompileFailure(
            "Failed to find translation files for %s on gcs" %
            context['{lang}'])

    def _init_locale_paths(self):
        try:
            self.locale_paths = self.call_with_output(
                ['gsutil', 'ls', 'gs://ka_translations']).split()
        except compile_rule.CompileFailure, e:
            raise compile_rule.CompileFailure(
                "%s.\nFailed to download translations from gcs. Make sure "
                "that you have gsutil installed via gcloud." % e)


class CompilePOFile(compile_rule.CompileBase):
    def version(self):
        """Update every time build() changes in a way that affects output."""
        return 9

    def build(self, outfile_name, infile_names, changed, context):
        """Merge the pofiles and approved pofiles & build pickle and chunks.

        We export from crowdin twice for each language. One time to get all the
        translated strings which winds up in
        intl/translation/pofile/{lang}.(rest|datastore).po files and another
        time to get just the approved translations which winds up in the
        intl/translation/approved_pofile/{lang}.(rest|datastore).po files. This
        merges them all together, preferring an entry in the approved pofile
        over the unapproved one, and adding a flag to the approved entries.  We
        then create our own specially formatted files that use less space.
        There is the genfiles/translations/{lang}/index.pickle that gets
        created, and a bunch of genfiles/translations/{lang}/chunk.# files that
        the index file points to and holds the actual translations.
        """

        # We import here so the kake system doesn't require these
        # imports unless they're actually used.
        import intl.translate
        from intl import polib_util

        full_content = ''
        for infile in sorted([n for n in infile_names
                              if "approved_pofiles" not in n]):
            with open(self.abspath(infile)) as f:
                log.v3("Reading %s" % infile)
                full_content += f.read()

        approved_full_content = ''
        for infile in sorted([n for n in infile_names
                              if "approved_pofiles" in n]):
            with open(self.abspath(infile)) as f:
                log.v3("Reading %s" % infile)
                approved_full_content += f.read()

        log.v3("Calculating md5 to get translation file version for %s" %
               context['{lang}'])
        # The output files need a version string.  We'll use an
        # md5sum of the input files.
        version_md5sum = md5.new(
            full_content + approved_full_content).hexdigest()
        version = 'compile_po_%s' % version_md5sum

        translate_writer = intl.translate.TranslateWriter(
            os.path.dirname(outfile_name), context['{lang}'], version)

        # Now lets combine the two po files and add a flag to the approved
        # pofile entries.
        log.v3("Creating .index and .chunk translation files for %s" %
               context['{lang}'])

        approved_msgids = set()

        def add_approved_entry(po_entry):
            po_entry.flags.append('approved')
            approved_msgids.add(po_entry.msgid)
            translate_writer.add_poentry(po_entry)

        def add_unapproved_entry(po_entry):
            if po_entry.msgid not in approved_msgids:
                translate_writer.add_poentry(po_entry)

        _ = polib_util.streaming_pofile(
            approved_full_content.decode('utf-8'),
            callback=add_approved_entry)   # called on each input POEntry.

        unapproved_pofile = polib_util.streaming_pofile(
            full_content.decode('utf-8'),
            callback=add_unapproved_entry)

        # This adds in the metadata (and only the metadata).
        translate_writer.add_pofile(unapproved_pofile)

        translate_writer.commit()


class IntlToGenfiles(computed_inputs.ComputedInputsBase):
    """Replace intl/translations/pofile/glob with genfiles/translations...

    This is because we want to read files from the
    genfiles/translations/pofiles/ directory, but can't do a glob in
    that directory because it holds generated files.  Luckily there's
    a 1-to-1 correspondence between files in the intl/ directory and
    in the genfiles/ directory, so we can say what the list of files
    in genfiles/translations/pofiles will be.
    """
    def version(self):
        """Update if input_patterns() changes in a way that affects output."""
        return 1

    def input_patterns(self, outfile_name, context, triggers, changed):
        return [x.replace('intl/', 'genfiles/') for x in triggers]


# "Expand" the intl/translations file if it's being stored as a sha1
# for use by 'git bigfile'.
# NOTE: Changing git branches can cause intl/translations timestamps
# to change.  Since a changed .po file causes every single file in
# that language to get recompiled -- expensive! -- it's worth it to
# depend on crc's rather than just timestamps for these files.
compile_rule.register_compile(
    'EXPAND PO-FILE',
    'genfiles/translations/pofiles/{{path}}',
    ['intl/translations/pofiles/{{path}}'],
    FetchFileFromS3(),
    compute_crc=True)


# Also "expand" approved_pofiles with git bigfile.
compile_rule.register_compile(
    'EXPAND APPROVED PO-FILES',
    'genfiles/translations/approved_pofiles/{{path}}',
    ['intl/translations/approved_pofiles/{{path}}'],
    FetchFileFromS3(),
    compute_crc=True)


# (This isn't really po-file-related, but it's about expanding: these
# are the other types of translation files that are stored in S3.)
compile_rule.register_compile(
    'EXPAND PICKLE-FILE',
    'genfiles/translations/{{path}}.pickle',
    ['intl/translations/{{path}}.pickle'],
    FetchFileFromS3(),
    compute_crc=True)

# In addition to index.pickle, this will also create all the chunk.# files.
if ka_globals.is_on_jenkins:
    compile_rule.register_compile(
        'DEPLOYED TRANSLATIONS',
        'genfiles/translations/{lang}/index.pickle',
        # This allows for .po files being split up (to get under github
        # filesize limits: foo.po, foo.po.2, foo.po.3, etc.)
        # We fetch the actual files from
        # genfiles/translations/combined_pofiles,
        # but there's a 1-to-1 relationship between those files and the
        # ones in intl/translations/pofiles.
        IntlToGenfiles(['intl/translations/pofiles/{lang}.*',
                        'intl/translations/approved_pofiles/{lang}.*']),
        CompilePOFile())
else:
    # If not on jenkins (ie. dev server) we do not build this file
    # ourselves as it is very expensive to compute and instead download
    # it from prod.  It's most likely fine to use out of date data, so we
    # don't rebuild on any inputs at all, but instead increase the version once
    # a month.  If a dev wanted a newer datastore sooner, they need to run:
    # make sync_prod_translations LANGS=<lang>
    compile_rule.register_compile(
        'DOWNLOAD PROD TRANSLATIONS',
        'genfiles/translations/{lang}/index.pickle',
        [],
        DownloadIndex())
