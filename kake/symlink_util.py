from __future__ import absolute_import

import os

from . import compile_rule
from . import log


def symlink(symlink_to, symlink_from):
    """Create a relative symlink.  Inputs must be absolute paths."""
    assert os.path.isabs(symlink_to), symlink_to
    assert os.path.isabs(symlink_from), symlink_from

    try:
        os.makedirs(os.path.dirname(symlink_from))
    except (IOError, OSError):
        pass

    relative_to = os.path.relpath(symlink_to, os.path.dirname(symlink_from))
    if (os.path.islink(symlink_from) and
            os.readlink(symlink_from) == relative_to):
        return        # already have the right contents!

    if os.path.exists(symlink_from) or os.path.islink(symlink_from):
        os.unlink(symlink_from)
    log.v1('   ... creating symlink %s -> %s', symlink_from, symlink_to)
    os.symlink(relative_to, symlink_from)


class CreateSymlink(compile_rule.CompileBase):
    def version(self):
        """Update every time build() changes in a way that affects output."""
        return 1

    def build(self, outfile_name, infile_names, _, context):
        assert len(infile_names) == 1, (
                "Can only symlink to one file, got %r" % infile_names)

        symlink(self.abspath(infile_names[0]), self.abspath(outfile_name))
