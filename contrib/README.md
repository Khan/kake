kake examples
=============

These are all kake rules that we use at Khan Academy.  They show off
uses of kake, from the simple to the complex.

They won't run on their own, since they depend on other Khan Academy
infrastructure that is not part of this project.  But they should give
a good example how to use kake.  I also include some test files, to
given an example of how to test kake code.

### simple

compile_calculator.py -- uses `call()`
compile_autoprefixed_css.py -- uses `{{globs}}`
compile_es6.py -- uses `build_many()`
compile_jinja2.py -- uses `**`, shows late imports
compile_less.py -- uses `ComputedIncludeInputs`
compile_npm.py -- uses `split_outputs()`, `compute_crc`
compile_perseus_hash.py -- uses `log()`
compile_zip.py -- a generic compile rule
compress_js.py -- uses `maybe_symlink_to`
sizeof_js_css_packages.py -- uses `_input_map`
translate_css.py -- uses `CreateSymlink`, `maybe_symlink_to`

### medium complexity

compile_handlebars.py -- `non_input_deps`, empty deps, ComputedInputs
compile_js_bundles.py -- `used_context_keys`, ComputedInputs
compile_js_dep_graph.py -- overridding `input_patterns`
compile_po_files.py -- complex filename munging, conditional rules
compile_topic_icons.py -- using a build manifest
translate_javascript.py -- `regsiter_translatesafe_compile()`

### most complexity

compress_css.py -- `CachedFile`, complex dependencies
