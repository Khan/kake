#!/usr/bin/env python

"""The 'driver' to build all the types of files needed by production.

The value-add of this driver is that you can ask to compile things
like 'handlebars' or 'jsx' and it knows how to convert those into
files.

However, you can also give it
1) a filename and it will compile that file
   (the filename will be genfiles/<something>)
2) a package name and it will compile that package
   (the packagename will be, e.g., shared.js or topics.css)
"""

import json
import multiprocessing
import os
import re
import sys

# Include ka-root in python path so we can import all this stuff.
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tools.appengine_tool_setup

tools.appengine_tool_setup.fix_sys_path()    # needed for jinja2 templates

from . import project_root

from deploy import list_files_uploaded_to_appengine
import intl.data
import intl.locale
import js_css_packages.packages
import js_css_packages.util
import kake.compile_js_css_manifest
from . import log
import kake.make
import memory_util
import modules_util


class BuildOptions(object):
    def __init__(self, langs, dev, readable, gae_version):
        """Arguments:
            langs: the natural languages to produce output in,
              always includes 'en'.
            dev: True if we want 'dev' files, False if we want 'prod' files;
              typically relevant only when building js or css packages
            readable: True if we don't want to compress files; only relevant
              when building js or css packages.
            gae_version: a string used to construct the manifest-TOC filename.
        """
        self.langs = langs
        self.dev = dev
        self.readable = readable
        self.gae_version = gae_version


def _package_outdir(js_or_css, build_options):
    assert js_or_css in ('js', 'css'), js_or_css
    dev_or_prod = ('dev' if build_options.dev else 'prod')
    compressed_or_readable = ('readable' if build_options.readable else
                              'compressed')
    return ('%s_%s_packages_%s'
            % (compressed_or_readable, js_or_css, dev_or_prod))


def _all_package_files(javascript_or_stylesheets, pkg_locale, dev,
                       precompiled=False):
    """Yield (package_name, filename-relative-to-ka-root) for all files."""
    packages = js_css_packages.packages.get(javascript_or_stylesheets)
    return js_css_packages.util.all_files(
        packages, precompiled, pkg_locale, dev)


def _all_package_paths(javascript_or_stylesheets, build_options):
    """Filenames for all <package>-package.js/css files.

    This does *not* include the md5-annotated package symlinks.
    """
    if javascript_or_stylesheets == 'javascript':
        basedir = _package_outdir('js', build_options)
    elif javascript_or_stylesheets == 'stylesheets':
        basedir = _package_outdir('css', build_options)
    else:
        raise ValueError('Unexpected value "%s"' % javascript_or_stylesheets)

    packages = js_css_packages.packages.get(javascript_or_stylesheets).keys()
    for lang in build_options.langs:
        for package in packages:
            (name, ext) = os.path.splitext(package)
            outfile = ('genfiles/%s/%s/%s-package%s'
                       % (basedir, lang, name, ext))
            yield (outfile, {})


def css_packages(build_options):
    for path in _all_package_paths('stylesheets', build_options):
        yield path
    if not build_options.dev:
        # For prod, we need the md5-symlinks (and manifest file) as well.
        compressed_or_readable = ('readable' if build_options.readable else
                                  'compressed')
        for lang in build_options.langs:
            yield ('genfiles/%s_manifests_prod/%s/'
                   'stylesheets-md5-packages.json'
                   % (compressed_or_readable, lang),
                   {})


def js_packages(build_options):
    for path in _all_package_paths('javascript', build_options):
        yield path
    if not build_options.dev:
        # For prod, we need the md5-symlinks (and manifest file) as well.
        compressed_or_readable = ('readable' if build_options.readable else
                                  'compressed')
        for lang in build_options.langs:
            yield ('genfiles/%s_manifests_prod/%s/javascript-md5-packages.json'
                   % (compressed_or_readable, lang),
                   {})


def js_and_css_packages(build_options):
    # Also builds the combined js/css manifest for javascript.
    for f in js_packages(build_options):
        yield f
    for f in css_packages(build_options):
        yield f

    dev_or_prod = ('dev' if build_options.dev else 'prod')
    compressed_or_readable = ('readable' if build_options.readable else
                              'compressed')
    outdir = '%s_manifests_%s' % (compressed_or_readable, dev_or_prod)
    for lang in build_options.langs:
        # gae_version is used to construct the toc-filename symlink.
        yield ('genfiles/%s/%s/package-manifest.js' % (outdir, lang),
               {'gae_version': build_options.gae_version})


def one_css_package(build_options, package_name):
    css_outdir = _package_outdir('css', build_options)
    for lang in build_options.langs:
        outfile = ('genfiles/%s/%s/%s-package.css'
                   % (css_outdir, lang, package_name[:-len('.css')]))
        yield (outfile, {})


def one_js_package(build_options, package_name):
    js_outdir = _package_outdir('js', build_options)
    for lang in build_options.langs:
        outfile = ('genfiles/%s/%s/%s-package.js'
                   % (js_outdir, lang, package_name[:-len('.js')]))
        yield (outfile, {})


def js_file_to_package_mapping(build_options):
    for lang in build_options.langs:
        outfile = ('genfiles/paths_and_packages/%s/js/'
                   'all_paths_to_packages_prod.json' % lang)
        yield (outfile, {})


def perseus_mobile_js_files(build_options):
    # These files are served directly by /api/v1/ios/static_redirect.
    max_perseus_version = 10  # perseus-10.js  <-- for greppability
    for version in xrange(0, max_perseus_version + 1):
        for lang in build_options.langs:
            outfile = os.path.join('genfiles', 'compressed_javascript', lang,
                                   'genfiles', 'compiled_es6', lang,
                                   'javascript', 'perseus-package',
                                   'perseus-%d.min.js' % version)
            yield (outfile, {})

        # Additionally, yield a hash of the source JS, for cache busting
        # purposes.
        outfile = os.path.join('genfiles', 'compiled_perseus_hash',
                               'perseus-%d-hash.txt' % version)
        yield (outfile, {})


def topic_icon_manifest(build_options):
    """Build out the topic icons and associated manifest.

    The topic icons are dropped in as 16:9 source images. Kake then resizes and
    crops these icons so as to make them available in multiple resolutions,
    etc. It also runs an optimization pass over them. In the future, it may
    _also_ convert them to alternative file formats.

    The list of uploaded sizes and file formats are made available through a
    topic icon manifest. This manifest is accessed both by webapp and by the
    mobile apps.

    The built icons are dependencies of the icon manifest, so building out the
    manifest triggers the building of the icons.
    """
    outfile = os.path.join('genfiles', 'topic-icons', 'icon-manifest.json')
    yield (outfile, {})


def handlebars_files(build_options, py=False, js=False):
    if py:
        # Search the filesystem for any handlebars templates we need to
        # compile to Python.  The rule is we compile all handlebars files
        # with an associated .json file (which is, or at least used to be,
        # used for testing).  .handlebars files without an associated json
        # file we assume are javascript-only.
        for (root, _, files) in os.walk("javascript"):
            for file in [f for f in files if f.endswith('.handlebars.json')]:
                for lang in build_options.langs:
                    file_py = file.replace('.handlebars.json', '.py')
                    outfile = os.path.join(
                        "genfiles",
                        "compiled_handlebars_py",
                        lang,
                        os.path.join(root, file_py))
                    yield (outfile, {})

    if js:
        all_files = _all_package_files("javascript", "en", build_options.dev)
        for (pkg_name, filename) in all_files:
            if not filename.endswith('.handlebars'):
                continue

            # (To support {{lang}}, we'd need a different
            # packages_and_files pair for each language.)
            assert '{{lang}}' not in filename, (
                'We do not yet support the {{lang}} tag for .handlebars')

            for lang in build_options.langs:
                outfile = os.path.join("genfiles", "compiled_handlebars_js",
                                       lang, filename + '.js')
                yield (outfile, {})


def i18n_files(build_options, handlebars=False, js=False,
               workers=False):
    """workers are js worker files, that potentially live outside packages."""
    i18n_langs = set(build_options.langs).difference(set(['en']))

    if not i18n_langs:
        log.warning('Not building anything for i18n; you did not specify'
                    ' any languages via the -l flag')

    # For handlebars and javascript, we use the javascript-packages.json
    # file to just translate the js/handlebars that are needed in prod.
    for lang in i18n_langs:
        for (pkg_name, f) in _all_package_files('javascript', lang,
                                                build_options.dev,
                                                precompiled=True):
            # Some files aren't translated, in which case we get back
            # the original file (no genfiles/xxx).  We can skip those.
            if not f.startswith('genfiles'):
                continue

            # We translate handlebars files before they're compiled,
            # but _all_package_files() gives the post-compile name, so
            # we need to convert
            #     genfiles/compiled_handlebars_js/<lang>/javascript/
            #       tasks-package/user-mission-progress-tooltip.handlebars.js
            # into
            #     genfiles/translations/<lang>/javascript/
            #       tasks-package/user-mission-progress-tooltip.handlebars
            if (f.startswith(os.path.join('genfiles, compiled_handlebars_js'))
                    and handlebars):
                outfile = f[:-len('.js')].replace('compiled_handlebars_js',
                                                  'translations')
                yield (outfile, {})
            elif js:
                yield (f, {})

    # In addition to the files listed in the package manifest,
    # there are a few translated files that we serve directly.
    # We get that list from app.yaml, and make sure those
    # translated files exist as well.
    if js or workers:
        files = list_files_uploaded_to_appengine.get_uploaded_files(True)

        # We'll only bother to build the translated worker files if
        # those files are allowed by skip-files.
        config = modules_util.module_yaml(
            'default', for_production=(not build_options.dev))
        should_skip_re = re.compile(config['skip_files'].regex)

        for f in files:
            if not f.endswith('.js'):
                continue
            # Do not count the package files; we're interested only
            # in js worker files here.
            if f.startswith(('genfiles/javascript/', 'genfiles/stylesheets/',
                             'genfiles/manifests/',
                             'genfiles/compressed_manifests_prod/',
                             'genfiles/compressed_manifests_dev/',
                             'genfiles/readable_manifests_prod/',
                             'genfiles/readable_manifests_dev/')):
                continue
            # If this is a generated file, make sure it's an English one.
            # We make use of the fact all the genfiles directories are
            # like genfiles/<something>/<lang>/...
            dirparts = f.split('/')
            if dirparts[0] == 'genfiles':
                if len(dirparts) < 3 or dirparts[2] != 'en':
                    continue

            for lang in i18n_langs:
                if f.startswith('genfiles') and '/en/' in f:
                    candidate = f.replace('/en/', '/%s/' % lang)
                else:
                    candidate = os.path.join('genfiles', 'translations', lang,
                                             f)
                if not should_skip_re.match(candidate):
                    yield (candidate, {})


def jsx_files(build_options):
    all_files = _all_package_files("javascript", "en", build_options.dev)
    for (pkg_name, filename) in all_files:
        if not filename.endswith('.jsx'):
            continue
        for lang in build_options.langs:
            outfile = os.path.join('genfiles', 'compiled_jsx', lang,
                                   filename + '.js')
            yield (outfile, {})


def less_files(build_options):
    all_files = _all_package_files("stylesheets", "en", build_options.dev)
    for (pkg_name, f) in all_files:
        if not f.endswith('.less'):
            continue
        for lang in build_options.langs:
            outfile = os.path.join('genfiles', 'compiled_less', lang,
                                   f + '.css')
            yield (outfile, {})


def npm_files(build_options):
    # We alays build all npm files when we build one, so just pick one.
    outfile = os.path.join('genfiles', 'node_modules', '.bin', 'handlebars')
    yield (outfile, {})


def jinja2_files(build_options):
    outfile = os.path.join('genfiles', 'compiled_jinja_templates.zip')
    yield (outfile, {})


def custom_search_engine(build_options):
    for lang in build_options.langs:
        outfile = os.path.join('genfiles', 'custom_search_engine', lang,
                               'context.xml')
        yield (outfile, {})

        outfile = os.path.join('genfiles', 'custom_search_engine', lang,
                               'annotations.xml')
        yield (outfile, {})


def all_pot_files(build_options):
    # This will create both the pickled all.pot file, which kake uses
    # itself, and a human readable all.pot.txt_for_debugging file.
    outfile = os.path.join('genfiles', 'translations',
                           'all.pot.pickle')
    yield (outfile, {})


def compiled_po_files(build_options):
    i18n_langs = set(build_options.langs).difference(set(['en']))
    for lang in i18n_langs:
        outfile = os.path.join('genfiles', 'translations', lang,
                               'index.pickle')
        yield (outfile, {})


def translated_graphie_label_files(build_options):
    index_files = [
        project_root.join('intl', 'translations', 'graphie_image_shas.json'),
        project_root.join('intl', 'translations',
                          'graphie_image_shas_in_articles.json'),
    ]

    for index_file in index_files:
        with open(index_file) as f:
            for sha in json.load(f).keys():
                for lang in build_options.langs:
                    if lang == 'en':
                        continue

                    yield (os.path.join('genfiles', 'labels', lang,
                                        '%s-data.json' % sha),
                           {})


def _perseus_exercises_with_graphie_labels():
    exercise_slugs = set()
    with open(project_root.join('intl', 'translations',
                                'graphie_image_shas.json')) as f:
        for data in json.load(f).values():
            exercise_slugs.add(data["exerciseSlug"])
    return exercise_slugs


def _particles_with_graphie_labels():
    article_slugs = set()
    with open(project_root.join('intl', 'translations',
                                'graphie_image_shas_in_articles.json')) as f:
        for data in json.load(f).values():
            article_slugs.add(data)
    return article_slugs


def template_strings(build_options):
    """Creates maps for all strings not in prod datastore."""
    yield (os.path.join('genfiles', 'combined_template_strings',
                        'combined_template_strings.json'), {})

    for exercise_slug in _perseus_exercises_with_graphie_labels():
        yield (os.path.join('genfiles', 'combined_template_strings',
                            'graphie_labels', '%s.json' % exercise_slug), {})

    for article_slug in _particles_with_graphie_labels():
        yield (os.path.join('genfiles', 'combined_template_strings',
                            'article_graphie_labels',
                            '%s.json' % article_slug), {})


# The key is a name the user can ask to build, and the value is a
# function that takes a BuildOptions object and yields a
# (outfile_filename, make_context) pair for every output-filenames
# that this key should make us build.  (make_context is a dict that
# is passed to make.build(), and is application-dependent: some build
# rules require particular values be set in the context.)  Note the
# function should pay attention to build_options to decide what to
# return, especially making sure to consider every language in langs.
_ARGUMENTS = {
    'compiled_po': compiled_po_files,
    'cse': custom_search_engine,
    'css': css_packages,
    'handlebars': lambda opts: handlebars_files(opts, py=True, js=True),
    'handlebars_js': lambda opts: handlebars_files(opts, js=True),
    'handlebars_py': lambda opts: handlebars_files(opts, py=True),
    'i18n': lambda o: i18n_files(o, handlebars=True, js=True),
    'i18n_graphie_labels': translated_graphie_label_files,
    'i18n_handlebars': lambda opts: i18n_files(opts, handlebars=True),
    'i18n_js': lambda opts: i18n_files(opts, js=True),
    'i18n_workers': lambda opts: i18n_files(opts, workers=True),
    'jinja': jinja2_files,        # an alias for jinja2
    'jinja2': jinja2_files,
    'js': js_packages,
    'js_and_css': js_and_css_packages,
    'js_file_to_package_mapping': js_file_to_package_mapping,
    'jsx': jsx_files,
    'less': less_files,
    'npm': npm_files,
    'perseus_mobile_js': perseus_mobile_js_files,
    'pot': all_pot_files,
    'template_strings': template_strings,
    'topic_icon_manifest': topic_icon_manifest
}

_BUILD_TARGETS_HELP = (
    "A file to build (e.g. "
    "genfiles/compiled_jsx/boxes/javascript/issues-package/issues.jsx.js), "
    "or a package to build (e.g. shared.js), "
    "or one of %s" % (", ".join(_ARGUMENTS.iterkeys())))


def get_build_args(build_targets, context, build_options):
    """Return a map from filename to filename's context."""
    build_args = {}   # a map from filename to filename's context
    for build_target in build_targets:
        if build_target in _ARGUMENTS:
            file_getter = _ARGUMENTS[build_target]
            build_args.update(dict(file_getter(build_options)))

        elif build_target.startswith('genfiles'):  # a literal file:
            build_args[build_target] = {}

        elif build_target.endswith('.js'):         # a javascript package
            build_args.update(dict(one_js_package(build_options,
                                                  build_target)))

        elif build_target.endswith('.css'):        # a css package
            build_args.update(dict(one_css_package(build_options,
                                                   build_target)))

        else:
            raise ValueError(
                'Unknown build target "%s", did you mean one of: %s ?'
                % (build_target, ' '.join(sorted(_ARGUMENTS.iterkeys()))))

    # We can save a bit of memory by stringifying all the filenames
    # (which might be unicode).
    build_args = {memory_util.save_bytes(k): v
                  for (k, v) in build_args.iteritems()}

    # Add the commandline-supplied context to all build targets.
    for (_, build_rule_context) in build_args.iteritems():
        build_rule_context.update(context)

    return build_args


def main(build_targets, langs, context={}, dev=False, readable=False,
         gae_version=None, num_processes=1, dry_run=False, force=False):
    build_options = BuildOptions(['en'] + list(langs), dev, readable,
                                 gae_version)

    build_args = get_build_args(build_targets, context, build_options)

    if dry_run:
        print '\n'.join(sorted('%s (%s)' % b for b in build_args.iteritems()))
        return

    kake.make.build_many(build_args.items(), num_processes, force,
                         checkpoint_interval=60)    # sync once a minute

    # It's safe to do this all the time, but only the rules that update
    # manifest files actually affect symlinks.  We do this here rather
    # than in the manifest rule itself, because we want *all* manifests
    # to be fully updated before going about deleting obsolete symlinks.
    if any('_manifests_' in f for f in build_args):
        kake.compile_js_css_manifest.remove_obsolete_symlinks(
            build_options.readable, build_options.dev)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('build_targets', metavar='BUILD-TARGET', nargs='*',
                        help=_BUILD_TARGETS_HELP)
    parser.add_argument('--build-targets-file',
                        help=("A file with one build target per line; "
                              "use in place of specifying BUILD-TARGETs "
                              "on the commandline"))
    parser.add_argument('--context', action='append', default=[],
                        help=("A string 'key=value', which is added to the"
                              " 'context' dict passed to all build rules."
                              " May be specified more than once."))
    parser.add_argument('--readable', action="store_true",
                        help=("Only concatenate package contents,"
                              " don't minify them."))
    parser.add_argument('--dev', action="store_true",
                        help=("Ignore the dev-to-prod mapping"
                              " (cf. js_css_packages.util.transformations)"))
    parser.add_argument('--gae-version',
                        help=("String used to construct the manifest-TOC "
                              "filename (only used for js_and_css rule)"))
    parser.add_argument('--language', '-l', action='append',
                        default=[],
                        help=('Additional languages to translate to'
                              ' (may be specified multiple times); or "all"'
                              ' to translate to all languages we have a'
                              ' .po and/or .mo file for, or "all-with-data"'
                              ' for all languages we have crowdin data for'))
    parser.add_argument('--jobs', '-j', type=int,
                        # We found using half the CPUs gives best performance
                        default=int(round(multiprocessing.cpu_count() / 2.0)),
                        help=('Use this many sub-processes when building'
                              ' (default %(default)s)'))
    parser.add_argument('--dry-run', '-n', action="store_true",
                        help=('Just list what files we would build;'
                              ' do not actually build them.'))
    parser.add_argument('--force', '-f', action='store_true',
                        help=('Rebuild all files even if they are up-to-date'))
    log.add_verbose_flag(parser)
    args = parser.parse_args()

    if args.dry_run and args.force:
        raise parser.error('Cannot specify both --dry-run and --force')

    build_targets = args.build_targets
    if args.build_targets_file:
        with open(args.build_targets_file) as f:
            build_targets.extend(f.read().splitlines())
    if not build_targets:
        raise parser.error('Must either specify BUILD-TARGETs or '
                           '--build-targets-file')

    langs = args.language

    # TODO(joshuan): Rename this to test-or-better
    if 'all' in langs:
        langs.remove('all')
        langs.extend(intl.locale.all_locales_for_packages())
        langs.remove('en')  # We don't have po files for English

    # TODO(joshuan): Rename this to getting-started-or-better
    if 'all-with-data' in langs:
        langs.remove('all-with-data')
        langs.extend(intl.data.all_ka_locales())

    context = dict(c.split('=') for c in args.context)

    main(build_targets, langs, context, args.dev, args.readable,
         args.gae_version, args.jobs, args.dry_run, args.force)
