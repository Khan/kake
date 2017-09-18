"""A simple compile-rule to construct a calculator widget for exercises.

The calculator is constructed using yacc rules, and compiled with jison.
We have to do that compilation here, and also add a footer.
"""

from __future__ import absolute_import

from kake.lib import compile_rule


class CompileCalculator(compile_rule.CompileBase):
    """Compiles a calculator widget for exercises."""
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
    'genfiles/khan-exercises/en/calculator.js',
    ['javascript/exercises-legacy-package/calculator.jison',
     'genfiles/node_modules/.bin/jison',
     'javascript/exercises-legacy-package/calculator.js-tail'],
    CompileCalculator(),
)
