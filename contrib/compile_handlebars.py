"""A Compile object (see compile_rule.py) for foo.handlebars files."""

from __future__ import absolute_import

import inspect
import json
import os
import re
import sys
import types

from kake.lib import compile_rule
from kake.lib import log

_PACKAGE_NAME_RE = re.compile(r'([^%s]+)-package' % os.sep)


def _extract_pkg_base_name_from_path(path):
    """Extract the base package name from a file path relative to ka-root.

    In order to compile a handlebars file, we need to know what package it's
    in. In order to avoid needing the compile rule to have a context passed to
    it, we extract the package name from the path.
    """
    # TODO(jlfwong): Remove the concept of package names for handlebars
    # compilation/rendering completely. This will require rewriting every call
    # handlebars_template and every call to #invokePartial.
    m = _PACKAGE_NAME_RE.search(path)
    assert m, "Can't figure out the package for '%s'" % path
    return (m.group(1) or m.group(2))


class CompilePyHandlebars(compile_rule.CompileBase):
    """Compiles .handlebars files to python code."""
    def version(self):
        """Update every time build() changes in a way that affects output."""
        return 3

    def build(self, outfile_name, infile_names, _, context):
        assert infile_names[0].endswith('.handlebars'), infile_names

        assert self.should_compile(infile_names[0]), infile_names[0]
        self._compile_file_to_python(self.abspath(infile_names[0]),
                                     self.abspath(outfile_name))
        log.v3("Compiled handlebars: %s -> %s",
               infile_names[0], outfile_name)

    @staticmethod
    def should_compile(handlebars_path):
        """handlebars_path should be relative to ka-root."""
        # We intentionally ignore Handlebars templates that don't have
        # unit tests when compiling to Python. If someday all
        # templates have unit tests we should emit an error here.
        if handlebars_path.startswith(os.path.join('genfiles',
                                                   'translations')):
            # Get to the english-language version of the name
            handlebars_path = os.sep.join(handlebars_path.split(os.sep)[3:])

        return os.path.exists(
            CompilePyHandlebars.abspath(handlebars_path + ".json"))

    def _compile_file_to_python(self, input_filename, output_filename):
        """filenames should be absolute.  Sets up __init__.py's as well."""
        import third_party.pybars

        with open(input_filename) as in_file:
            source = in_file.read().decode('utf-8')
        # Pybars doesn't handle {{else}} for some reason
        source = re.sub(r'{{\s*else\s*}}', "{{^}}", source)
        template = third_party.pybars.Compiler().compile(source)

        output_string = []

        # Some translated content may show up as literal utf-8 strings
        # in the code.  Make sure __import__ can handle that.  See
        #   genfiles/compiled_handlebars_py/pt-BR/'
        #   javascript/discussion-package/question-form.py
        # for an example.
        output_string.append("# coding: utf-8")

        output_string.append("import third_party.pybars as pybars")
        # We need to import strlist from pybars instead of
        # third_party.pybars._compiler because pybars
        # add_escaped_expand() checks the type against
        # pybars._compiler.strlist without the third_party.
        output_string.append("from pybars._compiler import strlist")
        output_string.append("from third_party.pybars._compiler import "
                             "_pybars_, Scope, escape, resolve, partial")
        output_string.append("")

        def write_fn(template, name, indent):
            output_string.append(
                "%sdef %s(context, helpers=None, partials=None):"
                % (indent, name))
            output_string.append("%s    pybars = _pybars_" % indent)
            output_string.append("")

            output_string.append("%s    # Begin constants" % indent)
            for name, val in template.func_globals.items():
                if name.startswith("constant_"):
                    if isinstance(val, unicode):
                        output_string.append("%s    %s = %s" %
                                             (indent, name, repr(val)))
            output_string.append("")
            for name, val in template.func_globals.items():
                if name.startswith("constant_"):
                    if isinstance(val, types.FunctionType):
                        write_fn(val, name, indent + "    ")
            output_string.append("%s    # End constants" % indent)

            compiled_fn = inspect.getsource(template).decode('utf-8')
            fn_lines = compiled_fn.split("\n")
            for line in fn_lines[1:]:
                output_string.append("%s%s" % (indent, line))

        # The function name is the same as our filename, but with _, not -.
        function_name = os.path.splitext(os.path.basename(output_filename))[0]
        function_name = function_name.replace('-', '_')
        write_fn(template, function_name, "")

        with open(output_filename, 'w') as out_file:
            out_file.write("\n".join(output_string).encode('utf-8'))


class CompileInitFiles(compile_rule.CompileBase):
    """Just makes sure __init__.py exists in the appropriate dir-tree."""
    def version(self):
        """Update every time build() changes in a way that affects output."""
        return 1

    def build(self, outfile_name, infile_names, _, context):
        dirpath = os.path.dirname(outfile_name)
        while dirpath:
            init_file = self.abspath(dirpath, '__init__.py')
            if not os.path.exists(init_file):
                open(init_file, 'w').close()
                log.info('WROTE %s', init_file)
            dirpath = os.path.dirname(dirpath)


class CompileHandlebarsPartials(compile_rule.CompileBase):
    """Compiles the __init__.py file holding all the handlebars_partials."""
    def version(self):
        """Update every time build() changes in a way that affects output."""
        return 2

    def build(self, outfile_name, infile_names, _, context):
        infile_names.sort()    # just to keep the output deterministic
        with open(self.abspath(outfile_name), 'w') as f:
            print >>f, 'from handlebars.render import handlebars_template'
            print >>f
            print >>f, 'handlebars_partials = {'
            for infile_name in infile_names:
                # We only need to store partials for handlebars files
                # we compile to python.
                if not CompilePyHandlebars.should_compile(infile_name):
                    continue
                pkg_name = _extract_pkg_base_name_from_path(infile_name)
                basename = os.path.splitext(os.path.basename(infile_name))[0]
                print >>f, ('    "%s_%s": '
                            'lambda params, partials=None, helpers=None: '
                            'handlebars_template("%s", "%s", params),'
                            % (pkg_name, basename, pkg_name, basename))
            print >>f, '}'


class CompileJsHandlebars(compile_rule.CompileBase):
    """Compiles .handlebars files to python code."""
    def version(self):
        """Update every time build() changes in a way that affects output."""
        return 4

    def build_many(self, output_inputs_changed_context):
        # compile_handlebars.js expects input to be a list of triples:
        #   (infile, outfile)
        compiler = None
        compile_args = []
        for (output, inputs, _, context) in output_inputs_changed_context:
            assert inputs[0].endswith('.handlebars')
            assert 'compile_handlebars.js' in inputs[1]
            if compiler is None:
                compiler = inputs[1]
            else:
                assert compiler == inputs[1], (
                    'All .handlebars files must use the same js compiler')

            compile_args.append((self.abspath(inputs[0]),
                                 self.abspath(output)))

        if compile_args:
            self.call_with_input(['node', compiler],
                                 input=json.dumps(compile_args))

    def num_outputs(self):
        """stdin can take as much data as we can throw at it!"""
        return sys.maxint


# Compiling handlebars.py files is surprisingly complicated.  First,
# we don't bother to do so at all unless we know we load the
# handlebars file in python, which we tell by whether there's an
# associated .handlebars.json file.  See PyHandlebarsInputs.__doc__
# for more info.
#
# Also, since handlebars.py files are imported, so we need __init__
# files in every directory leading to the compiled handlebars files,
# hence the first non_input_dep.  And handlebars files also need need
# the 'partials' information in the 'global' __init__.py, hence the
# second non_input_dep.
compile_rule.register_compile(
    'COMPILED PY HANDLEBARS',
    'genfiles/compiled_handlebars_py/en/{{dir}}/{base}.py',
    ['{{dir}}/{base}.handlebars'],
    CompilePyHandlebars(),
    non_input_deps=[
        'genfiles/compiled_handlebars_py/en/{{dir}}/__init__.py',
        'genfiles/compiled_handlebars_py/__init__.py'])

# We also handle translated handlebars files.
compile_rule.register_compile(
    'COMPILED TRANSLATED PY HANDLEBARS',
    'genfiles/compiled_handlebars_py/{lang}/{{dir}}/{base}.py',
    ['genfiles/translations/{lang}/{{dir}}/{base}.handlebars'],
    CompilePyHandlebars(),
    non_input_deps=[
        'genfiles/compiled_handlebars_py/{lang}/{{dir}}/__init__.py',
        'genfiles/compiled_handlebars_py/__init__.py'],
    maybe_symlink_to='genfiles/compiled_handlebars_py/en/{{dir}}/{base}.py')

# We need to be able to build the 'init' files.
compile_rule.register_compile(
    'PER-DIRECTORY HANDLEBARS __INIT__ FILES',
    'genfiles/compiled_handlebars_py/{{dir}}/__init__.py',
    [],
    CompileInitFiles())

# And we need to build the 'partials' file.
compile_rule.register_compile(
    'GLOBAL HANDLEBARS __INIT__ FILE',
    'genfiles/compiled_handlebars_py/__init__.py',
    ['javascript/*-package/*.handlebars'],
    CompileHandlebarsPartials())


# Now do handlebars.js files.  We have to list the dependencies on the
# handlebars and uglify node_modules explicitly; we can't magically
# parse it from 'require("handlebars")' and 'require("uglify-js")' in
# compile_handlebars.js, yet.
compile_rule.register_compile(
    'COMPILED JS HANDLEBARS',
    'genfiles/compiled_handlebars_js/en/{{path}}.handlebars.js',
    ['{{path}}.handlebars',
     'kake/compile_handlebars.js',
     'genfiles/node_modules/handlebars/lib/handlebars.js',
     'genfiles/node_modules/.bin/uglifyjs'],
    CompileJsHandlebars())

compile_rule.register_compile(
    'COMPILED JS TRANSLATED HANDLEBARS',
    'genfiles/compiled_handlebars_js/{lang}/{{path}}.handlebars.js',
    ['genfiles/translations/{lang}/{{path}}.handlebars',
     'kake/compile_handlebars.js',
     'genfiles/node_modules/handlebars/lib/handlebars.js',
     'genfiles/node_modules/.bin/uglifyjs'],
    CompileJsHandlebars(),
    maybe_symlink_to=('genfiles/compiled_handlebars_js/en/'
                      '{{path}}.handlebars.js'))
