"""Compute the hash of the latest Perseus bundle, for each major version.

These hashes are used to enforce cache busting behavior when delivering these
Perseus bundles to mobile clients.
"""

from __future__ import absolute_import

import md5

from kake.lib import compile_rule
from kake.lib import log


class CompilePerseusHash(compile_rule.CompileBase):
    def version(self):
        """Update every time build() changes in a way that affects output."""
        return 1

    def build(self, outfile_name, infile_names, _, context):
        assert len(infile_names) == 1, (
            "Each hash should be computed over a single version of Perseus")

        infile_name = infile_names[0]

        log.v3("Reading from Perseus build: %s" % infile_name)
        with open(self.abspath(infile_name)) as f:
            full_content = f.read()

        # We use just the first six characters of the hash. Per
        # compile_js_css_manifest.py: "Even if every deploy had a new md5,
        # this would give us a good 8 years between collisions."
        perseus_md5sum = md5.new(full_content).hexdigest()[:6]
        log.v3("Writing Perseus hash: %s" % perseus_md5sum)

        with open(self.abspath(outfile_name), 'w') as f:
            f.write(perseus_md5sum)

compile_rule.register_compile(
    'PERSEUS HASH',
    'genfiles/compiled_perseus_hash/perseus-{{version}}-hash.txt',
    ['javascript/perseus-package/perseus-{{version}}.js'],
    CompilePerseusHash())
