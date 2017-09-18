"""A Compile object (see compile_rule.py): foo.js -> foo.min.js."""

from __future__ import absolute_import

import os

from kake.lib import compile_rule
import shutil


_UGLIFY_ARGS = [
    '--mangle',
    '-r', '$,$_,i18n',

    # This pair of options makes uglifyjs use newlines instead of
    # semicolons, to aid with debugging (same code size)
    '--beautify', 'beautify=0,semicolons=0',

    # TODO(csilvers): Add source map support (--source-map)
]

# Stores a set of base name for files which should not be compressed.
# This is a bit hacky, if we ever need to add a generic name like 'compiled.js'
# to the skip_compression set, but we should rarely need this functionality,
# and when needed we can just use a more unique name.
# TODO(bbondy): It would be nice to add a way to specify that you should skip
# compression in source code itself, rather than hard-coding it in kake.
skip_compression = frozenset([
    # live-editor.output_sql_deps.js contains a huge sqlite asm.js
    # file which when compressed corrupts the package.
    "live-editor.output_sql_deps.js",
])


class CompressJavascript(compile_rule.CompileBase):
    def version(self):
        """Update every time build() changes in a way that affects output."""
        return 3

    def build(self, outfile_name, infile_names, _, context):
        assert len(infile_names) == 2, infile_names   # infile and uglifyjs

        if os.path.basename(infile_names[0]) in skip_compression:
            shutil.copyfile(infile_names[0], outfile_name)
            return

        with open(self.abspath(infile_names[0])) as inf:
            with open(self.abspath(outfile_name), 'w') as outf:
                # TODO(csilvers): add --no-copyright
                self.call([self.abspath(infile_names[1])] + _UGLIFY_ARGS,
                          stdin=inf, stdout=outf)


# When changing this path, it's necessary to change the path of the compressed,
# compiled JS used in api/internal/mobile_static.py correspondingly
compile_rule.register_compile(
    'COMPRESSED JS',
    'genfiles/compressed_javascript/en/{{path}}.min.js',
    ['{{path}}.js',
     'genfiles/node_modules/.bin/uglifyjs'],
    CompressJavascript())


# In terms of translated content, we only need to compress translated
# handlebars file.  js/jsx/etc are translated *after*
# compression (in kake/translate_javascript.py).
compile_rule.register_compile(
    'COMPRESSED TRANSLATED HANDLEBARS JS',
    'genfiles/compressed_javascript/{lang}/'
    'genfiles/compiled_handlebars_js/{lang}/{{path}}.min.js',
    ['genfiles/compiled_handlebars_js/{lang}/{{path}}.js',
     'genfiles/node_modules/.bin/uglifyjs'],
    CompressJavascript(),
    maybe_symlink_to=('genfiles/compressed_javascript/en/'
                      'genfiles/compiled_handlebars_js/en/{{path}}.min.js'))
