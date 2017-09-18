"""Routines to create and combine sourcemaps.

Sourcemaps are a method for mapping from a 'munged' js or css file
back to the unmunged one.  This makes debugging easier.  Every time we
create a new .js/.css file, we can create an associated
.js.map/.css.map file to go along with it.

Many tools kake uses -- lessc, uglifyjs -- can create sourcemaps as
they compile.  But kake creates a sourcemap of its own when combining
files: when concatenating a.js and b.js to combined.js, kake is the
one who needs to make combined.js.map.

As of 1 Dec 2013, the sourcemap v3 spec allows you to easily create a
sourcemap for a combined file by just referencing the sourcemaps of
all the individual files we are combining together.  This works great
if the individual files have a sourcemap, which they do if they are
.less files or .min files.  But if they are raw .css or .js files,
they don't have a sourcemap, so kake needs to create one.  Since these
files aren't actually munged in any way, their 'sourcemap' will just
map from line x, col y of the input to line x, col y of the output.
In fact, the input and output can be the same.  Easy enough, though
the encoding is kinda annoying.

(I've asked the sourcemap standards authors to allow you to omit a
sourcemap for an individual file, implying the identity map, so we
don't have to make them by hand.)

For more on sourcemaps, see
   https://docs.google.com/document/d/1U1RGAehQwRypUTovF1KRlpiOFze0b-_2gc6fAH0KY0k/edit#

For more on how to encode sourcemap data, see
   http://qfox.nl/weblog/281
   http://sourcemapper.qfox.nl/
"""

from __future__ import absolute_import

import json
import os

from . import project_root


def _num_lines(s):
    num_lines = s.count('\n')
    # "abc\n" is one line, but "abc\ndef" is two.  "abc\n\n" is also two.
    if not s.endswith('\n'):
        num_lines += 1
    return num_lines


def _identity_sourcemap(filename, file_contents):
    """Create a sourcemap mapping filename to itself.  filename can be None."""
    # For the identity mapping, we need one entry per line, always
    # starting at column 0.  The mapping structure is one 4-tuple per
    # line, with lines separated by a semicolon.
    # The 4-tuple is:
    #    dst-column (delta): always 0 ('A')
    #    index into 'sources' (delta): always 0 ('A')
    #    src-lineno (delta): starts at 0 ('A'), after that it's always 1 ('C')
    #    src-column (delta): always 0 ('A')
    # Note all these values are deltas: change from the previous value.
    #
    # If filename is absent, this represents contents in a 'combined'
    # sourcemap that were introduced in the combining step, and aren't
    # part of any source.  In that case, we can use just a 1-tuple:
    # the dst-column.
    num_lines = _num_lines(file_contents)

    if not file_contents:
        mappings = []
    elif filename:
        mappings = ['AAAA'] + ['AACA'] * (num_lines - 1)
    else:
        mappings = ['A'] * num_lines

    if filename:
        return {
            "version": 3,           # v3 of the sourcemap protocol
            "file": filename,
            "sourceRoot": "/",      # an absolute url on kake-server
            "sources": [filename],
            "names": [],
            "mappings": ';'.join(mappings)
            }
    else:
        return {
            "version": 3,           # v3 of the sourcemap protocol
            "sourceRoot": "/",      # an absolute url on kake-server
            "sources": [],
            "names": [],
            "mappings": ';'.join(mappings)
            }


class IndexSourcemap(object):
    """An 'index' sourcemap made of a bunch of individual sourcemaps."""
    def __init__(self, outfile_name):
        """outfile_name is the 'combined' file that this sourcemap is for."""
        self.sourcemap = {
            "version": 3,           # v3 of the sourcemap protocol
            "file": outfile_name,
            "sections": [],         # will be appended to below
            }

        self.lineno = 0
        self.colno = 0

    def add_section(self, filename, file_contents):
        """Indicate we've appended file_contents to the combined file."""
        num_lines = _num_lines(file_contents)

        if filename is None:
            # This content was added by the combiner-function, it's
            # not part of any input file.  We can inline a very simple
            # identity sourcemap.
            self.sourcemap['sections'].append({
                'offset': {'line': self.lineno, 'column': self.colno},
                'map': _identity_sourcemap(None, file_contents)
                })
        elif os.path.exists(project_root.join(filename + '.map')):
            # In theory, we could just reference the existing sourcemap
            # using the 'url' field.  But this isn't working on chrome 31.
            # Instead, we inline.  TODO(csilvers): figure out what's failing.
            with open(project_root.join(filename + '.map')) as f:
                self.sourcemap['sections'].append({
                    'offset': {'line': self.lineno, 'column': self.colno},
                    'map': json.load(f),
                    })
        else:
            # We will just use an identity sourcemap.
            self.sourcemap['sections'].append({
                'offset': {'line': self.lineno, 'column': self.colno},
                'map': _identity_sourcemap(filename, file_contents),
                })

        # Now update lineno and colno
        if file_contents.endswith('\n'):    # common case
            self.lineno += num_lines
            self.colno = 0
        elif num_lines == 1:
            self.colno += len(file_contents)
        else:
            self.lineno += num_lines - 1
            self.colno = len(file_contents) - (file_contents.rfind('\n') + 1)

    def to_json(self):
        """Sourcemaps are represented as json files."""
        # indent + sort_keys are to make the output more human-readable.
        return json.dumps(self.sourcemap, indent=2, sort_keys=True)
