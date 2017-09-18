"""A Compile object (see compile_rule.py): writes .js/.css package sizes as CSV

When working to improve client-side performance, reducing the number of
bytes sent over the network is important. To inform reduction of JS and
CSS package sizes it is useful to know the per-subfile breakdown of
bytes in a JS or CSS package.

This rule generates a CSV output of the bytes size of a package and its
constituent parts. For the homepage package CSS, the following is the
contents of genfiles/sizeof/homepage-package.css.csv:

  type,bytes,file
  PACKAGE,1888383,genfiles/compressed_css_packages_prod/en/homepage-package.css
  INPUT,1838896,genfiles/compressed_stylesheets/en/stylesheets/homepage-package/homepage.less.min.css  #@Nolint
  INPUT,28434,genfiles/compressed_stylesheets/en/stylesheets/stories-package/stories.min.css  #@Nolint
  INPUT,8125,genfiles/compressed_stylesheets/en/stylesheets/shared-package/jquery.qtip.min.css  #@Nolint
  INPUT,4671,genfiles/compressed_stylesheets/en/stylesheets/shared-package/default.min.css  #@Nolint
  INPUT,2144,genfiles/compressed_stylesheets/en/stylesheets/shared-package/base-badges.min.css  #@Nolint
  INPUT,1968,genfiles/compressed_stylesheets/en/stylesheets/shared-package/ka-autocomplete.min.css  #@Nolint
  INPUT,1740,genfiles/compressed_stylesheets/en/stylesheets/shared-package/nnw-thumbnails.min.css  #@Nolint
  INPUT,1230,genfiles/compressed_stylesheets/en/stylesheets/shared-package/info-box.min.css  #@Nolint
  INPUT,773,genfiles/compressed_stylesheets/en/stylesheets/shared-package/reset.min.css  #@Nolint
  INPUT,393,genfiles/compressed_stylesheets/en/stylesheets/shared-package/proxima-nova.min.css  #@Nolint

"""

from __future__ import absolute_import

import csv
import os

from kake.lib import compile_rule


class SizePackage(compile_rule.CompileBase):
    def version(self):
        """Update every time build() changes in a way that affects output."""
        return 1

    def build(self, outfile_name, pkgfile_names, _, context):
        assert len(pkgfile_names) == 1, pkgfile_names
        pkgfile_name = pkgfile_names[0]
        suffix = '.' + context['{ext}']
        assert suffix in ('.js', '.css'), suffix

        # Only .js or .css files are concatenated to produce the final product.
        infile_names = [f for f in context['_input_map'][pkgfile_name]
                        if f.endswith(suffix)]

        sizeof = lambda filename: os.stat(filename).st_size
        rows = [('INPUT', sizeof(infile_name), infile_name)
                for infile_name in infile_names]

        # There may be a size increase when combining a package. For
        # instance, JS packages add wrapper code to prepare for
        # "KAdefine.require()" calls.
        package_bytes = sizeof(pkgfile_name)
        input_bytes = sum(row[1] for row in rows)
        extra_bytes = package_bytes - input_bytes

        with open(outfile_name, 'wb') as csvfile:
            outwriter = csv.writer(csvfile)
            outwriter.writerow(['type', 'bytes', 'file'])
            outwriter.writerow(['PACKAGE', package_bytes, pkgfile_name])
            if extra_bytes:
                outwriter.writerow(['OVERHEAD', extra_bytes,
                                    '<bytes added during packaging>'])
            outwriter.writerows(sorted(rows, reverse=True))


compile_rule.register_compile(
    'SIZEOF PACKAGES',
    'genfiles/sizeof/{pkg}-package.{ext}.csv',
    ['genfiles/compressed_{ext}_packages_prod/en/{pkg}-package.{ext}'],
    SizePackage())
