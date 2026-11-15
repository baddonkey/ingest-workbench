"""Lossless source projection for archival arrangement; no filename-based hierarchy inference."""
import hashlib,uuid
from lxml import etree as ET
from .packages import NS,text,xml,PackageError,PROFILES
LOGICAL={'dossier','mappe','dokument'}
CONTAINERS=LOGICAL|{'dateiRef','ordnungssystemposition','ordnungssystem','ablieferung','inhaltsverzeichnis'}

def urn(ident):return 'urn:uuid:'+str(ident)
def stable(sip_id,kind,value):return urn(uuid.uuid5(uuid.UUID(sip_id),kind+':'+value))
def file_identifier(sip_id,file_id,digest):return stable(sip_id,'file',file_id+':'+digest)
def node_identifier(sip_id,node):return stable(sip_id,'node',node.get('id') or node.getroottree().getpath(node))
def local(node):return ET.QName(node).localname

def fields(node):
    return [ET.tostring(child,encoding='unicode',with_tail=False) for child in node if isinstance(child.tag,str) and not (child.tag.startswith('{'+NS['a']+'}') and local(child) in CONTAINERS)]

def project_node(node,sip_id):
    result={'id':node_identifier(sip_id,node),'source_id':node.get('id'),'source_xpath':node.getroottree().getpath(node),
            'kind':local(node),'title':text(node,'titel') or text(node,'name') or node.get('id') or local(node),'metadata':fields(node),'members':[]}
    for child in node:
        if not isinstance(child.tag,str):continue
        if local(child) in LOGICAL:result['members'].append(project_node(child,sip_id))
        elif local(child)=='dateiRef':result['members'].append({'kind':'file-reference','file_id':(child.text or '').strip(),'attributes':dict(child.attrib)})
    return result

def source_description(metadata,sip_id,unit_ids):
    root=xml(metadata);selected=[]
    for node in root.findall('.//a:dossier',NS):
        if node.get('id') not in unit_ids:continue
        if any(local(p)=='dossier' for p in node.iterancestors()):raise PackageError('Selected unit is not a top-level dossier')
        ancestors=list(reversed(list(node.iterancestors())))
        selected.append({'context':[{'kind':local(p),'id':node_identifier(sip_id,p),'source_id':p.get('id'),'title':text(p,'titel') or text(p,'name') or local(p),'metadata':fields(p)} for p in ancestors],
                         'unit':project_node(node,sip_id)})
    if {s['unit']['source_id'] for s in selected}!=set(unit_ids):raise PackageError('Selected source dossier missing')
    return {'sip_id':sip_id,'units':selected}

def inventory(root,prefix):
    entries={}
    def walk(folder,parent):
        path=parent+text(folder,'name')+'/'
        for file in folder.findall('a:datei',NS):
            entries[file.get('id')]={'path':prefix+path+text(file,'name'),'algorithm':text(file,'pruefalgorithmus'),'digest':text(file,'pruefsumme')}
        for child in folder.findall('a:ordner',NS):walk(child,path)
    for folder in root.findall('a:inhaltsverzeichnis/a:ordner',NS):walk(folder,'')
    return entries

def file_refs(node):
    if node['kind']=='file-reference':yield node
    else:
        for member in node['members']:yield from file_refs(member)

def nodes(node):
    if node['kind']!='file-reference':
        yield node
        for child in node['members']:yield from nodes(child)
