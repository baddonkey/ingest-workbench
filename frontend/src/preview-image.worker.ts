import type {PreviewImageRequest,PreviewImageResult} from './package-handler.types';

addEventListener('message',async(event:MessageEvent<PreviewImageRequest>)=>{
  try{
    const request=event.data,{width,height}=request;
    if(!Number.isInteger(width)||!Number.isInteger(height)||width<1||height<1||width>8192||height>8192||width*height>16_000_000)throw new Error('Image dimensions exceed preview limits');
    let pixels:Uint8ClampedArray<ArrayBuffer>;
    if(request.mime==='image/tiff'){
      const tiff=await import('utif2'),image=tiff.decode(request.buffer)[0];
      if(!image)throw new Error('TIFF contains no image');
      const tag=(name:string)=>{const value=image[name];return Array.isArray(value)?value:[];};
      if(tag('t256')[0]!==width||tag('t257')[0]!==height)throw new Error('TIFF dimensions changed');
      if(![1,3,4,5,7,8,32773,32946].includes(Number(tag('t259')[0]??1)))throw new Error('TIFF compression is not supported');
      if(Number(tag('t277')[0]??1)>4||tag('t258').some(value=>![1,2,4,8,16].includes(Number(value))))throw new Error('TIFF sample layout is not supported');
      tiff.decodeImage(request.buffer,image);pixels=new Uint8ClampedArray(tiff.toRGBA8(image));
    }else if(request.mime==='image/jp2'){
      const {JpxImage}=await import('pdfjs-dist/image_decoders/pdf.image_decoders.mjs');
      JpxImage.setOptions({useWasm:true,useWorkerFetch:true,wasmUrl:request.wasmUrl});
      const bytes=new Uint8Array(request.buffer),properties=JpxImage.parseImageProperties(bytes);
      if(properties.width!==width||properties.height!==height||![1,3].includes(properties.componentsCount))throw new Error('Unsupported JP2 component layout');
      const components=properties.componentsCount,decoded=await JpxImage.instance.decode(bytes,{numComponents:components});
      if(decoded.length!==width*height*components)throw new Error('JP2 decoded size mismatch');
      pixels=new Uint8ClampedArray(width*height*4);
      for(let pixel=0;pixel<width*height;pixel++){
        const source=pixel*components,target=pixel*4;
        pixels[target]=decoded[source];pixels[target+1]=decoded[source+(components===1?0:1)];
        pixels[target+2]=decoded[source+(components===1?0:2)];pixels[target+3]=255;
      }
    }else throw new Error('Unsupported image decoder');
    if(pixels.length!==width*height*4)throw new Error('Decoded image dimensions do not match the retained source');
    const result:PreviewImageResult={width,height,pixels};postMessage(result,{transfer:[pixels.buffer]});
  }catch(error){const result:PreviewImageResult={error:error instanceof Error?error.message:'Image decoding failed'};postMessage(result);}
});
