# (C) 2005 Canonical Ltd
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
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA

import sys
from bzrlib.osutils import is_inside_any
from bzrlib.delta import compare_trees
from bzrlib.log import line_log
from bzrlib.errors import NoSuchRevision

# TODO: when showing single-line logs, truncate to the width of the terminal
# if known, but only if really going to the terminal (not into a file)


def show_status(branch, show_unchanged=False,
                specific_files=None,
                show_ids=False,
                to_file=None,
                show_pending=True,
                revision=None):
    """Display summary of changes.

    By default this compares the working tree to a previous revision. 
    If the revision argument is given, summarizes changes between the 
    working tree and another, or between two revisions.

    The result is written out as Unicode and to_file should be able 
    to encode that.

    show_unchanged
        If set, includes unchanged files.

    specific_files
        If set, only show the status of files in this list.

    show_ids
        If set, includes each file's id.

    to_file
        If set, write to this file (default stdout.)

    show_pending
        If set, write pending merges.

    revision
        If None the compare latest revision with working tree
        If one revision show compared it with working tree.
        If two revisions show status between first and second.
    """
    if to_file == None:
        to_file = sys.stdout
    
    branch.lock_read()
    try:
        new_is_working_tree = True
        if revision is None:
            old = branch.basis_tree()
            new = branch.working_tree()
        elif len(revision) > 0:
            try:
                rev_id = revision[0].in_history(branch).rev_id
                old = branch.storage.revision_tree(rev_id)
            except NoSuchRevision, e:
                raise BzrCommandError(str(e))
            if len(revision) > 1:
                try:
                    rev_id = revision[1].in_history(branch).rev_id
                    new = branch.storage.revision_tree(rev_id)
                    new_is_working_tree = False
                except NoSuchRevision, e:
                    raise BzrCommandError(str(e))
            else:
                new = branch.working_tree()
                

        delta = compare_trees(old, new, want_unchanged=show_unchanged,
                              specific_files=specific_files)

        delta.show(to_file,
                   show_ids=show_ids,
                   show_unchanged=show_unchanged)

        if new_is_working_tree:
            list_paths('unknown', new.unknowns(), specific_files, to_file)
            list_paths('conflicts', new.iter_conflicts(), specific_files, to_file)
        if new_is_working_tree and show_pending:
            show_pending_merges(new, to_file)
    finally:
        branch.unlock()

def show_pending_merges(new, to_file):
    """Write out a display of pending merges in a working tree."""
    pending = new.pending_merges()
    branch = new.branch
    if len(pending) == 0:
        return
    print >>to_file, 'pending merges:'
    last_revision = branch.last_revision()
    if last_revision is not None:
        ignore = set(branch.storage.get_ancestry(last_revision))
    else:
        ignore = set()
    for merge in new.pending_merges():
        ignore.add(merge)
        try:
            m_revision = branch.storage.get_revision(merge)
            print >> to_file, ' ', line_log(m_revision, 77)
            inner_merges = branch.storage.get_ancestry(merge)
            inner_merges.reverse()
            for mmerge in inner_merges:
                if mmerge in ignore:
                    continue
                mm_revision = branch.storage.get_revision(mmerge)
                print >> to_file, '   ', line_log(mm_revision, 75)
                ignore.add(mmerge)
        except NoSuchRevision:
            print >> to_file, ' ', merge 
        
def list_paths(header, paths, specific_files, to_file):
    done_header = False
    for path in paths:
        if specific_files and not is_inside_any(specific_files, path):
            continue
        if not done_header:
            print >>to_file, '%s:' % header
            done_header = True
        print >>to_file, ' ', path
