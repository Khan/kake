"""Statically analyze our JavaScript files to extract a dependency graph.

Dependencies are gleaned from require() calls and use of undeclared variables.

Use of undeclared variables is determined using JSHint.

These are the kinds of dependencies we list:

    1. An explicit require('./b.js') in file 'a.js' tells us that 'a.js'
       depends on 'b.js'

    2. An explicit require.async(['./b.js']) in file 'a.js' tells us that
       'a.js' depends on 'b.js'

    3. An explicit listing of 'b.js' in the dependencies of 'a.js' if 'a.js' is
       in the _MANIFEST of third_party_js

    4. An explicit listing of 'b.js' in the plugins of 'a.js' if 'a.js' is
       in the _MANIFEST of third_party_js
"""

from __future__ import absolute_import

import json
import os

from shared import ka_root

import js_css_packages.analysis
import js_css_packages.packages
import js_css_packages.third_party_js
import js_css_packages.util
from kake import compile_js_bundles
from kake.lib import compile_rule
from kake.lib import computed_inputs

MANIFEST_PATH = 'javascript-packages.json'
THIRD_PARTY_MANIFEST_PATH = 'js_css_packages/third_party_js.py'


class CompileJSDepGraph(compile_rule.CompileBase):
    """Creates a json file containing the dependency graph of our JS files."""
    def __init__(self):
        """If top-level-only is true, ignore all indented 'requires' lines."""
        super(CompileJSDepGraph, self).__init__()

    def version(self):
        """Update every time build() changes in a way that affects output."""
        return 8

    def _extract_pkg_name_and_normalize(self, unnormalized_path,
                                        js_packages):
        """Return a (realpath, pkg_name) tuple for the given path.

        Converts unnormalized path relative to ka-root to a normalized path,
        inferring the package name in the process.

        The unnormalized path is assumed to have been constructed by
        concatenating a basepath from javascript_packages.json with a 'file'
        entry, also from javascript_packages.json.

        Examples:

            'javascript/banana-package/banana.js'
                -> ('javascript/banana-package/banana.js',
                    'banana.js')

            'javascript/gorilla-package/../banana-package/banana.js'
                -> ('javascript/banana-package/banana.js',
                    'gorilla.js')
        """
        normpath = os.path.normpath(unnormalized_path)

        for pkg_name in js_packages:
            base_path = js_css_packages.util.base_path(js_packages[pkg_name],
                                                       pkg_name)

            if unnormalized_path.startswith(base_path):
                return (ka_root.realpath(normpath), pkg_name)

        assert False, 'Could not infer package for %s' % unnormalized_path

    def _package_dependencies(self, js_packages):
        """Return a map from package name to their package dependencies.

        This is a small subset of the information encoded in
        javascript-packages.json.
        """
        pkg_deps = {}

        for pkg_name, pkg in js_packages.iteritems():
            pkg_deps[pkg_name] = pkg.get('dependencies', [])

        return pkg_deps

    def build(self, outfile_name, infile_names, _, context):
        assert context['{env}'] in ('dev', 'prod'), context['{env}']

        # third_party_js.py is an input file because it affects output, but we
        # don't want to concatenate it with the rest of the files, so we remove
        # it from the list of infile_names
        assert infile_names[-1] == THIRD_PARTY_MANIFEST_PATH, infile_names[-1]
        infile_names.pop()
        # Likewise for the manifest package, if present..
        assert infile_names[-1] == MANIFEST_PATH, infile_names[-1]
        infile_names.pop()

        js_packages = js_css_packages.packages.read_package_manifest(
            MANIFEST_PATH)

        # Extract the basepath from the infile names before they get
        # normalized.
        normalized_infiles_and_pkg_names = \
            [self._extract_pkg_name_and_normalize(x, js_packages)
             for x in infile_names]

        output = {
            'files': [],
            'packagesdeps': self._package_dependencies(js_packages)
        }

        for (f, pkg_name) in normalized_infiles_and_pkg_names:
            dependencies = []

            if js_css_packages.third_party_js.can_analyze_for_dependencies(f):
                required = compile_js_bundles.get_required_dependencies(
                    f,
                    dev=context['{env}'] != 'prod',
                    pkg_locale=context['{lang}'],
                    top_level_only=False)
                # Now get the requirements only paying attention to
                # 'top-level' (unindented) require lines.
                top_level_required = (
                    compile_js_bundles.get_required_dependencies(
                        f,
                        dev=context['{env}'] != 'prod',
                        pkg_locale=context['{lang}'],
                        top_level_only=True))

                for r in required:
                    # As per Rule 1, file f depends on file r because f
                    # requires r.
                    dependencies.append({
                        'type': 'required',
                        'path': ka_root.realpath(r),
                        'is_load_time_dep': r in top_level_required,
                    })

                for r in js_css_packages.analysis.get_async_dependencies(
                                            f,
                                            pkg_locale=context['{lang}'],
                                            dev=context['{env}'] != 'prod'):
                    dependencies.append({
                        'type': 'async',
                        'path': ka_root.realpath(r),
                        # All asynchronous loads are by nature not
                        # load time dependencies.
                        'is_load_time_dep': False
                    })
            else:
                listed = js_css_packages.third_party_js.listed_dependencies(
                    f,
                    dev=context['{env}'] != 'prod',
                    pkg_locale=context['{lang}'])
                for r in listed:
                    # As per Rule 2, file f depends on file r because r is
                    # listed in f's dependencies in the _MANIFEST in
                    # third_party_js.py
                    dependencies.append({
                        'type': 'listed',
                        'path': r,
                        # We assume all third-party deps are required at
                        # load time, and none is asynchronously loaded.
                        'is_load_time_dep': True,
                    })

            plugins = js_css_packages.third_party_js.listed_plugins(
                f,
                dev=context['{env}'] != 'prod',
                pkg_locale=context['{lang}'])
            for r in plugins:
                # As per Rule 3, file f depends on file r because r is listed
                # in f's plugins in the _MANIFEST in third_party_js.py
                dependencies.append({
                    'type': 'plugin',
                    'path': r,
                    # We assume all third-party deps are required at
                    # load time, and none is asynchronously loaded.
                    'is_load_time_dep': True,
                })

            output['files'].append({
                'path': f,
                'pkg_name': pkg_name,
                'dependencies': dependencies
            })

        with open(self.abspath(outfile_name), 'w') as f:
            json.dump(output, f, indent=2, sort_keys=True,
                      separators=(',', ': '))


class ComputeAllServedJSFiles(computed_inputs.ComputedInputsBase):
    def __init__(self):
        self.other_inputs = [
            MANIFEST_PATH,
            THIRD_PARTY_MANIFEST_PATH
        ]

        super(ComputeAllServedJSFiles, self).__init__([
            MANIFEST_PATH
        ])

    def version(self):
        """Update if input_patterns() changes in a way that affects output."""
        return 3

    def input_patterns(self, outfile_name, context, triggers, changed):
        assert triggers[0].endswith('.json'), triggers   # the manifest

        dev = context['{env}'] != 'prod'
        lang = context['{lang}']

        js_packages = js_css_packages.packages.read_package_manifest(
            triggers[0])
        all_files = js_css_packages.util.resolve_files(js_packages,
                                                       pkg_locale=lang,
                                                       dev=dev)
        infiles = []

        for (_, basepath, files) in all_files:
            for f in files:
                if not f.endswith(('.js', '.jsx', '.handlebars')):
                    continue

                # We intentionally don't normalize the file paths here because
                # we want to retain information about which file is part of
                # which package in the compile rule above.
                #
                # See CompileJSDepGraph._extract_pkg_name_and_normalize
                relpath = os.path.join(basepath, f)

                infiles.append(relpath)

        return infiles + self.other_inputs


compile_rule.register_compile(
    'JS DEPENDENCY GRAPH',
    'genfiles/jsdeps/{lang}/js_dep_graph_{env}.json',
    ComputeAllServedJSFiles(),
    CompileJSDepGraph()
)
