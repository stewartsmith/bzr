# Copyright (C) 2006-2011 Canonical Ltd
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

"""BzrDir logic. The BzrDir is the basic control directory used by bzr.

At format 7 this was split out into Branch, Repository and Checkout control
directories.

Note: This module has a lot of ``open`` functions/methods that return
references to in-memory objects. As a rule, there are no matching ``close``
methods. To free any associated resources, simply stop referencing the
objects returned.
"""

from __future__ import absolute_import

import sys

from bzrlib.lazy_import import lazy_import
lazy_import(globals(), """
import bzrlib
from bzrlib import (
    branch as _mod_branch,
    cleanup,
    errors,
    fetch,
    graph,
    lockable_files,
    lockdir,
    osutils,
    pyutils,
    remote,
    repository,
    revision as _mod_revision,
    transport as _mod_transport,
    ui,
    urlutils,
    vf_search,
    win32utils,
    workingtree_3,
    workingtree_4,
    )
from bzrlib.repofmt import knitpack_repo
from bzrlib.transport import (
    do_catching_redirections,
    local,
    )
from bzrlib.i18n import gettext
""")

from bzrlib.trace import (
    mutter,
    note,
    )

from bzrlib import (
    config,
    controldir,
    registry,
    )
from bzrlib.symbol_versioning import (
    deprecated_in,
    deprecated_method,
    )


class BzrDir(controldir.ControlDir):
    """A .bzr control diretory.

    BzrDir instances let you create or open any of the things that can be
    found within .bzr - checkouts, branches and repositories.

    :ivar transport:
        the transport which this bzr dir is rooted at (i.e. file:///.../.bzr/)
    :ivar root_transport:
        a transport connected to the directory this bzr was opened from
        (i.e. the parent directory holding the .bzr directory).

    Everything in the bzrdir should have the same file permissions.

    :cvar hooks: An instance of BzrDirHooks.
    """

    def break_lock(self):
        """Invoke break_lock on the first object in the bzrdir.

        If there is a tree, the tree is opened and break_lock() called.
        Otherwise, branch is tried, and finally repository.
        """
        # XXX: This seems more like a UI function than something that really
        # belongs in this class.
        try:
            thing_to_unlock = self.open_workingtree()
        except (errors.NotLocalUrl, errors.NoWorkingTree):
            try:
                thing_to_unlock = self.open_branch()
            except errors.NotBranchError:
                try:
                    thing_to_unlock = self.open_repository()
                except errors.NoRepositoryPresent:
                    return
        thing_to_unlock.break_lock()

    def check_conversion_target(self, target_format):
        """Check that a bzrdir as a whole can be converted to a new format."""
        # The only current restriction is that the repository content can be 
        # fetched compatibly with the target.
        target_repo_format = target_format.repository_format
        try:
            self.open_repository()._format.check_conversion_target(
                target_repo_format)
        except errors.NoRepositoryPresent:
            # No repo, no problem.
            pass

    def clone_on_transport(self, transport, revision_id=None,
        force_new_repo=False, preserve_stacking=False, stacked_on=None,
        create_prefix=False, use_existing_dir=True, no_tree=False):
        """Clone this bzrdir and its contents to transport verbatim.

        :param transport: The transport for the location to produce the clone
            at.  If the target directory does not exist, it will be created.
        :param revision_id: The tip revision-id to use for any branch or
            working tree.  If not None, then the clone operation may tune
            itself to download less data.
        :param force_new_repo: Do not use a shared repository for the target,
                               even if one is available.
        :param preserve_stacking: When cloning a stacked branch, stack the
            new branch on top of the other branch's stacked-on branch.
        :param create_prefix: Create any missing directories leading up to
            to_transport.
        :param use_existing_dir: Use an existing directory if one exists.
        :param no_tree: If set to true prevents creation of a working tree.
        """
        # Overview: put together a broad description of what we want to end up
        # with; then make as few api calls as possible to do it.

        # We may want to create a repo/branch/tree, if we do so what format
        # would we want for each:
        require_stacking = (stacked_on is not None)
        format = self.cloning_metadir(require_stacking)

        # Figure out what objects we want:
        try:
            local_repo = self.find_repository()
        except errors.NoRepositoryPresent:
            local_repo = None
        try:
            local_branch = self.open_branch()
        except errors.NotBranchError:
            local_branch = None
        else:
            # enable fallbacks when branch is not a branch reference
            if local_branch.repository.has_same_location(local_repo):
                local_repo = local_branch.repository
            if preserve_stacking:
                try:
                    stacked_on = local_branch.get_stacked_on_url()
                except (errors.UnstackableBranchFormat,
                        errors.UnstackableRepositoryFormat,
                        errors.NotStacked):
                    pass
        # Bug: We create a metadir without knowing if it can support stacking,
        # we should look up the policy needs first, or just use it as a hint,
        # or something.
        if local_repo:
            make_working_trees = local_repo.make_working_trees() and not no_tree
            want_shared = local_repo.is_shared()
            repo_format_name = format.repository_format.network_name()
        else:
            make_working_trees = False
            want_shared = False
            repo_format_name = None

        result_repo, result, require_stacking, repository_policy = \
            format.initialize_on_transport_ex(transport,
            use_existing_dir=use_existing_dir, create_prefix=create_prefix,
            force_new_repo=force_new_repo, stacked_on=stacked_on,
            stack_on_pwd=self.root_transport.base,
            repo_format_name=repo_format_name,
            make_working_trees=make_working_trees, shared_repo=want_shared)
        if repo_format_name:
            try:
                # If the result repository is in the same place as the
                # resulting bzr dir, it will have no content, further if the
                # result is not stacked then we know all content should be
                # copied, and finally if we are copying up to a specific
                # revision_id then we can use the pending-ancestry-result which
                # does not require traversing all of history to describe it.
                if (result_repo.user_url == result.user_url
                    and not require_stacking and
                    revision_id is not None):
                    fetch_spec = vf_search.PendingAncestryResult(
                        [revision_id], local_repo)
                    result_repo.fetch(local_repo, fetch_spec=fetch_spec)
                else:
                    result_repo.fetch(local_repo, revision_id=revision_id)
            finally:
                result_repo.unlock()
        else:
            if result_repo is not None:
                raise AssertionError('result_repo not None(%r)' % result_repo)
        # 1 if there is a branch present
        #   make sure its content is available in the target repository
        #   clone it.
        if local_branch is not None:
            result_branch = local_branch.clone(result, revision_id=revision_id,
                repository_policy=repository_policy)
        try:
            # Cheaper to check if the target is not local, than to try making
            # the tree and fail.
            result.root_transport.local_abspath('.')
            if result_repo is None or result_repo.make_working_trees():
                self.open_workingtree().clone(result, revision_id=revision_id)
        except (errors.NoWorkingTree, errors.NotLocalUrl):
            pass
        return result

    # TODO: This should be given a Transport, and should chdir up; otherwise
    # this will open a new connection.
    def _make_tail(self, url):
        t = _mod_transport.get_transport(url)
        t.ensure_base()

    def determine_repository_policy(self, force_new_repo=False, stack_on=None,
                                    stack_on_pwd=None, require_stacking=False):
        """Return an object representing a policy to use.

        This controls whether a new repository is created, and the format of
        that repository, or some existing shared repository used instead.

        If stack_on is supplied, will not seek a containing shared repo.

        :param force_new_repo: If True, require a new repository to be created.
        :param stack_on: If supplied, the location to stack on.  If not
            supplied, a default_stack_on location may be used.
        :param stack_on_pwd: If stack_on is relative, the location it is
            relative to.
        """
        def repository_policy(found_bzrdir):
            stack_on = None
            stack_on_pwd = None
            config = found_bzrdir.get_config()
            stop = False
            stack_on = config.get_default_stack_on()
            if stack_on is not None:
                stack_on_pwd = found_bzrdir.user_url
                stop = True
            # does it have a repository ?
            try:
                repository = found_bzrdir.open_repository()
            except errors.NoRepositoryPresent:
                repository = None
            else:
                if (found_bzrdir.user_url != self.user_url 
                    and not repository.is_shared()):
                    # Don't look higher, can't use a higher shared repo.
                    repository = None
                    stop = True
                else:
                    stop = True
            if not stop:
                return None, False
            if repository:
                return UseExistingRepository(repository, stack_on,
                    stack_on_pwd, require_stacking=require_stacking), True
            else:
                return CreateRepository(self, stack_on, stack_on_pwd,
                    require_stacking=require_stacking), True

        if not force_new_repo:
            if stack_on is None:
                policy = self._find_containing(repository_policy)
                if policy is not None:
                    return policy
            else:
                try:
                    return UseExistingRepository(self.open_repository(),
                        stack_on, stack_on_pwd,
                        require_stacking=require_stacking)
                except errors.NoRepositoryPresent:
                    pass
        return CreateRepository(self, stack_on, stack_on_pwd,
                                require_stacking=require_stacking)

    def _find_or_create_repository(self, force_new_repo):
        """Create a new repository if needed, returning the repository."""
        policy = self.determine_repository_policy(force_new_repo)
        return policy.acquire_repository()[0]

    def _find_source_repo(self, add_cleanup, source_branch):
        """Find the source branch and repo for a sprout operation.
        
        This is helper intended for use by _sprout.

        :returns: (source_branch, source_repository).  Either or both may be
            None.  If not None, they will be read-locked (and their unlock(s)
            scheduled via the add_cleanup param).
        """
        if source_branch is not None:
            add_cleanup(source_branch.lock_read().unlock)
            return source_branch, source_branch.repository
        try:
            source_branch = self.open_branch()
            source_repository = source_branch.repository
        except errors.NotBranchError:
            source_branch = None
            try:
                source_repository = self.open_repository()
            except errors.NoRepositoryPresent:
                source_repository = None
            else:
                add_cleanup(source_repository.lock_read().unlock)
        else:
            add_cleanup(source_branch.lock_read().unlock)
        return source_branch, source_repository

    def sprout(self, url, revision_id=None, force_new_repo=False,
               recurse='down', possible_transports=None,
               accelerator_tree=None, hardlink=False, stacked=False,
               source_branch=None, create_tree_if_local=True):
        """Create a copy of this controldir prepared for use as a new line of
        development.

        If url's last component does not exist, it will be created.

        Attributes related to the identity of the source branch like
        branch nickname will be cleaned, a working tree is created
        whether one existed before or not; and a local branch is always
        created.

        if revision_id is not None, then the clone operation may tune
            itself to download less data.

        :param accelerator_tree: A tree which can be used for retrieving file
            contents more quickly than the revision tree, i.e. a workingtree.
            The revision tree will be used for cases where accelerator_tree's
            content is different.
        :param hardlink: If true, hard-link files from accelerator_tree,
            where possible.
        :param stacked: If true, create a stacked branch referring to the
            location of this control directory.
        :param create_tree_if_local: If true, a working-tree will be created
            when working locally.
        """
        operation = cleanup.OperationWithCleanups(self._sprout)
        return operation.run(url, revision_id=revision_id,
            force_new_repo=force_new_repo, recurse=recurse,
            possible_transports=possible_transports,
            accelerator_tree=accelerator_tree, hardlink=hardlink,
            stacked=stacked, source_branch=source_branch,
            create_tree_if_local=create_tree_if_local)

    def _sprout(self, op, url, revision_id=None, force_new_repo=False,
               recurse='down', possible_transports=None,
               accelerator_tree=None, hardlink=False, stacked=False,
               source_branch=None, create_tree_if_local=True):
        add_cleanup = op.add_cleanup
        fetch_spec_factory = fetch.FetchSpecFactory()
        if revision_id is not None:
            fetch_spec_factory.add_revision_ids([revision_id])
            fetch_spec_factory.source_branch_stop_revision_id = revision_id
        if possible_transports is None:
            possible_transports = []
        else:
            possible_transports = list(possible_transports) + [
                self.root_transport]
        target_transport = _mod_transport.get_transport(url,
            possible_transports)
        target_transport.ensure_base()
        cloning_format = self.cloning_metadir(stacked)
        # Create/update the result branch
        try:
            result = controldir.ControlDir.open_from_transport(target_transport)
        except errors.NotBranchError:
            result = cloning_format.initialize_on_transport(target_transport)
        source_branch, source_repository = self._find_source_repo(
            add_cleanup, source_branch)
        fetch_spec_factory.source_branch = source_branch
        # if a stacked branch wasn't requested, we don't create one
        # even if the origin was stacked
        if stacked and source_branch is not None:
            stacked_branch_url = self.root_transport.base
        else:
            stacked_branch_url = None
        repository_policy = result.determine_repository_policy(
            force_new_repo, stacked_branch_url, require_stacking=stacked)
        result_repo, is_new_repo = repository_policy.acquire_repository(
            possible_transports=possible_transports)
        add_cleanup(result_repo.lock_write().unlock)
        fetch_spec_factory.source_repo = source_repository
        fetch_spec_factory.target_repo = result_repo
        if stacked or (len(result_repo._fallback_repositories) != 0):
            target_repo_kind = fetch.TargetRepoKinds.STACKED
        elif is_new_repo:
            target_repo_kind = fetch.TargetRepoKinds.EMPTY
        else:
            target_repo_kind = fetch.TargetRepoKinds.PREEXISTING
        fetch_spec_factory.target_repo_kind = target_repo_kind
        if source_repository is not None:
            fetch_spec = fetch_spec_factory.make_fetch_spec()
            result_repo.fetch(source_repository, fetch_spec=fetch_spec)

        if source_branch is None:
            # this is for sprouting a controldir without a branch; is that
            # actually useful?
            # Not especially, but it's part of the contract.
            result_branch = result.create_branch()
        else:
            result_branch = source_branch.sprout(result,
                revision_id=revision_id, repository_policy=repository_policy,
                repository=result_repo)
        mutter("created new branch %r" % (result_branch,))

        # Create/update the result working tree
        if (create_tree_if_local and not result.has_workingtree() and
            isinstance(target_transport, local.LocalTransport) and
            (result_repo is None or result_repo.make_working_trees())):
            wt = result.create_workingtree(accelerator_tree=accelerator_tree,
                hardlink=hardlink, from_branch=result_branch)
            wt.lock_write()
            try:
                if wt.path2id('') is None:
                    try:
                        wt.set_root_id(self.open_workingtree.get_root_id())
                    except errors.NoWorkingTree:
                        pass
            finally:
                wt.unlock()
        else:
            wt = None
        if recurse == 'down':
            basis = None
            if wt is not None:
                basis = wt.basis_tree()
            elif result_branch is not None:
                basis = result_branch.basis_tree()
            elif source_branch is not None:
                basis = source_branch.basis_tree()
            if basis is not None:
                add_cleanup(basis.lock_read().unlock)
                subtrees = basis.iter_references()
            else:
                subtrees = []
            for path, file_id in subtrees:
                target = urlutils.join(url, urlutils.escape(path))
                sublocation = source_branch.reference_parent(file_id, path)
                sublocation.bzrdir.sprout(target,
                    basis.get_reference_revision(file_id, path),
                    force_new_repo=force_new_repo, recurse=recurse,
                    stacked=stacked)
        return result

    @deprecated_method(deprecated_in((2, 3, 0)))
    def generate_backup_name(self, base):
        return self._available_backup_name(base)

    def _available_backup_name(self, base):
        """Find a non-existing backup file name based on base.

        See bzrlib.osutils.available_backup_name about race conditions.
        """
        return osutils.available_backup_name(base, self.root_transport.has)

    def backup_bzrdir(self):
        """Backup this bzr control directory.

        :return: Tuple with old path name and new path name
        """

        pb = ui.ui_factory.nested_progress_bar()
        try:
            old_path = self.root_transport.abspath('.bzr')
            backup_dir = self._available_backup_name('backup.bzr')
            new_path = self.root_transport.abspath(backup_dir)
            ui.ui_factory.note(gettext('making backup of {0}\n  to {1}').format(
                urlutils.unescape_for_display(old_path, 'utf-8'),
                urlutils.unescape_for_display(new_path, 'utf-8')))
            self.root_transport.copy_tree('.bzr', backup_dir)
            return (old_path, new_path)
        finally:
            pb.finished()

    def retire_bzrdir(self, limit=10000):
        """Permanently disable the bzrdir.

        This is done by renaming it to give the user some ability to recover
        if there was a problem.

        This will have horrible consequences if anyone has anything locked or
        in use.
        :param limit: number of times to retry
        """
        i  = 0
        while True:
            try:
                to_path = '.bzr.retired.%d' % i
                self.root_transport.rename('.bzr', to_path)
                note(gettext("renamed {0} to {1}").format(
                    self.root_transport.abspath('.bzr'), to_path))
                return
            except (errors.TransportError, IOError, errors.PathError):
                i += 1
                if i > limit:
                    raise
                else:
                    pass

    def _find_containing(self, evaluate):
        """Find something in a containing control directory.

        This method will scan containing control dirs, until it finds what
        it is looking for, decides that it will never find it, or runs out
        of containing control directories to check.

        It is used to implement find_repository and
        determine_repository_policy.

        :param evaluate: A function returning (value, stop).  If stop is True,
            the value will be returned.
        """
        found_bzrdir = self
        while True:
            result, stop = evaluate(found_bzrdir)
            if stop:
                return result
            next_transport = found_bzrdir.root_transport.clone('..')
            if (found_bzrdir.user_url == next_transport.base):
                # top of the file system
                return None
            # find the next containing bzrdir
            try:
                found_bzrdir = self.open_containing_from_transport(
                    next_transport)[0]
            except errors.NotBranchError:
                return None

    def find_repository(self):
        """Find the repository that should be used.

        This does not require a branch as we use it to find the repo for
        new branches as well as to hook existing branches up to their
        repository.
        """
        def usable_repository(found_bzrdir):
            # does it have a repository ?
            try:
                repository = found_bzrdir.open_repository()
            except errors.NoRepositoryPresent:
                return None, False
            if found_bzrdir.user_url == self.user_url:
                return repository, True
            elif repository.is_shared():
                return repository, True
            else:
                return None, True

        found_repo = self._find_containing(usable_repository)
        if found_repo is None:
            raise errors.NoRepositoryPresent(self)
        return found_repo

    def _find_creation_modes(self):
        """Determine the appropriate modes for files and directories.

        They're always set to be consistent with the base directory,
        assuming that this transport allows setting modes.
        """
        # TODO: Do we need or want an option (maybe a config setting) to turn
        # this off or override it for particular locations? -- mbp 20080512
        if self._mode_check_done:
            return
        self._mode_check_done = True
        try:
            st = self.transport.stat('.')
        except errors.TransportNotPossible:
            self._dir_mode = None
            self._file_mode = None
        else:
            # Check the directory mode, but also make sure the created
            # directories and files are read-write for this user. This is
            # mostly a workaround for filesystems which lie about being able to
            # write to a directory (cygwin & win32)
            if (st.st_mode & 07777 == 00000):
                # FTP allows stat but does not return dir/file modes
                self._dir_mode = None
                self._file_mode = None
            else:
                self._dir_mode = (st.st_mode & 07777) | 00700
                # Remove the sticky and execute bits for files
                self._file_mode = self._dir_mode & ~07111

    def _get_file_mode(self):
        """Return Unix mode for newly created files, or None.
        """
        if not self._mode_check_done:
            self._find_creation_modes()
        return self._file_mode

    def _get_dir_mode(self):
        """Return Unix mode for newly created directories, or None.
        """
        if not self._mode_check_done:
            self._find_creation_modes()
        return self._dir_mode

    def get_config(self):
        """Get configuration for this BzrDir."""
        return config.BzrDirConfig(self)

    def _get_config(self):
        """By default, no configuration is available."""
        return None

    def __init__(self, _transport, _format):
        """Initialize a Bzr control dir object.

        Only really common logic should reside here, concrete classes should be
        made with varying behaviours.

        :param _format: the format that is creating this BzrDir instance.
        :param _transport: the transport this dir is based at.
        """
        self._format = _format
        # these are also under the more standard names of 
        # control_transport and user_transport
        self.transport = _transport.clone('.bzr')
        self.root_transport = _transport
        self._mode_check_done = False

    @property 
    def user_transport(self):
        return self.root_transport

    @property
    def control_transport(self):
        return self.transport

    def is_control_filename(self, filename):
        """True if filename is the name of a path which is reserved for bzrdir's.

        :param filename: A filename within the root transport of this bzrdir.

        This is true IF and ONLY IF the filename is part of the namespace reserved
        for bzr control dirs. Currently this is the '.bzr' directory in the root
        of the root_transport. 
        """
        # this might be better on the BzrDirFormat class because it refers to
        # all the possible bzrdir disk formats.
        # This method is tested via the workingtree is_control_filename tests-
        # it was extracted from WorkingTree.is_control_filename. If the method's
        # contract is extended beyond the current trivial implementation, please
        # add new tests for it to the appropriate place.
        return filename == '.bzr' or filename.startswith('.bzr/')

    def _cloning_metadir(self):
        """Produce a metadir suitable for cloning with.

        :returns: (destination_bzrdir_format, source_repository)
        """
        result_format = self._format.__class__()
        try:
            try:
                branch = self.open_branch(ignore_fallbacks=True)
                source_repository = branch.repository
                result_format._branch_format = branch._format
            except errors.NotBranchError:
                source_branch = None
                source_repository = self.open_repository()
        except errors.NoRepositoryPresent:
            source_repository = None
        else:
            # XXX TODO: This isinstance is here because we have not implemented
            # the fix recommended in bug # 103195 - to delegate this choice the
            # repository itself.
            repo_format = source_repository._format
            if isinstance(repo_format, remote.RemoteRepositoryFormat):
                source_repository._ensure_real()
                repo_format = source_repository._real_repository._format
            result_format.repository_format = repo_format
        try:
            # TODO: Couldn't we just probe for the format in these cases,
            # rather than opening the whole tree?  It would be a little
            # faster. mbp 20070401
            tree = self.open_workingtree(recommend_upgrade=False)
        except (errors.NoWorkingTree, errors.NotLocalUrl):
            result_format.workingtree_format = None
        else:
            result_format.workingtree_format = tree._format.__class__()
        return result_format, source_repository

    def cloning_metadir(self, require_stacking=False):
        """Produce a metadir suitable for cloning or sprouting with.

        These operations may produce workingtrees (yes, even though they're
        "cloning" something that doesn't have a tree), so a viable workingtree
        format must be selected.

        :require_stacking: If True, non-stackable formats will be upgraded
            to similar stackable formats.
        :returns: a ControlDirFormat with all component formats either set
            appropriately or set to None if that component should not be
            created.
        """
        format, repository = self._cloning_metadir()
        if format._workingtree_format is None:
            # No tree in self.
            if repository is None:
                # No repository either
                return format
            # We have a repository, so set a working tree? (Why? This seems to
            # contradict the stated return value in the docstring).
            tree_format = repository._format._matchingbzrdir.workingtree_format
            format.workingtree_format = tree_format.__class__()
        if require_stacking:
            format.require_stacking()
        return format

    def get_branch_transport(self, branch_format, name=None):
        """Get the transport for use by branch format in this BzrDir.

        Note that bzr dirs that do not support format strings will raise
        IncompatibleFormat if the branch format they are given has
        a format string, and vice versa.

        If branch_format is None, the transport is returned with no
        checking. If it is not None, then the returned transport is
        guaranteed to point to an existing directory ready for use.
        """
        raise NotImplementedError(self.get_branch_transport)

    def get_repository_transport(self, repository_format):
        """Get the transport for use by repository format in this BzrDir.

        Note that bzr dirs that do not support format strings will raise
        IncompatibleFormat if the repository format they are given has
        a format string, and vice versa.

        If repository_format is None, the transport is returned with no
        checking. If it is not None, then the returned transport is
        guaranteed to point to an existing directory ready for use.
        """
        raise NotImplementedError(self.get_repository_transport)

    def get_workingtree_transport(self, tree_format):
        """Get the transport for use by workingtree format in this BzrDir.

        Note that bzr dirs that do not support format strings will raise
        IncompatibleFormat if the workingtree format they are given has a
        format string, and vice versa.

        If workingtree_format is None, the transport is returned with no
        checking. If it is not None, then the returned transport is
        guaranteed to point to an existing directory ready for use.
        """
        raise NotImplementedError(self.get_workingtree_transport)

    @classmethod
    def create(cls, base, format=None, possible_transports=None):
        """Create a new BzrDir at the url 'base'.

        :param format: If supplied, the format of branch to create.  If not
            supplied, the default is used.
        :param possible_transports: If supplied, a list of transports that
            can be reused to share a remote connection.
        """
        if cls is not BzrDir:
            raise AssertionError("BzrDir.create always creates the "
                "default format, not one of %r" % cls)
        return controldir.ControlDir.create(base, format=format,
                possible_transports=possible_transports)

    def __repr__(self):
        return "<%s at %r>" % (self.__class__.__name__, self.user_url)

    def update_feature_flags(self, updated_flags):
        """Update the features required by this bzrdir.

        :param updated_flags: Dictionary mapping feature names to necessities
            A necessity can be None to indicate the feature should be removed
        """
        self.control_files.lock_write()
        try:
            self._format._update_feature_flags(updated_flags)
            self.transport.put_bytes('branch-format', self._format.as_string())
        finally:
            self.control_files.unlock()


class BzrDirMeta1(BzrDir):
    """A .bzr meta version 1 control object.

    This is the first control object where the
    individual aspects are really split out: there are separate repository,
    workingtree and branch subdirectories and any subset of the three can be
    present within a BzrDir.
    """

    def __init__(self, _transport, _format):
        super(BzrDirMeta1, self).__init__(_transport, _format)
        self.control_files = lockable_files.LockableFiles(self.control_transport,
            self._format._lock_file_name, self._format._lock_class)

    def can_convert_format(self):
        """See BzrDir.can_convert_format()."""
        return True

    def create_branch(self, name=None, repository=None,
            append_revisions_only=None):
        """See BzrDir.create_branch."""
        if name is None:
            name = self._get_selected_branch()
        return self._format.get_branch_format().initialize(self, name=name,
                repository=repository,
                append_revisions_only=append_revisions_only)

    def destroy_branch(self, name=None):
        """See BzrDir.create_branch."""
        if name is not None:
            raise errors.NoColocatedBranchSupport(self)
        self.transport.delete_tree('branch')

    def create_repository(self, shared=False):
        """See BzrDir.create_repository."""
        return self._format.repository_format.initialize(self, shared)

    def destroy_repository(self):
        """See BzrDir.destroy_repository."""
        try:
            self.transport.delete_tree('repository')
        except errors.NoSuchFile:
            raise errors.NoRepositoryPresent(self)

    def create_workingtree(self, revision_id=None, from_branch=None,
                           accelerator_tree=None, hardlink=False):
        """See BzrDir.create_workingtree."""
        return self._format.workingtree_format.initialize(
            self, revision_id, from_branch=from_branch,
            accelerator_tree=accelerator_tree, hardlink=hardlink)

    def destroy_workingtree(self):
        """See BzrDir.destroy_workingtree."""
        wt = self.open_workingtree(recommend_upgrade=False)
        repository = wt.branch.repository
        empty = repository.revision_tree(_mod_revision.NULL_REVISION)
        # We ignore the conflicts returned by wt.revert since we're about to
        # delete the wt metadata anyway, all that should be left here are
        # detritus. But see bug #634470 about subtree .bzr dirs.
        conflicts = wt.revert(old_tree=empty)
        self.destroy_workingtree_metadata()

    def destroy_workingtree_metadata(self):
        self.transport.delete_tree('checkout')

    def find_branch_format(self, name=None):
        """Find the branch 'format' for this bzrdir.

        This might be a synthetic object for e.g. RemoteBranch and SVN.
        """
        from bzrlib.branch import BranchFormatMetadir
        return BranchFormatMetadir.find_format(self, name=name)

    def _get_mkdir_mode(self):
        """Figure out the mode to use when creating a bzrdir subdir."""
        temp_control = lockable_files.LockableFiles(self.transport, '',
                                     lockable_files.TransportLock)
        return temp_control._dir_mode

    def get_branch_reference(self, name=None):
        """See BzrDir.get_branch_reference()."""
        from bzrlib.branch import BranchFormatMetadir
        format = BranchFormatMetadir.find_format(self, name=name)
        return format.get_reference(self, name=name)

    def get_branch_transport(self, branch_format, name=None):
        """See BzrDir.get_branch_transport()."""
        if name is not None:
            raise errors.NoColocatedBranchSupport(self)
        # XXX: this shouldn't implicitly create the directory if it's just
        # promising to get a transport -- mbp 20090727
        if branch_format is None:
            return self.transport.clone('branch')
        try:
            branch_format.get_format_string()
        except NotImplementedError:
            raise errors.IncompatibleFormat(branch_format, self._format)
        try:
            self.transport.mkdir('branch', mode=self._get_mkdir_mode())
        except errors.FileExists:
            pass
        return self.transport.clone('branch')

    def get_repository_transport(self, repository_format):
        """See BzrDir.get_repository_transport()."""
        if repository_format is None:
            return self.transport.clone('repository')
        try:
            repository_format.get_format_string()
        except NotImplementedError:
            raise errors.IncompatibleFormat(repository_format, self._format)
        try:
            self.transport.mkdir('repository', mode=self._get_mkdir_mode())
        except errors.FileExists:
            pass
        return self.transport.clone('repository')

    def get_workingtree_transport(self, workingtree_format):
        """See BzrDir.get_workingtree_transport()."""
        if workingtree_format is None:
            return self.transport.clone('checkout')
        try:
            workingtree_format.get_format_string()
        except NotImplementedError:
            raise errors.IncompatibleFormat(workingtree_format, self._format)
        try:
            self.transport.mkdir('checkout', mode=self._get_mkdir_mode())
        except errors.FileExists:
            pass
        return self.transport.clone('checkout')

    def has_workingtree(self):
        """Tell if this bzrdir contains a working tree.

        Note: if you're going to open the working tree, you should just go
        ahead and try, and not ask permission first.
        """
        from bzrlib.workingtree import WorkingTreeFormatMetaDir
        try:
            WorkingTreeFormatMetaDir.find_format_string(self)
        except errors.NoWorkingTree:
            return False
        return True

    def needs_format_conversion(self, format):
        """See BzrDir.needs_format_conversion()."""
        if (not isinstance(self._format, format.__class__) or
            self._format.get_format_string() != format.get_format_string()):
            # it is not a meta dir format, conversion is needed.
            return True
        # we might want to push this down to the repository?
        try:
            if not isinstance(self.open_repository()._format,
                              format.repository_format.__class__):
                # the repository needs an upgrade.
                return True
        except errors.NoRepositoryPresent:
            pass
        for branch in self.list_branches():
            if not isinstance(branch._format,
                              format.get_branch_format().__class__):
                # the branch needs an upgrade.
                return True
        try:
            my_wt = self.open_workingtree(recommend_upgrade=False)
            if not isinstance(my_wt._format,
                              format.workingtree_format.__class__):
                # the workingtree needs an upgrade.
                return True
        except (errors.NoWorkingTree, errors.NotLocalUrl):
            pass
        return False

    def open_branch(self, name=None, unsupported=False,
                    ignore_fallbacks=False, possible_transports=None):
        """See ControlDir.open_branch."""
        if name is None:
            name = self._get_selected_branch()
        format = self.find_branch_format(name=name)
        format.check_support_status(unsupported)
        return format.open(self, name=name,
            _found=True, ignore_fallbacks=ignore_fallbacks,
            possible_transports=possible_transports)

    def open_repository(self, unsupported=False):
        """See BzrDir.open_repository."""
        from bzrlib.repository import RepositoryFormatMetaDir
        format = RepositoryFormatMetaDir.find_format(self)
        format.check_support_status(unsupported)
        return format.open(self, _found=True)

    def open_workingtree(self, unsupported=False,
            recommend_upgrade=True):
        """See BzrDir.open_workingtree."""
        from bzrlib.workingtree import WorkingTreeFormatMetaDir
        format = WorkingTreeFormatMetaDir.find_format(self)
        format.check_support_status(unsupported, recommend_upgrade,
            basedir=self.root_transport.base)
        return format.open(self, _found=True)

    def _get_config(self):
        return config.TransportConfig(self.transport, 'control.conf')


class BzrDirMeta1Colo(BzrDirMeta1):
    """BzrDirMeta1 with support for colocated branches.

    This format is experimental, and will eventually be merged back into
    BzrDirMeta1.
    """

    def _get_branch_path(self, name):
        """Obtain the branch path to use.

        This uses the API specified branch name first, and then falls back to
        the branch name specified in the URL. If neither of those is specified,
        it uses the default branch.

        :param name: Optional branch name to use
        :return: Relative path to branch
        """
        if name is None:
            return 'branch'
        return urlutils.join('branches', name.encode("utf-8"))

    def _read_branch_list(self):
        """Read the branch list.

        :return: List of utf-8 encoded branch names.
        """
        try:
            f = self.control_transport.get('branch-list')
        except errors.NoSuchFile:
            return []

        ret = []
        try:
            for name in f:
                ret.append(name.rstrip("\n"))
        finally:
            f.close()
        return ret

    def _write_branch_list(self, branches):
        """Write out the branch list.

        :param branches: List of utf-8 branch names to write
        """
        self.transport.put_bytes('branch-list',
            "".join([name+"\n" for name in branches]))

    def destroy_branch(self, name=None):
        """See BzrDir.create_branch."""
        if name is None:
            name = self._get_selected_branch()
        path = self._get_branch_path(name)
        if name is not None:
            self.control_files.lock_write()
            try:
                branches = self._read_branch_list()
                try:
                    branches.remove(name.encode("utf-8"))
                except ValueError:
                    raise errors.NotBranchError(name)
                self._write_branch_list(branches)
            finally:
                self.control_files.unlock()
        self.transport.delete_tree(path)

    def get_branches(self):
        """See ControlDir.get_branches."""
        ret = {}
        try:
            ret[None] = self.open_branch()
        except (errors.NotBranchError, errors.NoRepositoryPresent):
            pass

        for name in self._read_branch_list():
            ret[name] = self.open_branch(name.decode('utf-8'))

        return ret

    def get_branch_transport(self, branch_format, name=None):
        """See BzrDir.get_branch_transport()."""
        path = self._get_branch_path(name)
        # XXX: this shouldn't implicitly create the directory if it's just
        # promising to get a transport -- mbp 20090727
        if branch_format is None:
            return self.transport.clone(path)
        try:
            branch_format.get_format_string()
        except NotImplementedError:
            raise errors.IncompatibleFormat(branch_format, self._format)
        if name is not None:
            try:
                self.transport.mkdir('branches', mode=self._get_mkdir_mode())
            except errors.FileExists:
                pass
            branches = self._read_branch_list()
            utf8_name = name.encode("utf-8")
            if not utf8_name in branches:
                self.control_files.lock_write()
                try:
                    branches = self._read_branch_list()
                    branches.append(utf8_name)
                    self._write_branch_list(branches)
                finally:
                    self.control_files.unlock()
        try:
            self.transport.mkdir(path, mode=self._get_mkdir_mode())
        except errors.FileExists:
            pass
        return self.transport.clone(path)


class BzrFormat(object):
    """Base class for all formats of things living in metadirs.

    This class manages the format string that is stored in the 'format'
    or 'branch-format' file.

    All classes for (branch-, repository-, workingtree-) formats that
    live in meta directories and have their own 'format' file
    (i.e. different from .bzr/branch-format) derive from this class,
    as well as the relevant base class for their kind
    (BranchFormat, WorkingTreeFormat, RepositoryFormat).

    Each format is identified by a "format" or "branch-format" file with a
    single line containing the base format name and then an optional list of
    feature flags.

    Feature flags are supported as of bzr 2.5. Setting feature flags on formats
    will render them inaccessible to older versions of bzr.

    :ivar features: Dictionary mapping feature names to their necessity
    """

    _present_features = set()

    def __init__(self):
        self.features = {}

    @classmethod
    def register_feature(cls, name):
        """Register a feature as being present.

        :param name: Name of the feature
        """
        if " " in name:
            raise ValueError("spaces are not allowed in feature names")
        if name in cls._present_features:
            raise errors.FeatureAlreadyRegistered(name)
        cls._present_features.add(name)

    @classmethod
    def unregister_feature(cls, name):
        """Unregister a feature."""
        cls._present_features.remove(name)

    def check_support_status(self, allow_unsupported, recommend_upgrade=True,
            basedir=None):
        for name, necessity in self.features.iteritems():
            if name in self._present_features:
                continue
            if necessity == "optional":
                mutter("ignoring optional missing feature %s", name)
                continue
            elif necessity == "required":
                raise errors.MissingFeature(name)
            else:
                mutter("treating unknown necessity as require for %s",
                       name)
                raise errors.MissingFeature(name)

    @classmethod
    def get_format_string(cls):
        """Return the ASCII format string that identifies this format."""
        raise NotImplementedError(cls.get_format_string)

    @classmethod
    def from_string(cls, text):
        format_string = cls.get_format_string()
        if not text.startswith(format_string):
            raise AssertionError("Invalid format header %r for %r" % (text, cls))
        lines = text[len(format_string):].splitlines()
        ret = cls()
        for lineno, line in enumerate(lines):
            try:
                (necessity, feature) = line.split(" ", 1)
            except ValueError:
                raise errors.ParseFormatError(format=cls, lineno=lineno+2,
                    line=line, text=text)
            ret.features[feature] = necessity
        return ret

    def as_string(self):
        """Return the string representation of this format.
        """
        lines = [self.get_format_string()]
        lines.extend([("%s %s\n" % (item[1], item[0])) for item in
            self.features.iteritems()])
        return "".join(lines)

    @classmethod
    def _find_format(klass, registry, kind, format_string):
        try:
            first_line = format_string[:format_string.index("\n")+1]
        except ValueError:
            first_line = format_string
        try:
            cls = registry.get(first_line)
        except KeyError:
            raise errors.UnknownFormatError(format=first_line, kind=kind)
        return cls.from_string(format_string)

    def network_name(self):
        """A simple byte string uniquely identifying this format for RPC calls.

        Metadir branch formats use their format string.
        """
        return self.as_string()

    def __eq__(self, other):
        return (self.__class__ is other.__class__ and
                self.features == other.features)

    def _update_feature_flags(self, updated_flags):
        """Update the feature flags in this format.

        :param updated_flags: Updated feature flags
        """
        for name, necessity in updated_flags.iteritems():
            if necessity is None:
                try:
                    del self.features[name]
                except KeyError:
                    pass
            else:
                self.features[name] = necessity


class BzrProber(controldir.Prober):
    """Prober for formats that use a .bzr/ control directory."""

    formats = registry.FormatRegistry(controldir.network_format_registry)
    """The known .bzr formats."""

    @classmethod
    @deprecated_method(deprecated_in((2, 4, 0)))
    def register_bzrdir_format(klass, format):
        klass.formats.register(format.get_format_string(), format)

    @classmethod
    @deprecated_method(deprecated_in((2, 4, 0)))
    def unregister_bzrdir_format(klass, format):
        klass.formats.remove(format.get_format_string())

    @classmethod
    def probe_transport(klass, transport):
        """Return the .bzrdir style format present in a directory."""
        try:
            format_string = transport.get_bytes(".bzr/branch-format")
        except errors.NoSuchFile:
            raise errors.NotBranchError(path=transport.base)
        try:
            first_line = format_string[:format_string.index("\n")+1]
        except ValueError:
            first_line = format_string
        try:
            cls = klass.formats.get(first_line)
        except KeyError:
            raise errors.UnknownFormatError(format=first_line, kind='bzrdir')
        return cls.from_string(format_string)

    @classmethod
    def known_formats(cls):
        result = set()
        for name, format in cls.formats.iteritems():
            if callable(format):
                format = format()
            result.add(format)
        return result


controldir.ControlDirFormat.register_prober(BzrProber)


class RemoteBzrProber(controldir.Prober):
    """Prober for remote servers that provide a Bazaar smart server."""

    @classmethod
    def probe_transport(klass, transport):
        """Return a RemoteBzrDirFormat object if it looks possible."""
        try:
            medium = transport.get_smart_medium()
        except (NotImplementedError, AttributeError,
                errors.TransportNotPossible, errors.NoSmartMedium,
                errors.SmartProtocolError):
            # no smart server, so not a branch for this format type.
            raise errors.NotBranchError(path=transport.base)
        else:
            # Decline to open it if the server doesn't support our required
            # version (3) so that the VFS-based transport will do it.
            if medium.should_probe():
                try:
                    server_version = medium.protocol_version()
                except errors.SmartProtocolError:
                    # Apparently there's no usable smart server there, even though
                    # the medium supports the smart protocol.
                    raise errors.NotBranchError(path=transport.base)
                if server_version != '2':
                    raise errors.NotBranchError(path=transport.base)
            from bzrlib.remote import RemoteBzrDirFormat
            return RemoteBzrDirFormat()

    @classmethod
    def known_formats(cls):
        from bzrlib.remote import RemoteBzrDirFormat
        return set([RemoteBzrDirFormat()])


class BzrDirFormat(BzrFormat, controldir.ControlDirFormat):
    """ControlDirFormat base class for .bzr/ directories.

    Formats are placed in a dict by their format string for reference
    during bzrdir opening. These should be subclasses of BzrDirFormat
    for consistency.

    Once a format is deprecated, just deprecate the initialize and open
    methods on the format class. Do not deprecate the object, as the
    object will be created every system load.
    """

    _lock_file_name = 'branch-lock'

    # _lock_class must be set in subclasses to the lock type, typ.
    # TransportLock or LockDir

    def initialize_on_transport(self, transport):
        """Initialize a new bzrdir in the base directory of a Transport."""
        try:
            # can we hand off the request to the smart server rather than using
            # vfs calls?
            client_medium = transport.get_smart_medium()
        except errors.NoSmartMedium:
            return self._initialize_on_transport_vfs(transport)
        else:
            # Current RPC's only know how to create bzr metadir1 instances, so
            # we still delegate to vfs methods if the requested format is not a
            # metadir1
            if type(self) != BzrDirMetaFormat1:
                return self._initialize_on_transport_vfs(transport)
            from bzrlib.remote import RemoteBzrDirFormat
            remote_format = RemoteBzrDirFormat()
            self._supply_sub_formats_to(remote_format)
            return remote_format.initialize_on_transport(transport)

    def initialize_on_transport_ex(self, transport, use_existing_dir=False,
        create_prefix=False, force_new_repo=False, stacked_on=None,
        stack_on_pwd=None, repo_format_name=None, make_working_trees=None,
        shared_repo=False, vfs_only=False):
        """Create this format on transport.

        The directory to initialize will be created.

        :param force_new_repo: Do not use a shared repository for the target,
                               even if one is available.
        :param create_prefix: Create any missing directories leading up to
            to_transport.
        :param use_existing_dir: Use an existing directory if one exists.
        :param stacked_on: A url to stack any created branch on, None to follow
            any target stacking policy.
        :param stack_on_pwd: If stack_on is relative, the location it is
            relative to.
        :param repo_format_name: If non-None, a repository will be
            made-or-found. Should none be found, or if force_new_repo is True
            the repo_format_name is used to select the format of repository to
            create.
        :param make_working_trees: Control the setting of make_working_trees
            for a new shared repository when one is made. None to use whatever
            default the format has.
        :param shared_repo: Control whether made repositories are shared or
            not.
        :param vfs_only: If True do not attempt to use a smart server
        :return: repo, bzrdir, require_stacking, repository_policy. repo is
            None if none was created or found, bzrdir is always valid.
            require_stacking is the result of examining the stacked_on
            parameter and any stacking policy found for the target.
        """
        if not vfs_only:
            # Try to hand off to a smart server 
            try:
                client_medium = transport.get_smart_medium()
            except errors.NoSmartMedium:
                pass
            else:
                from bzrlib.remote import RemoteBzrDirFormat
                # TODO: lookup the local format from a server hint.
                remote_dir_format = RemoteBzrDirFormat()
                remote_dir_format._network_name = self.network_name()
                self._supply_sub_formats_to(remote_dir_format)
                return remote_dir_format.initialize_on_transport_ex(transport,
                    use_existing_dir=use_existing_dir, create_prefix=create_prefix,
                    force_new_repo=force_new_repo, stacked_on=stacked_on,
                    stack_on_pwd=stack_on_pwd, repo_format_name=repo_format_name,
                    make_working_trees=make_working_trees, shared_repo=shared_repo)
        # XXX: Refactor the create_prefix/no_create_prefix code into a
        #      common helper function
        # The destination may not exist - if so make it according to policy.
        def make_directory(transport):
            transport.mkdir('.')
            return transport
        def redirected(transport, e, redirection_notice):
            note(redirection_notice)
            return transport._redirected_to(e.source, e.target)
        try:
            transport = do_catching_redirections(make_directory, transport,
                redirected)
        except errors.FileExists:
            if not use_existing_dir:
                raise
        except errors.NoSuchFile:
            if not create_prefix:
                raise
            transport.create_prefix()

        require_stacking = (stacked_on is not None)
        # Now the target directory exists, but doesn't have a .bzr
        # directory. So we need to create it, along with any work to create
        # all of the dependent branches, etc.

        result = self.initialize_on_transport(transport)
        if repo_format_name:
            try:
                # use a custom format
                result._format.repository_format = \
                    repository.network_format_registry.get(repo_format_name)
            except AttributeError:
                # The format didn't permit it to be set.
                pass
            # A repository is desired, either in-place or shared.
            repository_policy = result.determine_repository_policy(
                force_new_repo, stacked_on, stack_on_pwd,
                require_stacking=require_stacking)
            result_repo, is_new_repo = repository_policy.acquire_repository(
                make_working_trees, shared_repo)
            if not require_stacking and repository_policy._require_stacking:
                require_stacking = True
                result._format.require_stacking()
            result_repo.lock_write()
        else:
            result_repo = None
            repository_policy = None
        return result_repo, result, require_stacking, repository_policy

    def _initialize_on_transport_vfs(self, transport):
        """Initialize a new bzrdir using VFS calls.

        :param transport: The transport to create the .bzr directory in.
        :return: A
        """
        # Since we are creating a .bzr directory, inherit the
        # mode from the root directory
        temp_control = lockable_files.LockableFiles(transport,
                            '', lockable_files.TransportLock)
        temp_control._transport.mkdir('.bzr',
                                      # FIXME: RBC 20060121 don't peek under
                                      # the covers
                                      mode=temp_control._dir_mode)
        if sys.platform == 'win32' and isinstance(transport, local.LocalTransport):
            win32utils.set_file_attr_hidden(transport._abspath('.bzr'))
        file_mode = temp_control._file_mode
        del temp_control
        bzrdir_transport = transport.clone('.bzr')
        utf8_files = [('README',
                       "This is a Bazaar control directory.\n"
                       "Do not change any files in this directory.\n"
                       "See http://bazaar.canonical.com/ for more information about Bazaar.\n"),
                      ('branch-format', self.as_string()),
                      ]
        # NB: no need to escape relative paths that are url safe.
        control_files = lockable_files.LockableFiles(bzrdir_transport,
            self._lock_file_name, self._lock_class)
        control_files.create_lock()
        control_files.lock_write()
        try:
            for (filename, content) in utf8_files:
                bzrdir_transport.put_bytes(filename, content,
                    mode=file_mode)
        finally:
            control_files.unlock()
        return self.open(transport, _found=True)

    def open(self, transport, _found=False):
        """Return an instance of this format for the dir transport points at.

        _found is a private parameter, do not use it.
        """
        if not _found:
            found_format = controldir.ControlDirFormat.find_format(transport)
            if not isinstance(found_format, self.__class__):
                raise AssertionError("%s was asked to open %s, but it seems to need "
                        "format %s"
                        % (self, transport, found_format))
            # Allow subclasses - use the found format.
            self._supply_sub_formats_to(found_format)
            return found_format._open(transport)
        return self._open(transport)

    def _open(self, transport):
        """Template method helper for opening BzrDirectories.

        This performs the actual open and any additional logic or parameter
        passing.
        """
        raise NotImplementedError(self._open)

    def _supply_sub_formats_to(self, other_format):
        """Give other_format the same values for sub formats as this has.

        This method is expected to be used when parameterising a
        RemoteBzrDirFormat instance with the parameters from a
        BzrDirMetaFormat1 instance.

        :param other_format: other_format is a format which should be
            compatible with whatever sub formats are supported by self.
        :return: None.
        """
        other_format.features = dict(self.features)

    def supports_transport(self, transport):
        # bzr formats can be opened over all known transports
        return True

    def check_support_status(self, allow_unsupported, recommend_upgrade=True,
            basedir=None):
        controldir.ControlDirFormat.check_support_status(self,
            allow_unsupported=allow_unsupported, recommend_upgrade=recommend_upgrade,
            basedir=basedir)
        BzrFormat.check_support_status(self, allow_unsupported=allow_unsupported,
            recommend_upgrade=recommend_upgrade, basedir=basedir)


class BzrDirMetaFormat1(BzrDirFormat):
    """Bzr meta control format 1

    This is the first format with split out working tree, branch and repository
    disk storage.

    It has:

    - Format 3 working trees [optional]
    - Format 5 branches [optional]
    - Format 7 repositories [optional]
    """

    _lock_class = lockdir.LockDir

    fixed_components = False

    colocated_branches = False

    def __init__(self):
        BzrDirFormat.__init__(self)
        self._workingtree_format = None
        self._branch_format = None
        self._repository_format = None

    def __eq__(self, other):
        if other.__class__ is not self.__class__:
            return False
        if other.repository_format != self.repository_format:
            return False
        if other.workingtree_format != self.workingtree_format:
            return False
        if other.features != self.features:
            return False
        return True

    def __ne__(self, other):
        return not self == other

    def get_branch_format(self):
        if self._branch_format is None:
            from bzrlib.branch import format_registry as branch_format_registry
            self._branch_format = branch_format_registry.get_default()
        return self._branch_format

    def set_branch_format(self, format):
        self._branch_format = format

    def require_stacking(self, stack_on=None, possible_transports=None,
            _skip_repo=False):
        """We have a request to stack, try to ensure the formats support it.

        :param stack_on: If supplied, it is the URL to a branch that we want to
            stack on. Check to see if that format supports stacking before
            forcing an upgrade.
        """
        # Stacking is desired. requested by the target, but does the place it
        # points at support stacking? If it doesn't then we should
        # not implicitly upgrade. We check this here.
        new_repo_format = None
        new_branch_format = None

        # a bit of state for get_target_branch so that we don't try to open it
        # 2 times, for both repo *and* branch
        target = [None, False, None] # target_branch, checked, upgrade anyway
        def get_target_branch():
            if target[1]:
                # We've checked, don't check again
                return target
            if stack_on is None:
                # No target format, that means we want to force upgrading
                target[:] = [None, True, True]
                return target
            try:
                target_dir = BzrDir.open(stack_on,
                    possible_transports=possible_transports)
            except errors.NotBranchError:
                # Nothing there, don't change formats
                target[:] = [None, True, False]
                return target
            except errors.JailBreak:
                # JailBreak, JFDI and upgrade anyway
                target[:] = [None, True, True]
                return target
            try:
                target_branch = target_dir.open_branch()
            except errors.NotBranchError:
                # No branch, don't upgrade formats
                target[:] = [None, True, False]
                return target
            target[:] = [target_branch, True, False]
            return target

        if (not _skip_repo and
                 not self.repository_format.supports_external_lookups):
            # We need to upgrade the Repository.
            target_branch, _, do_upgrade = get_target_branch()
            if target_branch is None:
                # We don't have a target branch, should we upgrade anyway?
                if do_upgrade:
                    # stack_on is inaccessible, JFDI.
                    # TODO: bad monkey, hard-coded formats...
                    if self.repository_format.rich_root_data:
                        new_repo_format = knitpack_repo.RepositoryFormatKnitPack5RichRoot()
                    else:
                        new_repo_format = knitpack_repo.RepositoryFormatKnitPack5()
            else:
                # If the target already supports stacking, then we know the
                # project is already able to use stacking, so auto-upgrade
                # for them
                new_repo_format = target_branch.repository._format
                if not new_repo_format.supports_external_lookups:
                    # target doesn't, source doesn't, so don't auto upgrade
                    # repo
                    new_repo_format = None
            if new_repo_format is not None:
                self.repository_format = new_repo_format
                note(gettext('Source repository format does not support stacking,'
                     ' using format:\n  %s'),
                     new_repo_format.get_format_description())

        if not self.get_branch_format().supports_stacking():
            # We just checked the repo, now lets check if we need to
            # upgrade the branch format
            target_branch, _, do_upgrade = get_target_branch()
            if target_branch is None:
                if do_upgrade:
                    # TODO: bad monkey, hard-coded formats...
                    from bzrlib.branch import BzrBranchFormat7
                    new_branch_format = BzrBranchFormat7()
            else:
                new_branch_format = target_branch._format
                if not new_branch_format.supports_stacking():
                    new_branch_format = None
            if new_branch_format is not None:
                # Does support stacking, use its format.
                self.set_branch_format(new_branch_format)
                note(gettext('Source branch format does not support stacking,'
                     ' using format:\n  %s'),
                     new_branch_format.get_format_description())

    def get_converter(self, format=None):
        """See BzrDirFormat.get_converter()."""
        if format is None:
            format = BzrDirFormat.get_default_format()
        if (type(self) is BzrDirMetaFormat1 and
            type(format) is BzrDirMetaFormat1Colo):
            return ConvertMetaToColo(format)
        if (type(self) is BzrDirMetaFormat1Colo and
            type(format) is BzrDirMetaFormat1):
            return ConvertMetaRemoveColo(format)
        if not isinstance(self, format.__class__):
            # converting away from metadir is not implemented
            raise NotImplementedError(self.get_converter)
        return ConvertMetaToMeta(format)

    @classmethod
    def get_format_string(cls):
        """See BzrDirFormat.get_format_string()."""
        return "Bazaar-NG meta directory, format 1\n"

    def get_format_description(self):
        """See BzrDirFormat.get_format_description()."""
        return "Meta directory format 1"

    def _open(self, transport):
        """See BzrDirFormat._open."""
        # Create a new format instance because otherwise initialisation of new
        # metadirs share the global default format object leading to alias
        # problems.
        format = BzrDirMetaFormat1()
        self._supply_sub_formats_to(format)
        return BzrDirMeta1(transport, format)

    def __return_repository_format(self):
        """Circular import protection."""
        if self._repository_format:
            return self._repository_format
        from bzrlib.repository import format_registry
        return format_registry.get_default()

    def _set_repository_format(self, value):
        """Allow changing the repository format for metadir formats."""
        self._repository_format = value

    repository_format = property(__return_repository_format,
        _set_repository_format)

    def _supply_sub_formats_to(self, other_format):
        """Give other_format the same values for sub formats as this has.

        This method is expected to be used when parameterising a
        RemoteBzrDirFormat instance with the parameters from a
        BzrDirMetaFormat1 instance.

        :param other_format: other_format is a format which should be
            compatible with whatever sub formats are supported by self.
        :return: None.
        """
        super(BzrDirMetaFormat1, self)._supply_sub_formats_to(other_format)
        if getattr(self, '_repository_format', None) is not None:
            other_format.repository_format = self.repository_format
        if self._branch_format is not None:
            other_format._branch_format = self._branch_format
        if self._workingtree_format is not None:
            other_format.workingtree_format = self.workingtree_format

    def __get_workingtree_format(self):
        if self._workingtree_format is None:
            from bzrlib.workingtree import (
                format_registry as wt_format_registry,
                )
            self._workingtree_format = wt_format_registry.get_default()
        return self._workingtree_format

    def __set_workingtree_format(self, wt_format):
        self._workingtree_format = wt_format

    def __repr__(self):
        return "<%r>" % (self.__class__.__name__,)

    workingtree_format = property(__get_workingtree_format,
                                  __set_workingtree_format)


# Register bzr formats
BzrProber.formats.register(BzrDirMetaFormat1.get_format_string(),
    BzrDirMetaFormat1)
controldir.ControlDirFormat._default_format = BzrDirMetaFormat1()


class BzrDirMetaFormat1Colo(BzrDirMetaFormat1):
    """BzrDirMeta1 format with support for colocated branches."""

    colocated_branches = True

    @classmethod
    def get_format_string(cls):
        """See BzrDirFormat.get_format_string()."""
        return "Bazaar meta directory, format 1 (with colocated branches)\n"

    def get_format_description(self):
        """See BzrDirFormat.get_format_description()."""
        return "Meta directory format 1 with support for colocated branches"

    def _open(self, transport):
        """See BzrDirFormat._open."""
        # Create a new format instance because otherwise initialisation of new
        # metadirs share the global default format object leading to alias
        # problems.
        format = BzrDirMetaFormat1Colo()
        self._supply_sub_formats_to(format)
        return BzrDirMeta1Colo(transport, format)


BzrProber.formats.register(BzrDirMetaFormat1Colo.get_format_string(),
    BzrDirMetaFormat1Colo)


class ConvertMetaToMeta(controldir.Converter):
    """Converts the components of metadirs."""

    def __init__(self, target_format):
        """Create a metadir to metadir converter.

        :param target_format: The final metadir format that is desired.
        """
        self.target_format = target_format

    def convert(self, to_convert, pb):
        """See Converter.convert()."""
        self.bzrdir = to_convert
        self.pb = ui.ui_factory.nested_progress_bar()
        self.count = 0
        self.total = 1
        self.step('checking repository format')
        try:
            repo = self.bzrdir.open_repository()
        except errors.NoRepositoryPresent:
            pass
        else:
            if not isinstance(repo._format, self.target_format.repository_format.__class__):
                from bzrlib.repository import CopyConverter
                ui.ui_factory.note(gettext('starting repository conversion'))
                converter = CopyConverter(self.target_format.repository_format)
                converter.convert(repo, pb)
        for branch in self.bzrdir.list_branches():
            # TODO: conversions of Branch and Tree should be done by
            # InterXFormat lookups/some sort of registry.
            # Avoid circular imports
            old = branch._format.__class__
            new = self.target_format.get_branch_format().__class__
            while old != new:
                if (old == _mod_branch.BzrBranchFormat5 and
                    new in (_mod_branch.BzrBranchFormat6,
                        _mod_branch.BzrBranchFormat7,
                        _mod_branch.BzrBranchFormat8)):
                    branch_converter = _mod_branch.Converter5to6()
                elif (old == _mod_branch.BzrBranchFormat6 and
                    new in (_mod_branch.BzrBranchFormat7,
                            _mod_branch.BzrBranchFormat8)):
                    branch_converter = _mod_branch.Converter6to7()
                elif (old == _mod_branch.BzrBranchFormat7 and
                      new is _mod_branch.BzrBranchFormat8):
                    branch_converter = _mod_branch.Converter7to8()
                else:
                    raise errors.BadConversionTarget("No converter", new,
                        branch._format)
                branch_converter.convert(branch)
                branch = self.bzrdir.open_branch()
                old = branch._format.__class__
        try:
            tree = self.bzrdir.open_workingtree(recommend_upgrade=False)
        except (errors.NoWorkingTree, errors.NotLocalUrl):
            pass
        else:
            # TODO: conversions of Branch and Tree should be done by
            # InterXFormat lookups
            if (isinstance(tree, workingtree_3.WorkingTree3) and
                not isinstance(tree, workingtree_4.DirStateWorkingTree) and
                isinstance(self.target_format.workingtree_format,
                    workingtree_4.DirStateWorkingTreeFormat)):
                workingtree_4.Converter3to4().convert(tree)
            if (isinstance(tree, workingtree_4.DirStateWorkingTree) and
                not isinstance(tree, workingtree_4.WorkingTree5) and
                isinstance(self.target_format.workingtree_format,
                    workingtree_4.WorkingTreeFormat5)):
                workingtree_4.Converter4to5().convert(tree)
            if (isinstance(tree, workingtree_4.DirStateWorkingTree) and
                not isinstance(tree, workingtree_4.WorkingTree6) and
                isinstance(self.target_format.workingtree_format,
                    workingtree_4.WorkingTreeFormat6)):
                workingtree_4.Converter4or5to6().convert(tree)
        self.pb.finished()
        return to_convert


class ConvertMetaToColo(controldir.Converter):
    """Add colocated branch support."""

    def __init__(self, target_format):
        """Create a converter.that upgrades a metadir to the colo format.

        :param target_format: The final metadir format that is desired.
        """
        self.target_format = target_format

    def convert(self, to_convert, pb):
        """See Converter.convert()."""
        to_convert.transport.put_bytes('branch-format',
            self.target_format.as_string())
        return BzrDir.open_from_transport(to_convert.root_transport)


class ConvertMetaRemoveColo(controldir.Converter):
    """Remove colocated branch support from a bzrdir."""

    def __init__(self, target_format):
        """Create a converter.that downgrades a colocated branch metadir
        to a regular metadir.

        :param target_format: The final metadir format that is desired.
        """
        self.target_format = target_format

    def convert(self, to_convert, pb):
        """See Converter.convert()."""
        to_convert.control_files.lock_write()
        try:
            branches = to_convert.list_branches()
            if len(branches) > 1:
                raise errors.BzrError("remove all but a single "
                    "colocated branch when downgrading")
        finally:
            to_convert.control_files.unlock()
        to_convert.transport.put_bytes('branch-format',
            self.target_format.as_string())
        return BzrDir.open_from_transport(to_convert.root_transport)


controldir.ControlDirFormat.register_server_prober(RemoteBzrProber)


class RepositoryAcquisitionPolicy(object):
    """Abstract base class for repository acquisition policies.

    A repository acquisition policy decides how a BzrDir acquires a repository
    for a branch that is being created.  The most basic policy decision is
    whether to create a new repository or use an existing one.
    """
    def __init__(self, stack_on, stack_on_pwd, require_stacking):
        """Constructor.

        :param stack_on: A location to stack on
        :param stack_on_pwd: If stack_on is relative, the location it is
            relative to.
        :param require_stacking: If True, it is a failure to not stack.
        """
        self._stack_on = stack_on
        self._stack_on_pwd = stack_on_pwd
        self._require_stacking = require_stacking

    def configure_branch(self, branch):
        """Apply any configuration data from this policy to the branch.

        Default implementation sets repository stacking.
        """
        if self._stack_on is None:
            return
        if self._stack_on_pwd is None:
            stack_on = self._stack_on
        else:
            try:
                stack_on = urlutils.rebase_url(self._stack_on,
                    self._stack_on_pwd,
                    branch.user_url)
            except errors.InvalidRebaseURLs:
                stack_on = self._get_full_stack_on()
        try:
            branch.set_stacked_on_url(stack_on)
        except (errors.UnstackableBranchFormat,
                errors.UnstackableRepositoryFormat):
            if self._require_stacking:
                raise

    def requires_stacking(self):
        """Return True if this policy requires stacking."""
        return self._stack_on is not None and self._require_stacking

    def _get_full_stack_on(self):
        """Get a fully-qualified URL for the stack_on location."""
        if self._stack_on is None:
            return None
        if self._stack_on_pwd is None:
            return self._stack_on
        else:
            return urlutils.join(self._stack_on_pwd, self._stack_on)

    def _add_fallback(self, repository, possible_transports=None):
        """Add a fallback to the supplied repository, if stacking is set."""
        stack_on = self._get_full_stack_on()
        if stack_on is None:
            return
        try:
            stacked_dir = BzrDir.open(stack_on,
                                      possible_transports=possible_transports)
        except errors.JailBreak:
            # We keep the stacking details, but we are in the server code so
            # actually stacking is not needed.
            return
        try:
            stacked_repo = stacked_dir.open_branch().repository
        except errors.NotBranchError:
            stacked_repo = stacked_dir.open_repository()
        try:
            repository.add_fallback_repository(stacked_repo)
        except errors.UnstackableRepositoryFormat:
            if self._require_stacking:
                raise
        else:
            self._require_stacking = True

    def acquire_repository(self, make_working_trees=None, shared=False,
            possible_transports=None):
        """Acquire a repository for this bzrdir.

        Implementations may create a new repository or use a pre-exising
        repository.

        :param make_working_trees: If creating a repository, set
            make_working_trees to this value (if non-None)
        :param shared: If creating a repository, make it shared if True
        :return: A repository, is_new_flag (True if the repository was
            created).
        """
        raise NotImplementedError(RepositoryAcquisitionPolicy.acquire_repository)


class CreateRepository(RepositoryAcquisitionPolicy):
    """A policy of creating a new repository"""

    def __init__(self, bzrdir, stack_on=None, stack_on_pwd=None,
                 require_stacking=False):
        """Constructor.

        :param bzrdir: The bzrdir to create the repository on.
        :param stack_on: A location to stack on
        :param stack_on_pwd: If stack_on is relative, the location it is
            relative to.
        """
        RepositoryAcquisitionPolicy.__init__(self, stack_on, stack_on_pwd,
                                             require_stacking)
        self._bzrdir = bzrdir

    def acquire_repository(self, make_working_trees=None, shared=False,
            possible_transports=None):
        """Implementation of RepositoryAcquisitionPolicy.acquire_repository

        Creates the desired repository in the bzrdir we already have.
        """
        if possible_transports is None:
            possible_transports = []
        else:
            possible_transports = list(possible_transports)
        possible_transports.append(self._bzrdir.root_transport)
        stack_on = self._get_full_stack_on()
        if stack_on:
            format = self._bzrdir._format
            format.require_stacking(stack_on=stack_on,
                                    possible_transports=possible_transports)
            if not self._require_stacking:
                # We have picked up automatic stacking somewhere.
                note(gettext('Using default stacking branch {0} at {1}').format(
                    self._stack_on, self._stack_on_pwd))
        repository = self._bzrdir.create_repository(shared=shared)
        self._add_fallback(repository,
                           possible_transports=possible_transports)
        if make_working_trees is not None:
            repository.set_make_working_trees(make_working_trees)
        return repository, True


class UseExistingRepository(RepositoryAcquisitionPolicy):
    """A policy of reusing an existing repository"""

    def __init__(self, repository, stack_on=None, stack_on_pwd=None,
                 require_stacking=False):
        """Constructor.

        :param repository: The repository to use.
        :param stack_on: A location to stack on
        :param stack_on_pwd: If stack_on is relative, the location it is
            relative to.
        """
        RepositoryAcquisitionPolicy.__init__(self, stack_on, stack_on_pwd,
                                             require_stacking)
        self._repository = repository

    def acquire_repository(self, make_working_trees=None, shared=False,
            possible_transports=None):
        """Implementation of RepositoryAcquisitionPolicy.acquire_repository

        Returns an existing repository to use.
        """
        if possible_transports is None:
            possible_transports = []
        else:
            possible_transports = list(possible_transports)
        possible_transports.append(self._repository.bzrdir.transport)
        self._add_fallback(self._repository,
                       possible_transports=possible_transports)
        return self._repository, False


def register_metadir(registry, key,
         repository_format, help, native=True, deprecated=False,
         branch_format=None,
         tree_format=None,
         hidden=False,
         experimental=False,
         alias=False, bzrdir_format=None):
    """Register a metadir subformat.

    These all use a meta bzrdir, but can be parameterized by the
    Repository/Branch/WorkingTreeformats.

    :param repository_format: The fully-qualified repository format class
        name as a string.
    :param branch_format: Fully-qualified branch format class name as
        a string.
    :param tree_format: Fully-qualified tree format class name as
        a string.
    """
    if bzrdir_format is None:
        bzrdir_format = BzrDirMetaFormat1
    # This should be expanded to support setting WorkingTree and Branch
    # formats, once the API supports that.
    def _load(full_name):
        mod_name, factory_name = full_name.rsplit('.', 1)
        try:
            factory = pyutils.get_named_object(mod_name, factory_name)
        except ImportError, e:
            raise ImportError('failed to load %s: %s' % (full_name, e))
        except AttributeError:
            raise AttributeError('no factory %s in module %r'
                % (full_name, sys.modules[mod_name]))
        return factory()

    def helper():
        bd = bzrdir_format()
        if branch_format is not None:
            bd.set_branch_format(_load(branch_format))
        if tree_format is not None:
            bd.workingtree_format = _load(tree_format)
        if repository_format is not None:
            bd.repository_format = _load(repository_format)
        return bd
    registry.register(key, helper, help, native, deprecated, hidden,
        experimental, alias)

register_metadir(controldir.format_registry, 'knit',
    'bzrlib.repofmt.knitrepo.RepositoryFormatKnit1',
    'Format using knits.  Recommended for interoperation with bzr <= 0.14.',
    branch_format='bzrlib.branch.BzrBranchFormat5',
    tree_format='bzrlib.workingtree_3.WorkingTreeFormat3',
    hidden=True,
    deprecated=True)
register_metadir(controldir.format_registry, 'dirstate',
    'bzrlib.repofmt.knitrepo.RepositoryFormatKnit1',
    help='Format using dirstate for working trees. '
        'Compatible with bzr 0.8 and '
        'above when accessed over the network. Introduced in bzr 0.15.',
    branch_format='bzrlib.branch.BzrBranchFormat5',
    tree_format='bzrlib.workingtree_4.WorkingTreeFormat4',
    hidden=True,
    deprecated=True)
register_metadir(controldir.format_registry, 'dirstate-tags',
    'bzrlib.repofmt.knitrepo.RepositoryFormatKnit1',
    help='Variant of dirstate with support for tags. '
        'Introduced in bzr 0.15.',
    branch_format='bzrlib.branch.BzrBranchFormat6',
    tree_format='bzrlib.workingtree_4.WorkingTreeFormat4',
    hidden=True,
    deprecated=True)
register_metadir(controldir.format_registry, 'rich-root',
    'bzrlib.repofmt.knitrepo.RepositoryFormatKnit4',
    help='Variant of dirstate with better handling of tree roots. '
        'Introduced in bzr 1.0',
    branch_format='bzrlib.branch.BzrBranchFormat6',
    tree_format='bzrlib.workingtree_4.WorkingTreeFormat4',
    hidden=True,
    deprecated=True)
register_metadir(controldir.format_registry, 'dirstate-with-subtree',
    'bzrlib.repofmt.knitrepo.RepositoryFormatKnit3',
    help='Variant of dirstate with support for nested trees. '
         'Introduced in 0.15.',
    branch_format='bzrlib.branch.BzrBranchFormat6',
    tree_format='bzrlib.workingtree_4.WorkingTreeFormat4',
    experimental=True,
    hidden=True,
    )
register_metadir(controldir.format_registry, 'pack-0.92',
    'bzrlib.repofmt.knitpack_repo.RepositoryFormatKnitPack1',
    help='Pack-based format used in 1.x series. Introduced in 0.92. '
        'Interoperates with bzr repositories before 0.92 but cannot be '
        'read by bzr < 0.92. '
        ,
    branch_format='bzrlib.branch.BzrBranchFormat6',
    tree_format='bzrlib.workingtree_4.WorkingTreeFormat4',
    deprecated=True,
    )
register_metadir(controldir.format_registry, 'pack-0.92-subtree',
    'bzrlib.repofmt.knitpack_repo.RepositoryFormatKnitPack3',
    help='Pack-based format used in 1.x series, with subtree support. '
        'Introduced in 0.92. Interoperates with '
        'bzr repositories before 0.92 but cannot be read by bzr < 0.92. '
        ,
    branch_format='bzrlib.branch.BzrBranchFormat6',
    tree_format='bzrlib.workingtree_4.WorkingTreeFormat4',
    hidden=True,
    deprecated=True,
    experimental=True,
    )
register_metadir(controldir.format_registry, 'rich-root-pack',
    'bzrlib.repofmt.knitpack_repo.RepositoryFormatKnitPack4',
    help='A variant of pack-0.92 that supports rich-root data '
         '(needed for bzr-svn and bzr-git). Introduced in 1.0.',
    branch_format='bzrlib.branch.BzrBranchFormat6',
    tree_format='bzrlib.workingtree_4.WorkingTreeFormat4',
    hidden=True,
    deprecated=True,
    )
register_metadir(controldir.format_registry, '1.6',
    'bzrlib.repofmt.knitpack_repo.RepositoryFormatKnitPack5',
    help='A format that allows a branch to indicate that there is another '
         '(stacked) repository that should be used to access data that is '
         'not present locally.',
    branch_format='bzrlib.branch.BzrBranchFormat7',
    tree_format='bzrlib.workingtree_4.WorkingTreeFormat4',
    hidden=True,
    deprecated=True,
    )
register_metadir(controldir.format_registry, '1.6.1-rich-root',
    'bzrlib.repofmt.knitpack_repo.RepositoryFormatKnitPack5RichRoot',
    help='A variant of 1.6 that supports rich-root data '
         '(needed for bzr-svn and bzr-git).',
    branch_format='bzrlib.branch.BzrBranchFormat7',
    tree_format='bzrlib.workingtree_4.WorkingTreeFormat4',
    hidden=True,
    deprecated=True,
    )
register_metadir(controldir.format_registry, '1.9',
    'bzrlib.repofmt.knitpack_repo.RepositoryFormatKnitPack6',
    help='A repository format using B+tree indexes. These indexes '
         'are smaller in size, have smarter caching and provide faster '
         'performance for most operations.',
    branch_format='bzrlib.branch.BzrBranchFormat7',
    tree_format='bzrlib.workingtree_4.WorkingTreeFormat4',
    hidden=True,
    deprecated=True,
    )
register_metadir(controldir.format_registry, '1.9-rich-root',
    'bzrlib.repofmt.knitpack_repo.RepositoryFormatKnitPack6RichRoot',
    help='A variant of 1.9 that supports rich-root data '
         '(needed for bzr-svn and bzr-git).',
    branch_format='bzrlib.branch.BzrBranchFormat7',
    tree_format='bzrlib.workingtree_4.WorkingTreeFormat4',
    hidden=True,
    deprecated=True,
    )
register_metadir(controldir.format_registry, '1.14',
    'bzrlib.repofmt.knitpack_repo.RepositoryFormatKnitPack6',
    help='A working-tree format that supports content filtering.',
    branch_format='bzrlib.branch.BzrBranchFormat7',
    tree_format='bzrlib.workingtree_4.WorkingTreeFormat5',
    hidden=True,
    deprecated=True,
    )
register_metadir(controldir.format_registry, '1.14-rich-root',
    'bzrlib.repofmt.knitpack_repo.RepositoryFormatKnitPack6RichRoot',
    help='A variant of 1.14 that supports rich-root data '
         '(needed for bzr-svn and bzr-git).',
    branch_format='bzrlib.branch.BzrBranchFormat7',
    tree_format='bzrlib.workingtree_4.WorkingTreeFormat5',
    hidden=True,
    deprecated=True,
    )
# The following un-numbered 'development' formats should always just be aliases.
register_metadir(controldir.format_registry, 'development-subtree',
    'bzrlib.repofmt.groupcompress_repo.RepositoryFormat2aSubtree',
    help='Current development format, subtree variant. Can convert data to and '
        'from pack-0.92-subtree (and anything compatible with '
        'pack-0.92-subtree) format repositories. Repositories and branches in '
        'this format can only be read by bzr.dev. Please read '
        'http://doc.bazaar.canonical.com/latest/developers/development-repo.html '
        'before use.',
    branch_format='bzrlib.branch.BzrBranchFormat7',
    tree_format='bzrlib.workingtree_4.WorkingTreeFormat6',
    experimental=True,
    hidden=True,
    alias=False, # Restore to being an alias when an actual development subtree format is added
                 # This current non-alias status is simply because we did not introduce a
                 # chk based subtree format.
    )
register_metadir(controldir.format_registry, 'development5-subtree',
    'bzrlib.repofmt.knitpack_repo.RepositoryFormatPackDevelopment2Subtree',
    help='Development format, subtree variant. Can convert data to and '
        'from pack-0.92-subtree (and anything compatible with '
        'pack-0.92-subtree) format repositories. Repositories and branches in '
        'this format can only be read by bzr.dev. Please read '
        'http://doc.bazaar.canonical.com/latest/developers/development-repo.html '
        'before use.',
    branch_format='bzrlib.branch.BzrBranchFormat7',
    tree_format='bzrlib.workingtree_4.WorkingTreeFormat6',
    experimental=True,
    hidden=True,
    alias=False,
    )

register_metadir(controldir.format_registry, 'development-colo',
    'bzrlib.repofmt.groupcompress_repo.RepositoryFormat2a',
    help='The 2a format with experimental support for colocated branches.\n',
    branch_format='bzrlib.branch.BzrBranchFormat7',
    tree_format='bzrlib.workingtree_4.WorkingTreeFormat6',
    experimental=True,
    bzrdir_format=BzrDirMetaFormat1Colo,
    )


# And the development formats above will have aliased one of the following:

# Finally, the current format.
register_metadir(controldir.format_registry, '2a',
    'bzrlib.repofmt.groupcompress_repo.RepositoryFormat2a',
    help='Format for the bzr 2.0 series.\n'
        'Uses group-compress storage.\n'
        'Provides rich roots which are a one-way transition.\n',
        # 'storage in packs, 255-way hashed CHK inventory, bencode revision, group compress, '
        # 'rich roots. Supported by bzr 1.16 and later.',
    branch_format='bzrlib.branch.BzrBranchFormat7',
    tree_format='bzrlib.workingtree_4.WorkingTreeFormat6',
    experimental=False,
    )

# The following format should be an alias for the rich root equivalent 
# of the default format
register_metadir(controldir.format_registry, 'default-rich-root',
    'bzrlib.repofmt.groupcompress_repo.RepositoryFormat2a',
    branch_format='bzrlib.branch.BzrBranchFormat7',
    tree_format='bzrlib.workingtree_4.WorkingTreeFormat6',
    alias=True,
    hidden=True,
    help='Same as 2a.')

# The current format that is made on 'bzr init'.
format_name = config.GlobalStack().get('default_format')
controldir.format_registry.set_default(format_name)

# XXX 2010-08-20 JRV: There is still a lot of code relying on
# bzrlib.bzrdir.format_registry existing. When BzrDir.create/BzrDir.open/etc
# get changed to ControlDir.create/ControlDir.open/etc this should be removed.
format_registry = controldir.format_registry
