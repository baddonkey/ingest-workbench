"""Offline schema resolver: official schemas remain unchanged on disk."""
from pathlib import Path
from lxml import etree as ET
from .packages import PackageError, xml

class Resolver(ET.Resolver):
    def __init__(self,root:Path):self.root=root.resolve()
    def resolve(self,url,pubid,context):
        # libxml2 may pass a native Windows path for the root schema and URLs
        # for imports. Always resolve the allowlisted basename into our vault.
        name=url.replace('\\','/').rsplit('/',1)[-1]
        if name not in ('mets.xsd','premis.xsd','xlink.xsd','xml.xsd'):
            raise PackageError('Unapproved schema dependency')
        return self.resolve_filename(str(self.root/name),context)

def validate_metadata(payload:dict[str,bytes],root:Path):
    parser=ET.XMLParser(resolve_entities=False,no_network=True);parser.resolvers.add(Resolver(root))
    for metadata,schema in [('metadata/mets.xml','mets.xsd'),('metadata/premis.xml','premis.xsd')]:
        validator=ET.XMLSchema(ET.parse(str(root/schema),parser))
        document=xml(payload[metadata])
        if not validator.validate(document):raise PackageError('Generated metadata failed XSD: '+str(validator.error_log.last_error))
