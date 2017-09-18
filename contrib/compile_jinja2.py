"""A Compile object (see compile_rule.py) for jinja2 foo.html files."""

from __future__ import absolute_import

import contextlib
import os

import jinja2.nodes

from kake.lib import compile_rule


@contextlib.contextmanager
def _patch(object, attr, newval):
    """A simple version of mock.patch, so we don't have to import mock."""
    oldval = getattr(object, attr)
    setattr(object, attr, newval)
    try:
        yield
    finally:
        setattr(object, attr, oldval)


def _can_never_be_const(*args, **kwargs):
    """Version of nodes.Filter.as_const that disables constant-folding."""
    raise jinja2.nodes.Impossible()


class CompileJinja2Templates(compile_rule.CompileBase):
    """Compiles all jinja2 .html files and puts them in a zipfile."""
    def version(self):
        """Update every time build() changes in a way that affects output."""
        return 2

    def build(self, outfile_name, infile_names, _, context):
        import jinja2
        import webapp2
        import webapp2_extras.jinja2

        # Use our app's standard jinja config so we pick up custom
        # globals and filters.  Because this is an expensive import
        # (it brings in much of webapp), we only do it when it's
        # actually needed.  This keeps most of kake lightweight.
        import config_jinja          # @UnusedImport

        # The jinja2 routines work relative to the templates/ directory.
        rootdir = self.abspath('templates')
        rel_infiles = [f[len('templates' + os.sep):] for f in infile_names]

        app = webapp2.WSGIApplication()
        env = webapp2_extras.jinja2.get_jinja2(app=app).environment

        # 1) Mock our environment to use the filesystem loader, but
        # to get the list of filenames directly from us.
        # 2) Turn off constant-folding of filters -- the jinja2
        # optimizer is unsound in that it does 'constant'-folding of
        # function calls that are not constant (because they depend on
        # os.environ, say).  This was breaking use of the static_url
        # filter, among others.
        with _patch(jinja2.nodes.Filter, 'as_const', _can_never_be_const):
            with _patch(env, 'loader', jinja2.FileSystemLoader(rootdir)):
                with _patch(env, 'list_templates', lambda *args: rel_infiles):
                    # Compile templates to zip, crashing on any
                    # compilation errors.
                    env.compile_templates(outfile_name,
                                          ignore_errors=False,
                                          py_compile=True,
                                          zip='deflated')


compile_rule.register_compile(
    'JINJA2 TEMPLATES',
    'genfiles/compiled_jinja_templates.zip',
    # We compile everything under templates/ that has an extension.
    ['templates/**.*'],
    CompileJinja2Templates())
