"""Test filemod_db.py."""
from __future__ import absolute_import

import os

from kake import filemod_db
import testutil


class FilemodDbBase(testutil.KakeTestBase):
    def setUp(self):
        super(FilemodDbBase, self).setUp()
        self._create_files()

    def tearDown(self):
        super(FilemodDbBase, self).tearDown()

    def _create_files(self, outnames=None):
        """If outnames is None, create them all."""
        # The filesystem is:
        #   i1-4: input files
        #   o1-2: output files
        #   l1-4: symlinks to i1-4
        #   l11-22: symlinks to l1-2  (symlink chain of length 2)
        for (i, filename) in enumerate(('i1', 'i2', 'i3', 'i4', 'o1', 'o2')):
            if outnames is None or filename in outnames:
                with open(os.path.join(self.tmpdir, filename), 'w') as f:
                    # Make sure each filename has a different size.
                    f.write(filename * (i + 1))
        for filename in ('l1', 'l2', 'l3', 'l4'):
            if outnames is None or filename in outnames:
                os.symlink('i' + filename[1],
                           os.path.join(self.tmpdir, filename))
        for filename in ('l11', 'l22'):
            if outnames is None or filename in outnames:
                os.symlink(filename[:2], os.path.join(self.tmpdir, filename))

        if outnames is None or 'third_party/werkzeug' in outnames:
            os.makedirs(os.path.join(self.tmpdir, 'third_party',
                                     'werkzeug-src', 'werkzeug'))
            os.symlink(os.path.join('werkzeug-src', 'werkzeug'),
                       os.path.join(self.tmpdir, 'third_party', 'werkzeug'))

    def _change_mtime(self, relpath):
        """Modify the mtime of relpath to something different."""
        filename = self._abspath(relpath)
        # You can't change the mtime of a symlink.  If you want
        # to change the mtime of where it points to, you can do
        #   self._change_mtime(os.path.realpath(my_symlink))
        assert not os.path.islink(filename), filename
        mtime = os.path.getmtime(filename)
        os.utime(filename, (mtime - 1, mtime - 1))

    def _change_size(self, relpath):
        """Modify the size of relpath, leaving mtime alone."""
        filename = self._abspath(relpath)
        mtime = os.path.getmtime(filename)
        with open(filename, 'a') as f:
            f.write('moar content!')
        os.utime(filename, (mtime, mtime))

    def _change_dest(self, relpath, newdest):
        """Modify where the symlink relpath points to."""
        filename = self._abspath(relpath)
        assert os.path.islink(filename)
        os.unlink(filename)
        os.symlink(newdest, filename)

    def _changed_files(self, outfile_name, *infile_names, **kwargs):
        kwargs.setdefault('context', 'test')
        return filemod_db.changed_files(outfile_name, *infile_names, **kwargs)

    def _add_to_db(self, outfile_name, *infile_names, **kwargs):
        """Adds info about outfile_name and infile_names to filemod-db."""
        _ = self._changed_files(outfile_name, *infile_names, **kwargs)
        self._create_files([outfile_name])     # because it's 'changed'!
        filemod_db.set_up_to_date(outfile_name)
        # Now we'll simulate restarting the server, so all state in
        # filemod_db gets erased.
        filemod_db.sync()
        filemod_db.reset_for_tests()


class FilemodDbTest(FilemodDbBase):
    def test_outfile_not_in_filemod_db(self):
        actual = self._changed_files('o1', 'i1', 'i2')
        # When the outfile has changed on us (or is new), it's the only retval.
        expected = set(['o1'])
        self.assertEqual(expected, actual)

    def test_outfile_does_not_exist(self):
        # Create the test-db
        self._add_to_db('o1', 'i1', 'i2')
        # We should say o0 has changed, since it doesn't exist.
        actual = self._changed_files('o0', 'i1', 'i2')
        expected = set(['o0'])
        self.assertEqual(expected, actual)
        filemod_db.set_up_to_date('o0')

        # We should still say it's changed, even though there's now
        # old filemod-db info (in particular, a negative cache entry).
        actual = self._changed_files('o0', 'i1', 'i2')
        expected = set(['o0'])
        self.assertEqual(expected, actual)

    def test_outfile_is_removed(self):
        # Create the test-db
        self._add_to_db('o1', 'i1', 'i2')
        # Now delete o1, and one of the input files too.
        os.unlink(self._abspath('o1'))
        os.unlink(self._abspath('i1'))
        # Now we should say o1, and only o1, has changed.
        actual = self._changed_files('o1', 'i1', 'i2')
        expected = set(['o1'])
        self.assertEqual(expected, actual)

    def test_unchanged_outfile(self):
        self._add_to_db('o1', 'i1', 'i2')
        actual = self._changed_files('o1', 'i1', 'i2')
        self.assertEqual(set(), actual)

    def test_changed_outfile_mtime(self):
        self._add_to_db('o1', 'i1', 'i2')
        self._change_mtime('o1')
        actual = self._changed_files('o1', 'i1', 'i2')
        expected = set(['o1'])
        self.assertEqual(expected, actual)

    def test_changed_outfile_size(self):
        self._add_to_db('o1', 'i1', 'i2')
        self._change_size('o1')
        actual = self._changed_files('o1', 'i1', 'i2')
        expected = set(['o1'])
        self.assertEqual(expected, actual)

    def test_changed_infile(self):
        self._add_to_db('o1', 'i1', 'i2', 'i3')
        self._change_size('i1')
        self._change_mtime('i2')
        actual = self._changed_files('o1', 'i1', 'i2', 'i3')
        expected = set(['i1', 'i2'])
        self.assertEqual(expected, actual)

    def test_symlink_replaced_by_what_it_points_to(self):
        self._add_to_db('o1', 'l1')
        actual = self._changed_files('o1', 'i1')
        self.assertEqual(set(), actual)

    def test_symlink_replaced_by_another_file(self):
        self._add_to_db('o1', 'l1')
        actual = self._changed_files('o1', 'i2')
        # l1 used to point to i1, so we also mark that i1 is no longer a dep.
        expected = set(['i1', 'i2'])
        self.assertEqual(expected, actual)

    def test_changed_symlink(self):
        self._add_to_db('o1', 'l1')
        self._change_dest('l1', 'i2')
        actual = self._changed_files('o1', 'l1')
        # l1 used to point to i1, so we also mark that i1 is no longer a dep.
        expected = set(['i1', 'l1'])
        self.assertEqual(expected, actual)

    def test_changed_symlink_target(self):
        self._add_to_db('o1', 'l1')
        self._change_mtime('i1')
        actual = self._changed_files('o1', 'l1')
        expected = set(['l1'])
        self.assertEqual(expected, actual)

    def test_changed_double_symlink(self):
        self._add_to_db('o1', 'l11')
        self._change_dest('l11', 'l2')
        actual = self._changed_files('o1', 'l11')
        # l11 used to point to i1, so we also mark that i1 is no longer a dep.
        expected = set(['i1', 'l11'])
        self.assertEqual(expected, actual)

    def test_changed_double_symlink_target1(self):
        self._add_to_db('o1', 'l11')
        self._change_dest('l1', 'i2')
        actual = self._changed_files('o1', 'l11')
        # l11 used to point to i1, so we also mark that i1 is no longer a dep.
        expected = set(['i1', 'l11'])
        self.assertEqual(expected, actual)

    def test_changed_double_symlink_target2(self):
        self._add_to_db('o1', 'l11')
        self._change_mtime('i1')
        actual = self._changed_files('o1', 'l11')
        expected = set(['l11'])
        self.assertEqual(expected, actual)

    def test_changed_outfile_symlink(self):
        self._add_to_db('l11', 'i2')
        self._change_dest('l11', 'i3')
        actual = self._changed_files('l11', 'i2')
        expected = set(['l11'])
        self.assertEqual(expected, actual)

    def test_changed_outfile_symlink_target(self):
        self._add_to_db('l11', 'i2')
        self._change_dest('l1', 'i3')
        actual = self._changed_files('l11', 'i2')
        expected = set(['l11'])
        self.assertEqual(expected, actual)

    def test_changed_outfile_symlink_target2(self):
        self._add_to_db('l11', 'i2')
        self._change_mtime('i1')
        actual = self._changed_files('l11', 'i2')
        expected = set(['l11'])
        self.assertEqual(expected, actual)

    def test_changed_outfile_symlink_to_file(self):
        self._add_to_db('l11', 'i2')
        os.unlink(self._abspath('l11'))
        with open(self._abspath('l11'), 'w') as f:
            f.write("i'm a real boy!")
        actual = self._changed_files('l11', 'i2')
        expected = set(['l11'])
        self.assertEqual(expected, actual)

    def test_added_infile(self):
        self._add_to_db('o1', 'i1')
        actual = self._changed_files('o1', 'i1', 'i2')
        expected = set(['i2'])
        self.assertEqual(expected, actual)

    def test_removed_infile(self):
        self._add_to_db('o1', 'i1', 'i2')
        actual = self._changed_files('o1', 'i1')
        expected = set(['i2'])
        self.assertEqual(expected, actual)

    def test_changed_outfile_as_infile(self):
        """Test when the outfile for one process is the infile for the next."""
        self._add_to_db('o1', 'i1')
        self._add_to_db('o2', 'o1')

        # Now we're going to change 'o1', and it should tell us that
        # 'o2' needs to change.  Before changing 'o1' though, 'o2' is
        # happy:
        self.assertEqual(set(), self._changed_files('o2', 'o1'))

        self._changed_files('o1', 'i1', force=True)
        with open(self._abspath('o1'), 'a') as f:
            f.write('changed!')
        filemod_db.set_up_to_date('o1')

        actual = self._changed_files('o2', 'o1')
        expected = set(['o1'])
        self.assertEqual(expected, actual)

    def test_changed_files_destroys_symlinks(self):
        self._add_to_db('o1', 'i2', 'i3')

        self.assertEqual(set(['o_link']),
                         filemod_db.changed_files('o_link', 'i2', 'i3',
                                                  context='test'))
        os.symlink('o1', self._abspath('o_link'))
        self.assertTrue(os.path.islink(self._abspath('o_link')))
        self.assertTrue(os.path.samefile(self._abspath('o1'),
                                         self._abspath('o_link')))
        filemod_db.set_up_to_date('o_link')

        self._change_mtime('i2')
        del filemod_db._CURRENT_FILE_INFO['i2']
        actual = self._changed_files('o_link', 'i2', 'i3')
        self.assertEqual(set(['i2']), actual)
        filemod_db.set_up_to_date('o_link')
        self.assertFalse(os.path.islink(self._abspath('o_link')))

    def test_force_flag_needed(self):
        # When we call changed_files and it says nothing has changed,
        # it's an error to then call set_up_to_date().
        self._add_to_db('o1', 'i1')
        actual = self._changed_files('o1', 'i1')
        self.assertEqual(set(), actual)
        self.assertRaises(KeyError, filemod_db.set_up_to_date, 'o1')

    def test_force_flag_present(self):
        # When we call changed_files with force=True, and it says
        # nothing has changed, it's ok to then call set_up_to_date().
        self._add_to_db('o1', 'i1')
        actual = self._changed_files('o1', 'i1', force=True)
        self.assertEqual(set(['o1']), actual)
        filemod_db.set_up_to_date('o1')

    def test_crc_trumps_mtime(self):
        self._add_to_db('o1', 'i2', compute_crc=True)

        self._change_mtime('i2')
        actual = self._changed_files('o1', 'i2', compute_crc=True)
        self.assertEqual(set(), actual)

        # This works even if we clear the caches first.
        filemod_db.reset_for_tests()
        actual = self._changed_files('o1', 'i2', compute_crc=True)
        self.assertEqual(set(), actual)

        # But if you don't include compute_crc the second time, we'll
        # fail because the mtimes differ.  (We need to clear caches
        # for this to work, since crcs are in the cache.)
        filemod_db.reset_for_tests()
        actual = self._changed_files('o1', 'i2')
        expected = set(['i2'])
        self.assertEqual(expected, actual)

    def test_crc_is_stored_properly(self):
        self._add_to_db('o1', 'i2', compute_crc=True)

        self._change_mtime('i2')
        self._change_size('o1')
        actual = self._changed_files('o1', 'i2', compute_crc=True)
        expected = set(['o1'])
        self.assertEqual(expected, actual)
        filemod_db.set_up_to_date('o1')

        # Even though we changed mtime, we're ok because the crc is stored.
        self._change_mtime('o1')
        actual = self._changed_files('o1', 'i2', compute_crc=True)
        self.assertEqual(set(), actual)

        # This is true even if we get rid of caches, and fall back on the db.
        filemod_db.reset_for_tests()
        actual = self._changed_files('o1', 'i2', compute_crc=True)
        self.assertEqual(set(), actual)

        # Again, if we don't include compute_crc, the test fails.
        filemod_db.reset_for_tests()
        actual = self._changed_files('o1', 'i2')
        expected = set(['o1'])
        self.assertEqual(expected, actual)

    def test_crc_with_bust_cache(self):
        # We'll have two versions of the file, created so close
        # together they have the same mtime and size, but should have
        # different crc's.
        with open(self._abspath('bc'), 'w') as f:
            f.write('2 words')
        file_info_1 = filemod_db.get_file_info('bc',
                                               bust_cache=True,
                                               compute_crc=True)
        self.assertNotEqual(None, file_info_1[0])

        with open(self._abspath('bc'), 'w') as f:
            f.write('3 words')
        file_info_2 = filemod_db.get_file_info('bc',
                                               bust_cache=True,
                                               compute_crc=True)
        self.assertNotEqual(file_info_1, file_info_2)

    def test_context(self):
        self._add_to_db('o1', 'i2', 'i3')
        self._add_to_db('o2', 'i2', 'i3', context='2')
        self._changed_files('o2', 'i2', 'i3', context='2')
        self.assertFalse(os.path.islink(self._abspath('o2')))

    def test_changed_context_shows_outfile_to_show_up_as_changed(self):
        self._add_to_db('o1', 'i2', 'i3')
        actual = self._changed_files('o1', 'i2', 'i3', context='2')
        self.assertEqual(set(['o1']), actual)


class FilemodClassTest(FilemodDbBase):
    def setUp(self):
        super(FilemodClassTest, self).setUp()
        self.db1 = filemod_db.FilemodDb(os.path.join(self.tmpdir, 'genfiles',
                                                     'db1.pickle'))
        self.db2 = filemod_db.FilemodDb(os.path.join(self.tmpdir, 'genfiles',
                                                     'db2.pickle'))

    def tearDown(self):
        # Force the db update to happen *before* the rmtree
        self.db1.sync()
        self.db2.sync()
        super(FilemodClassTest, self).tearDown()

    def _changed_files(self, db, outfile_name, *infile_names, **kwargs):
        kwargs.setdefault('context', 'test')
        return db.changed_files(outfile_name, *infile_names, **kwargs)

    def _add_to_db(self, db, outfile_name, *infile_names, **kwargs):
        """Adds info about outfile_name and infile_names to filemod-db."""
        _ = self._changed_files(outfile_name, *infile_names, **kwargs)
        self._create_files([outfile_name])     # because it's 'changed'!
        db.set_up_to_date(outfile_name)
        # Now we'll simulate restarting the server, so all state in
        # filemod_db gets erased.
        db.sync()
        filemod_db.reset_for_tests()

    def test_two_different_dbs(self):
        actual = self._changed_files(self.db1, 'o1', 'i1', 'i2')
        expected = set(['o1'])
        self.assertEqual(expected, actual)
        self.db1.set_up_to_date('o1')

        # db1 should see o1 as up to date, but db2 shouldn't...
        actual = self._changed_files(self.db1, 'o1', 'i1', 'i2')
        self.assertEqual(set([]), actual)
        actual = self._changed_files(self.db2, 'o1', 'i1', 'i2')
        self.assertEqual(expected, actual)

    def test_needs_update(self):
        self._change_mtime('i1')
        with self.db1.needs_update('o1', ['i1'], 'test') as c:
            if c:
                with open(self._abspath('o1'), 'w') as f:
                    f.write('updated!')
        with open(self._abspath('o1')) as f:
            self.assertEqual('updated!', f.read())


class ResolveSymlinksTest(FilemodDbBase):
    def test_resolve_symlinks(self):
        self.assertEqual('i1', filemod_db._resolve_symlinks('l1'))
        self.assertEqual('i1', filemod_db._resolve_symlinks('l11'))

    def test_not_a_symlinks(self):
        self.assertEqual('i1', filemod_db._resolve_symlinks('i1'))

    def test_symlink_in_a_directory_component(self):
        self.assertEqual(
            os.path.join('third_party', 'werkzeug-src', 'werkzeug', 'foo.js'),
            filemod_db._resolve_symlinks(
                os.path.join('third_party', 'werkzeug', 'foo.js')))

    def test_symlink_loop(self):
        os.symlink('loop', self._abspath('loop'))
        with self.assertRaises(OSError):
            filemod_db._resolve_symlinks('loop')

    def test_absolute_symlink(self):
        os.symlink('/etc/issue', self._abspath('abs'))
        with self.assertRaises(AssertionError):
            filemod_db._resolve_symlinks('abs')

    def test_absolute_symlink_in_directory_component(self):
        os.symlink('/etc', self._abspath('absdir'))
        with self.assertRaises(AssertionError):
            filemod_db._resolve_symlinks('absdir/issue')

    def test_symlink_outside_tree(self):
        os.symlink('../foo', self._abspath('outside'))
        with self.assertRaises(AssertionError):
            filemod_db._resolve_symlinks('outside')

    def test_symlink_outside_tree_in_directory_component(self):
        os.symlink('../foo', self._abspath('outside_dir'))
        with self.assertRaises(AssertionError):
            filemod_db._resolve_symlinks('outside_dir/bar')


class CanSymlinkToTest(FilemodDbBase):
    def _get_infomap(self, *files, **kwargs):
        context = kwargs.get('context', 'test')
        retval = {f: filemod_db.get_file_info(f) for f in files}
        retval['//context//'] = (context, None, None)
        return retval

    def test_same_input(self):
        self._add_to_db('o1', 'i1', 'i2')
        # Start the update-transaction, as needed to call can_symlink_to().
        filemod_db.changed_files('o2', 'i1', 'i2', context='test')
        self.assertTrue(filemod_db.can_symlink_to('o2', 'o1'))

    def test_different_input(self):
        self._add_to_db('o1', 'i1', 'i2', 'i3')
        filemod_db.changed_files('o2', 'i1', 'i2', context='test')
        self.assertFalse(filemod_db.can_symlink_to('o2', 'o1'))

    def test_infile_is_symlink(self):
        self._add_to_db('o1', 'l1', 'l22')
        filemod_db.changed_files('o2', 'i1', 'i2', context='test')
        self.assertTrue(filemod_db.can_symlink_to('o2', 'o1'))

    def test_symlink_to_self(self):
        self._add_to_db('o2', 'i1', 'i2')
        # We need to do this to force changed_files to return False.
        self._change_mtime('i1')
        filemod_db.changed_files('o2', 'i1', 'i2', context='test')
        self.assertFalse(filemod_db.can_symlink_to('o2', 'o2'))

    def test_outfile_has_changed(self):
        self._add_to_db('o1', 'i1', 'i2')
        filemod_db.changed_files('o2', 'i1', 'i2', context='test')
        self._change_mtime('o1')
        self.assertFalse(filemod_db.can_symlink_to('o2', 'o1'))

    def test_infile_has_changed(self):
        self._add_to_db('o1', 'i1', 'i2')
        self._change_mtime('i1')
        filemod_db.changed_files('o2', 'i1', 'i2', context='test')
        self.assertFalse(filemod_db.can_symlink_to('o2', 'o1'))

    def test_symlink_candidate_is_in_transaction(self):
        self._add_to_db('o1', 'i1', 'i2')
        filemod_db.changed_files('o2', 'i1', 'i2', context='test')
        self._change_mtime('o1')
        self.assertEqual(set(['o1']),
                         filemod_db.changed_files('o1', 'i1', 'i2'))
        self.assertFalse(filemod_db.can_symlink_to('o2', 'o1'))

    def test_symlinks_differ_in_whether_they_compute_crc(self):
        self._add_to_db('o1', 'i1', 'i2')
        # Start the update-transaction, as needed to call can_symlink_to().
        filemod_db.changed_files('o2', 'i1', 'i2', context='test',
                                 compute_crc=True)
        self.assertTrue(filemod_db.can_symlink_to('o2', 'o1'))


class UpdateIfNeededTest(FilemodDbBase):
    def _maybe_update(self, outfile_name, infile_names, **kwargs):
        kwargs.setdefault('context', 'test')
        with filemod_db.needs_update(outfile_name, infile_names,
                                     **kwargs) as c:
            if c:
                with open(self._abspath(outfile_name), 'w') as f:
                    f.write('updated!')

    def test_update(self):
        self._add_to_db('o1', 'i1')
        self._change_mtime('i1')
        self._maybe_update('o1', ['i1'])
        with open(self._abspath('o1')) as f:
            self.assertEqual('updated!', f.read())

    def test_no_update(self):
        self._add_to_db('o1', 'i1')
        self._maybe_update('o1', ['i1'])
        with open(self._abspath('o1')) as f:
            self.assertEqual('o1o1o1o1o1', f.read())

    def test_force_flag(self):
        self._add_to_db('o1', 'i1')
        self._maybe_update('o1', ['i1'], force=True)
        with open(self._abspath('o1')) as f:
            self.assertEqual('updated!', f.read())

    def test_compute_crc_flag(self):
        self._add_to_db('o1', 'i1', compute_crc=True)
        self._change_mtime('i1')
        filemod_db.clear_mtime_cache()

        self._maybe_update('o1', ['i1'], compute_crc=True)
        with open(self._abspath('o1')) as f:
            self.assertEqual('o1o1o1o1o1', f.read())


if __name__ == '__main__':
    testutil.main()
