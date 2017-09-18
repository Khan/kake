/**
 * This file is used by compile_es6.py and compile_jsx.py build rules.  It's
 * used to compile ES6/JSX code into ES5 code.
 *
 * Usage:
 *  node compile_js.js [options] < input_output_paths.json
 *
 * The input_output_paths.json should contain an array of tuples with absolute
 * input and output paths, e.g.
 * [[input_path_1, output_path_1], [input_path_2, output_path_2], ...]
 */
"use strict";

const fs = require('fs');
const babel = require('babel-core');

function compile(inPath, outPath) {
    try {
        const outCode = babel.transformFileSync(inPath, {
            presets: [
                // NOTE(jeresig): We could change this to `modules: "commonjs"`
                // however that strips usage of top-level `this`, which we
                // still use. Thus we use the commonjs transform below and
                // explicitly allow top-level `this`.
                ['es2015', {modules: false, loose: true}],
                'stage-2',
                'react',
            ],
            plugins: [
                'flow-react-proptypes',
                'transform-flow-strip-types',
                'i18n-babel-plugin',
                'external-helpers',
                'syntax-trailing-function-commas',
                ['transform-es2015-modules-commonjs', {
                    'allowTopLevelThis': true,
                    'strict': false,
                }],
            ],
            retainLines: true,
        }).code;
        fs.writeFileSync(outPath, outCode, 'utf8');
    } catch (err) {
        console.log('** Exception while compiling: ' + inPath); //@Nolint
        console.log(err); //@Nolint
        throw err;
    }
}

// Read a json file saying what to do, from stdin.  The json
// should look like
//    [[input_filename, output_filename], ...]
let dataText = '';
process.stdin.on('data', function(chunk) {
    dataText = dataText + chunk;
});
process.stdin.on('end', function() {
    const data = JSON.parse(dataText);
    data.forEach(function(paths) {
        const inPath = paths[0];
        const outPath = paths[1];
        compile(inPath, outPath);
    });
});
