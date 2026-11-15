"""Schema-valid preparation review evidence with digest references to retained bytes."""
import io
import zipfile
from lxml import etree as ET
from .aip import el, identifier, M, P, X, I
from .json_codec import canonical
from .metadata_validation import validate_metadata
from .packages import MAX_ZIP, MAX_EXPANDED, PackageError, sha

VOCABULARY = 'urn:ingest-workbench:preparation-review:1'


def payload(plan, review, schemas=None):
    descriptor = {k: v for k, v in review.items() if k != 'evidence_sha256'}
    files = {'plan.json': canonical(plan), 'review.json': canonical(descriptor)}
    external = {}
    for item in plan['inputs']:
        external.setdefault(item['source_sha256'], {'size': None, 'format': 'ZIP'})
        external[item['input_sha256']] = {'size': item['size'], 'format': 'ZIP'}
        for key in ('preparation_sha256', 'freeze_evidence_sha256'):
            if item.get(key): external.setdefault(item[key], {'size': None, 'format': 'ZIP'})
    for resources in review['target_resources'].values():
        for digest in resources.values(): external.setdefault(digest, {'size': None, 'format': 'unassessed'})
    premis = ET.Element('{'+P+'}premis', nsmap={'premis': P, 'xsi': I}, version='3.0')
    objects = {**external, **{sha(data): {'size': len(data), 'format': 'JSON'} for data in files.values()}}
    for digest, info in sorted(objects.items()):
        obj = el(premis, P, 'object', **{'{'+I+'}type': 'premis:file'})
        identifier(obj, 'objectIdentifier', 'urn:sha256:'+digest)
        char = el(obj, P, 'objectCharacteristics'); el(char, P, 'compositionLevel', 0)
        fix = el(char, P, 'fixity'); el(fix, P, 'messageDigestAlgorithm', 'SHA-256'); el(fix, P, 'messageDigest', digest)
        if info['size'] is not None: el(char, P, 'size', info['size'])
        el(el(el(char, P, 'format'), P, 'formatDesignation'), P, 'formatName', info['format'])
    ev = el(premis, P, 'event'); identifier(ev, 'eventIdentifier', review['id'])
    el(ev, P, 'eventType', VOCABULARY+':review'); el(ev, P, 'eventDateTime', review['created'])
    el(el(ev, P, 'eventDetailInformation'), P, 'eventDetail', canonical(descriptor).decode())
    el(el(ev, P, 'eventOutcomeInformation'), P, 'eventOutcome', 'success')
    identifier(ev, 'linkingAgentIdentifier', review['actor'])
    for digest in sorted(objects): identifier(ev, 'linkingObjectIdentifier', 'urn:sha256:'+digest)
    agent = el(premis, P, 'agent'); identifier(agent, 'agentIdentifier', review['actor'])
    el(agent, P, 'agentName', review['actor']); el(agent, P, 'agentType', 'person')
    files['metadata/premis.xml'] = ET.tostring(premis, encoding='UTF-8', xml_declaration=True)
    mets = ET.Element('{'+M+'}mets', nsmap={'mets': M, 'xlink': X}, OBJID='urn:uuid:'+review['id'],
                      PROFILE=VOCABULARY, LABEL='Independent preparation review; AIP authorization pending')
    el(mets, M, 'metsHdr', CREATEDATE=review['created'], RECORDSTATUS='PREPARATION_REVIEWED')
    amd = el(mets, M, 'amdSec', ID='PROVENANCE')
    el(el(amd, M, 'digiprovMD', ID='REVIEW'), M, 'mdRef', MDTYPE='PREMIS', LOCTYPE='URL', **{'{'+X+'}href': 'premis.xml'})
    section = el(mets, M, 'fileSec')
    local = el(section, M, 'fileGrp', USE='review-descriptors')
    retained = el(section, M, 'fileGrp', USE='external-content-addressed-evidence-vault')
    root = el(el(mets, M, 'structMap', TYPE='physical'), M, 'div', TYPE='preparation-review', ADMID='PROVENANCE')
    for index, (path, data) in enumerate(sorted(files.items())):
        ident = 'F'+str(index)
        f = el(local, M, 'file', ID=ident, SIZE=str(len(data)), CHECKSUMTYPE='SHA-256', CHECKSUM=sha(data))
        el(f, M, 'FLocat', LOCTYPE='URL', **{'{'+X+'}href': '../'+path})
        el(el(root, M, 'div', TYPE='local-descriptor', LABEL=path), M, 'fptr', FILEID=ident)
    for index, (digest, info) in enumerate(sorted(external.items())):
        ident = 'EXT'+str(index)
        f = el(retained, M, 'file', ID=ident, CHECKSUMTYPE='SHA-256', CHECKSUM=digest)
        if info['size'] is not None: f.set('SIZE', str(info['size']))
        el(f, M, 'FLocat', LOCTYPE='URL', **{'{'+X+'}href': 'urn:sha256:'+digest})
        el(el(root, M, 'div', TYPE='retained-external-evidence', LABEL=digest), M, 'fptr', FILEID=ident)
    files['metadata/mets.xml'] = ET.tostring(mets, encoding='UTF-8', xml_declaration=True)
    if schemas is not None: validate_metadata(files, schemas/'preservation')
    files['manifest-sha256.txt'] = ''.join(sha(data)+'  '+path+'\n' for path, data in sorted(files.items())).encode()
    return files


def bundle(plan, review, schemas):
    files = payload(plan, review, schemas)
    if sum(map(len, files.values())) > MAX_EXPANDED: raise PackageError('Preparation review evidence exceeds expanded limit')
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path, data in files.items(): archive.writestr(path, data)
    data = stream.getvalue()
    if len(data) > MAX_ZIP: raise PackageError('Preparation review evidence exceeds ZIP limit')
    return data
