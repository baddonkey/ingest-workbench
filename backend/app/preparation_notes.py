"""Object-linked preparation notes; never an implicit description/publication export."""
import copy
from .packages import PackageError
from .store import Conflict

KINDS = {'note-add', 'note-edit', 'note-delete'}
PURPOSES = {'internal', 'description-candidate'}
MAX_NOTES = 200


def text_value(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 8000:
        raise PackageError('A note needs text (8000 characters maximum)')
    try:
        value.encode('utf-8')
    except UnicodeError as error:
        raise PackageError('Note text must be valid Unicode') from error
    if any(ord(c) < 32 and c not in '\t\n\r' for c in value):
        raise PackageError('Note text contains unsupported control characters')
    return value


def change(request, current, nodes, actor, created):
    """Construct server-attributed before/after values against a checked baseline."""
    node_id = request.get('node_id')
    if node_id not in {n['id'] for n in nodes}:
        raise PackageError('Unknown note object')
    kind = request['kind']
    ident = request['request_id'] if kind == 'note-add' else request.get('note_id')
    before = next((n for n in current if n['id'] == ident), None)
    if kind == 'note-add':
        if before is not None or request.get('note_before') is not None:
            raise Conflict('Note already exists or has a different baseline')
        if len(current) >= MAX_NOTES:
            raise PackageError('Local active note limit reached')
    elif before is None or before != request.get('note_before') or before['node_id'] != node_id:
        raise Conflict('Note baseline changed; reload before applying')
    after = None
    if kind != 'note-delete':
        text = text_value(request.get('note_text'))
        purpose = request.get('note_purpose')
        if purpose not in PURPOSES:
            raise PackageError('Choose internal or proposed-description note purpose')
        if before and before['text'] == text and before['purpose'] == purpose:
            raise PackageError('No note changes to apply')
        after = dict(id=ident, node_id=node_id, text=text, purpose=purpose,
                     created=before['created'] if before else created,
                     created_by=before['created_by'] if before else actor,
                     updated=created, updated_by=actor, revision=before['revision'] + 1 if before else 1)
    return dict(node_id=node_id, note_id=ident, note_before=copy.deepcopy(before), note_after=after)


def snapshots(records):
    """Replay sidecar snapshots, including shared undo/redo and restoration.

    Old metadata-only records imply an empty or unchanged note set. Snapshots are
    derived from the immutable journal rather than a second mutable head.
    """
    current = {}
    versions = {}
    for record in records:
        before = dict(current)
        kind, changes = record['kind'], record['changes']
        if kind in KINDS:
            if set(changes) != {'node_id', 'note_id', 'note_before', 'note_after'}:
                raise PackageError('Unexpected retained note change fields')
            ident = changes.get('note_id')
            prior = current.get(ident)
            after = changes.get('note_after')
            if (not ident or changes.get('note_before') != prior
                    or (kind == 'note-add' and (prior is not None or ident != record['id']))
                    or (kind != 'note-add' and prior is None)):
                raise PackageError('Retained note baseline differs from journal')
            if kind == 'note-delete':
                if after is not None or changes.get('node_id') != prior['node_id']:
                    raise PackageError('Retained note deletion differs from its object')
                del current[ident]
            else:
                if not isinstance(after, dict):
                    raise PackageError('Missing retained note value')
                expected = dict(id=ident, node_id=changes.get('node_id'), text=text_value(after.get('text')),
                                purpose=after.get('purpose'), created=prior['created'] if prior else record['created'],
                                created_by=prior['created_by'] if prior else record['actor'], updated=record['created'],
                                updated_by=record['actor'], revision=prior['revision'] + 1 if prior else 1)
                if (after != expected or after['purpose'] not in PURPOSES
                        or (prior and (after['node_id'] != prior['node_id']
                                       or (after['text'], after['purpose']) == (prior['text'], prior['purpose'])))):
                    raise PackageError('Retained note attribution, purpose or revision differs')
                current[ident] = after
                if len(current) > MAX_NOTES:
                    raise PackageError('Retained active note limit exceeded')
        elif kind in ('undo', 'redo', 'restore'):
            target = versions.get(changes.get('related_operation_id'))
            if target is None:
                raise PackageError('Note snapshot refers to a missing earlier operation')
            current = dict(target[0 if kind == 'undo' else 1])
        versions[record['id']] = (before, dict(current))
    return versions


def state(records):
    versions = snapshots(records)
    return copy.deepcopy(list(versions[records[-1]['id']][1].values())) if records else []


def document(records):
    return {'version': 1, 'publication': 'held', 'notes': state(records)}
