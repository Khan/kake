"""Tests for sourcemap_util.py."""

from __future__ import absolute_import

import json

from kake import sourcemap_util
import testutil


class TestIdentitySourcemap(testutil.KakeTestBase):
    def test_num_lines_one_line(self):
        sm = sourcemap_util._identity_sourcemap('foo', 'aaa\n')
        self.assertEqual('foo', sm['file'])
        self.assertEqual('AAAA', sm['mappings'])

    def test_num_lines_hanging_line(self):
        sm = sourcemap_util._identity_sourcemap('foo', 'aaa\nbbb')
        self.assertEqual('foo', sm['file'])
        self.assertEqual('AAAA;AACA', sm['mappings'])

    def test_num_lines_many_newlines(self):
        sm = sourcemap_util._identity_sourcemap('foo', 'aaa\n\n')
        self.assertEqual('foo', sm['file'])
        self.assertEqual('AAAA;AACA', sm['mappings'])

    def test_no_filename(self):
        sm = sourcemap_util._identity_sourcemap(None, 'aaa\n\n')
        self.assertFalse('file' in sm, sm)
        self.assertEqual('A;A', sm['mappings'])

    def test_no_content(self):
        sm = sourcemap_util._identity_sourcemap('foo', '')
        self.assertEqual('foo', sm['file'])
        self.assertEqual('', sm['mappings'])


class TestIndexSourcemap(testutil.KakeTestBase):
    def setUp(self):
        super(TestIndexSourcemap, self).setUp()
        self.maxDiff = None

    def test_simple(self):
        sm = sourcemap_util.IndexSourcemap('foo.out')
        sm.add_section('i1', 'This is i1\n')
        sm.add_section('i2', 'This is i2\n')
        expected = {"version": 3,
                    "file": "foo.out",
                    "sections": [
                        {'offset': {'line': 0, 'column': 0},
                         'map': {"version": 3,
                                 "file": "i1",
                                 "sourceRoot": "/",
                                 "sources": ["i1"],
                                 "names": [],
                                 "mappings": "AAAA"},
                         },
                        {'offset': {'line': 1, 'column': 0},
                         'map': {"version": 3,
                                 "file": "i2",
                                 "sourceRoot": "/",
                                 "sources": ["i2"],
                                 "names": [],
                                 "mappings": "AAAA"},
                         },
                        ]}
        self.assertDictEqual(expected, sm.sourcemap)

    def test_simple_offsets(self):
        sm = sourcemap_util.IndexSourcemap('foo.out')
        sm.add_section('i1', 'This is i1\n')
        sm.add_section('i2', 'This is i2\n')
        sm.add_section('i3', 'This is i3\n')
        self.assertEqual({'line': 0, 'column': 0},
                         sm.sourcemap['sections'][0]['offset'])
        self.assertEqual({'line': 1, 'column': 0},
                         sm.sourcemap['sections'][1]['offset'])
        self.assertEqual({'line': 2, 'column': 0},
                         sm.sourcemap['sections'][2]['offset'])

    def test_multiline_offsets(self):
        sm = sourcemap_util.IndexSourcemap('foo.out')
        sm.add_section('i1', 'This is i1\nAnd it is still i1\n')
        sm.add_section('i2', 'This is i2\n\n\n')
        sm.add_section('i3', 'This is i3\n')
        self.assertEqual({'line': 0, 'column': 0},
                         sm.sourcemap['sections'][0]['offset'])
        self.assertEqual({'line': 2, 'column': 0},
                         sm.sourcemap['sections'][1]['offset'])
        self.assertEqual({'line': 5, 'column': 0},
                         sm.sourcemap['sections'][2]['offset'])

    def test_offsets_with_columns(self):
        sm = sourcemap_util.IndexSourcemap('foo.out')
        sm.add_section('i1', 'This is i1\nAnd it is still i1')
        sm.add_section('i2', 'This is i2\n\n\nWhat do you think, yo?')
        sm.add_section('i3', 'This is i3\n')
        sm.add_section('i4', 'This is i4.')
        sm.add_section('i5', 'This is i5.')
        sm.add_section('i6', 'This is i6.\n')
        sm.add_section('i7', 'This is i7.\nAnd this...')
        sm.add_section('i8', 'is i8!')

        self.assertEqual({'line': 0, 'column': 0},
                         sm.sourcemap['sections'][0]['offset'])
        self.assertEqual({'line': 1, 'column': 18},
                         sm.sourcemap['sections'][1]['offset'])
        self.assertEqual({'line': 4, 'column': 22},
                         sm.sourcemap['sections'][2]['offset'])
        self.assertEqual({'line': 5, 'column': 0},
                         sm.sourcemap['sections'][3]['offset'])
        self.assertEqual({'line': 5, 'column': 11},
                         sm.sourcemap['sections'][4]['offset'])
        self.assertEqual({'line': 5, 'column': 22},
                         sm.sourcemap['sections'][5]['offset'])
        self.assertEqual({'line': 6, 'column': 0},
                         sm.sourcemap['sections'][6]['offset'])
        self.assertEqual({'line': 7, 'column': 11},
                         sm.sourcemap['sections'][7]['offset'])

    def test_offsets_with_no_filenames(self):
        sm = sourcemap_util.IndexSourcemap('foo.out')
        sm.add_section('i1', 'This is i1')
        sm.add_section(None, ';')
        sm.add_section('i2', 'This is i2')
        sm.add_section(None, '\n')
        sm.add_section('i3', 'This is i3')
        sm.add_section('i4', 'This is i4')

        self.assertEqual({'line': 0, 'column': 0},
                         sm.sourcemap['sections'][0]['offset'])
        self.assertEqual({'line': 0, 'column': 10},
                         sm.sourcemap['sections'][1]['offset'])
        self.assertEqual({'line': 0, 'column': 11},
                         sm.sourcemap['sections'][2]['offset'])
        self.assertEqual({'line': 0, 'column': 21},
                         sm.sourcemap['sections'][3]['offset'])
        self.assertEqual({'line': 1, 'column': 0},
                         sm.sourcemap['sections'][4]['offset'])
        self.assertEqual({'line': 1, 'column': 10},
                         sm.sourcemap['sections'][5]['offset'])

    def test_references_to_other_sourcemaps(self):
        sm = sourcemap_util.IndexSourcemap('foo.out')
        sm.add_section('i1', 'This is i1\n')
        sm.add_section('i2', 'This is i2\n')
        with open(self._abspath('sub.map'), 'w') as f:
            print >>f, sm.to_json()

        sm = sourcemap_util.IndexSourcemap('bar.out')
        sm.add_section('i3', 'This is i3\n')
        sm.add_section('sub', 'This is the sub-map\n')
        expected = {
            'version': 3,
            'file': 'bar.out',
            'sections': [{'map': {'mappings': 'AAAA',
                                  'sourceRoot': '/',
                                  'sources': ['i3'],
                                  'version': 3,
                                  'names': [],
                                  'file': 'i3'},
                          'offset': {'column': 0, 'line': 0}},
                         {'map': {'version': 3,
                                  'file': 'foo.out',
                                  'sections': [{'map': {'mappings': 'AAAA',
                                                        'sourceRoot': '/',
                                                        'sources': ['i1'],
                                                        'version': 3,
                                                        'names': [],
                                                        'file': 'i1'},
                                                'offset': {'column': 0,
                                                           'line': 0}},
                                               {'map': {'mappings': 'AAAA',
                                                        'sourceRoot': '/',
                                                        'sources': ['i2'],
                                                        'version': 3,
                                                        'names': [],
                                                        'file': 'i2'},
                                                'offset': {'column': 0,
                                                           'line': 1}}
                                               ]},
                          'offset': {'column': 0, 'line': 1}},
                         ],
            }
        self.assertEqual(expected, sm.sourcemap)

    def test_to_json(self):
        sm = sourcemap_util.IndexSourcemap('foo.out')
        sm.add_section('i1', 'This is i1\n')
        sm.add_section('i2', 'This is i2\n')
        expected = json.dumps({"version": 3,
                               "file": "foo.out",
                               "sections": [
                                   {'offset': {'line': 0, 'column': 0},
                                    'map': {"version": 3,
                                            "file": "i1",
                                            "sourceRoot": "/",
                                            "sources": ["i1"],
                                            "names": [],
                                            "mappings": "AAAA"},
                                    },
                                   {'offset': {'line': 1, 'column': 0},
                                    'map': {"version": 3,
                                            "file": "i2",
                                            "sourceRoot": "/",
                                            "sources": ["i2"],
                                            "names": [],
                                            "mappings": "AAAA"},
                                    }
                                   ]},
                              indent=2, sort_keys=True)
        self.assertEqual(expected, sm.to_json())


if __name__ == '__main__':
    testutil.main()
