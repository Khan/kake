# TODO(colin): fix these lint errors (http://pep8.readthedocs.io/en/release-1.7.x/intro.html#error-codes)
# pep8-disable:E131
"""A Compile object (see compile_rule.py): foo.less -> foo.less.css."""

from __future__ import absolute_import

import json

from kake.lib import compile_rule
from kake.lib import computed_inputs

_LESS_COMPILATION_FAILURE_RESPONSE = """
body * {
    display: none !important;
}

body {
    background: #bbb !important;
    margin: 20px !important;
    color: #900 !important;
    font-family: Menlo, Consolas, Monaco, monospace !important;
    font-weight: bold !important;
    white-space: pre !important;
}

body:before {
    content: %s
}
"""


class CompileLess(compile_rule.CompileBase):
    def version(self):
        """Update every time build() changes in a way that affects output."""
        return 3

    def build(self, outfile_name, infile_names, _, context):
        # As the lone other_input, the lessc compiler is the last infile.
        (retcode, stdout, stderr) = self.try_call_with_output(
            [self.abspath(infile_names[-1]),
             '--no-color',
             '--source-map',                  # writes to <outfile>.map
             '--source-map-rootpath=/',
             '--source-map-basepath=%s' % self.abspath(''),
             self.abspath(infile_names[0]),
             self.abspath(outfile_name)])
        if retcode:
            message = 'Compiling Less file %s failed:\n%s\n' % (
                infile_names[0], stderr)
            raise compile_rule.GracefulCompileFailure(
                message,
                _LESS_COMPILATION_FAILURE_RESPONSE %
                    # Use \A instead of \n in CSS strings:
                    # http://stackoverflow.com/a/9063069
                    json.dumps(message).replace("\\n", " \\A "))


# Less files have an include-structure, which means that whenever an
# included file changes, we need to rebuild.  Hence we need to use a
# computed input.
compile_rule.register_compile(
    'COMPILED LESS',
    'genfiles/compiled_less/en/{{path}}.less.css',
    computed_inputs.ComputedIncludeInputs(
        '{{path}}.less',
        r'^@import\s*"([^"]*)"',
        other_inputs=['genfiles/node_modules/.bin/lessc']),
    CompileLess())
