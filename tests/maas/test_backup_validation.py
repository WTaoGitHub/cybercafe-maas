import tarfile
import pytest
from maas_platform.backup_validation import validate_members


def test_backup_rejects_traversal_links_and_incomplete_sets():
 for name,kind in [('../escape',tarfile.REGTYPE),('/absolute',tarfile.REGTYPE),('link',tarfile.SYMTYPE),('device',tarfile.CHRTYPE)]:
  m=tarfile.TarInfo(name);m.type=kind
  with pytest.raises(ValueError):validate_members([m])
 with pytest.raises(ValueError):validate_members([tarfile.TarInfo('db')],{'db','artifacts'})
 validate_members([tarfile.TarInfo('db'),tarfile.TarInfo('artifacts')],{'db','artifacts'})
