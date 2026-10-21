"""Archive path checks for self-created backup restore; never extract links/devices."""
from pathlib import PurePosixPath


def validate_members(members,allowed=None):
    names=set()
    for m in members:
        p=PurePosixPath(m.name)
        if p.is_absolute() or '..' in p.parts or not (m.isfile() or m.isdir()):
            raise ValueError('Unsafe backup archive member')
        if allowed is not None and (m.name not in allowed or not m.isfile()):raise ValueError('Unexpected backup member')
        if m.name in names:raise ValueError('Duplicate backup member')
        names.add(m.name)
    if allowed is not None and names!=set(allowed):raise ValueError('Incomplete backup archive')
