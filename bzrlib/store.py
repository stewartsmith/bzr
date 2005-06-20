# Copyright (C) 2005 by Canonical Development Ltd

# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA

"""
Stores are the main data-storage mechanism for Bazaar-NG.

A store is a simple write-once container indexed by a universally
unique ID.
"""

import os, tempfile, types, osutils, gzip, errno
from stat import ST_SIZE
from StringIO import StringIO
from trace import mutter

######################################################################
# stores

class StoreError(Exception):
    pass


class ImmutableStore(object):
    """Store that holds files indexed by unique names.

    Files can be added, but not modified once they are in.  Typically
    the hash is used as the name, or something else known to be unique,
    such as a UUID.

    >>> st = ImmutableScratchStore()

    >>> st.add(StringIO('hello'), 'aa')
    >>> 'aa' in st
    True
    >>> 'foo' in st
    False

    You are not allowed to add an id that is already present.

    Entries can be retrieved as files, which may then be read.

    >>> st.add(StringIO('goodbye'), '123123')
    >>> st['123123'].read()
    'goodbye'

    TODO: Atomic add by writing to a temporary file and renaming.

    In bzr 0.0.5 and earlier, files within the store were marked
    readonly on disk.  This is no longer done but existing stores need
    to be accomodated.
    """

    def __init__(self, basedir):
        self._basedir = basedir

    def _path(self, id):
        if '\\' in id or '/' in id:
            raise ValueError("invalid store id %r" % id)
        return os.path.join(self._basedir, id)

    def __repr__(self):
        return "%s(%r)" % (self.__class__.__name__, self._basedir)

    def add(self, f, fileid, compressed=True):
        """Add contents of a file into the store.

        f -- An open file, or file-like object."""
        # FIXME: Only works on smallish files
        # TODO: Can be optimized by copying at the same time as
        # computing the sum.
        mutter("add store entry %r" % (fileid))
        if isinstance(f, types.StringTypes):
            content = f
        else:
            content = f.read()

        p = self._path(fileid)
        if os.access(p, os.F_OK) or os.access(p + '.gz', os.F_OK):
            raise BzrError("store %r already contains id %r" % (self._basedir, fileid))

        if compressed:
            f = gzip.GzipFile(p + '.gz', 'wb')
        else:
            f = file(p, 'wb')
            
        f.write(content)
        f.close()


    def copy_multi(self, other, ids):
        """Copy texts for ids from other into self.

        If an id is present in self, it is skipped.  A count of copied
        ids is returned, which may be less than len(ids).
        """
        from bzrlib.progress import ProgressBar
        pb = ProgressBar()
        pb.update('preparing to copy')
        to_copy = [id for id in ids if id not in self]
        count = 0
        for id in to_copy:
            count += 1
            pb.update('copy', count, len(to_copy))
            self.add(other[id], id)
        assert count == len(to_copy)
        pb.clear()
        return count
    

    def __contains__(self, fileid):
        """"""
        p = self._path(fileid)
        return (os.access(p, os.R_OK)
                or os.access(p + '.gz', os.R_OK))

    # TODO: Guard against the same thing being stored twice, compressed and uncompresse

    def __iter__(self):
        for f in os.listdir(self._basedir):
            if f[-3:] == '.gz':
                # TODO: case-insensitive?
                yield f[:-3]
            else:
                yield f

    def __len__(self):
        return len(os.listdir(self._basedir))

    def __getitem__(self, fileid):
        """Returns a file reading from a particular entry."""
        p = self._path(fileid)
        try:
            return gzip.GzipFile(p + '.gz', 'rb')
        except IOError, e:
            if e.errno == errno.ENOENT:
                return file(p, 'rb')
            else:
                raise e

    def total_size(self):
        """Return (count, bytes)

        This is the (compressed) size stored on disk, not the size of
        the content."""
        total = 0
        count = 0
        for fid in self:
            count += 1
            p = self._path(fid)
            try:
                total += os.stat(p)[ST_SIZE]
            except OSError:
                total += os.stat(p + '.gz')[ST_SIZE]
                
        return count, total




class ImmutableScratchStore(ImmutableStore):
    """Self-destructing test subclass of ImmutableStore.

    The Store only exists for the lifetime of the Python object.
 Obviously you should not put anything precious in it.
    """
    def __init__(self):
        ImmutableStore.__init__(self, tempfile.mkdtemp())

    def __del__(self):
        for f in os.listdir(self._basedir):
            fpath = os.path.join(self._basedir, f)
            # needed on windows, and maybe some other filesystems
            os.chmod(fpath, 0600)
            os.remove(fpath)
        os.rmdir(self._basedir)
        mutter("%r destroyed" % self)
