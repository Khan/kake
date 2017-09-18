"""Test compile_js_dep_graph.py."""

from __future__ import absolute_import

import os
import shutil

import intl.data
import kake.lib.testutil
from kake import make


class TestCompileJsDepGraph(kake.lib.testutil.KakeTestBase):
    # If you make some change that changes the output for all dep graph json
    # outputs, then you can update all the test outputs by uncommenting this:
    #
    #  def assertFileContentsMatch(self, filename, expected):
    #      target_path = os.path.join(self.real_ka_root,
    #          'kake/compile_js_dep_graph-testfiles/', expected)
    #      with open(target_path, 'w') as fout:
    #          fout.write(self._file_contents(filename))
    #
    # and running the tests:
    #
    #   tools/runtests.py kake/compile_js_dep_graph_test.py
    #
    # Be sure to examine the diff afterwards to make sure your change made
    # sense!

    def setUp(self):
        super(TestCompileJsDepGraph, self).setUp()
        self._copy_to_test_tmpdir(
                os.path.join('kake', 'compile_js_dep_graph-testfiles'))

        self.mock_value('js_css_packages.third_party_js._DEPENDENCIES', {
            'third_party/javascript-khansrc/hackbone.js': [
                'third_party/javascript-khansrc/dunderscore.js'
            ]
        })
        self.mock_value('js_css_packages.third_party_js._PLUGINS', {
            'third_party/javascript-khansrc/hackbone.js': [
                'third_party/javascript-khansrc/hackbone.{{lang}}.js'
            ]
        })

        self.mock_value('intl.data._LanguageStatus._LANGUAGES',
                        {
                            'es': intl.data._LanguageStatus.ROCK_STAR
                        })

        os.makedirs(os.path.join(self.tmpdir, 'kake'))
        # We need this file from the main repo for building.
        #
        # NOTE: We intentionally make a copy instead of symlinking here because
        # node resolves dependencies based on the where the real file is. We
        # want compile_handlebars.js to look inside the sandbox for
        # dependencies, not the real ka-root.
        shutil.copyfile(os.path.join(self.real_ka_root,
                                     'kake', 'compile_handlebars.js'),
                        os.path.join(self.tmpdir,
                                     'kake', 'compile_handlebars.js'))

    def test_build_en_dev(self):
        path = 'genfiles/jsdeps/en/js_dep_graph_dev.json'
        make.build(path)
        self.assertFileContentsMatch(path, 'expected/en_dev.json')

    def test_build_en_prod(self):
        path = 'genfiles/jsdeps/en/js_dep_graph_prod.json'
        make.build(path)
        self.assertFileContentsMatch(path, 'expected/en_prod.json')

    def test_build_es_MX_dev(self):
        path = 'genfiles/jsdeps/es-MX/js_dep_graph_dev.json'
        make.build(path)
        self.assertFileContentsMatch(path, 'expected/es-MX_dev.json')
