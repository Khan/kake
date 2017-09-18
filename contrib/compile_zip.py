"""A generic compile rule (see compile_util.py) for creating a zipfile."""

from __future__ import absolute_import

import os
import zipfile

from kake.lib import compile_rule


class CompileZip(compile_rule.CompileBase):
    """Compile all the input files into a zipfile."""
    def __init__(self, *args, **kwargs):
        """Can specify file_mapper as a kwarg.

        If specified, file_mapper should be a function taking an input
        filename (relative to ka-root) and emitting what the file should
        be called inside the zipfile.
        """
        self.file_mapper = kwargs.pop('file_mapper', lambda f: f)
        super(CompileZip, self).__init__(*args, **kwargs)

    def version(self):
        """Update every time build() changes in a way that affects output."""
        return 1

    def build(self, outfile_name, infile_names, _, context):
        # We could look at changed files and update the zipfile in
        # place, but it's probably faster just to re-create it from
        # scratch.
        with zipfile.ZipFile(self.abspath(outfile_name), 'w') as z:
            for f in infile_names:
                zipname = self.file_mapper(f)
                # TODO(csilvers): do ZIP_DEFLATE instead?
                z.write(self.abspath(f), zipname, zipfile.ZIP_STORED)


# Create a convenient file-mapper.
def nix_prefix_dirs(levels):
    """Return a file-mapper that gets rid of the first 'levels' dir-parts."""
    def file_mapper(infile):
        assert not os.path.isabs(infile)   # infile is relative to ka-root
        return os.sep.join(infile.split(os.sep)[levels:])

    return file_mapper
