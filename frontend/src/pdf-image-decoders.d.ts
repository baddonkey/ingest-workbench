declare module 'pdfjs-dist/image_decoders/pdf.image_decoders.mjs' {
  export class JpxImage {
    static setOptions(options:{useWasm:boolean;useWorkerFetch:boolean;wasmUrl:string}):void;
    static readonly instance:JpxImage;
    static parseImageProperties(bytes:Uint8Array):{width:number;height:number;componentsCount:number;bitsPerComponent:number};
    decode(bytes:Uint8Array,options:{numComponents:number}):Promise<Uint8ClampedArray>;
  }
}
