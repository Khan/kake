"""Tests for compile_util.py."""
from __future__ import absolute_import

import os

from kake import compile_util
import testutil


class TestExtendedFnmatchCompile(testutil.KakeTestBase):
    def _test(self, expected, pattern):
        self.assertEqual(
            expected, compile_util._extended_fnmatch_compile(pattern).pattern)

    def test_glob_patterns(self):
        self._test(r'hello[^/]*.world$', 'hello*?world')

    def test_re_escape(self):
        self._test(r'hello\.world$', 'hello.world')

    def test_brackets(self):
        self._test(r'h[ello]\,\ world$', 'h[ello], world')
        self._test(r'h[^ello]\,\ world$', 'h[!ello], world')
        self._test(r'h[]ello]\,\ world$', 'h[]ello], world')
        self._test(r'h[^]ello]\,\ world$', 'h[!]ello], world')
        self._test(r'h\[\!\]ello$', 'h[!]ello')

    def test_vars(self):
        self._test(r'h(?P<brace_ello>[^/]*)\,\ world$', 'h{ello}, world')
        self._test(r'h(?P<bracebrace_ello>.*)\,\ world$', 'h{{ello}}, world')

    def test_backrefs(self):
        self._test(r'h(?P<brace_ello>[^/]*)\,\ (?P=brace_ello)$',
                   'h{ello}, {ello}')
        self._test(r'h(?P<bracebrace_ello>.*)\,\ (?P=bracebrace_ello)$',
                   'h{{ello}}, {{ello}}')
        self._test(r'h(?P<brace_ello>[^/]*)\,\ (?P<bracebrace_ello>.*)$',
                   'h{ello}, {{ello}}')

    def test_starstar(self):
        self._test(r'hello\,((?!/\.).)*world$', 'hello,**world')


class TestResolvePatterns(testutil.KakeTestBase):
    def setUp(self):
        super(TestResolvePatterns, self).setUp()    # sets up self.tmpdir
        self._create_file('a.txt')
        self._create_file('.a.txt')
        self._create_file('dir1', 'a.txt')
        self._create_file('dir1', 'dir2', 'a.txt')
        self._create_file('dir1', 'dir2', 'a.py')
        self._create_file('dir1', 'dir2', '.a.txt')
        self._create_file('dir1', 'dir2', '.a.py')
        self._create_file('dir1', 'dir2', 'dir3', 'README')

    def _create_file(self, *dirparts):
        dirparts = [self.tmpdir] + list(dirparts)   # make absolute :-)
        filename = os.path.join(*dirparts)
        dirname = os.path.dirname(filename)
        if not os.path.isdir(dirname):
            os.makedirs(dirname)
        open(filename, 'w').close()

    def _test(self, expected, glob_pattern, var_values={}):
        # extended_glob() requires everything to be absolute, so convert.
        self.assertItemsEqual(
            expected,
            compile_util.resolve_patterns([glob_pattern], var_values))

    def test_normal_glob(self):
        self._test(['a.txt'], '*.txt')
        self._test(['dir1/a.txt'], 'dir1/*.txt')
        self._test(['dir1/dir2/a.txt', 'dir1/dir2/a.py'], 'dir1/dir2/*.*')

    def test_just_starstar(self):
        self._test(['a.txt', 'dir1/a.txt', 'dir1/dir2/a.txt',
                    'dir1/dir2/a.py', 'dir1/dir2/dir3/README',
                    'dir1', 'dir1/dir2', 'dir1/dir2/dir3',
                    # These files are created by the superclass setUp().
                    'genfiles', 'package.json', 'app.yaml'],
                   '**')

    def test_starting_starstar(self):
        self._test(['a.txt', 'dir1/a.txt', 'dir1/dir2/a.txt'], '**.txt')

    def test_middle_starstar(self):
        self._test(['dir1/a.txt', 'dir1/dir2/a.txt'], 'dir1/**.txt')

    def test_dir_surrounded_starstar(self):
        self._test(['dir1/dir2/a.txt', 'dir1/dir2/a.py',
                    'dir1/dir2/dir3/README'],
                   'dir1/**/[aR]*')

    def test_filename_ending_with_starstar(self):
        self._test(['dir1/dir2/a.txt', 'dir1/dir2/a.py'], 'dir1/dir2/a**')

    def test_starstar_ending_with_slash(self):
        self._test(['dir1/a.txt', 'dir1/dir2/a.txt'], 'dir1/**a.txt')

    def test_normal_glob_with_starstar(self):
        self._test(['dir1/dir2/a.txt'], 'dir1/**/*.txt')

    def test_dotfiles(self):
        self._test([], 'dir1/dir2/?a.py')
        self._test(['dir1/dir2/a.py'], 'dir1/dir2/*a.py')
        self._test(['dir1/dir2/a.py'], 'dir1/dir2/**.py')
        self._test(['dir1/dir2/a.py'], 'dir1/**.py')
        self._test([], 'dir1/dir/[!-~]a.py')

        # Test at the beginning of the string as well.
        self._test([], '?a.txt')
        self._test(['a.txt'], '*a.txt')
        self._test(['a.txt', 'dir1/a.txt', 'dir1/dir2/a.txt'], '**.txt')
        self._test([], '[!-~]a.txt')

    def test_backreference(self):
        self._create_file('dir1', 'dir_same', 'dir_same', 'README')
        self._create_file('dir1', 'dir_same', 'dir_different', 'README')

        self._test(['dir1/dir_same/dir_same/README'],
                   'dir1/{subdir}/{subdir}/README',
                   {'{subdir}': 'dir_same'})
        self._test(['dir1/dir_same/dir_same/README'],
                   'dir1/{{subdir}}/{{subdir}}/README',
                   {'{{subdir}}': 'dir_same'})


class TestCachedFile(testutil.KakeTestBase):
    def test_put_and_get_same_cached_file(self):
        a = compile_util.CachedFile('cache.pickle')
        a.put({1: 2, 3: 4, 5: 6})
        actual = a.get()
        self.assertEqual({1: 2, 3: 4, 5: 6}, actual)

    def test_put_and_get_different_cached_files(self):
        a = compile_util.CachedFile('cache.pickle')
        a.put({1: 2, 3: 4, 5: 6})

        b = compile_util.CachedFile('cache.pickle')
        actual = b.get()
        self.assertEqual({1: 2, 3: 4, 5: 6}, actual)

    def test_get_after_modification(self):
        a = compile_util.CachedFile('cache.pickle')
        a.put({1: 2, 3: 4, 5: 6})

        b = compile_util.CachedFile('cache.pickle')
        actual1 = b.get()

        a.put({'a': 'b', 'c': 'd', 'e': 'f'})

        actual2 = b.get()

        self.assertEqual({1: 2, 3: 4, 5: 6}, actual1)
        self.assertEqual({'a': 'b', 'c': 'd', 'e': 'f'}, actual2)


if __name__ == '__main__':
    testutil.main()
