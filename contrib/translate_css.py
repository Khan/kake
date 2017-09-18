"""A Compile object (see compile_rule.py):
   foo.css -> /genfiles/translations/LANG/foo.css.

   Translates CSS files into various languages.
   Currently this is only used for right-to-left languages (Hebrew, Arabic,
   Urdu) and all it does is mirror the CSS using Janus.

    TODO(tom): Fix these Janus bugs:
      - drop-shadow styles are not being mirrored
      - text-shadow is mirrored incorrectly; this:
            text-shadow: #000000 0 0 1em
        becomes this:
            text-shadow: #000000 1em 0 0
"""

from __future__ import absolute_import

import intl.data
from kake import translate_util
from kake.lib import compile_rule
from kake.lib import symlink_util


def mirror_css(original_css):
    """Mirror the CSS for right-to-left languages.

    Takes and returns a list of source lines.
    """
    # Import here so kake users who don't actually need the
    # translate_css rule can still import this module without needing
    # to install cssjanus first.
    from third_party.cssjanus import cssjanus

    # Disable the BackgroundPositionError. We can re-enable this locally to
    # find and fix the un-mirrorable background positions that Janus is
    # complaining about but for now we don't want janus throwing an exception
    # and breaking our build
    cssjanus.setflags([("--ignore_bad_bgp", 1)])
    return cssjanus.ChangeLeftToRightToLeft(original_css)


class TranslateAndMirrorCss(compile_rule.CompileBase):
    """For languages written right-to-left, translate & mirror the CSS."""
    def version(self):
        """Update every time build() changes in a way that affects output."""
        return 2

    def build(self, outfile_name, infile_names, _, context):
        assert len(infile_names) == 1, infile_names   # infile
        with open(self.abspath(infile_names[0])) as f:
            original_css = f.readlines()

        mirrored_css = mirror_css(original_css)

        with open(self.abspath(outfile_name), 'w') as f:
            f.write("".join(mirrored_css))


# Most languages don't need any CSS translation, so we just 'copy' the
# input to the output.  (The languages that do need translation are
# special-cased below.)
# CSS files are translated after autoprefixing (which happens after LESS
# compilation, when appropriate)
translate_util.register_translatesafe_compile(
    'TRANSLATED AUTOPREFIXED CSS',
    'genfiles/compiled_autoprefixed_css/{lang}/{{path}}.css',
    ['genfiles/compiled_autoprefixed_css/en/{{path}}.css'],
    symlink_util.CreateSymlink())

# Generate rules for right-to-left languages
# These differ in that they:
#   1) mirror the CSS,
#   2) symlink to each other rather than 'en'
symlink_lang = intl.data.right_to_left_languages()[0]
for lang in intl.data.right_to_left_languages():
    translate_util.register_translatesafe_compile(
        'TRANSLATED AUTOPREFIXED CSS (%s)' % lang,
        'genfiles/compiled_autoprefixed_css/%s/{{path}}.css' % lang,
        ['genfiles/compiled_autoprefixed_css/en/{{path}}.css'],
        TranslateAndMirrorCss(),
        maybe_symlink_to=('genfiles/compiled_autoprefixed_css/%s/{{path}}.css'
                          % symlink_lang))
