"""A Compile object (see compile_rule.py) for creating node files."""

from __future__ import absolute_import

import itertools
import fcntl
import os
import tempfile

from kake.lib import compile_rule


class CompileNpm(compile_rule.CompileBase):
    def version(self):
        """Update every time build() changes in a way that affects output."""
        return 1

    def build_many(self, outfile_infiles_changed_context):
        """We can build all the npm files with just one npm command!"""
        # It would be faster to load just the input node modules,
        # instead of all of them, but it's too difficult to know
        # what module to load to get a given binary.  Ah well.
        # We run 'npm install' in the directory holding node_modules/.
        #
        # We just do this on a best-effort basis, since the user might
        # not even be connected to the internet.  If it fails, we
        # depend on the sanity-checking below to make sure we actually
        # built what we needed to.
        #
        # It's bad if two 'npm install' calls happen at the same time,
        # so I use file-locking to prevent that.  If I were cool I
        # could lock on the package-json file, to allow independent
        # npm installs to happen at the same time.
        lockfile = os.path.join(tempfile.gettempdir(), 'npm_install.lock')
        with open(lockfile, 'w') as f:
            fcntl.lockf(f, fcntl.LOCK_EX)
            (rc, _, _) = self.try_call_with_output(
                ['npm', 'install', '--no-save'],
                stderr=None, stdout=None)

        # This is just sanity-checking that we built what we needed to.
        for (outfile_name, infile_names, _, context) in (
                outfile_infiles_changed_context):
            if not os.path.exists(self.abspath(outfile_name)):
                if rc != 0:
                    msg = 'Could not run "npm install"'
                else:
                    msg = 'Need to add %s to %s' % (outfile_name,
                                                    infile_names[0])
                raise compile_rule.CompileFailure(msg)

    def split_outputs(self, outfile_infiles_changed_context, num_processes):
        """We group together npm binaries with the same package.json."""
        keyfn = lambda (o, i, c, ctx): i[0]      # infile[0] is package.json
        outfile_infiles_changed_context.sort(key=keyfn)
        for (_, chunk) in itertools.groupby(outfile_infiles_changed_context,
                                            keyfn):
            yield list(chunk)


# The top-level node_modules/ is a symlink to genfiles/node_modules/.
# So even though the target npm directory lives in genfiles, the
# package.json it should use is at the top level.
compile_rule.register_compile('NODE-MODULES DIR',
                              'genfiles/node_modules/**',
                              ['package.json'],
                              CompileNpm(),
                              # npm install is slow, so make extra effort
                              # to verify package.json has actually changed.
                              compute_crc=True)

# We need another rule to match the dotfile directory .bin (which ** ignores)
compile_rule.register_compile('NODE-MODULES BIN',
                              'genfiles/node_modules/.bin/**',
                              ['package.json'],
                              CompileNpm(),
                              compute_crc=True)
