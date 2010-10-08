# Copyright (C) 2008, 2009, 2010 Canonical Ltd
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

from bzrlib import (
    cache_utf8,
    errors,
    inventory,
    xml6,
    xml8,
    )

class Serializer_v5(xml6.Serializer_v6):
    """Version 5 serializer

    Packs objects into XML and vice versa.
    """
    format_num = '5'
    root_id = inventory.ROOT_ID

    def _unpack_inventory(self, elt, revision_id, entry_cache=None,
                          return_from_cache=False):
        """Construct from XML Element
        """
        root_id = elt.get('file_id') or inventory.ROOT_ID
        root_id = xml8._get_utf8_or_ascii(root_id)

        format = elt.get('format')
        if format is not None:
            if format != '5':
                raise errors.BzrError("invalid format version %r on inventory"
                                      % format)
        data_revision_id = elt.get('revision_id')
        if data_revision_id is not None:
            revision_id = cache_utf8.encode(data_revision_id)
        inv = inventory.Inventory(root_id, revision_id=revision_id)
        # Optimizations tested
        #   baseline w/entry cache  2.85s
        #   using inv._byid         2.55s
        #   avoiding attributes     2.46s
        #   adding assertions       2.50s
        #   last_parent cache       2.52s (worse, removed)
        unpack_entry = self._unpack_entry
        byid = inv._byid
        for e in elt:
            ie = unpack_entry(e, entry_cache=entry_cache,
                              return_from_cache=return_from_cache)
            parent_id = ie.parent_id
            if parent_id is None:
                ie.parent_id = parent_id = root_id
            try:
                parent = byid[parent_id]
            except KeyError:
                raise errors.BzrError("parent_id {%s} not in inventory"
                                      % (parent_id,))
            if ie.file_id in byid:
                raise errors.DuplicateFileId(ie.file_id,
                                             byid[ie.file_id])
            if ie.name in parent.children:
                raise errors.BzrError("%s is already versioned"
                    % (osutils.pathjoin(inv.id2path(parent_id),
                       ie.name).encode('utf-8'),))
            parent.children[ie.name] = ie
            byid[ie.file_id] = ie
        if revision_id is not None:
            inv.root.revision = revision_id
        self._check_cache_size(len(inv), entry_cache)
        return inv

    def _check_revisions(self, inv):
        """Extension point for subclasses to check during serialisation.

        In this version, no checking is done.

        :param inv: An inventory about to be serialised, to be checked.
        :raises: AssertionError if an error has occurred.
        """

    def _append_inventory_root(self, append, inv):
        """Append the inventory root to output."""
        if inv.root.file_id not in (None, inventory.ROOT_ID):
            fileid1 = ' file_id="'
            fileid2 = xml8._encode_and_escape(inv.root.file_id)
        else:
            fileid1 = ""
            fileid2 = ""
        if inv.revision_id is not None:
            revid1 = ' revision_id="'
            revid2 = xml8._encode_and_escape(inv.revision_id)
        else:
            revid1 = ""
            revid2 = ""
        append('<inventory%s%s format="5"%s%s>\n' % (
            fileid1, fileid2, revid1, revid2))


serializer_v5 = Serializer_v5()
