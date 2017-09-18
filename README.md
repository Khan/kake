kake
====

> :speaker: A `make` library in Python.

Kake is a library that can track and update dependencies, like the
unix make tool and its various descendants.  But unlike `make` and
other tools, kake is designed as a library, and not a commandline
tool.  Kake is "make as hot-loader".

Kake is written in Python, and is thus (in library form, though see
below) only usable from Python apps.  Your app defines a set of
dependency rules using some simple Python code, and then you it can
execute code like tihs:
```python
import kake.make
kake.make.build('genfiles/compressed_javascript/foo/main.js')
```
which will rebuild that file if needed, or be a noop otherwise.  At
that point, your webserver can either load that file or just serve a
reference to it in its html.

Kake can also be used as a dependency server, where you talk HTTP to a
kake server asking it to rebuild a file if necessary.

Finally, it can be used as a normal commandline tool, as a replacement
for `make` and similar tools.

## Motivation

The original motivation for kake was this situation: Khan Academy
developers would change a `.less` file, say, and want to see the
effect on our app by using our development webserver.  The workflow
was: change the file, type `make`, reload the webapge.  We wanted to
automate the rebuilding so you could just change the file and reload
the webpage.

It was easy to do this by having our development webserver call out to
`make` every time you loaded a page.  But this was slow: you have to
pay the cost to fork a new binary, and then `make` had to process its
configuration file listing all the dependencies -- and for Khan
Academy there are a lot -- and then it had to collect last-modified
times so it could decide what to rebuild.  It gave us a multi-second
pause on every page.

Instead, what we needed was a dependency *library* that was part of
our development webserver.  It would just load the dependency tree
once, when the dev webserver started.  It could cache some of the
last-modified times.  And it could respond to rebuild requests
instantly, without needing to fork a new process.

Kake was built to fill this need.  Our development webserver liberally
uses `kake.make.build` to build any artifacts it needs before
consuming or serving them.  As a result, our development webserver
environment closely mirrors our production webserver.  All this
happens without any need for manual intervention.

### Background

There are many many tools out there that wish to manage dependencies.
There are even many of them written in Python.  But it's rare to find
a dependency system written to be used as a library.  While it's
possible `waf` or `SCons` could be used as a library, for instance,
it's clear reading the source that the authors did not anticipate that
use-case, and neither project is written in a way that would be easy
(if it's even possible).


## Usage

### Library mode

To use kake as a library, first you need to specify your
dependencies.  This process is described in more detail below, but
here is a simple example:

```python
"""A simple compile-rule to construct a calculator javascript widget.

The calculator is constructed using yacc rules, and compiled with jison.
We do that compilation here, and also add a footer.
"""
from kake.lib import compile_rule

class CompileCalculator(compile_rule.CompileBase):
    def version(self):
        """Update every time build() changes in a way that affects output."""
        return 1

    def build(self, outfile_name, infile_names, _, context):
        assert infile_names[0].endswith('.jison'), infile_names
        compiler = infile_names[1]
        self.call(['node', compiler, '-m', 'js',
                   self.abspath(infile_names[0]),
                   '-o', self.abspath(outfile_name)])
        # The rest of the infile-names are just appended to the outfile.
        with open(self.abspath(outfile_name), 'a') as f:
            for copy_from in infile_names[2:]:
                with open(self.abspath(copy_from)) as f2:
                    f.write(f2.read())

compile_rule.register_compile(
    'COMPILED CALCULATOR',
    'genfiles/widgets/en/calculator.js',
    ['javascript/widget-package/calculator.jison',
     '/bin/jison',
     'javascript/widget-package/calculator.js-tail'],
    CompileCalculator(),
)
```

`register_compile()` adds a dependency rule to kake.  You specify an
arbitrary label (used for debugging), the output filename, the input
filenames, and the class that's responsible for the actual building.
That class just needs to define a `build()` method that does the
actual work.  `build()` has access to several utility functions, such
as `self.call()` to call out to a subprocess.

Note that the output file starts with `genfiles/`.  This is one of the
rules of kake: all generated files must live in a particular directory
under your project-root.  No checked-in files should live there.

Once you have a rule like that you need to import it in your
`kake/make.py` file, so `kake` knows about it:
```python
import kake_rules.compile_calculator    # @UnusedImport

import kake.make_template

CompileFailure = kake.make_template.CompileFailure
BadRequestFailure = kake.make_template.BadRequestFailure

build = kake.make_template.build
build_many = kake.make_template.build_many
```

Then your webserver app can have code like this:
```python
@flask.route('/render_calculator')
def render_calculator():
    # In production, the calculator is pre-built, but on dev it's
    # built on demand
    if IS_DEVELOPMENT_WEBSERVER:
        import kake.make
        kake.make.build('genfiles/widgets/en/calculator.js')
    return ('<title>Happy calculator</title>'
            '<iframe src="/genfiles/widgets/en/calculator.js">')
```

### Server mode

To use kake as a server, just run `kake/server.py`.  Then you can send
it HTTP requests like this:
```
curl http://localhost:5000/genfiles/widgets/en/calculator.js
```
It will return the contents of `calculator.js` as a string, rebuilding
it if necessary.  The server respects the `If-modified-since` and
`If-none-match` headers.  You can also send a `HEAD` request if you
don't need the full contents, but just want to force the file to be
rebuilt on disk.

### Commandline mode

You can run:
```
kake/kake.py genfiles/widgets/en/calculator.js
```
and it will rebuild `calculator.js` if needed.

You can add "fake" rules such as `kake.py test` or `kake.py
build_js`.  Like the compile-rules, these rules are written in
Python.


## Main library API

### register_compile

TODO

#### Matching rules

TODO

### CompileBase

TODO

### ComputedInputsBase

TODO (including `version()` and `context`)

### build()

TODO

### build_many()

TODO


## Advanced library API

### ComputedIncludeInputs

TODO

### CachedFile

TODO

### create_symlink

TODO
