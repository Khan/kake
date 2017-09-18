# TODO(colin): fix these lint errors (http://pep8.readthedocs.io/en/release-1.7.x/intro.html#error-codes)
# pep8-disable:E713
"""A Compile object (see compile_rule.py) to build topic icons.

Topic icons are used to represent nodes in the topic tree. A single icon can be
re-used across topics, and icons are typically 'inherited'. You can see the
icons for all the children of the 'arithmetic' subject at:
    https://www.khanacademy.org/math/arithmetic

When we 'build' a topic icon, we take a source icon, shrink it down, possibly
crop it, compress it, and possibly modify its file format.

The icon pipeline is thus responsible for taking a set of source icons (PNGs
and JPGs) and an icon_data.json file (which maps topic slugs to the source
icons), building out all the icons that clients might need, hash-suffixing
them, and creating a final manifest which contains the original mapping (from
slugs to icons), a list of supported extensions and sizes, and the
hash-suffixes for the source icons. (The hash suffixes are necessary as the
mobile apps ship with small versions of the icons and request larger versions
to display in certain contexts. If the icons were not hash suffixed, the
requested icons may not align with the versions that shipped with the app.)

The pipeline is split into several jobs to optimize for incremental compilation
time:

1. In the first step, the source icons are resized and compressed (in the
   future, they may also be converted to alternative file formats).
2. Next, an MD5 hash is computed for each source icons.
3. Next, using the MD5 hashes, each built icon is symlinked to a hash-suffixed
   location. (The process used here is similar in nature to that of the
   compile_js_css_manifest rule.)
4. Finally, the MD5 hashes and the mapping from topic slugs to icons are
   collated into a single manifest, which also lists the available sizes and
   file formats for each icon.

Some of these jobs are admittedly not natural fits for Kake -- namely, the
hash-suffixing step. However, each of those steps outputs a metadata object
that is used by the next job in the pipeline, and the advantage of having all
of these dependencies made explicit and the jobs colocated outweighs the costs
of distributing the responsibilities between various utilities.

For reference, the final output manifest looks something like:

{
    'sizes': ['128c', '416', '800', ...],
    'formats': {
        // A mapping from source file extension (e.g., from 'algebra.png') to
        // uploaded file extensions. In the future, we may support uploading
        // files in multiple formats, like WebP or SVG.
        'png': ['png'],
        ...
    },
    'inherited_icons': { 'algebra': 'algebra.png', ... },
    'non_inherited_icons': { 'grade_1': 'grade_1.png', ... },
    'md5sums': {
        'algebra.png': 'abc123',
        ...
    },
    'base_url': 'https://fastly.kastatic.org/genfiles/topic-icons/icons/',
    'webapp_commit': 'a4fe8ce585e1ddfdf179a77641fa6a2709eff22b',
}

Clients should reconstruct the URLs as follows:
    {base_url}/{source_icon_name}-{source_icon_suffix}-{desired_size}.{format}

As an example, to request the 'algebra.png' icon as a 416px-wide PNG:
    https://fastly.kastatic.org/genfiles/topic-icons/icons/algebra.png-abc123-416.png
"""

from __future__ import absolute_import

import json
import os

from kake.lib import compile_rule
from kake.lib import computed_inputs
from kake.lib import log
from kake.lib import symlink_util


class CompileTopicIcons(compile_rule.CompileBase):

    def version(self):
        """Update every time build() changes in a way that affects output."""
        return 2

    def build(self, outfile_name, infile_names, _, context):
        # TODO(charlie): Add a Kake rule to install all the binaries that
        # pngcrush uses.
        from deploy import pngcrush
        import topic_icons.icon_util

        assert len(infile_names) == 1, (
            "Each built icon should be created from a single source icon")

        icon_file_name = infile_names[0]
        icon_path = self.abspath(icon_file_name)

        # Determine the appropriate sizing parameters for the icon.
        size_config_str = context['{{size}}']
        (width, center_crop) = topic_icons.icon_util.deserialize_size_config(
            size_config_str)

        # Resize by writing to a staging file, which we'll then compress
        # in-place. We could save to the final destination and then compress,
        # but if the compression errored, Kake would get confused (since the
        # proper outfile would've been built already).
        tmpfile_name = outfile_name + '.tmp'
        tmpfile_path = self.abspath(tmpfile_name)

        # Resize and compress.
        topic_icons.icon_util.resize_icon(
            icon_path, tmpfile_path, width, center_crop)
        pngcrush.main([tmpfile_path])

        # Rename the staging file to match the outfile name.
        outfile_path = self.abspath(outfile_name)
        os.rename(tmpfile_path, outfile_path)


class ComputeMd5ManifestInput(computed_inputs.ComputedInputsBase):
    """Compute the dependencies for the MD5 manifest.

    In particular, we compute an MD5 for each icon listed in the initial icon
    data manifest. Though each source icon will be transformed to produce
    multiple built icons, we use the MD5 of the source icon when hash-suffixing
    the built icons, simplicity.
    """

    def version(self):
        """Update if input_patterns() changes in a way that affects output."""
        return 1

    def input_patterns(self, outfile_name, context, triggers, changed):
        import topic_icons.icon_util

        manifest_infile_name = triggers[0]
        assert os.path.basename(manifest_infile_name) == 'icon_data.json', (
            "Topic-to-icon mapping should be provided as first input")

        # Determine the set of source icons that are actually needed by the
        # manifest.
        icon_data_path = self.abspath(manifest_infile_name)
        source_icons = topic_icons.icon_util.list_source_icons(icon_data_path)
        return [os.path.join('topic_icons', 'icons', n) for n in source_icons]


class TopicIconMd5Manifest(compile_rule.CompileBase):

    def version(self):
        """Update every time build() changes in a way that affects output."""
        return 1

    def build(self, outfile_name, infile_names, _, context):
        import topic_icons.icon_util

        md5_manifest = {}
        for infile_name in infile_names:
            src_icon_name = os.path.basename(infile_name)
            md5_manifest[src_icon_name] = topic_icons.icon_util.compute_md5(
                self.abspath(infile_name))

        with open(self.abspath(outfile_name), 'w') as f:
            json.dump(md5_manifest, f, sort_keys=True)


class ComputeSymlinkMapInput(computed_inputs.ComputedInputsBase):
    """Compute the dependencies for the symlink map.

    In particular, for each source icon that has been registered with an MD5
    sum, we compute all of the built icons for that source icon (by iterating
    over the list of supported file formats and sizes) and label them as
    inputs.
    """

    def version(self):
        """Update if input_patterns() changes in a way that affects output."""
        return 1

    def input_patterns(self, outfile_name, context, triggers, changed):
        import topic_icons.icon_util

        manifest_infile_name = triggers[0]
        assert os.path.basename(manifest_infile_name) == 'md5-manifest.json', (
            "Icon-to-MD5 mapping should be provided as first input")

        with open(self.abspath(manifest_infile_name), 'r') as f:
            md5_manifest = json.load(f)

        outfiles = []
        size_configs = topic_icons.icon_util.SUPPORTED_SIZE_CONFIGS
        for icon_name in md5_manifest:
            formats = topic_icons.icon_util.output_formats_for_icon(icon_name)
            for format in formats:
                for size_config in size_configs:
                    size_config_str = (
                        topic_icons.icon_util.serialize_size_config(
                            size_config))
                    outfile = os.path.join('genfiles', 'topic-icons',
                                           'icons-src', '%s.%s.%s' % (
                                            icon_name, size_config_str,
                                            format))
                    outfiles.append(outfile)

        return [manifest_infile_name] + outfiles


class TopicIconSymlinks(compile_rule.CompileBase):

    def version(self):
        """Update every time build() changes in a way that affects output."""
        return 1

    def build(self, outfile_name, infile_names, _, context):
        import topic_icons.icon_util

        with open(self.abspath(infile_names[0]), 'r') as f:
            md5_manifest = json.load(f)

        symlink_map = {}
        symlink_from_dir = os.path.join('genfiles', 'topic-icons', 'icons')
        for infile_name in infile_names[1:]:
            # The infile names are, e.g., 'unit_circle.png.416.png'.
            infile_basename = os.path.basename(infile_name)

            # Gives us, e.g., 'unit_circle.png.416' and '.png', the source
            # icon name with the size configuration still attached, and the
            # final icon format.
            (src_icon_name_and_size, icon_format) = os.path.splitext(
                infile_basename)

            # Strip the '.' from the icon format.
            icon_format = icon_format[1:]

            # Gives us, e.g., 'unit_circle.png' and '.416', the source icon
            # name and the size configuration.
            (src_icon_name, size) = os.path.splitext(src_icon_name_and_size)

            # Strip the '.' from the size configuration.
            size = size[1:]

            # Given 'unit_circle.png.416.png', we want the symlink to be called
            # 'icons/unit_circle.png-123abc-416.png', so that the format is:
            # '{src-icon}-{hash-suffix}-{size}.{format}'.
            symlink_to = self.abspath(infile_name)

            md5sum = md5_manifest[src_icon_name]

            file_name = topic_icons.icon_util.name_for_built_icon(
                src_icon_name, md5sum, size, icon_format)
            symlink_from = self.abspath(os.path.join(
                symlink_from_dir, file_name))

            symlink_util.symlink(symlink_to, symlink_from)

            symlink_map[symlink_to] = symlink_from

        # We can also clean-up any unused symlinks in the directory.
        symlink_from_dir_abspath = self.abspath(symlink_from_dir)
        for f in os.listdir(symlink_from_dir_abspath):
            abspath = os.path.join(symlink_from_dir_abspath, f)
            if os.path.islink(abspath) and not abspath in symlink_map.values():
                log.v1('   ... removing obsolete symlink %s', abspath)
                os.unlink(abspath)

        with open(self.abspath(outfile_name), 'w') as f:
            json.dump(symlink_map, f, sort_keys=True)


class CompileTopicIconManifest(compile_rule.CompileBase):

    def version(self):
        """Update every time build() changes in a way that affects output.

        If the format of this output manifest changes, be sure to update the
        fallback manifest that we ship locally on App Engine (see:
        topic_icons/fallback-icon-manifest.json), in addition to updating any
        clients of the manifest.
        """
        return 3

    def build(self, outfile_name, infile_names, _, context):
        from deploy import git_util
        import topic_icons.icon_util
        import url_util

        icon_data_infile_name = infile_names[0]
        md5_manifest_infile_name = infile_names[1]
        symlink_map_infile_name = infile_names[2]

        # Assert that the first three inputs match expectations.
        icon_data_infile_basename = os.path.basename(icon_data_infile_name)
        md5_manifest_infile_basename = os.path.basename(
            md5_manifest_infile_name)
        symlink_map_infile_basename = os.path.basename(
            symlink_map_infile_name)
        assert icon_data_infile_basename == 'icon_data.json', (
            "Topic-to-icon mapping should be provided as first input")
        assert md5_manifest_infile_basename == 'md5-manifest.json', (
            "Icon-to-MD5 mapping should be provided as second input")
        assert symlink_map_infile_basename == 'symlink-map.json', (
            "Symlinks must be created prior to creating the final manifest")

        # Read the input files. There's no need to read the input symlink map;
        # we depend on it to guarantee that the symlinks have been created.
        with open(self.abspath(icon_data_infile_name), 'r') as f:
            icon_data = json.load(f)
        with open(self.abspath(md5_manifest_infile_name), 'r') as f:
            md5_manifest = json.load(f)

        output_manifest = {}
        for field in ['inherited_icons', 'non_inherited_icons']:
            output_manifest[field] = icon_data[field]
        output_manifest['md5sums'] = md5_manifest
        output_manifest['sizes'] = [
            topic_icons.icon_util.serialize_size_config(s)
            for s in topic_icons.icon_util.SUPPORTED_SIZE_CONFIGS]
        output_manifest['formats'] = topic_icons.icon_util.SUPPORTED_FORMATS

        # TODO(charlie): We always serve the production URL here, and so we
        # always point dev_appserver to the production icons. The intention is
        # to avoid requiring that all devs are generating these topic icons
        # locally, since the process is lengthy and will get lengthier as we
        # add new dimensions, shapes, etc. That said, we should come up with a
        # system that will allow for use of a local manifest and local icons,
        # for testing. We could, for example, skip the optimization pass and
        # only generate the icons at the densities necessary to be used in
        # webapp.
        output_manifest['base_url'] = url_util.gcs_url(
            '/genfiles/topic-icons/icons/')

        # Add in the Git hash, so we can trace back the commit from which the
        # manifest was generated.
        output_manifest['webapp_commit'] = git_util.Git().current_version()

        # TODO(charlie): Validate that all the icons exist before announcing
        # that we're complete.
        with open(self.abspath(outfile_name), 'w') as f:
            json.dump(output_manifest, f, sort_keys=True)

# When compiling the topic icons, take care to preserve the entirety of the
# source icon's name, including its extension. Otherwise, we risk collisions.
# For example, if we had separate icons named 'foo.png' and 'foo.jpg', and we
# were producing WebP versions of both, then ignoring extensions, we'd collide
# when building 'foo.webp'. (Note that we don't currently build WebP versions
# of our icons, but we might in the future.)
# Note that the {{size}} parameter should be a size configuration string -- an
# integral width followed by an optional 'c' to enforce square-cropping.
compile_rule.register_compile(
    'TOPIC ICONS (PNG->PNG)',
    'genfiles/topic-icons/icons-src/{{path}}.png.{{size}}.png',
    ['topic_icons/icons/{{path}}.png'],
    CompileTopicIcons(),
    compute_crc=True)         # compressing is slow, so be extra careful

compile_rule.register_compile(
    'TOPIC ICONS (JPG->JPEG)',
    'genfiles/topic-icons/icons-src/{{path}}.jpg.{{size}}.jpeg',
    ['topic_icons/icons/{{path}}.jpg'],
    CompileTopicIcons(),
    compute_crc=True)

compile_rule.register_compile(
    'TOPIC ICONS (JPEG->JPEG)',
    'genfiles/topic-icons/icons-src/{{path}}.jpeg.{{size}}.jpeg',
    ['topic_icons/icons/{{path}}.jpeg'],
    CompileTopicIcons(),
    compute_crc=True)

compile_rule.register_compile(
    'MD5 MANIFEST',
    'genfiles/topic-icons/md5-manifest.json',
    ComputeMd5ManifestInput(['topic_icons/icon_data.json']),
    TopicIconMd5Manifest())

compile_rule.register_compile(
    'TOPIC ICON SYMLINKS',
    'genfiles/topic-icons/symlink-map.json',
    ComputeSymlinkMapInput(['genfiles/topic-icons/md5-manifest.json']),
    TopicIconSymlinks())

compile_rule.register_compile(
    'TOPIC ICON MANIFEST',
    'genfiles/topic-icons/icon-manifest.json',
    ['topic_icons/icon_data.json',
     'genfiles/topic-icons/md5-manifest.json',
     'genfiles/topic-icons/symlink-map.json'],
    CompileTopicIconManifest())
