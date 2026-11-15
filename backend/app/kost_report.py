"""Bounded, versioned projection of retained KOST XML into navigable findings."""
from collections import Counter
from pathlib import PurePosixPath, PureWindowsPath
import io
import zipfile
from lxml import etree

VERSION = 1
MAX_REPORT = 16*1024*1024
MAX_ENTRIES = 20000
MAX_FINDINGS = 20000


def value(element, name):
    return element.findtext(name, default='').strip()


def member_path(reported, inventory):
    reported = reported.strip()
    if reported.startswith('->'):
        reported = reported[2:].strip()
    if len(reported) > 8192 or '..' in reported.replace('\\', '/').split('/'):
        return None
    path = PureWindowsPath(reported)
    parts = path.parts[1:] if path.anchor else path.parts
    if len(parts) > 256:
        return None
    matches = [inventory[parts[index:]] for index in range(len(parts)) if parts[index:] in inventory]
    return matches[0] if len(matches) == 1 else None


def project(data, members):
    if len(data) > MAX_REPORT:
        raise ValueError('KOST XML report exceeds the local size limit')
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False)
    root = etree.fromstring(data, parser)
    if root.tag != 'KOSTValLog' or root.getroottree().docinfo.doctype:
        raise ValueError('Unsupported KOST XML report or document type')
    inventory = {PurePosixPath(path).parts: path for path in members}
    validations = root.findall('./Format/Validation')+root.findall('./Sip/Validation')
    if len(validations) > MAX_ENTRIES:
        raise ValueError('KOST report has too many validation entries')
    entries = []
    finding_count = 0
    for index, validation in enumerate(validations):
        scope = 'package' if validation.getparent().tag == 'Sip' else 'file'
        reported = value(validation, 'ValFile')
        path = member_path(reported, inventory) if scope == 'file' else None
        outcome = ('invalid' if validation.find('Invalid') is not None else
                   'valid' if validation.find('Valid') is not None else
                   'recognized' if validation.find('Accepted') is not None else 'not_assessed')
        findings = []
        previous = None
        for child in validation:
            if child.tag == 'Error':
                module = value(child, 'Modul')
                messages = [''.join(message.itertext()).strip() for message in child.findall('Message')]
                message = '\n'.join(messages)
                runtime_error = any(fragment in message.lower() for fragment in (
                    "can't find resource", 'can`t find resource', 'java.util.missingresourceexception',
                    'an unknown error has occurred'))
                severity = ('validator_error' if runtime_error else
                    'warning' if module.lower().startswith('w)') else
                    'info' if message.lstrip().lower().startswith('information:') else 'error')
                previous = {'module': module, 'message': message, 'severity': severity}
                findings.append(previous)
            elif child.tag == 'Warning' and previous is not None and previous['severity'] != 'validator_error':
                previous['severity'] = 'warning'
        finding_count += len(findings)
        if finding_count > MAX_FINDINGS:
            raise ValueError('KOST report has too many findings')
        entries.append({'id': 'validation-'+str(index+1), 'scope': scope,
            'path': path, 'reported_path': reported,
            'mapping': 'package' if scope == 'package' else 'matched' if path else 'unmapped',
            'check': value(validation, 'ValType'), 'format_version': value(validation, 'FormatVL'),
            'outcome': outcome, 'findings': findings})
    counts = Counter(entry['outcome'] for entry in entries if entry['scope'] == 'file')
    severities = Counter(finding['severity'] for entry in entries for finding in entry['findings'])
    return {'parser_version': VERSION, 'summary': value(root.find('Format/Infos'), 'Summary') if root.find('Format/Infos') is not None else '',
        'file_counts': {key: counts[key] for key in ('valid', 'invalid', 'recognized', 'not_assessed')},
        'finding_counts': {key: severities[key] for key in ('error', 'warning', 'info', 'validator_error')},
        'validator_error': bool(severities['validator_error']), 'entries': entries}


def findings(service, ident, tenant, sip_id, run_id):
    from .kost_validation import scoped
    service.get(ident, tenant)
    with service.store.transaction() as tx:
        run = scoped(tx, ident, tenant, sip_id, run_id)
    binding = {'run_id': run_id, 'input_sha256': run['input_sha256'],
        'report_sha256': run['report_sha256'], 'prepared_id': run['prepared_id'],
        'reported_result': run['result'], 'filename': run.get('filename'),
        'checked_at': run.get('finished') or run.get('started') or run['created'],
        'report_path': '/api/transfers/'+ident+'/sips/'+sip_id+'/kost-validations/runs/'+run_id+'/report'}
    if not run['report_sha256']:
        return {**binding, 'available': False, 'reason': 'XML report is '+run['report_status']+'.'}
    data = service.blob(run['report_sha256'])
    try:
        with zipfile.ZipFile(io.BytesIO(service.blob(run['input_sha256']))) as archive:
            members = [entry.filename for entry in archive.infolist() if not entry.is_dir()]
        result = project(data, members)
    except (ValueError, etree.XMLSyntaxError, zipfile.BadZipFile):
        return {**binding, 'available': False,
            'reason': 'Structured findings unavailable: unsupported or malformed report. Original XML remains downloadable.'}
    return {**binding, 'available': True, **result}