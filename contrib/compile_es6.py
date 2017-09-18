"""A Compile object (see compile_rule.py) transpiling ES6 .js/jsx files to ES5.

This enables the use of the following features in all JS files inside of
javascript/:
- arrow functions
- classes
- object shorthand
- rest params
- templates

It also compiles .jsx files from JSX to plain javascript.

We don't transpile things in third_party because we know they're written in
es5.
"""

from __future__ import absolute_import

import json
import os
import shutil
import sys

from kake.lib import compile_rule

# We skip compiling this files because they've already been compiled when
# `make subperseus` was run in the perseus project.  It saves some time during
# deploys because babel is slow compiling large files.
_SKIP_COMPILATION = frozenset([
    "perseus-0.js",
    "perseus-1.js",
    "perseus-2.js",
    "perseus-3.js",
    "perseus-4.js",
    "perseus-5.js",
    "perseus-6.js",
    "perseus-7.js",
    "perseus-8.js",
    "perseus-9.js",
    "perseus-10.js",
    "editor-perseus.js",
])


class CompileES6(compile_rule.CompileBase):
    def version(self):
        """Update every time build() changes in a way that affects output."""
        return 3

    def build_many(self, output_inputs_changed_context):
        compiler = None
        compile_args = []
        for (output, inputs, _, context) in output_inputs_changed_context:
            assert len(inputs) == 3, inputs
            assert inputs[0].endswith(('.js', '.jsx'))
            assert 'compile_js.js' in inputs[1]
            if compiler is None:
                compiler = inputs[1]
            else:
                assert compiler == inputs[1], (
                    'All js files must use the same js compiler')

            if os.path.basename(inputs[0]) in _SKIP_COMPILATION:
                shutil.copyfile(self.abspath(inputs[0]), self.abspath(output))
            else:
                compile_args.append((self.abspath(inputs[0]),
                                     self.abspath(output)))

        if compile_args:
            (retcode, stdout, stderr) = self.try_call_with_input(
                ['node', compiler],
                input=json.dumps(compile_args))
            if retcode != 0:
                input_files = [x[1][0] for x in output_inputs_changed_context]
                message = 'Compiling JS files %s failed:\n%s\n' % (
                    input_files, stderr)
                raise compile_rule.GracefulCompileFailure(
                        message,
                        'console.error(%s);' % json.dumps(stderr))

    def num_outputs(self):
        """stdin can take as much data as we can throw at it!"""
        return sys.maxint


# We have to list the dependencies on node_modules explicitly; we can't
# magically parse them from the require calls in compile_js.js, yet.
compile_rule.register_compile(
    'COMPILED ES6',
    'genfiles/compiled_es6/en/{{path}}.js',
    ['{{path}}.js',
     'kake/compile_js.js',
     'genfiles/node_modules/babel-core/package.json'],
    CompileES6())

compile_rule.register_compile(
    'COMPILED JSX',
    'genfiles/compiled_jsx/en/{{path}}.jsx.js',
    ['{{path}}.jsx',
     'kake/compile_js.js',
     'genfiles/node_modules/babel-core/package.json'],
    CompileES6())
