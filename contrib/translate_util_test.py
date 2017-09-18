"""Tests for translate_util.py."""
from __future__ import absolute_import

from shared.testutil import testsize

from kake import translate_util
from kake.lib import compile_rule
import kake.lib.testutil


translate_util.register_translatesafe_compile(
    'LANG',
    'genfiles/translate_util_test/{lang}/{{path}}.min.js',
    ['genfiles/translate_util_test/en/{{path}}.min.js',
     'genfiles/extracted_strings/{lang}/{{path}}.small_mo.pickle'],
    None)


translate_util.register_translatesafe_compile(
    'BOXES',
    'genfiles/translate_util_test/boxes/{{path}}.min.js',
    ['genfiles/translate_util_test/en/{{path}}.min.js',
     ('genfiles/extracted_strings/boxes/'
      'genfiles/translate_util_test/boxes/{{path}}.small_mo.pickle')],
    None)


@testsize.tiny
class TestRegisterTranslatesafeCompile(kake.lib.testutil.KakeTestBase):
    def test_var_for_language(self):
        """Test a rule where the lang is specified as a variable: {lang}."""
        # I test by making sure the different nestings all get their own rule.
        rules = [
            compile_rule.find_compile_rule(
                'genfiles/translate_util_test/es/foo.min.js'),
            compile_rule.find_compile_rule(
                'genfiles/translate_util_test/es/'
                'genfiles/nest1/es/foo.min.js'),
            compile_rule.find_compile_rule(
                'genfiles/translate_util_test/es/'
                'genfiles/nest1/es/genfiles/nest2/es/foo.min.js'),
            compile_rule.find_compile_rule(
                'genfiles/translate_util_test/es/'
                'genfiles/nest1/es/genfiles/nest2/es/'
                'genfiles/nest3/es/foo.min.js'),
            compile_rule.find_compile_rule(
                'genfiles/translate_util_test/es/'
                'genfiles/nest1/es/genfiles/nest2/es/'
                'genfiles/nest3/es/genfiles/nest4/es/foo.min.js'),
            ]

        for i in xrange(len(rules)):
            for j in (xrange(i)):
                self.assertNotEqual(rules[j], rules[i], (j, i))

        # Now spot-check the inputs are right.
        self.assertEqual('genfiles/translate_util_test/en/'
                         'genfiles/{d1}/en/genfiles/{d2}/en/{{path}}.min.js',
                         rules[2].input_patterns[0])

    def test_explicit_language(self):
        """Test a rule where the lang is specified as a constant: 'boxes'."""
        rules = [
            compile_rule.find_compile_rule(
                'genfiles/translate_util_test/boxes/foo.min.js'),
            compile_rule.find_compile_rule(
                'genfiles/translate_util_test/boxes/'
                'genfiles/nest1/boxes/foo.min.js'),
            compile_rule.find_compile_rule(
                'genfiles/translate_util_test/boxes/'
                'genfiles/nest1/boxes/genfiles/nest2/boxes/foo.min.js'),
            compile_rule.find_compile_rule(
                'genfiles/translate_util_test/boxes/'
                'genfiles/nest1/boxes/genfiles/nest2/boxes/'
                'genfiles/nest3/boxes/foo.min.js'),
            compile_rule.find_compile_rule(
                'genfiles/translate_util_test/boxes/'
                'genfiles/nest1/boxes/genfiles/nest2/boxes/'
                'genfiles/nest3/boxes/genfiles/nest4/boxes/foo.min.js'),
            ]

        for i in xrange(len(rules)):
            for j in (xrange(i)):
                self.assertNotEqual(rules[j], rules[i], (j, i))

        # Now spot-check the inputs are right.
        self.assertEqual('genfiles/translate_util_test/en/'
                         'genfiles/{d1}/en/genfiles/{d2}/en/{{path}}.min.js',
                         rules[2].input_patterns[0])

    def test_other_infiles(self):
        r = compile_rule.find_compile_rule(
            'genfiles/translate_util_test/boxes/'
            'genfiles/nest1/boxes/genfiles/nest2/boxes/foo.min.js')

        self.assertEqual('genfiles/translate_util_test/en/'
                         'genfiles/{d1}/en/genfiles/{d2}/en/{{path}}.min.js',
                         r.input_patterns[0])
        self.assertEqual('genfiles/extracted_strings/boxes/'
                         'genfiles/translate_util_test/boxes/'
                         'genfiles/{d1}/boxes/genfiles/{d2}/boxes/'
                         '{{path}}.small_mo.pickle',
                         r.input_patterns[1])

    def test_bad_outfile(self):
        with self.assertRaises(ValueError):
            translate_util.register_translatesafe_compile(
                'BAD OUTFILE',
                '{{path}}.min.js',
                ['genfiles/translate_util_test/en/{{path}}.min.js',
                 'genfiles/extracted_strings/boxes/{{path}}.small_mo.pickle'],
                None)

    def test_bad_infile(self):
        with self.assertRaises(ValueError):
            translate_util.register_translatesafe_compile(
                'BAD INFILE',
                'genfiles/translate_util_test/{lang}/{{path}}.min.js',
                ['genfiles/translate_util_test/{lang}/{{path}}.js',
                 'genfiles/extracted_strings/{lang}/{{path}}.small_mo.pickle'],
                None)
