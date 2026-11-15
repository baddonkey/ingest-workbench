"""Lossless evidence transport and bounded, content-addressed reconstruction."""
import copy
import io
import json
import unittest
import zipfile
from app.evidence_codec import Encoder,Decoder
from app.packages import sha,PackageError
from app.aip import canonical


def archive(entries,comment=b''):
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
        z.comment=comment
        for path,data in entries:z.writestr(path,data)
    return out.getvalue()


class EvidenceCodecTests(unittest.TestCase):
    def test_nested_archives_deduplicate_and_restore_byte_exact_with_order_and_comment(self):
        original=archive([('a.txt',b'Preserve these exact bytes')],b'Original comment')
        nested=archive([('original.zip',original),('copies/',''),('same.zip',original)])
        encoder=Encoder('blobs/');digest=encoder.add(nested)
        decoder=Decoder(encoder.files,'blobs/')
        self.assertEqual(decoder(digest),nested)
        self.assertEqual(decoder(sha(original)),original)
        self.assertEqual(decoder.used,set(encoder.files))
        self.assertEqual(len([p for p in encoder.files if p.endswith(sha(b'Preserve these exact bytes'))]),1)

    def test_unreproducible_archive_is_retained_opaque(self):
        data=archive([('a.txt',b'original')])+b'Retained trailing bytes'
        encoder=Encoder('blobs/');digest=encoder.add(data)
        self.assertEqual(encoder.files,{'blobs/'+digest:data})
        self.assertEqual(Decoder(encoder.files,'blobs/')(digest),data)

    def test_recipe_tampering_missing_child_cycle_and_unknown_members_are_refused(self):
        data=archive([('a.txt',b'original')]);encoder=Encoder('blobs/');digest=encoder.add(data)
        path='blobs/'+digest+'.zip-recipe.json'
        recipe=json.loads(encoder.files[path])
        cases=[]
        files=copy.copy(encoder.files);files.pop('blobs/'+sha(b'original'));cases.append(files)
        altered=copy.deepcopy(recipe);altered['comment']=b'forged'.hex()
        files=copy.copy(encoder.files);files[path]=canonical(altered);cases.append(files)
        altered=copy.deepcopy(recipe);altered['entries'][0]['sha256']=digest
        files=copy.copy(encoder.files);files[path]=canonical(altered);cases.append(files)
        altered=copy.deepcopy(recipe);altered['entries'][0]['attributes']['filename']='../escape'
        files=copy.copy(encoder.files);files[path]=canonical(altered);cases.append(files)
        for files in cases:
            with self.subTest(files=list(files)),self.assertRaises((PackageError,KeyError)):
                Decoder(files,'blobs/')(digest)
