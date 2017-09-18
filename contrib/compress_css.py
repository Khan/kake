"""A Compile object (see compile_rule.py): foo.css -> foo.min.css.

We pass the CSS thorugh cssmin to minify it.  However, we also inline
images at compress-time (it doesn't technically 'compress' this file,
but it does compress the overall number of network round-trips the
user needs, so we'll count it).  We inline 'url(...)' CSS rules, but
only if the files are small enough, and only occur once (otherwise,
it's better to just let the user cache them); or alternately, if the
user manually indicates a desire for inlining via a text annotation:
    /*! data-uri... */
"""

from __future__ import absolute_import

import base64
import os
import re

from shared import ka_root

import intl.data
import js_css_packages.packages
import js_css_packages.util
from kake.lib import compile_rule
from kake.lib import compile_util
from kake.lib import computed_inputs
from kake.lib import log


_IMAGE_EXTENSION = r'(?:png|gif|jpg|jpeg)'

# We capture only host-absolute urls (start with /, but no hostname),
# since we know what files those resolve to, and we know they're
# referring to KA content.
# This matches url(/image/foo.png), url('/image/foo.png?k'), etc.
# It also matches /*! data-uri */ right after this url().
# group(1): the url-path of the image
# group(2): the data-uri comment, if it exists (None otherwise)
_CSS_IMAGE_RE = re.compile(
    r"\burl\(['\"]?(/[^)]*\.%s(?:\?[^'\")]*)?)"
    r"(?:['\");} ]*?(\s*/\*! data-uri.*?\*/))?"
    % _IMAGE_EXTENSION, re.I)

# This isn't used right now, so this is mostly for documentation purposes.
# This matches src="/img/foo.png", src='{{"/img/foo.png?k"|static_url}}', etc.
_HTML_IMAGE_RE = re.compile(
    r"""\bsrc\s*=\s*['""]?(/[^'"" >?]*\.%s(\?[^'"" >]*)?)"""
    r"""|\bsrc\s*=\s*['""]?{{"(/[^""?]*\.%s(\?[^""]*)?)"|static_url}}"""
    % (_IMAGE_EXTENSION, _IMAGE_EXTENSION), re.I)

# Always inline files <= this size, but not bigger files.  This should
# be well smaller than 32Kb, since IE8 only supports data-URIs shorter
# than 32K (after base64-encoding):
#   http://caniuse.com/datauri
_MAX_INLINE_SIZE = 4 * 1024

# For files that occur twice, we'll *still* inline them if they're
# small enough.  For instance, about the same size as just the url
# reference would be.
_MAX_INLINE_SIZE_IF_TWICE = 128

# This cache stores the information for images referenced in css files.
# The key is an image url, and the value is
#    (list of files .css files with this image url, image relpath, img size)
# A .css file can be in the list multiple times if it includes the image
# multiple times.
# Whenever we notice a .css file has changed, we should update this cache.
_IMAGE_URL_INFO = compile_util.CachedFile(
    os.path.join('genfiles', 'css_image_url_info.pickle'))


def _image_urls_and_file_info(content):
    """Given an image-url string, return an iterator with image info."""
    matches = _CSS_IMAGE_RE.finditer(content)
    for m in matches:
        relative_filename = m.group(1)[1:]     # relative to ka-root
        # Sometimes urls have ?'s (url queries) in them to bust
        # caches.  Those are not part of the filename. :-)
        relative_filename = relative_filename.split('?')[0]
        pathname = ka_root.join(relative_filename)
        try:
            filesize = os.stat(pathname).st_size
            yield (m.group(1), relative_filename, filesize)
        except OSError:   # file not found
            log.warning('reference to non-existent image %s', pathname)


def _update_image_url_info(css_filename, image_url_info):
    """Given css_filenames relative to ka-root, update _IMAGE_URL_INFO.

    Returns:
        A list of image filenames, relative to ka-root, mentioned in
        this css-filename.
    """
    # First, we need to delete all old references to css_filenames.
    for file_info in image_url_info.itervalues():
        new_files = [f for f in file_info[0] if f != css_filename]
        if len(new_files) < len(file_info[0]):
            # We go through this contortion so we can edit the list in place.
            del file_info[0][:]
            file_info[0].extend(new_files)

    # If the file no longer exists (has been deleted), we're done!
    if not os.path.exists(ka_root.join(css_filename)):
        log.v3("removing image-url info for %s: it's been deleted",
               css_filename)
        return

    # Then, we need to add updated references, based on the current
    # file contents.
    log.v2('Parsing image-urls from %s', css_filename)
    with open(ka_root.join(css_filename)) as f:
        content = f.read()

    retval = []
    for (img_url, img_relpath, img_size) in (
            _image_urls_and_file_info(content)):
        image_url_info.setdefault(img_url, ([], img_relpath, img_size))
        image_url_info[img_url][0].append(css_filename)
        retval.append(img_relpath)

    log.v4('Image-url info: %s', retval)
    return retval


def _data_uri_for_file(filename, file_contents):
    ext = os.path.splitext(filename)[1][1:].lower()
    if ext == 'jpg':
        ext = 'jpeg'

    return 'data:image/%s;base64,%s' % (ext, base64.b64encode(file_contents))


def _maybe_inline_images(compressed_content):
    """For small images, it's more efficient to inline them in the html.

    Most modern browsers support inlining image contents in html:
       css: background-image: url(data:image/png;base64,...)
       html: <img src='data:image/png;base64,...'>
    The advantage of doing this is to avoid an http request.  The
    disadvantages are that the image can't be cached separately from
    the webpage (bad if the web page changes often and the image
    changes never), and the total size is bigger due to the need to
    base64-encode.

    In general, it makes sense to use data uris for small images, for
    some value of 'small', or for (possibly large) images that a) are
    only used on one web page, b) are on html pages that do not change
    very much, and c) are on pages where rendering speed matters (just
    because it's not worth the effort otherwise).

    We also support a manual decision to inline via a text annotation:
    /*! data-uri... */.

    Arguments:
        compressed_content: The content to inline the image-urls in.

    Returns:
        Returns the input content, but with zero, some, or all images
        inlined.
    """
    output = []
    lastpos = 0
    for m in _CSS_IMAGE_RE.finditer(compressed_content):
        image_url = m.group(1)
        always_inline = m.group(2)

        # Find how often the image appears in our packages.  If it
        # only appears once, inlining it is a no-brainer (if it's
        # 'small', anyway).  If it appears twice, we probably don't
        # want to inline -- it's better to use the browser cache.
        # If it appears more than twice, we definitely don't inline.
        try:
            (callers, img_relpath, img_size) = _IMAGE_URL_INFO.get()[image_url]
        except KeyError:
            log.v4('Not inlining image-content of %s: file not found on disk',
                   image_url)
            continue
        url_count = len(callers)
        if (always_inline or
                (url_count == 1 and img_size <= _MAX_INLINE_SIZE) or
                (url_count == 2 and img_size <= _MAX_INLINE_SIZE_IF_TWICE)):
            log.v1('Inlining image-content of %s', img_relpath)
            with open(ka_root.join(img_relpath)) as f:
                image_content = f.read()
            output.append(compressed_content[lastpos:m.start(1)])
            output.append(_data_uri_for_file(img_relpath, image_content))
            lastpos = m.end(1)
            if always_inline:   # let's nix the !data-uri comment in the output
                output.append(compressed_content[lastpos:m.start(2)])
                lastpos = m.end(2)
        else:
            log.v4('Not inlining image-content of %s '
                   '(url-count %s, img size %s)',
                   img_relpath, url_count, img_size)

    # Get the last chunk, and then we're done!
    output.append(compressed_content[lastpos:])
    return ''.join(output)


class CalculateCssImageInfo(compile_rule.CompileBase):
    def version(self):
        """Update every time build() changes in a way that affects output."""
        return 2

    def build(self, outfile_name, infile_names, changed_infiles, context):
        image_url_info = {}

        # The rule: if outfile-name has changed, we need to rebuild everything.
        if outfile_name in changed_infiles:
            changed_infiles = infile_names

        # Start by modifying the existing data, except when we know we
        # need to recompute *everything* (why bother then)?
        if changed_infiles != infile_names:
            try:
                image_url_info = _IMAGE_URL_INFO.get()   # start with old info
            except Exception:      # we are just best-effort to read old info
                changed_infiles = infile_names

        for infile_name in changed_infiles:
            _update_image_url_info(infile_name, image_url_info)

        # Store the image_url_info both in cache and on disk.
        _IMAGE_URL_INFO.put(image_url_info)


class ComputedCssImageInfoInputs(computed_inputs.ComputedInputsBase):
    def version(self):
        """Update if input_patterns() changes in a way that affects output."""
        return 2

    def input_patterns(self, outfile_name, context, triggers, changed):
        # We depend on every .css file listed in the stylesheet.
        retval = set()

        # If the manifest file itself has changed, make sure we read
        # the latest version in the get_by_name() calls below.
        assert self.triggers[0].endswith('.json')  # the manifest
        packages = js_css_packages.packages.read_package_manifest(
            self.triggers[0])
        for (_, f) in js_css_packages.util.all_files(
                packages, precompiled=True, dev=False):
            if f.endswith('.css'):
                retval.add(f)

        return list(retval)


class CompressCss(compile_rule.CompileBase):
    def version(self):
        """Update every time build() changes in a way that affects output."""
        return 1

    def build(self, outfile_name, infile_names, _, context):
        assert len(infile_names) >= 2, infile_names   # infile, cssmin, images
        with open(self.abspath(infile_names[0])) as f:
            minified_css = self.call_with_output(
                [self.abspath(infile_names[1])], stdin=f)

        minified_and_inlined_css = _maybe_inline_images(minified_css)

        with open(self.abspath(outfile_name), 'w') as f:
            f.write(minified_and_inlined_css)


class ComputedCssInputs(computed_inputs.ComputedInputsBase):
    def __init__(self, triggers, infile_pattern):
        super(ComputedCssInputs, self).__init__(triggers)
        # The pattern (including `{{path}}` and `{lang}`) that
        # indicates what the css input file should be for this rule.
        self.infile_pattern = infile_pattern

    def version(self):
        """Update if input_patterns() changes in a way that affects output."""
        return 3

    def input_patterns(self, outfile_name, context, triggers, changed):
        (infile_name,) = compile_util.resolve_patterns([self.infile_pattern],
                                                       context)
        # And we also need the module that minifies .css, and the
        # rule that makes sure _IMAGE_URL_INFO is up to date.
        retval = [infile_name,
                  'genfiles/node_modules/.bin/cssmin',
                  _IMAGE_URL_INFO.filename()]

        # Finally, we also depend on each image in our .css file,
        # since we (possibly) inline those images, so if they change,
        # we need to know so we can re-inline them.
        image_deps = []
        for (image_url, (css_files, image_relpath, _)) in (
                _IMAGE_URL_INFO.get().iteritems()):
            if infile_name in css_files:
                image_deps.append(image_relpath)
        # We only sort to make diffs easier.
        image_deps.sort()
        retval.extend(image_deps)

        return retval


# This holds the data that's read into _IMAGE_URL_INFO.
compile_rule.register_compile(
    'CSS IMAGE INFO',
    _IMAGE_URL_INFO.filename(),
    ComputedCssImageInfoInputs(['stylesheets-packages.json']),
    CalculateCssImageInfo())

# This also captures css that has been compiled, and lives in
# genfiles/compiled_less or wherever:
#
# genfiles/compiled_less/en/a/b.less.css ->
# genfiles/compressed_stylesheets/en/genfiles/compiled_less/en/a/b.less.min.css
#
compile_rule.register_compile(
    'COMPRESSED CSS',
    'genfiles/compressed_stylesheets/en/{{path}}.min.css',
    # We depend on our input .css file, but we also depend on images
    # that our input .css file has inlined, since if those images
    # change we'll need to re-inline them.  The information about what
    # images our input .css currently file has inlined is stored in
    # _IMAGE_URL_INFO, so whenever that changes we need to recalculate
    # our inputs, in case what-we've-inlined has changed.
    ComputedCssInputs(
        [_IMAGE_URL_INFO.filename()],
        infile_pattern='genfiles/compiled_autoprefixed_css/en/{{path}}.css'),
    CompressCss())

# This gets translations.
compile_rule.register_compile(
    'TRANSLATED COMPRESSED CSS',
    'genfiles/compressed_stylesheets/{lang}/{{path}}.min.css',
    ComputedCssInputs(
        [_IMAGE_URL_INFO.filename()],
        infile_pattern=(
            'genfiles/compiled_autoprefixed_css/{lang}/{{path}}.css')),
    CompressCss(),
    maybe_symlink_to='genfiles/compressed_stylesheets/en/{{path}}.min.css')

# Special-case rule for translated CSS for RTL languages (he, ar, ur)
# so they can all symlink to one RTL file
symlink_lang = intl.data.right_to_left_languages()[0]
for lang in intl.data.right_to_left_languages():
    compile_rule.register_compile(
        'TRANSLATED COMPRESSED CSS (%s)' % lang,
        'genfiles/compressed_stylesheets/%s/{{path}}.min.css' % lang,
        ComputedCssInputs(
            [_IMAGE_URL_INFO.filename()],
            infile_pattern=(
                'genfiles/compiled_autoprefixed_css/%s/{{path}}.css' % lang)),
        CompressCss(),
        maybe_symlink_to='genfiles/compressed_stylesheets/%s/{{path}}.min.css'
                         % symlink_lang)
