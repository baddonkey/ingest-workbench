"""Passive-preview eligibility and precise resource limits over retained bytes."""
import struct
import unittest
from pathlib import Path
from app import content_previews as previews
from app.packages import members

ROOT=Path(__file__).resolve().parents[2]


class PreviewPolicyTests(unittest.TestCase):
    def png(self,width,height):
        return b'\x89PNG\r\n\x1a\n\0\0\0\rIHDR'+struct.pack('>II',width,height)+b'\x08\x06\0\0\0'+b'\0'*4

    def test_reference_pdf_tiff_jp2_and_wave_are_eligible_without_transforming_sources(self):
        files=members((ROOT/'fixtures/reference-1.3.zip').read_bytes())
        formats=set()
        for path,data in files.items():
            if Path(path).suffix not in ('.pdf','.tiff','.jp2','.wav'):continue
            with self.subTest(path=path):
                result=previews.project(path,data)
                self.assertTrue(result['available'],result);formats.add(Path(path).suffix)
                if result['kind']=='image':self.assertGreater(result['width']*result['height'],0)
        self.assertEqual(formats,{'.pdf','.tiff','.jp2','.wav'})

    def test_exact_image_side_pixel_and_source_byte_thresholds(self):
        self.assertTrue(previews.project('image.png',self.png(8000,2000))['available'])
        for width,height in ((8193,1),(8000,2001),(0,1)):
            self.assertEqual(previews.project('image.png',self.png(width,height))['reason'],'image-limit')
        data=self.png(1,1);data+=b'\0'*(previews.IMAGE_BYTES-len(data))
        self.assertTrue(previews.project('image.png',data)['available'])
        self.assertEqual(previews.project('image.png',data+b'\0')['reason'],'byte-limit')
        pdf=b'%PDF-1.7\n'+b' '*(previews.PDF_BYTES-15)+b'%%EOF\n'
        self.assertEqual(len(pdf),previews.PDF_BYTES)
        self.assertTrue(previews.project('file.pdf',pdf)['available'])
        self.assertEqual(previews.project('file.pdf',pdf+b'\n')['reason'],'byte-limit')
        wave=b'RIFF'+b'\0'*4+b'WAVE'+b'\0'*(previews.MEDIA_BYTES-12)
        self.assertTrue(previews.project('audio.wav',wave)['available'])
        self.assertEqual(previews.project('audio.wav',wave+b'\0')['reason'],'byte-limit')

    def test_active_unknown_malformed_and_cyclic_headers_remain_attachments(self):
        for path,data in [('page.html',b'<script>'),('image.svg',b'<svg/>'),('image.png',b'<svg/>'),
                          ('doc.pdf',b'%PDF'),('movie.mp4',b'not a movie'),('unknown.bin',b'RIFF0000WAVE'),
                          ('cycle.tiff',b'II*\0'+struct.pack('<IHI',8,0,8))]:
            with self.subTest(path=path):self.assertFalse(previews.project(path,data)['available'])
        files=members((ROOT/'fixtures/reference-1.3.zip').read_bytes())
        data=bytearray(next(data for path,data in files.items() if path.endswith('/00000004.jp2')))
        position=data.index(b'\xffO\xffQ')+40;data[position:position+2]=b'\0\x04'
        self.assertEqual(previews.project('unsupported-components.jp2',bytes(data))['reason'],'image-header')
