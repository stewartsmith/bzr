from bzrlib.bundle.serializer import BUNDLE_HEADER
from bzrlib.bundle.serializer.v08 import BundleSerializerV08, BundleReader
from bzrlib.testament import StrictTestament2
from bzrlib.bundle.bundle_data import BundleInfo

class BundleSerializerV09(BundleSerializerV08):

    def check_compatible(self):
        pass

    def _write_main_header(self):
        """Write the header for the changes"""
        f = self.to_file
        f.write(BUNDLE_HEADER)
        f.write('0.9\n')
        f.write('#\n')

    def _testament_sha1(self, revision_id):
        return StrictTestament2.from_revision(self.source, 
                                              revision_id).as_sha1()

    def read(self, f):
        """Read the rest of the bundles from the supplied file.

        :param f: The file to read from
        :return: A list of bundles
        """
        return BundleReaderV09(f).info


class BundleInfo09(BundleInfo):

    def _testament_sha1_from_revision(self, repository, revision_id):
        testament = StrictTestament2.from_revision(repository, revision_id)
        return testament.as_sha1()

    def _testament_sha1(self, revision, inventory):
        return StrictTestament2(revision, inventory).as_sha1()


class BundleReaderV09(BundleReader):
    
    def _get_info(self):
        return BundleInfo09()
