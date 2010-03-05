# Copyright (C) 2005, 2006, 2007, 2009, 2010 Canonical Ltd
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA

# TODO: 'bzr resolve' should accept a directory name and work from that
# point down

import os
import re

from bzrlib.lazy_import import lazy_import
lazy_import(globals(), """
import errno

from bzrlib import (
    builtins,
    cleanup,
    commands,
    errors,
    osutils,
    rio,
    trace,
    transform,
    workingtree,
    )
""")
from bzrlib import (
    option,
    registry,
    )


CONFLICT_SUFFIXES = ('.THIS', '.BASE', '.OTHER')


class cmd_conflicts(commands.Command):
    """List files with conflicts.

    Merge will do its best to combine the changes in two branches, but there
    are some kinds of problems only a human can fix.  When it encounters those,
    it will mark a conflict.  A conflict means that you need to fix something,
    before you should commit.

    Conflicts normally are listed as short, human-readable messages.  If --text
    is supplied, the pathnames of files with text conflicts are listed,
    instead.  (This is useful for editing all files with text conflicts.)

    Use bzr resolve when you have fixed a problem.
    """
    takes_options = [
            option.Option('text',
                          help='List paths of files with text conflicts.'),
        ]
    _see_also = ['resolve', 'conflict-types']

    def run(self, text=False):
        wt = workingtree.WorkingTree.open_containing(u'.')[0]
        for conflict in wt.conflicts():
            if text:
                if conflict.typestring != 'text conflict':
                    continue
                self.outf.write(conflict.path + '\n')
            else:
                self.outf.write(str(conflict) + '\n')


resolve_action_registry = registry.Registry()


resolve_action_registry.register(
    'done', 'done', 'Marks the conflict as resolved' )
resolve_action_registry.register(
    'take-this', 'take_this',
    'Resolve the conflict preserving the version in the working tree' )
resolve_action_registry.register(
    'take-other', 'take_other',
    'Resolve the conflict taking the merged version into account' )
resolve_action_registry.default_key = 'done'

class ResolveActionOption(option.RegistryOption):

    def __init__(self):
        super(ResolveActionOption, self).__init__(
            'action', 'How to resolve the conflict.',
            value_switches=True,
            registry=resolve_action_registry)


class cmd_resolve(commands.Command):
    """Mark a conflict as resolved.

    Merge will do its best to combine the changes in two branches, but there
    are some kinds of problems only a human can fix.  When it encounters those,
    it will mark a conflict.  A conflict means that you need to fix something,
    before you should commit.

    Once you have fixed a problem, use "bzr resolve" to automatically mark
    text conflicts as fixed, "bzr resolve FILE" to mark a specific conflict as
    resolved, or "bzr resolve --all" to mark all conflicts as resolved.
    """
    aliases = ['resolved']
    takes_args = ['file*']
    takes_options = [
            option.Option('all', help='Resolve all conflicts in this tree.'),
            ResolveActionOption(),
            ]
    _see_also = ['conflicts']
    def run(self, file_list=None, all=False, action=None):
        if all:
            if file_list:
                raise errors.BzrCommandError("If --all is specified,"
                                             " no FILE may be provided")
            tree = workingtree.WorkingTree.open_containing('.')[0]
            if action is None:
                action = 'done'
        else:
            tree, file_list = builtins.tree_files(file_list)
            if file_list is None:
                if action is None:
                    # FIXME: There is a special case here related to the option
                    # handling that could be clearer and easier to discover by
                    # providing an --auto action (bug #344013 and #383396) and
                    # make it mandatory instead of implicit and active only
                    # when no file_list is provided -- vila 091229
                    action = 'auto'
            else:
                if action is None:
                    action = 'done'
        if action == 'auto':
            if file_list is None:
                un_resolved, resolved = tree.auto_resolve()
                if len(un_resolved) > 0:
                    trace.note('%d conflict(s) auto-resolved.', len(resolved))
                    trace.note('Remaining conflicts:')
                    for conflict in un_resolved:
                        trace.note(conflict)
                    return 1
                else:
                    trace.note('All conflicts resolved.')
                    return 0
            else:
                # FIXME: This can never occur but the block above needs some
                # refactoring to transfer tree.auto_resolve() to
                # conflict.auto(tree) --vila 091242
                pass
        else:
            resolve(tree, file_list, action=action)


def resolve(tree, paths=None, ignore_misses=False, recursive=False,
            action='done'):
    """Resolve some or all of the conflicts in a working tree.

    :param paths: If None, resolve all conflicts.  Otherwise, select only
        specified conflicts.
    :param recursive: If True, then elements of paths which are directories
        have all their children resolved, etc.  When invoked as part of
        recursive commands like revert, this should be True.  For commands
        or applications wishing finer-grained control, like the resolve
        command, this should be False.
    :param ignore_misses: If False, warnings will be printed if the supplied
        paths do not have conflicts.
    :param action: How the conflict should be resolved,
    """
    tree.lock_tree_write()
    try:
        tree_conflicts = tree.conflicts()
        if paths is None:
            new_conflicts = ConflictList()
            to_process = tree_conflicts
        else:
            new_conflicts, to_process = tree_conflicts.select_conflicts(
                tree, paths, ignore_misses, recursive)
        for conflict in to_process:
            try:
                conflict._do(action, tree)
                conflict.cleanup(tree)
            except NotImplementedError:
                new_conflicts.append(conflict)
        try:
            tree.set_conflicts(new_conflicts)
        except errors.UnsupportedOperation:
            pass
    finally:
        tree.unlock()


def restore(filename):
    """Restore a conflicted file to the state it was in before merging.

    Only text restoration is supported at present.
    """
    conflicted = False
    try:
        osutils.rename(filename + ".THIS", filename)
        conflicted = True
    except OSError, e:
        if e.errno != errno.ENOENT:
            raise
    try:
        os.unlink(filename + ".BASE")
        conflicted = True
    except OSError, e:
        if e.errno != errno.ENOENT:
            raise
    try:
        os.unlink(filename + ".OTHER")
        conflicted = True
    except OSError, e:
        if e.errno != errno.ENOENT:
            raise
    if not conflicted:
        raise errors.NotConflicted(filename)


class ConflictList(object):
    """List of conflicts.

    Typically obtained from WorkingTree.conflicts()

    Can be instantiated from stanzas or from Conflict subclasses.
    """

    def __init__(self, conflicts=None):
        object.__init__(self)
        if conflicts is None:
            self.__list = []
        else:
            self.__list = conflicts

    def is_empty(self):
        return len(self.__list) == 0

    def __len__(self):
        return len(self.__list)

    def __iter__(self):
        return iter(self.__list)

    def __getitem__(self, key):
        return self.__list[key]

    def append(self, conflict):
        return self.__list.append(conflict)

    def __eq__(self, other_list):
        return list(self) == list(other_list)

    def __ne__(self, other_list):
        return not (self == other_list)

    def __repr__(self):
        return "ConflictList(%r)" % self.__list

    @staticmethod
    def from_stanzas(stanzas):
        """Produce a new ConflictList from an iterable of stanzas"""
        conflicts = ConflictList()
        for stanza in stanzas:
            conflicts.append(Conflict.factory(**stanza.as_dict()))
        return conflicts

    def to_stanzas(self):
        """Generator of stanzas"""
        for conflict in self:
            yield conflict.as_stanza()

    def to_strings(self):
        """Generate strings for the provided conflicts"""
        for conflict in self:
            yield str(conflict)

    def remove_files(self, tree):
        """Remove the THIS, BASE and OTHER files for listed conflicts"""
        for conflict in self:
            if not conflict.has_files:
                continue
            conflict.cleanup(tree)

    def select_conflicts(self, tree, paths, ignore_misses=False,
                         recurse=False):
        """Select the conflicts associated with paths in a tree.

        File-ids are also used for this.
        :return: a pair of ConflictLists: (not_selected, selected)
        """
        path_set = set(paths)
        ids = {}
        selected_paths = set()
        new_conflicts = ConflictList()
        selected_conflicts = ConflictList()
        for path in paths:
            file_id = tree.path2id(path)
            if file_id is not None:
                ids[file_id] = path

        for conflict in self:
            selected = False
            for key in ('path', 'conflict_path'):
                cpath = getattr(conflict, key, None)
                if cpath is None:
                    continue
                if cpath in path_set:
                    selected = True
                    selected_paths.add(cpath)
                if recurse:
                    if osutils.is_inside_any(path_set, cpath):
                        selected = True
                        selected_paths.add(cpath)

            for key in ('file_id', 'conflict_file_id'):
                cfile_id = getattr(conflict, key, None)
                if cfile_id is None:
                    continue
                try:
                    cpath = ids[cfile_id]
                except KeyError:
                    continue
                selected = True
                selected_paths.add(cpath)
            if selected:
                selected_conflicts.append(conflict)
            else:
                new_conflicts.append(conflict)
        if ignore_misses is not True:
            for path in [p for p in paths if p not in selected_paths]:
                if not os.path.exists(tree.abspath(path)):
                    print "%s does not exist" % path
                else:
                    print "%s is not conflicted" % path
        return new_conflicts, selected_conflicts


class Conflict(object):
    """Base class for all types of conflict"""

    # FIXME: cleanup should take care of that ? -- vila 091229
    has_files = False

    def __init__(self, path, file_id=None):
        self.path = path
        # warn turned off, because the factory blindly transfers the Stanza
        # values to __init__ and Stanza is purely a Unicode api.
        self.file_id = osutils.safe_file_id(file_id, warn=False)

    def as_stanza(self):
        s = rio.Stanza(type=self.typestring, path=self.path)
        if self.file_id is not None:
            # Stanza requires Unicode apis
            s.add('file_id', self.file_id.decode('utf8'))
        return s

    def _cmp_list(self):
        return [type(self), self.path, self.file_id]

    def __cmp__(self, other):
        if getattr(other, "_cmp_list", None) is None:
            return -1
        return cmp(self._cmp_list(), other._cmp_list())

    def __hash__(self):
        return hash((type(self), self.path, self.file_id))

    def __eq__(self, other):
        return self.__cmp__(other) == 0

    def __ne__(self, other):
        return not self.__eq__(other)

    def __str__(self):
        return self.format % self.__dict__

    def __repr__(self):
        rdict = dict(self.__dict__)
        rdict['class'] = self.__class__.__name__
        return self.rformat % rdict

    @staticmethod
    def factory(type, **kwargs):
        global ctype
        return ctype[type](**kwargs)

    @staticmethod
    def sort_key(conflict):
        if conflict.path is not None:
            return conflict.path, conflict.typestring
        elif getattr(conflict, "conflict_path", None) is not None:
            return conflict.conflict_path, conflict.typestring
        else:
            return None, conflict.typestring

    def _do(self, action, tree):
        """Apply the specified action to the conflict.

        :param action: The method name to call.

        :param tree: The tree passed as a parameter to the method.
        """
        meth = getattr(self, 'action_%s' % action, None)
        if meth is None:
            raise NotImplementedError(self.__class__.__name__ + '.' + action)
        meth(tree)

    def associated_filenames(self):
        """The names of the files generated to help resolve the conflict."""
        raise NotImplementedError(self.associated_filenames)

    def cleanup(self, tree):
        for fname in self.associated_filenames():
            try:
                osutils.delete_any(tree.abspath(fname))
            except OSError, e:
                if e.errno != errno.ENOENT:
                    raise

    def action_done(self, tree):
        """Mark the conflict as solved once it has been handled."""
        # This method does nothing but simplifies the design of upper levels.
        pass

    def action_take_this(self, tree):
        raise NotImplementedError(self.action_take_this)

    def action_take_other(self, tree):
        raise NotImplementedError(self.action_take_other)

    def _resolve_with_cleanups(self, tree, *args, **kwargs):
        tt = transform.TreeTransform(tree)
        op = cleanup.OperationWithCleanups(self._resolve)
        op.add_cleanup(tt.finalize)
        op.run_simple(tt, *args, **kwargs)


class PathConflict(Conflict):
    """A conflict was encountered merging file paths"""

    typestring = 'path conflict'

    format = 'Path conflict: %(path)s / %(conflict_path)s'

    rformat = '%(class)s(%(path)r, %(conflict_path)r, %(file_id)r)'

    def __init__(self, path, conflict_path=None, file_id=None):
        Conflict.__init__(self, path, file_id)
        self.conflict_path = conflict_path

    def as_stanza(self):
        s = Conflict.as_stanza(self)
        if self.conflict_path is not None:
            s.add('conflict_path', self.conflict_path)
        return s

    def associated_filenames(self):
        # No additional files have been generated here
        return []

    def _resolve(self, tt, file_id, path):
        """Resolve the conflict.

        :param tt: The TreeTransform where the conflict is resolved.
        :param file_id: The retained file id.
        :param path: The retained path.
        """
        # Rename 'item.suffix_to_remove' (note that if
        # 'item.suffix_to_remove' has been deleted, this is a no-op)
        tid = tt.trans_id_file_id(file_id)
        parent_tid = tt.get_tree_parent(tid)
        tt.adjust_path(path, parent_tid, tid)
        tt.apply()

    def _get_or_infer_file_id(self, tree):
        if self.file_id is not None:
            return self.file_id

        # Prior to bug #531967, file_id wasn't always set, there may still be
        # conflict files in the wild so we need to cope with them
        return tree.path2id(self.conflict_path)

    def action_take_this(self, tree):
        file_id = self._get_or_infer_file_id(tree)
        if file_id is None:
            import pdb ; pdb.set_trace()
        self._resolve_with_cleanups(tree, file_id, self.path)

    def action_take_other(self, tree):
        # just acccept bzr proposal
        pass


class ContentsConflict(PathConflict):
    """The files are of different types, or not present"""

    has_files = True

    typestring = 'contents conflict'

    format = 'Contents conflict in %(path)s'

    def associated_filenames(self):
        return [self.path + suffix for suffix in ('.BASE', '.OTHER')]

    def _resolve(self, tt, suffix_to_remove):
        """Resolve the conflict.

        :param tt: The TreeTransform where the conflict is resolved.
        :param suffix_to_remove: Either 'THIS' or 'OTHER'

        The resolution is symmetric, when taking THIS, OTHER is deleted and
        item.THIS is renamed into item and vice-versa.
        """
        try:
            # Delete 'item.THIS' or 'item.OTHER' depending on
            # suffix_to_remove
            tt.delete_contents(
                tt.trans_id_tree_path(self.path + '.' + suffix_to_remove))
        except errors.NoSuchFile:
            # There are valid cases where 'item.suffix_to_remove' either
            # never existed or was already deleted (including the case
            # where the user deleted it)
            pass
        # Rename 'item.suffix_to_remove' (note that if
        # 'item.suffix_to_remove' has been deleted, this is a no-op)
        this_tid = tt.trans_id_file_id(self.file_id)
        parent_tid = tt.get_tree_parent(this_tid)
        tt.adjust_path(self.path, parent_tid, this_tid)
        tt.apply()

    def action_take_this(self, tree):
        self._resolve_with_cleanups(tree, 'OTHER')

    def action_take_other(self, tree):
        self._resolve_with_cleanups(tree, 'THIS')


# FIXME: TextConflict is about a single file-id, there never is a conflict_path
# attribute so we shouldn't inherit from PathConflict but simply from Conflict

# TODO: There should be a base revid attribute to better inform the user about
# how the conflicts were generated.
class TextConflict(PathConflict):
    """The merge algorithm could not resolve all differences encountered."""

    has_files = True

    typestring = 'text conflict'

    format = 'Text conflict in %(path)s'

    def associated_filenames(self):
        return [self.path + suffix for suffix in CONFLICT_SUFFIXES]


class HandledConflict(Conflict):
    """A path problem that has been provisionally resolved.
    This is intended to be a base class.
    """

    rformat = "%(class)s(%(action)r, %(path)r, %(file_id)r)"

    def __init__(self, action, path, file_id=None):
        Conflict.__init__(self, path, file_id)
        self.action = action

    def _cmp_list(self):
        return Conflict._cmp_list(self) + [self.action]

    def as_stanza(self):
        s = Conflict.as_stanza(self)
        s.add('action', self.action)
        return s

    def associated_filenames(self):
        # Nothing has been generated here
        return []


class HandledPathConflict(HandledConflict):
    """A provisionally-resolved path problem involving two paths.
    This is intended to be a base class.
    """

    rformat = "%(class)s(%(action)r, %(path)r, %(conflict_path)r,"\
        " %(file_id)r, %(conflict_file_id)r)"

    def __init__(self, action, path, conflict_path, file_id=None,
                 conflict_file_id=None):
        HandledConflict.__init__(self, action, path, file_id)
        self.conflict_path = conflict_path
        # warn turned off, because the factory blindly transfers the Stanza
        # values to __init__.
        self.conflict_file_id = osutils.safe_file_id(conflict_file_id,
                                                     warn=False)

    def _cmp_list(self):
        return HandledConflict._cmp_list(self) + [self.conflict_path,
                                                  self.conflict_file_id]

    def as_stanza(self):
        s = HandledConflict.as_stanza(self)
        s.add('conflict_path', self.conflict_path)
        if self.conflict_file_id is not None:
            s.add('conflict_file_id', self.conflict_file_id.decode('utf8'))

        return s


class DuplicateID(HandledPathConflict):
    """Two files want the same file_id."""

    typestring = 'duplicate id'

    format = 'Conflict adding id to %(conflict_path)s.  %(action)s %(path)s.'


class DuplicateEntry(HandledPathConflict):
    """Two directory entries want to have the same name."""

    typestring = 'duplicate'

    format = 'Conflict adding file %(conflict_path)s.  %(action)s %(path)s.'

    def action_take_this(self, tree):
        tree.remove([self.conflict_path], force=True, keep_files=False)
        tree.rename_one(self.path, self.conflict_path)

    def action_take_other(self, tree):
        tree.remove([self.path], force=True, keep_files=False)


class ParentLoop(HandledPathConflict):
    """An attempt to create an infinitely-looping directory structure.
    This is rare, but can be produced like so:

    tree A:
      mv foo bar
    tree B:
      mv bar foo
    merge A and B
    """

    typestring = 'parent loop'

    format = 'Conflict moving %(conflict_path)s into %(path)s.  %(action)s.'

    def action_take_this(self, tree):
        # just acccept bzr proposal
        pass

    def action_take_other(self, tree):
        # FIXME: We shouldn't have to manipulate so many paths here (and there
        # is probably a bug or two...)
        base_path = osutils.basename(self.path)
        conflict_base_path = osutils.basename(self.conflict_path)
        tt = transform.TreeTransform(tree)
        try:
            p_tid = tt.trans_id_file_id(self.file_id)
            parent_tid = tt.get_tree_parent(p_tid)
            cp_tid = tt.trans_id_file_id(self.conflict_file_id)
            cparent_tid = tt.get_tree_parent(cp_tid)
            tt.adjust_path(base_path, cparent_tid, cp_tid)
            tt.adjust_path(conflict_base_path, parent_tid, p_tid)
            tt.apply()
        finally:
            tt.finalize()


class UnversionedParent(HandledConflict):
    """An attempt to version a file whose parent directory is not versioned.
    Typically, the result of a merge where one tree unversioned the directory
    and the other added a versioned file to it.
    """

    typestring = 'unversioned parent'

    format = 'Conflict because %(path)s is not versioned, but has versioned'\
             ' children.  %(action)s.'

    # FIXME: We silently do nothing to make tests pass, but most probably the
    # conflict shouldn't exist (the long story is that the conflict is
    # generated with another one that can be resolved properly) -- vila 091224
    def action_take_this(self, tree):
        pass

    def action_take_other(self, tree):
        pass


class MissingParent(HandledConflict):
    """An attempt to add files to a directory that is not present.
    Typically, the result of a merge where THIS deleted the directory and
    the OTHER added a file to it.
    See also: DeletingParent (same situation, THIS and OTHER reversed)
    """

    typestring = 'missing parent'

    format = 'Conflict adding files to %(path)s.  %(action)s.'

    def action_take_this(self, tree):
        tree.remove([self.path], force=True, keep_files=False)

    def action_take_other(self, tree):
        # just acccept bzr proposal
        pass


class DeletingParent(HandledConflict):
    """An attempt to add files to a directory that is not present.
    Typically, the result of a merge where one OTHER deleted the directory and
    the THIS added a file to it.
    """

    typestring = 'deleting parent'

    format = "Conflict: can't delete %(path)s because it is not empty.  "\
             "%(action)s."

    # FIXME: It's a bit strange that the default action is not coherent with
    # MissingParent from the *user* pov.

    def action_take_this(self, tree):
        # just acccept bzr proposal
        pass

    def action_take_other(self, tree):
        tree.remove([self.path], force=True, keep_files=False)


class NonDirectoryParent(HandledConflict):
    """An attempt to add files to a directory that is not a directory or
    an attempt to change the kind of a directory with files.
    """

    typestring = 'non-directory parent'

    format = "Conflict: %(path)s is not a directory, but has files in it."\
             "  %(action)s."

    # FIXME: .OTHER should be used instead of .new when the conflict is created

    def action_take_this(self, tree):
        # FIXME: we should preserve that path when the conflict is generated !
        if self.path.endswith('.new'):
            conflict_path = self.path[:-(len('.new'))]
            tree.remove([self.path], force=True, keep_files=False)
            tree.add(conflict_path)
        else:
            raise NotImplementedError(self.action_take_this)

    def action_take_other(self, tree):
        # FIXME: we should preserve that path when the conflict is generated !
        if self.path.endswith('.new'):
            conflict_path = self.path[:-(len('.new'))]
            tree.remove([conflict_path], force=True, keep_files=False)
            tree.rename_one(self.path, conflict_path)
        else:
            raise NotImplementedError(self.action_take_other)


ctype = {}


def register_types(*conflict_types):
    """Register a Conflict subclass for serialization purposes"""
    global ctype
    for conflict_type in conflict_types:
        ctype[conflict_type.typestring] = conflict_type

register_types(ContentsConflict, TextConflict, PathConflict, DuplicateID,
               DuplicateEntry, ParentLoop, UnversionedParent, MissingParent,
               DeletingParent, NonDirectoryParent)
