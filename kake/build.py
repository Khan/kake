from __future__ import absolute_import

import fcntl
import itertools
import multiprocessing
import os
import resource
import time

from . import compile_rule
from . import filemod_db
from . import log
from . import project_root


# for convenience:
CompileFailure = compile_rule.CompileFailure
BadRequestFailure = compile_rule.BadRequestFailure
NoBuildRuleCompileFailure = compile_rule.NoBuildRuleCompileFailure


class DependencyNode(object):
    """Information about a single output file and what is needed to build it.

    We store the compile-rule that compiles the output file from the
    inputs, and the context-dict that we want to be available when
    compiling this output-file.  We also store the input files and
    var-values: while these can be re-computed from the compile rule +
    output file, we might as well cache them.

    Most important, we store the compile rule's 'level' -- this is a
    simplification of where it would be in a dependency graph.
    Everything at level 0 depends only on static files.  Everything at
    level 1 depends only on static files and generated files in level
    0, and so forth.  We will then build files a level at a time.

    We assume that, before this is created, that all trigger files --
    needed for computing input files at runtime, which is now -- for
    this output file have been built.  (This is a non-issue until we
    allow generated files to serve as trigger files.)
    """
    # Save some memory since we'll have lots of these nodes.
    __slots__ = ('compile_rule', 'context', 'input_files',
                 'non_input_deps', 'level')

    def __init__(self, compile_rule, context, input_files, non_input_deps):
        # Note that the output file is not stored!  DependencyNode is
        # used as the values in a outfile_name -> DependencyNode map,
        # so the node doesn't need to store the output file itself.
        # This way, several output files can depend on the same node.
        # To save even more space, we intern the input-files, since
        # they may be repeated across a bunch of dep-rules, and also
        # the context keys (but not values, which probably differ).
        maybe_intern = lambda s: intern(s) if isinstance(s, str) else s

        self.compile_rule = compile_rule
        self.context = {maybe_intern(k): v for (k, v) in context.iteritems()}
        self.input_files = [maybe_intern(f) for f in input_files]
        self.non_input_deps = non_input_deps
        self.level = None


class DependencyGraph(object):
    """Information about what outfiles depend on other outfiles.

    The DependencyGraph is a graph of DependencyNode's.  We don't keep
    full graph information, just enough to get a partial order for the
    build.  In fact, it's just a map from output file to
    DependencyNode.  So: given an output file, you can see what input
    files depend on it, etc.
    """
    def __init__(self):
        self.deps = {}

    def _add_depnode(self, output_filename, dependency_node):
        """Add a DependencyNode to the dependency graph."""
        self.deps[output_filename] = dependency_node

    def _get(self, output_filename):
        """The DependencyNode for this filename, or None if not found."""
        return self.deps.get(output_filename, None)

    def iteritems(self):
        """Return a iterator over (output_filename, dependency_node) pairs."""
        return self.deps.iteritems()

    def items(self):
        """Return a list of all (output_filename, dependency_node) pairs."""
        return self.deps.items()

    def len(self):
        return len(self.deps)

    def add_file(self, output_filename, context, already_built, timing_map,
                 force, include_static_files=False):
        """Add output_filename and all its deps to the dependency_graph.

        This figures out all dependencies that output_filename has and adds
        those as well.  It also figures out what 'level' each dependency has
        (see the docstring for DependencyNode).

        Raises compile errors if it
        1) doesn't know how to build a dependency
        2) detects a dependency cycle (a depends on b depends on a)
        3) [CURRENTLY DISABLED:
           detects that we want to build the same file but with different
           contexts.  (That is, someone else depended on the same file that
           you did, but in a different context.)  It's an error to build
           the same file with a different context, since the build rule
           might depend on the context, but the output filename doesn't.
           (So the output wouldn't be consistent in that case.)]

        Arguments:
            output_filename: the filename of the file we want to compile,
              relative to ka-root
            context: an arbitrary dict that we will pass to all the
              dependencies when building them.
            already_built: a list of rules that have been already built
              during the dep-finding process (via _immediate_build).  This
              keeps us from calling _immediate_build on the same file a
              bunch of times.
            timing_map: a map from compile-rule label to
              how much time has been spent so far compiling files using
              that compile-rule.  We update this map in place with time
              spent building in this function.  (We sometimes build when
              needed to compute the input files for a rule.)
            force: force rebuilds (only needed for trigger files).
            include_static_files: include non-generated files in the
              dependency graph.  They are not useful and only take up
              space, so this is typically only set when debugging or
              print statistics and the like.

        Returns:
            The 'level' that output_filename should be at.
        """
        if not output_filename.startswith(compile_rule.GENDIR):
            # If it's a static file, then we don't even need to add it
            # to the graph.  We only do if the caller specifically
            # asked for it, and it's not already in the dep-graph.
            if include_static_files and not self._get(output_filename):
                depnode = DependencyNode(None, context, [], [])
                depnode.level = 0
                self._add_depnode(output_filename, depnode)
            # Static files always have level 0.
            return 0

        # If outfile is already in the dependency graph, then we're done.
        # This is also where we check for cycles: if it's in the
        # dependency graph but level is still None, it means that the
        # recursive call has run into a cycle.
        depnode = self._get(output_filename)
        if depnode is not None:
            if depnode.level is None:
                # TODO(csilvers): keep track of the compile-rules so we can
                #                 report the circular-dependency chain.
                raise CompileFailure("Circular dependencies for %s"
                                     % output_filename)
            if depnode.context != context:
                # This is a problem if the compile_rule actually uses the
                # context, since it would give different results each
                # time.  But it's very common for lots of files to depend
                # on a rule (like node_modules/.bin) that doesn't use the
                # context.  TODO(csilvers): warn, but only when problematic.
                if False:
                    raise CompileFailure(
                        "We want to compile %s with two different contexts: "
                        "%s and %s"
                        % (output_filename, depnode.context, context))
            return depnode.level

        # outfile isn't already in the dependency graph, so let's add it!
        cr = compile_rule.find_compile_rule(output_filename)
        if cr is None:
            raise NoBuildRuleCompileFailure(output_filename)
        log.v4('%s matches compile-rule "%s"', output_filename, cr.label)

        var_values = cr.var_values(output_filename)   # the {var}'s.

        # We create a new context here instead of updating the existing
        # one to avoid modifying the caller's context.
        outfile_context = context.copy()
        outfile_context.update(var_values)

        # If we have inputs that are computed at runtime, make sure that
        # the computed input's dependencies are already built.
        #
        # We do these one by one because trigger_files may not be able to able
        # to compute all of the trigger files without building some of them
        # first. This happens if a file imports a generated file which may in
        # turn have imports. In cases such as these, input_trigger_files will
        # return a generator.
        for trigger_file in cr.input_trigger_files(output_filename,
                                                   outfile_context):
            _immediate_build([trigger_file], context, output_filename,
                             already_built, timing_map, force)

        # Next, figure out what our dependencies are.  We have two types
        # of dependencies: input-files and non-input deps.  For our
        # purposes, we treat them the same (they differ when it comes to
        # deciding whether to regenerate output_file: a change in a
        # non-input dep doesn't require us to regenerate).
        # When inputs are computed, this can be slow, so we record timing.
        start_time = time.time()

        input_filenames = cr.input_files(output_filename, outfile_context,
                                         force)
        non_input_deps = cr.non_input_deps_files(output_filename, var_values)
        maybe_symlink_to = cr.maybe_symlink_to(output_filename, var_values)

        end_time = time.time()
        timing_map.setdefault(cr.label, 0)
        timing_map[cr.label] += end_time - start_time

        # Treat the file we may symlink to as a non-input dep.  (While we
        # don't technically depend on our maybe_symlink_to, it's a good
        # idea to build it before us so we have a chance of symlinking to
        # it.)  This does extra work if we didn't ask to build the
        # maybe_symlink_to target, but in practice that is pretty unlikely
        # to happen.
        # TODO(csilvers): get the best of both worlds and don't add
        # maybe_symlink_to unless it is also in the depgraph somewhere.
        if maybe_symlink_to and maybe_symlink_to != output_filename:
            non_input_deps.append(maybe_symlink_to)

        # OK, we have all the info we need (except the level, which we'll
        # do last), let's add ourselves to the dependency graph.
        depnode = DependencyNode(cr, outfile_context, input_filenames,
                                 non_input_deps)
        self._add_depnode(output_filename, depnode)

        # We depend on our inputs.  We also depend on our non_input_deps
        # (we promise in the API we make these before we make ourself).
        deps = set(input_filenames)
        deps.update(non_input_deps)

        # Recurse to add our dependencies to the dependency graph.  This
        # will also give us our level: its the highest of our deps'
        # levels, plus one.
        max_dep_level = 0
        for dep in deps:
            log.v4('Marking that %s depends on %s', output_filename, dep)
            dep_level = self.add_file(dep, context, already_built, timing_map,
                                      force, include_static_files)
            max_dep_level = max(max_dep_level, dep_level)

        depnode.level = max_dep_level + 1

        log.v3('Adding %s to dependency graph, level %s',
               output_filename, depnode.level)
        return depnode.level

    def emit_to_dot(self, outfile_name):
        """Emit the dependencies in the current build to a dot file.

        dot is a language for describing graphs:
           http://en.wikipedia.org/wiki/DOT_%28graph_description_language%29
        ('dot' is also a tool for compiling that language to pdf's.)
        Sadly, it's not practical to graph the full deps, with all the files
        -- there are just too many of them, and dot can't handle it --
        so instead we graph the 'rule deps': edges if the inputs to one
        compile-rule depends on the output of another.
        We write a dot file into 'genfiles/' with these graphs.

        Arguments:
            outfile_name: a filename relative to project_root.
        """
        # For every file, collect the rule that built it (by label).
        file_rules = {}
        for (output_filename, dependency_node) in self.iteritems():
            file_rules[output_filename] = dependency_node.compile_rule.label
        # We'll mark the terminal rules (those that are not an input to
        # another rule) specially.  We start by including all rules, then
        # marking them out as they're shown to be non-terminal.
        terminal_rules = set(r for r in file_rules.itervalues())

        rule_graph = {}
        # Now, the rule_graph is a list of edges, where rule X points to
        # rule Y if file Y is built via rule Y, and file Y has as an input
        # file X, which is built via rule X.  We weight each edge by how
        # many times we see that edge.
        for (output_filename, dependency_node) in self.iteritems():
            output_rule = file_rules[output_filename]
            for input_filename in dependency_node.input_files:
                input_rule = file_rules.get(input_filename)
                if input_rule:
                    rule_graph.setdefault((input_rule, output_rule), 0)
                    rule_graph[(input_rule, output_rule)] += 1
                    terminal_rules.discard(input_rule)   # not terminal!

        with open(project_root.join(outfile_name), 'w') as f:
            print >>f, '// TO VIEW THIS: install "dot" and run'
            print >>f, ('//   dot -Tpdf %s > /tmp/rule_deps.pdf'
                        % outfile_name)
            print >>f
            print >>f, 'digraph ruledeps {'
            for ((input_rule, output_rule), count) in rule_graph.iteritems():
                print >>f, ('    "%s" -> "%s" [label="%s" weight=%s];'
                            % (input_rule, output_rule, count, count))
            print >>f
            print >>f, '    { rank=same;'
            for rule in terminal_rules:
                print >> f, '     "%s" [shape=box];' % rule
            print >>f, '    }'
            print >>f, '}'
        log.v1('WROTE dependency graph to %s' % outfile_name)


def _deps_to_compile_together(dependency_graph):
    """Yield a chunk of (outfile, depnode) pairs.

    The rule is that we yield all the chunks at level 1 before any
    chunks at level 2, etc.  Each chunk holds only files with the same
    compile_instance.  The caller is still responsible for divvying up
    chunks based on compile_rule.num_outputs().
    """
    flattened_graph = dependency_graph.items()
    keyfn = lambda kv: (kv[1].level, kv[1].compile_rule.compile_instance)
    flattened_graph.sort(key=keyfn)
    for (_, chunk) in itertools.groupby(flattened_graph, keyfn):
        yield list(chunk)


def _subprocess_run_build(buildmany_arg):
    """Call compile_instance.build(bm_arg).  For use in multiprocessing.

    Returns the compile-rule used (by label), and how much time it
    took to build these files.
    """
    # buildmany_arg is a list of 4-tuples, appropriate for passing to
    # build_many().  But we need to figure out who to call
    # build_many() *on*...  Luckily, the API requirement for this
    # method is all outputs share the same compile-rule, so it's easy
    # to figure out.
    arbitrary_output_file = buildmany_arg[0][0]
    cr = compile_rule.find_compile_rule(arbitrary_output_file)
    assert cr, arbitrary_output_file
    compile_instance = cr.compile_instance

    # Ok, now we can call build() or build_many() on the compile instance.
    for (outfile_name, _, changed, _) in buildmany_arg:
        log.v1('Building %s (due to changes in %s)',
               outfile_name, ' '.join(changed))

    # Lock the files so two processes don't try to build them at the
    # same time.
    # TODO(csilvers): there can be hundreds of thousands of these.
    # Have some way of reaping obsolete ones.
    locked_files = []
    for (outfile, _, _, _) in buildmany_arg:
        # NOTE: if we notice a lot of time being spent in mkdir,
        # we could reduce the number of dirs by replacing the 'outfile'
        # below with `outfile.replace('/', '_')`, or some such.
        fname = project_root.join('genfiles', '_lockfiles', outfile)
        try:
            locked_files.append(open(fname, 'w'))
        except IOError as why:
            if why.errno == 2:      # "No such file or directory"
                try:
                    os.makedirs(os.path.dirname(fname))
                except (IOError, OSError):
                    pass    # a concurrent process could have made this dir
                locked_files.append(open(fname, 'w'))
            else:
                raise

    # Sort the filenames in a canonical order to avoid deadlock!
    locked_files.sort(key=lambda l: l.name)
    for f in locked_files:
        # If someone else is building f, this will block until they're
        # done.  Note we still rebuild f again after they're done,
        # which is usually unnecessary, but not always.  Consider this
        # case: file A depends on B and C.  B changes so process 1
        # starts to rebuild A.  Then C changes and process 2 starts to
        # rebuild A, and hits this lock.  Because of the change in C,
        # process 2 needs to rebuild A *again* after the lock is lifted.)
        fcntl.lockf(f, fcntl.LOCK_EX)
    try:
        start_time = time.time()
        if compile_instance.should_call_build_many():
            try:
                compile_instance.build_many(buildmany_arg)
            except Exception:
                log.error('Fatal error in a build_many() call; re-building '
                          'the targets one at a time to find the culprit.')
                filemod_db.abandon_pending_transactions()
                for build_only_one in buildmany_arg:
                    try:
                        compile_instance.build_many([build_only_one])
                    except Exception:
                        log.exception('FATAL ERROR building %s',
                                      build_only_one[0])
                        filemod_db.abandon_pending_transactions()
                        raise
                log.error('Could not narrow down the problematic target rule')
                raise
        else:
            assert len(buildmany_arg) == 1, buildmany_arg
            build_only_one = buildmany_arg[0]
            try:
                compile_instance.build(*build_only_one)   # the 4-tuple
            except Exception:
                log.exception('FATAL ERROR building %s', build_only_one[0])
                filemod_db.abandon_pending_transactions()
                raise
        end_time = time.time()
    finally:
        for f in locked_files:
            # Sadly, it is not safe to clean up this lockfile in this
            # function, so these lockfiles may accumulate.  For more
            # details on what race conditions can occur if we were to
            # unlink(lockfile) -- it involves 3 processes -- see
            # https://www.ruby-forum.com/topic/77244
            f.close()

    for (outfile_name, _, _, _) in buildmany_arg:
        log.info('WROTE %s', outfile_name)

    return {cr.label: end_time - start_time}


def _compile_together(outfile_names_and_deprules, pool, num_processes,
                      force, timing_map):
    """Given a list of (outfile_name, deprule) pairs, compile them.

    The main requirement for this method is all the outfiles must
    share a common compile_instance.

    We divide these outfiles based on compile_instance.num_outputs(),
    ignoring files that don't need to be recompiled because they're
    up-to-date.

    Updates the timing-map -- a map from compile-rule (identified by
    label) to the time we spent building files using that compile rule
    -- in place.

    Returns the outfile_names of files that were actually re-built.
    """
    if len(outfile_names_and_deprules) > 1:
        log.v4('Grouping together: %s',
               [f for (f, _) in outfile_names_and_deprules])

    compile_instance = (
        outfile_names_and_deprules[0][1].compile_rule.compile_instance)
    build_args = []
    for (outfile_name, deprule) in outfile_names_and_deprules:
        # TODO(csilvers): change filemod_db so if the context has
        #    changed, it's passed back to 'changed' in some way rather
        #    than returning the output filename.
        changed = filemod_db.changed_files(
            outfile_name, *deprule.input_files,
            context=compile_instance.full_version(deprule.context),
            compute_crc=deprule.compile_rule.compute_crc,
            force=force)
        if not changed:
            log.v1('Skipping build of %s: up to date', outfile_name)
            continue

        # Make the output directory, if needed.
        try:
            outfile_path = project_root.join(outfile_name)
            os.makedirs(os.path.dirname(outfile_path))
        except (OSError, IOError):   # directory already exists, probably
            pass

        # Check if we can symlink rather than having to build.
        maybe_symlink_to = deprule.compile_rule.maybe_symlink_to(
            outfile_name, deprule.context)
        if maybe_symlink_to and filemod_db.can_symlink_to(outfile_name,
                                                          maybe_symlink_to):
            rel_link = os.path.relpath(project_root.join(maybe_symlink_to),
                                       os.path.dirname(outfile_path))
            try:
                os.unlink(outfile_path)
            except (IOError, OSError):       # file does not exist
                pass

            log.v1('Building %s by symlinking it to %s',
                   outfile_name, maybe_symlink_to)
            os.symlink(rel_link, outfile_path)
            filemod_db.set_up_to_date(outfile_name)
            continue

        log.v4('Preparing to build %s (extra info: %s)',
               outfile_name, deprule.compile_rule.output_pattern)

        build_args.append((outfile_name, deprule.input_files,
                           changed, deprule.context))

    if not build_args:         # nothing to do?  cool, let's vacay.
        return []

    # Now build_args is a list of 4-tuples, and this list is something
    # that in theory could be passed to compile_rule.build_many()...
    # except for two things.  1) some compile_rules support build(),
    # not build_many(), and 2) even those compile_rules that support
    # build_many() can't take lists of arbitrary length, the length is
    # limited based on num_outputs() (or, for some compile rules,
    # split_outputs()).  So now we partition build_args into a number
    # of lists of 4-tuples.  Each list-of-4tuples is legal to pass to
    # build_many() or to build() (in which case the list will always
    # have length 1).
    # TODO(csilvers): instead of special-casing split_outputs(), just
    # have a default implementation that splits based on num_outputs().

    # We place a hard limit on the max size of a chunk to avoid going over the
    # ulimit. This limit is imposed because during compilation, we lock all of
    # the files being built. If the ulimit is 1024, and we try to build 1025
    # files, we'll open 1025 file locks, and the last one will fail like this:
    #
    #   [Errno 24] Too many open files:
    #
    # We leave 200 file descriptors available for files other than the
    # lockfiles (or half of them, if less than 400 are available).
    try:
        descriptor_limit = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
    except resource.error as why:
        descriptor_limit = 1024
        log.warn('Could not find ulimit: %s. Using default of %d.',
                 why, descriptor_limit)
    max_chunk_size = max(descriptor_limit - 200, descriptor_limit / 2)

    partitions_of_build_args = []
    if hasattr(compile_instance, 'split_outputs'):
        for one_partition in compile_instance.split_outputs(build_args,
                                                            num_processes):
            for i in xrange(0, len(one_partition), max_chunk_size):
                partitions_of_build_args.append(
                    one_partition[i:i + max_chunk_size])
    elif compile_instance.num_outputs() == 0:
        for one_4tuple in build_args:
            one_partition = [one_4tuple]
            partitions_of_build_args.append(one_partition)
    else:
        chunk_size = min(compile_instance.num_outputs(), max_chunk_size)
        for i in xrange(0, len(build_args), chunk_size):
            one_partition = build_args[i:i + chunk_size]
            partitions_of_build_args.append(one_partition)

    # Now build!
    if pool:
        timing_info = pool.map(_subprocess_run_build, partitions_of_build_args)
    else:
        timing_info = map(_subprocess_run_build, partitions_of_build_args)

    # Combine the individual timing-info's into an aggregated map.
    for other_map in timing_info:
        for (k, v) in other_map.iteritems():
            timing_map.setdefault(k, 0.0)
            timing_map[k] += v

    # And let the filemod-db know that we're done building.
    output_filenames = [of for (of, _, _, _) in build_args]
    filemod_db.set_up_to_date(*output_filenames)
    return output_filenames


def _build_with_optional_checkpoints(outfile_names_and_contexts,
                                     num_processes,
                                     force,
                                     checkpoint_interval):
    """See build_with_optional_checkpoints.__doc__."""
    changed_files = []
    timing_map = {}

    # First, construct the dependency-graph to build these outfiles.
    log.v1('Determining the dependency graph for %s files',
           len(outfile_names_and_contexts))
    log.v2('\n'.join('   ... %s' % f for (f, _) in outfile_names_and_contexts))

    # Create the pool of sub-processes that will be doing the building.
    # We do this early so the forked processes use less memory (they
    # don't inherit all the memory used to build the dependency graph).
    if num_processes > 1:
        pool = multiprocessing.Pool(num_processes)
    else:
        pool = None

    dependency_graph = DependencyGraph()
    already_built = set()    # a cache of built files as we build them
    for (outfile_name, context) in outfile_names_and_contexts:
        dependency_graph.add_file(outfile_name, context, already_built,
                                  timing_map, force)

    # Let's emit the dependency graph as a dot file or two!
    dependency_graph.emit_to_dot('genfiles/_rule_deps.dot')

    # Now add the 'system variables' to the context of each node.
    # These context keys all have names that start with '_':
    # _input_map: a map from output-file to its input-files.  This
    #     gives every rule a mini-dependency-graph to work with.
    _input_map = {}
    for (outfile_name, depnode) in dependency_graph.iteritems():
        _input_map[outfile_name] = depnode.input_files

    for (_, depnode) in dependency_graph.iteritems():
        depnode.context['_input_map'] = _input_map

    # Now, extract the files in dependency order, yielding a chunk of
    # files at a time -- all with the same compile_instance -- that we
    # pass to build() or build_many().
    log.v1('Building %s files', dependency_graph.len())
    last_checkpoint = time.time()
    for to_build in _deps_to_compile_together(dependency_graph):
        new_changed_files = _compile_together(to_build, pool, num_processes,
                                              force, timing_map)
        changed_files.extend(new_changed_files)

        if (checkpoint_interval is not None and
                time.time() - last_checkpoint >= checkpoint_interval):
            filemod_db.sync()
            last_checkpoint = time.time()
    log.v1('Done building %s files', dependency_graph.len())

    # Close down the sub-process pool nicely.
    if pool:
        pool.close()
        pool.join()

    # Log the timing info.
    log.v1('Time spent in each build rule:')
    timing = timing_map.items()
    timing.sort(key=lambda kv: kv[1], reverse=True)    # slowest first
    total_time = 0.0
    for (compile_rule_label, cr_time) in timing:
        log.v1('   %s: %.2f sec' % (compile_rule_label, cr_time))
        total_time += cr_time
    log.v1('   -- TOTAL: %.2f sec' % total_time)

    # We only want to return the changed files that the user asked to
    # build, not dependent files that had to be rebuilt as well.
    outfile_names = set(of for (of, _) in outfile_names_and_contexts)
    return [f for f in changed_files if f in outfile_names]


def build_with_optional_checkpoints(outfile_names_and_contexts,
                                    num_processes,
                                    force,
                                    checkpoint_interval=60 * 5):
    """Create, or update if it's out of date, the given filename.

    Raises an CompileFailure exception if it could not create or
    update the file.

    A 'checkpoint' is a filemod-db sync -- this makes sure we record
    the state of what files have been built in the filemod-db.  After
    a checkpoint, we won't need to rebuild files that were built
    before the checkpoint (unless, of course, their dependencies have
    changed since then).

    Arguments:
       outfile_names_and_contexts: filenames relative to ka-root, each
          context is an arbitrary dict passed to the build rule for
          outfile_name and all its dependencies.
       num_processes: the number of sub-processes to spawn to do the building.
       force: if True, force deps to be rebuilt even if they are up-to-date.
       checkpoint_interval: if not None, sync the filemod db every
          this many seconds, approximately.  (We only sync when the
          filemod-db is consistent, so times are not exact.)

    Returns:
        The filenames for given files that were actually re-built,
        because they weren't up-to-date.
    """
    if not outfile_names_and_contexts:   # small optimization for empty build
        return []

    try:
        return _build_with_optional_checkpoints(outfile_names_and_contexts,
                                                num_processes,
                                                force,
                                                checkpoint_interval)
    finally:
        if checkpoint_interval is not None:
            filemod_db.sync()


def _immediate_build(output_filenames, context, caller,
                     already_built, timing_map, force):
    """Build all output_filenames without using a dependency graph.

    This is less efficient than do_build, but simpler.  We use it
    when compiling trigger files, since it's otherwise too complicated
    to keep track of what files are needed to generate the inputs to
    what other files, etc.

    We augment already_built with files as we build them, to avoid having
    to re-build them on future _immediate_build() calls.

    Note all outputs must share the same context.

    We update the timing_map -- a map from compile-rule label to the
    time taken to build files using that compile-rule -- in-place.
    """
    for output_filename in output_filenames:
        if not output_filename.startswith(compile_rule.GENDIR):
            continue
        if output_filename in already_built:
            continue

        log.v2('Doing "immediate" build of %s, needed to determine '
               'inputs for %s', output_filename, caller)
        cr = compile_rule.find_compile_rule(output_filename)
        if cr is None:
            raise NoBuildRuleCompileFailure(output_filename)

        var_values = cr.var_values(output_filename)

        # We create a new context here instead of updating the existing
        # one to avoid modifying the caller's context.
        outfile_context = context.copy()
        outfile_context.update(var_values)

        # Recursively build the triggers needed to compute our inputs, if any.
        #
        # We do these one by one because trigger_files may not be able to able
        # to compute all of the trigger files without building some of them
        # first. This happens if a file imports a generated file which may in
        # turn have imports. In cases such as these, input_trigger_files will
        # return a generator.
        for trigger in cr.input_trigger_files(output_filename,
                                              outfile_context):
            _immediate_build([trigger], context, output_filename,
                             already_built, timing_map, force)

        # Recursively build our inputs.
        input_filenames = cr.input_files(output_filename, outfile_context)
        _immediate_build(input_filenames, context, output_filename,
                         already_built, timing_map, force)

        # Also build our non-input dependencies, which the API promises
        # are built before we are.
        non_input_deps = cr.non_input_deps_files(output_filename, var_values)
        maybe_symlink_to = cr.maybe_symlink_to(output_filename, var_values)
        if maybe_symlink_to and maybe_symlink_to != output_filename:
            non_input_deps.append(maybe_symlink_to)
        _immediate_build(non_input_deps, context, output_filename,
                         already_built, timing_map, force)

        depnode = DependencyNode(cr, outfile_context, input_filenames,
                                 non_input_deps)
        # Immediate builds always happen in the main process, we don't
        # spawn sub-processes for them.
        _compile_together([(output_filename, depnode)], None, 1,
                          force, timing_map)

        already_built.add(output_filename)


def build_many(outfile_names_and_contexts, num_processes=1, force=False,
               checkpoint_interval=60 * 5):
    """Create, or update if it's out of date, the given filename.

    Raises an CompileFailure exception if it could not create or
    update the file.

    Arguments:
       outfile_name_and_contexts: filenames relative to ka-root, each
          context is an arbitrary dict passed to the build rule for
          outfile_name and all its dependencies.
       num_processes: the number of sub-processes to spawn to do the
          building.  (1 means to build in the main process only.)
       force: if True, force deps to be rebuilt even if they are up-to-date.
       checkpoint_interval: if not None, flush the filemod db every
          this many seconds, approximately.  This records the work
          we've done so we don't have to re-do it if the process dies.

    Returns:
        The list of outfile_names that were actually rebuilt
        (because they weren't up to date).
    """
    # We don't trust that files haven't changed since the last time we
    # were called, so we can't use the mtime cache.
    filemod_db.clear_mtime_cache()
    return build_with_optional_checkpoints(
        outfile_names_and_contexts, num_processes, force, checkpoint_interval)


def build(outfile_name, context={}, num_processes=1, force=False,
          checkpoint_interval=60 * 5):
    """Create, or update if it's out of date, the given filename.

    Raises an CompileFailure exception if it could not create or
    update the file.

    Arguments:
       outfile_name: filename relative to ka-root.
       context: an arbitrary dict passed to all build rules for this file.
       num_processes: the number of sub-processes to spawn to do the
          building.  (1 means to build in the main process only.)
       force: if True, force deps to be rebuilt even if they are up-to-date.
       checkpoint_interval: if not None, flush the filemod db every
          this many seconds, approximately.  This records the work
          we've done so we don't have to re-do it if the process dies.

    Returns:
        [outfile_name] if it had to be rebuilt (because it wasn't up
        to date), or [] else.
    """
    return build_many([(outfile_name, context)], num_processes, force,
                      checkpoint_interval)
