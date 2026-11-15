"""Offline schema resolution across native paths; no source schema is rewritten."""
import unittest
from pathlib import Path
from lxml import etree as ET
from app.metadata_validation import Resolver
from app.packages import PackageError

ROOT=Path(__file__).resolve().parents[2]


class ResolverTests(unittest.TestCase):
    def test_windows_paths_and_official_urls_use_local_allowlist(self):
        parser=ET.XMLParser(resolve_entities=False,no_network=True)
        parser.resolvers.add(Resolver(ROOT/'schemas/preservation'))
        for location in (
            r'C:\unavailable\mets.xsd',
            'http://www.loc.gov/standards/mets/mets.xsd',
            (ROOT/'schemas/preservation/mets.xsd').as_uri(),
            str(ROOT/'schemas/preservation/premis.xsd'),
        ):
            with self.subTest(location=location):
                ET.XMLSchema(ET.parse(location,parser))

    def test_unknown_dependency_is_rejected(self):
        parser=ET.XMLParser(resolve_entities=False,no_network=True)
        parser.resolvers.add(Resolver(ROOT/'schemas/preservation'))
        for location in ('http://example.invalid/unknown.xsd',r'C:\outside\unknown.xsd'):
            with self.subTest(location=location),self.assertRaises(PackageError):
                ET.parse(location,parser)
