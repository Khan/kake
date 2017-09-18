# TODO(colin): fix these lint errors (http://pep8.readthedocs.io/en/release-1.7.x/intro.html#error-codes)
# pep8-disable:E128
"""A Compile object (see compile_rule.py): auto-adds vendor prefixes to CSS."""

from __future__ import absolute_import

from kake.lib import compile_rule


class CompileAutoprefixedCss(compile_rule.CompileBase):
    def version(self):
        """Update every time build() changes in a way that affects output."""
        return 2

    def build(self, outfile_name, infile_names, _, context):
        # infile and autoprefixer binary
        assert len(infile_names) == 2, infile_names
        # TODO(nick): autoprefixer params should be passed in dynamically
        browser_option = "--browsers"
        browser_option_value = "IE >= 10, IOS >= 8"

        self.call(
            # infile_names[1] is the autoprefixer binary
            # Settings for autoprefixer:
            # https://github.com/postcss/autoprefixer#options
            [infile_names[1], '-o', outfile_name, browser_option,
            browser_option_value, '--map', infile_names[0]])


compile_rule.register_compile(
    'AUTOPREFIXED CSS',
    'genfiles/compiled_autoprefixed_css/en/{{path}}.css',
    ['{{path}}.css',
     'genfiles/node_modules/.bin/autoprefixer'],
    CompileAutoprefixedCss())

compile_rule.register_compile(
    'AUTOPREFIXED LESS.CSS',
    'genfiles/compiled_autoprefixed_css/en/{{path}}.less.css',
    ['genfiles/compiled_less/en/{{path}}.less.css',
     'genfiles/node_modules/.bin/autoprefixer'],
    CompileAutoprefixedCss())
