"""Digest-linked freeze event, exact prepared bytes and retained editing history."""
import io
import json
import zipfile
from lxml import etree as ET
from .aip import el, identifier, relation, M, P, X, I
from .json_codec import canonical
from .metadata_validation import validate_metadata
from .packages import MAX_EXPANDED, MAX_ZIP, PackageError, sha

VOCABULARY = 'urn:ingest-workbench:prepared-input:1'


def payload(record, lineage, prepared, preparation, schemas=None):
    vocabulary = VOCABULARY if record['version']==1 else 'urn:ingest-workbench:prepared-input:'+str(record['version'])
    descriptor = {k: v for k, v in record.items() if k != 'evidence_sha256'}
    files = {'prepared.zip': prepared, 'preparation.zip': preparation,
             'freeze.json': canonical({'prepared_input': descriptor, 'members': lineage,
                 'transformation': 'Replace only metadata; conserve other member bytes, ordered paths, directories and archive comment',
                 'internal_scope': 'Trusted XSD, inventory, submitted fixity, references and member conservation',
                 'promoted': False})}
    if record['version'] in (2,3):
        descriptor_scope = json.loads(files['freeze.json'])
        descriptor_scope['reference_order'] = 'Logical sibling order may change; every file-reference occurrence and attribute is conserved'
        files['freeze.json'] = canonical(descriptor_scope)
    if record['version']==3:
        descriptor_scope = json.loads(files['freeze.json'])
        descriptor_scope['logical_creation'] = 'Native-ID logical objects may be added; exact v5 operation replay verifies scope and existing identities'
        files['freeze.json'] = canonical(descriptor_scope)
    premis = ET.Element('{'+P+'}premis', nsmap={'premis': P, 'xsi': I}, version='3.0')
    objects = {record['source_sha256']: None, record['sha256']: len(prepared),
               record['preparation_sha256']: len(preparation)}
    for digest, size in objects.items():
        obj = el(premis, P, 'object', **{'{'+I+'}type': 'premis:file'})
        identifier(obj, 'objectIdentifier', 'urn:sha256:'+digest)
        char = el(obj, P, 'objectCharacteristics'); el(char, P, 'compositionLevel', 0)
        fix = el(char, P, 'fixity'); el(fix, P, 'messageDigestAlgorithm', 'SHA-256'); el(fix, P, 'messageDigest', digest)
        if size is not None:
            el(char, P, 'size', size)
        el(el(el(char, P, 'format'), P, 'formatDesignation'), P, 'formatName', 'ZIP')
        if digest == record['sha256'] and digest != record['source_sha256']:
            relation(obj, 'derivation', 'prepared from submitted SIP', 'urn:sha256:'+record['source_sha256'])
    ev = el(premis, P, 'event'); identifier(ev, 'eventIdentifier', record['id'])
    el(ev, P, 'eventType', vocabulary+':freeze'); el(ev, P, 'eventDateTime', record['created'])
    el(el(ev, P, 'eventDetailInformation'), P, 'eventDetail', canonical(descriptor).decode())
    el(el(ev, P, 'eventOutcomeInformation'), P, 'eventOutcome', 'success')
    identifier(ev, 'linkingAgentIdentifier', record['actor'])
    for digest in dict.fromkeys((record['source_sha256'], record['sha256'], record['preparation_sha256'])):
        identifier(ev, 'linkingObjectIdentifier', 'urn:sha256:'+digest)
    agent = el(premis, P, 'agent'); identifier(agent, 'agentIdentifier', record['actor'])
    el(agent, P, 'agentName', record['actor']); el(agent, P, 'agentType', 'person')
    files['metadata/premis.xml'] = ET.tostring(premis, encoding='UTF-8', xml_declaration=True)
    mets = ET.Element('{'+M+'}mets', nsmap={'mets': M, 'xlink': X}, OBJID='urn:uuid:'+record['id'],
                      PROFILE=vocabulary, LABEL='Frozen prepared SIP and editing evidence')
    el(mets, M, 'metsHdr', CREATEDATE=record['created'], RECORDSTATUS='PREPARATION')
    amd = el(mets, M, 'amdSec', ID='PROVENANCE')
    el(el(amd, M, 'digiprovMD', ID='FREEZE'), M, 'mdRef', MDTYPE='PREMIS', LOCTYPE='URL', **{'{'+X+'}href': 'premis.xml'})
    group = el(el(mets, M, 'fileSec'), M, 'fileGrp', USE='prepared-input')
    root = el(el(mets, M, 'structMap', TYPE='physical'), M, 'div', TYPE='frozen-preparation', ADMID='PROVENANCE')
    for index, (path, data) in enumerate(sorted(files.items())):
        ident = 'F'+str(index)
        file = el(group, M, 'file', ID=ident, SIZE=str(len(data)), CHECKSUMTYPE='SHA-256', CHECKSUM=sha(data))
        el(file, M, 'FLocat', LOCTYPE='URL', **{'{'+X+'}href': '../'+path})
        el(el(root, M, 'div', TYPE='evidence', LABEL=path), M, 'fptr', FILEID=ident)
    files['metadata/mets.xml'] = ET.tostring(mets, encoding='UTF-8', xml_declaration=True)
    if schemas is not None:
        validate_metadata(files, schemas/'preservation')
    files['manifest-sha256.txt'] = ''.join(sha(data)+'  '+path+'\n' for path, data in sorted(files.items())).encode()
    return files


def bundle(record, lineage, prepared, preparation, schemas):
    files = payload(record, lineage, prepared, preparation, schemas)
    if sum(map(len, files.values())) > MAX_EXPANDED:
        raise PackageError('Frozen evidence exceeds local expanded limit')
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path, data in files.items():
            archive.writestr(path, data)
    data = stream.getvalue()
    if len(data) > MAX_ZIP:
        raise PackageError('Frozen evidence exceeds local ZIP limit')
    return data
