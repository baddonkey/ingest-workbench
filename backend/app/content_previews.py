"""Eligibility checks for opt-in passive browser previews, not format validation."""
import struct
from pathlib import PurePosixPath

IMAGE_BYTES = 8 * 1024 * 1024
PDF_BYTES = 12 * 1024 * 1024
MEDIA_BYTES = 32 * 1024 * 1024
IMAGE_PIXELS = 16_000_000
IMAGE_SIDE = 8192


def tiff_size(data):
    endian = '<' if data[:4] == b'II*\0' else '>' if data[:4] == b'MM\0*' else None
    if endian is None:raise ValueError('Not classic TIFF')
    offset = struct.unpack_from(endian+'I',data,4)[0]
    pending,seen,dimensions = [offset],set(),{}
    while pending:
        offset=pending.pop()
        if not offset:continue
        if offset in seen or len(seen)>=64:raise ValueError('TIFF directory cycle or limit')
        seen.add(offset)
        count=struct.unpack_from(endian+'H',data,offset)[0]
        if count>512 or offset+2+count*12+4>len(data):raise ValueError('TIFF directory limit')
        for index in range(count):
            position=offset+2+index*12
            tag,kind,size=struct.unpack_from(endian+'HHI',data,position)
            unit={1:1,2:1,3:2,4:4,5:8,6:1,7:1,8:2,9:4,10:8,11:4,12:8,13:4}.get(kind)
            if unit is None or size*unit>len(data):raise ValueError('TIFF value limit')
            start=position+8 if size*unit<=4 else struct.unpack_from(endian+'I',data,position+8)[0]
            if start+size*unit>len(data):raise ValueError('TIFF value outside file')
            if len(seen)==1 and tag in (256,257) and kind in (3,4) and size==1:
                dimensions[tag]=struct.unpack_from(endian+('H' if kind==3 else 'I'),data,start)[0]
            if tag in (330,34665,34853,40965) or kind==13:
                if kind not in (4,13) or size>64:raise ValueError('TIFF subdirectory limit')
                pending.extend(struct.unpack_from(endian+str(size)+'I',data,start))
        pending.append(struct.unpack_from(endian+'I',data,offset+2+count*12)[0])
    return dimensions[256],dimensions[257]


def jpeg_size(data):
    if not data.startswith(b'\xff\xd8'):raise ValueError('Not JPEG')
    offset=2
    for _ in range(4096):
        if offset+4>min(len(data),256*1024) or data[offset]!=255:break
        while offset<len(data) and data[offset]==255:offset+=1
        marker=data[offset];offset+=1
        if marker in (0xDA,0xD9):break
        if marker==1 or 0xD0<=marker<=0xD8:continue
        size=struct.unpack_from('>H',data,offset)[0]
        if size<2 or offset+size>len(data):break
        if marker in (0xC0,0xC1,0xC2,0xC3,0xC5,0xC6,0xC7,0xC9,0xCA,0xCB,0xCD,0xCE,0xCF):
            if size<8:break
            height,width=struct.unpack_from('>HH',data,offset+3)
            return width,height
        offset+=size
    raise ValueError('JPEG dimensions unavailable within header limit')


def jp2_size(data):
    if not data.startswith(b'\0\0\0\x0cjP  \r\n\x87\n'):raise ValueError('Not JP2')
    def boxes(start,end):
        for _ in range(512):
            if start==end:return
            size,kind=struct.unpack_from('>I4s',data,start);header=8
            if size==1:size=struct.unpack_from('>Q',data,start+8)[0];header=16
            elif size==0:size=end-start
            if size<header or start+size>end:raise ValueError('Invalid JP2 box')
            yield kind,start+header,start+size
            start+=size
        raise ValueError('JP2 box limit')
    dimensions=None;codestream=None
    for kind,start,end in boxes(0,len(data)):
        if kind==b'jp2h':
            for child,position,limit in boxes(start,end):
                if child==b'ihdr' and limit-position>=14:
                    height,width=struct.unpack_from('>II',data,position)
                    dimensions=width,height
        elif kind==b'jp2c':codestream=data[start:min(end,start+64)]
    if dimensions is None or codestream is None or not codestream.startswith(b'\xffO\xffQ'):
        raise ValueError('JP2 image header missing')
    width,height,left,top=struct.unpack_from('>IIII',codestream,8)
    if struct.unpack_from('>H',codestream,40)[0] not in (1,3):raise ValueError('Unsupported JP2 components')
    if dimensions!=(width-left,height-top):raise ValueError('JP2 dimension mismatch')
    return dimensions


def image_size(suffix,data):
    if suffix=='.png' and data.startswith(b'\x89PNG\r\n\x1a\n') and data[8:16]==b'\0\0\0\rIHDR':
        return struct.unpack_from('>II',data,16)
    if suffix in ('.jpg','.jpeg'):return jpeg_size(data)
    if suffix=='.gif' and data[:6] in (b'GIF87a',b'GIF89a'):return struct.unpack_from('<HH',data,6)
    if suffix in ('.tif','.tiff'):return tiff_size(data)
    if suffix=='.jp2':return jp2_size(data)
    if suffix=='.webp' and data[:4]==b'RIFF' and data[8:12]==b'WEBP':
        kind=data[12:16]
        if kind==b'VP8X':return 1+int.from_bytes(data[24:27],'little'),1+int.from_bytes(data[27:30],'little')
        if kind==b'VP8 ' and data[23:26]==b'\x9d\x01\x2a':
            width,height=struct.unpack_from('<HH',data,26);return width&0x3fff,height&0x3fff
        if kind==b'VP8L' and data[20:21]==b'/':
            size=int.from_bytes(data[21:25],'little');return 1+(size&0x3fff),1+((size>>14)&0x3fff)
    raise ValueError('Image header unavailable')


def project(path,data):
    suffix=PurePosixPath(path).suffix.lower()
    images={'.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg','.gif':'image/gif',
            '.webp':'image/webp','.tif':'image/tiff','.tiff':'image/tiff','.jp2':'image/jp2'}
    media={'.wav':('audio','audio/wav'),'.mp3':('audio','audio/mpeg'),
           '.ogg':('audio','audio/ogg'),'.oga':('audio','audio/ogg'),
           '.m4a':('audio','audio/mp4'),'.mp4':('video','video/mp4'),'.webm':('video','video/webm')}
    if suffix not in images and suffix!='.pdf' and suffix not in media:
        return {'available':False,'reason':'unsupported-format'}
    limit=IMAGE_BYTES if suffix in images else PDF_BYTES if suffix=='.pdf' else MEDIA_BYTES
    if not data or len(data)>limit:return {'available':False,'reason':'byte-limit','byte_limit':limit}
    if suffix in images:
        try:width,height=image_size(suffix,data)
        except (ValueError,KeyError,IndexError,struct.error):
            return {'available':False,'reason':'image-header'}
        if not 0<width<=IMAGE_SIDE or not 0<height<=IMAGE_SIDE or width*height>IMAGE_PIXELS:
            return {'available':False,'reason':'image-limit','pixel_limit':IMAGE_PIXELS,'side_limit':IMAGE_SIDE}
        return dict(available=True,kind='image',mime=images[suffix],byte_limit=limit,width=width,height=height)
    if suffix=='.pdf':
        valid=data[:8] in [b'%PDF-1.'+bytes([version]) for version in range(48,56)]+[b'%PDF-2.0'] and b'%%EOF' in data[-1024:]
        kind,mime='pdf','application/pdf'
    else:
        kind,mime=media[suffix]
        valid=(suffix=='.wav' and data[:4]==b'RIFF' and data[8:12]==b'WAVE'
               or suffix=='.mp3' and (data.startswith(b'ID3') or len(data)>2 and data[0]==255 and data[1]&0xe0==0xe0)
               or suffix in ('.ogg','.oga') and data.startswith(b'OggS\0') and any(mark in data[:4096] for mark in (b'OpusHead',b'\x01vorbis'))
               or suffix in ('.mp4','.m4a') and data[4:8]==b'ftyp' and len(data)>=16
               or suffix=='.webm' and data.startswith(b'\x1aE\xdf\xa3') and b'webm' in data[:4096])
    return dict(available=True,kind=kind,mime=mime,byte_limit=limit) if valid else {'available':False,'reason':'invalid-signature'}
