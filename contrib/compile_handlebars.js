/* TODO(csilvers): fix these lint errors (http://eslint.org/docs/rules): */
/* eslint-disable camelcase, no-console, no-var, prefer-spread */
/* To fix, remove an entry above, run "make linc", and fix errors. */

/**
 * Script to compile a bunch of handlebars templates.
 *
 * Run as:
 *
 *     node deploy/compile_handlebars.js < <json>
 *
 * where <json> is the JSON encoding of a list with an list for each template
 * that needs to be compiled -- the inner list should have three elements:
 *
 *     0. in abspath ('/absolute/path/to/hover-card.handlebars')
 *     1. out abspath ('/absolute/path/to/hover-card.handlebars.js')
 *
 * Thus a call looks something like:
 *
 *     echo '[["/absolute/path/to/hover-card.handlebars", ...], ...]' \
 *         | node deploy/compile_handlebars.js
 *
 * Based loosely on
 * https://github.com/wycats/handlebars.js/blob/master/bin/handlebars
 */

var fs = require("fs");

var handlebars = require("handlebars");

function compileTemplate(inPath, outPath) {
    try {
        var data = fs.readFileSync(inPath, "utf8");

        // Wrap the output of all Handlebars templates with the
        // makeHtmlLinksSafe helper, to ensure that any absolute URLs will be
        // rewritten for zero-rated users.
        var js = (
            "var absoluteLinks = (" +
            "    require('../shared-package/absolute-links.js'));" +
            "var template = Handlebars.template(" +
            handlebars.precompile(data, {}) +
            ");\n" +
            "function wrapped_template(context, options) {" +
            "    return absoluteLinks.makeHtmlLinksSafe(" +
            "        template(context, options));" +
            "};" +
            "module.exports = wrapped_template;\n");

        fs.writeFileSync(outPath, js, "utf8");
    } catch (err) {
        console.log("** Exception while compiling: " + inPath);
        console.log(err);
        throw err;
    }
}


// Read a json file saying what to do, from stdin.  The json
// should look like
//    [[input_filename, output_filename], ...]
var dataText = "";
process.stdin.resume();
process.stdin.on("data", function(chunk) {
    dataText = dataText + chunk;
});
process.stdin.on("end", function() {
    var data = JSON.parse(dataText);
    data.forEach(function(tmpl_info) {
        compileTemplate.apply(null, tmpl_info);
    });
});
