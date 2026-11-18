"""Bounded external sources, explicit identity decisions and scalar metadata proposals."""
import base64
import binascii
import copy
import csv
import io
import json
import re
import uuid
import zipfile
import zlib
from lxml import etree as ET
from openpyxl import Workbook, load_workbook
from openpyxl.utils.exceptions import InvalidFileException
from defusedxml.common import DefusedXmlException
from xml.etree.ElementTree import ParseError
from . import editor_fields as fields
from .json_codec import canonical
from .packages import PackageError, sha, xml
from .store import Conflict

KINDS = {'import-record', 'import-apply'}
MAX_SOURCE = 256*1024
MAX_ROWS = 2000
FORMATS = {'csv', 'xlsx', 'xml', 'json'}


def source(value):
    if (not isinstance(value, dict) or set(value)-{'filename','format','data_base64','sha256'}
            or not {'filename','format','data_base64'} <= set(value)):
        raise PackageError('Invalid metadata import source')
    name, fmt, encoded = value['filename'], value['format'], value['data_base64']
    if (not isinstance(name,str) or not name or len(name)>200
            or any(ord(char)<32 or char in '/\\' for char in name)
            or not isinstance(fmt,str) or fmt not in FORMATS or not isinstance(encoded,str) or len(encoded)>4*((MAX_SOURCE+2)//3)):
        raise PackageError('Metadata import requires a supported format, safe filename and at most 256 KiB')
    try:name.encode('utf-8')
    except UnicodeError as exc:raise PackageError('Invalid metadata import filename encoding') from exc
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise PackageError('Invalid metadata import encoding') from exc
    if not data or len(data)>MAX_SOURCE or base64.b64encode(data).decode()!=encoded:
        raise PackageError('Invalid or oversized metadata import source')
    if 'sha256' in value and value['sha256']!=sha(data):
        raise PackageError('Metadata import source digest mismatch')
    return {**value,'sha256':sha(data)}, data


def evidence_sources(records):
    result={}
    for record in records:
        if record['kind']=='import-record':
            src,data=source(record['changes']['metadata_import_proposal']['source'])
            result['imports/'+src['sha256']+'.'+src['format']]=data
    return result


def operation_source(record, records):
    if record['kind']=='import-record':selected=record
    elif record['kind']=='import-apply':
        ident=record['changes'].get('metadata_import',{}).get('import_id')
        selected=next((item for item in records if item['id']==ident and item['kind']=='import-record'),None)
        if selected is None:raise PackageError('Metadata import application has no retained source')
    else:return None
    src,_=source(selected['changes']['metadata_import_proposal']['source'])
    return src['sha256']


def eligible(field):
    return field['editable'] and not field['children'] and not field['attributes'] and field['maximum']==1


def scalar(field):
    return (field['values'][0]['text'] if field and field['values'] else '')


def json_object(pairs):
    result = {}
    for key,value in pairs:
        if key in result:raise PackageError('Duplicate JSON metadata import property')
        result[key] = value
    return result


def text(data):
    try:return data.decode('utf-8-sig')
    except UnicodeError as exc:raise PackageError('Metadata import text must be UTF-8') from exc


def records(rows):
    if not isinstance(rows,list) or len(rows)>MAX_ROWS:
        raise PackageError('Metadata import supports at most 2000 source rows')
    result = []
    for row in rows:
        if (not isinstance(row,dict) or not {'object_id','field','value'} <= set(row)
                or set(row)-{'object_id','field','value','before'}
                or any(not isinstance(value,str) or len(value)>8000 for value in row.values())
                or not row['object_id'] or not row['field']):
            raise PackageError('Import rows require text object_id, field and value; before is optional')
        try:
            for value in row.values():value.encode('utf-8')
        except UnicodeError as exc:raise PackageError('Invalid metadata import text encoding') from exc
        result.append(row)
    return result


def table(rows):
    if not rows:raise PackageError('Metadata import table has no header')
    headers = rows[0]
    allowed = {'object_id','field','value','before','value_json','before_json'}
    if (any(not isinstance(header,str) or header not in allowed for header in headers)
            or len(set(headers))!=len(headers) or not {'object_id','field'}<=set(headers)
            or ('value' in headers)==('value_json' in headers)
            or ('before' in headers and 'before_json' in headers)):
        raise PackageError('Expected object_id, field, value (or value_json), and optional before (or before_json)')
    converted = []
    for values in rows[1:]:
        if not any(value is not None and value!='' for value in values):continue
        if len(values)!=len(headers) or any(value is not None and not isinstance(value,str) for value in values):
            raise PackageError('Every import cell must be text; formulas, dates and numeric cells are not supported')
        row = dict(zip(headers, (value if value is not None else '' for value in values)))
        for key in ('value','before'):
            if key+'_json' in row:
                try:row[key] = json.loads(row.pop(key+'_json'))
                except (ValueError,TypeError) as exc:raise PackageError('Invalid JSON string in '+key+'_json') from exc
        converted.append(row)
    return records(converted)


def spreadsheet(data):
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries)>100 or sum(entry.file_size for entry in entries)>4*1024*1024:
                raise PackageError('Spreadsheet exceeds the local expanded-size limit')
            seen = set()
            for entry in entries:
                path = entry.filename
                if (path in seen or entry.flag_bits&1 or path.startswith(('/', '\\'))
                        or '..' in path.replace('\\','/').split('/') or '\x00' in path
                        or path.lower().endswith('.bin')):
                    raise PackageError('Unsupported encrypted, active or ambiguous spreadsheet member')
                seen.add(path)
                if path.endswith(('.xml','.rels')):
                    root = xml(archive.read(entry))
                    if root.getroottree().docinfo.doctype:raise PackageError('Spreadsheet DTDs are not supported')
                    if any(element.tag.endswith('}Relationship') and element.get('TargetMode')=='External'
                           for element in root.iter() if isinstance(element.tag,str)):
                        raise PackageError('External spreadsheet relationships are not supported')
                    if path.startswith('xl/worksheets/'):
                        cells = root.findall('.//{*}c')
                        if any(not re.fullmatch(r'[A-D][1-9][0-9]{0,3}',cell.get('r',''))
                               or int(re.search(r'\d+',cell.get('r')).group())>MAX_ROWS+1
                               or cell.find('{*}f') is not None for cell in cells):
                            raise PackageError('Spreadsheet supports four text columns, 2000 rows and no formulas')
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=False, keep_links=False)
        try:
            if len(workbook.worksheets)!=1:raise PackageError('Metadata import requires exactly one worksheet')
            sheet = workbook.worksheets[0]
            sheet.reset_dimensions()
            rows = []
            for row in sheet.iter_rows():
                if len(rows)>MAX_ROWS:raise PackageError('Spreadsheet has too many rows')
                if any(cell.data_type=='f' for cell in row):raise PackageError('Spreadsheet formulas are not supported')
                rows.append([cell.value for cell in row])
            if rows:
                while rows[0] and rows[0][-1] is None:rows[0].pop()
                width=len(rows[0])
                for index,row in enumerate(rows[1:],1):
                    if any(value is not None for value in row[width:]):raise PackageError('Spreadsheet data has no corresponding header')
                    rows[index]=row[:width]+[None]*max(0,width-len(row))
            return table(rows)
        finally:workbook.close()
    except (zipfile.BadZipFile,InvalidFileException,ET.XMLSyntaxError,ParseError,
            DefusedXmlException,zlib.error,KeyError,ValueError,TypeError,IndexError) as exc:
        raise PackageError('Cannot read the metadata spreadsheet') from exc


def stripped_shape(root, info):
    root = copy.deepcopy(root)
    _, available = fields.catalog(root,info)
    for element,field,_ in available.values():
        for child in list(element.findall(fields.A+field['tag'])):element.remove(child)
    def shape(element):
        return (element.tag,sorted(element.attrib.items()),(element.text or '').strip(),
                [shape(child) for child in element if isinstance(child.tag,str)])
    return shape(root)


def rows_from_source(src, data, root, info, schemas, sip, identities=None):
    fmt = src['format']
    if fmt=='csv':
        try:return table(list(csv.reader(io.StringIO(text(data),newline=''),strict=True)))
        except csv.Error as exc:raise PackageError('Malformed metadata CSV') from exc
    if fmt=='json':
        try:return records(json.loads(text(data),object_pairs_hook=json_object))
        except (ValueError,TypeError) as exc:raise PackageError('Malformed metadata JSON') from exc
    if fmt=='xlsx':return spreadsheet(data)
    incoming = xml(data)
    if incoming.getroottree().docinfo.doctype:raise PackageError('Metadata import DTDs are not supported')
    fields.validate(incoming,schemas,sip)
    # XML has no sidecar for process/activity identities. Do not infer identity
    # from its position; changed ID-less siblings need an explicit target review.
    nodes,_ = fields.catalog(incoming,info)
    result = []
    for node in nodes:
        for field in node['fields']:
            result.append({'object_id':node['id'],'field':field['tag'],'value':scalar(field),
                           'xml_values':field['values'],'blocked':not eligible(field),
                           'ambiguous':bool(identities) and node['kind'] in ('vorgang','aktivitaet')})
    if stripped_shape(root,info)!=stripped_shape(incoming,info):
        result.append({'object_id':'','field':'XML structure / references / unexposed metadata',
                       'value':'Unsupported XML differences must be explicitly excluded.','blocked':True})
    if len(result)>MAX_ROWS:raise PackageError('XML import exceeds the local field limit')
    return result


def without_ids(value):
    if isinstance(value,dict):return {key:without_ids(item) for key,item in value.items() if key!='id'}
    if isinstance(value,list):return [without_ids(item) for item in value]
    return value


def proposal(root, info, supplied, schemas, sip, metadata_sha256, predecessor, identities=None):
    src,data = source(supplied)
    nodes,_ = fields.catalog(root,info,identities=identities)
    rows = []
    for index,row in enumerate(rows_from_source(src,data,root,info,schemas,sip,identities)):
        matches = [node for node in nodes if row['object_id'] in (node['id'],node['source_id']) and not row.get('ambiguous')]
        node = matches[0] if len(matches)==1 else None
        field = next((field for field in node['fields'] if field['tag']==row['field']),None) if node else None
        if field and 'xml_values' in row:
            if without_ids(field['values'])==without_ids(row['xml_values']):continue
        elif field and eligible(field) and scalar(field)==row['value']:continue
        status = ('unsupported-field' if row.get('blocked') else 'identity-conflict' if node is None else
                  'field-conflict' if field is None else 'protected-field' if not eligible(field) else
                  'baseline-conflict' if 'before' in row and row['before']!=scalar(field) else 'ready')
        rows.append({'id':'row-'+str(index),'object_id':row['object_id'],'field':row['field'],
                     'value':row['value'] if 'xml_values' not in row or not row.get('blocked') else canonical(without_ids(row['xml_values'])).decode(),
                     'external_before':row.get('before'),'node_id':node['id'] if node else '',
                     'current':field['values'] if field else [],'status':status,'blocked':bool(row.get('blocked'))})
    if len(rows)>200:raise PackageError('Import has more than 200 changed or unresolved fields; reduce the source')
    return {'source':src,'metadata_sha256':metadata_sha256,'input_operation_id':predecessor,'rows':rows}


def resolve(root, info, records, request, head, metadata_sha256, identities=None):
    retained = next((record for record in records if record['id']==request.get('import_id') and record['kind']=='import-record'),None)
    if retained is None:raise PackageError('Unknown retained metadata import')
    proposed = retained['changes']['metadata_import_proposal']
    if head!=retained['id'] or metadata_sha256!=proposed['metadata_sha256']:
        raise Conflict('Import baseline changed; retain a successor proposal against the current version')
    decisions = request.get('import_decisions')
    by_id = {row['id']:row for row in proposed['rows']}
    if (not isinstance(decisions,list) or len(decisions)!=len(by_id)
            or {decision.get('row_id') for decision in decisions if isinstance(decision,dict)}!=set(by_id)):
        raise PackageError('Review every imported row exactly once')
    nodes,_ = fields.catalog(root,info,identities=identities)
    edits,seen,reviewed = [],set(),[]
    for decision in decisions:
        if (not isinstance(decision,dict) or set(decision)!={'row_id','action','node_id','field','reason'}
                or decision['row_id'] in reviewed or decision['action'] not in ('apply','exclude')
                or not isinstance(decision['reason'],str) or len(decision['reason'])>500):
            raise PackageError('Invalid or duplicate import review decision')
        reviewed.append(decision['row_id'])
        row = by_id[decision['row_id']]
        if decision['action']=='exclude':
            if not decision['reason'].strip():raise PackageError('Explain each excluded import row')
            continue
        node = next((node for node in nodes if node['id']==decision['node_id']),None)
        field = next((field for field in node['fields'] if field['tag']==decision['field']),None) if node else None
        if row['blocked'] or field is None or not eligible(field):
            raise PackageError('Resolve the import identity to an editable scalar, single-valued field')
        if field['id'] in seen:raise PackageError('Multiple imported rows target the same field; exclude or remap duplicates')
        seen.add(field['id'])
        value = row['value']
        values = copy.deepcopy(field['values'])
        if not value and not field['minimum']:values=[]
        elif values:values[0]['text']=value
        else:values=[{'id':'new-'+str(uuid.uuid5(uuid.NAMESPACE_URL,retained['id']+row['id']+field['id'])),
                      'text':value,'attributes':{},'children':{}}]
        if values!=field['values']:
            edits.append({'node_id':node['id'],'field_id':field['id'],'before':field['values'],'values':values})
    return edits


def template(nodes, fmt, metadata):
    if fmt=='xml':return metadata
    rows = [{'object_id':node['id'],'field':field['tag'],'before':scalar(field),'value':scalar(field)}
            for node in nodes for field in node['fields'] if eligible(field)]
    if len(rows)>MAX_ROWS:raise PackageError('Template exceeds the local import-row limit')
    if fmt=='json':return json.dumps(rows,ensure_ascii=False,indent=2).encode()
    stream = io.BytesIO()
    if fmt=='xlsx':
        workbook=Workbook();sheet=workbook.active;sheet.title='Metadata'
        sheet.append(['object_id','field','before','value'])
        for row in rows:
            sheet.append(list(row.values()))
            for cell in sheet[sheet.max_row]:cell.data_type='s';cell.number_format='@'
        workbook.save(stream);workbook.close()
        return stream.getvalue()
    if fmt!='csv':raise PackageError('Unknown metadata import template format')
    output=io.StringIO(newline='');writer=csv.writer(output,lineterminator='\r\n')
    writer.writerow(['object_id','field','before_json','value_json'])
    for row in rows:writer.writerow([row['object_id'],row['field'],json.dumps(row['before'],ensure_ascii=False),json.dumps(row['value'],ensure_ascii=False)])
    return b'\xef\xbb\xbf'+output.getvalue().encode()
