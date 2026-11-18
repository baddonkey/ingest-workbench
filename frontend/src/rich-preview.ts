import {Component,ElementRef,OnChanges,OnDestroy,inject,input,signal,viewChild} from '@angular/core';
import type {PDFDocumentLoadingTask,PDFDocumentProxy,RenderTask} from 'pdfjs-dist';
import {ArchiveApi} from './archive-api';
import {EditorLanguage,editorText} from './metadata-editor.locale';
import type {PackageNodeDetail,PreviewImageRequest,PreviewImageResult} from './package-handler.types';

@Component({
  selector:'archive-rich-preview',standalone:true,template:`
    <section [attr.aria-label]="text('previewOriginal')">
      <p>{{text('previewScope')}}</p>
      <p class="identity">{{detail().input_sha256}} · {{detail().file?.sha256}}</p>
      @if(!detail().rich_preview?.available){<p role="status">{{text('previewReason_'+detail().rich_preview?.reason)}}</p>}
      @else{
        <p>{{text('previewLimits')}}</p>
        <button (click)="load()" [disabled]="loading()">{{text(ready()?'previewReload':'previewLoad')}}</button>
        @if(loading()){<p role="status">{{text('previewLoading')}}</p>}
        @if(pages()){<nav [attr.aria-label]="text('previewPages')"><button (click)="changePage(-1)" [disabled]="loading()||page()<=1">{{text('previewPrevious')}}</button><span>{{page()}} / {{pages()}}</span><button (click)="changePage(1)" [disabled]="loading()||page()>=pages()">{{text('previewNext')}}</button></nav>}
        <canvas #canvas [hidden]="!ready()||!!mediaUrl()" role="img" [attr.aria-label]="detail().file?.title"></canvas>
        @if(mediaUrl()){
          @if(detail().rich_preview?.kind==='audio'){<audio #audio controls preload="metadata" [src]="mediaUrl()" [disableRemotePlayback]="true" (loadedmetadata)="checkMedia($event)" (error)="mediaError()" [attr.aria-label]="detail().file?.title"></audio>}
          @else{<video #video controls preload="metadata" playsinline [src]="mediaUrl()" [disableRemotePlayback]="true" [disablePictureInPicture]="true" (loadedmetadata)="checkMedia($event)" (error)="mediaError()" [attr.aria-label]="detail().file?.title"></video>}
        }
      }
      @if(error()){<p role="alert">{{error()}}</p>}
    </section>`,
  styles:[`:host{display:block;min-width:0}section{font:13px/1.4 'Segoe UI',sans-serif}p{overflow-wrap:anywhere}.identity{font-family:monospace;font-size:10px}canvas,video{display:block;max-width:100%;max-height:65dvh;margin:12px auto;object-fit:contain}canvas[hidden]{display:none}audio{display:block;width:100%;margin:12px 0}nav{display:flex;align-items:center;gap:12px;margin:12px 0}[role=alert]{color:#8f251b;white-space:pre-wrap}button{padding:5px 10px}`],
})
export class RichPreview implements OnChanges,OnDestroy {
  detail=input.required<PackageNodeDetail>();contentPath=input.required<string>();language=input<EditorLanguage>('en');
  loading=signal(false);ready=signal(false);error=signal('');mediaUrl=signal('');page=signal(1);pages=signal(0);
  private api=inject(ArchiveApi);
  private canvas=viewChild<ElementRef<HTMLCanvasElement>>('canvas');
  private audio=viewChild<ElementRef<HTMLAudioElement>>('audio');
  private video=viewChild<ElementRef<HTMLVideoElement>>('video');
  private generation=0;private controller:AbortController|null=null;private timer:ReturnType<typeof setTimeout>|null=null;
  private pdfTask:PDFDocumentLoadingTask|null=null;private pdf:PDFDocumentProxy|null=null;private renderTask:RenderTask|null=null;private imageWorker:Worker|null=null;
  private rejectImage:((reason:Error)=>void)|null=null;
  text(key:string){return editorText(key,this.language());}
  ngOnChanges(){this.release();this.error.set('');}
  ngOnDestroy(){this.release();}
  private release(){
    this.generation++;this.controller?.abort();this.controller=null;if(this.timer)clearTimeout(this.timer);this.timer=null;
    this.renderTask?.cancel();this.renderTask=null;this.imageWorker?.terminate();this.imageWorker=null;
    this.rejectImage?.(new DOMException('Preview closed','AbortError'));this.rejectImage=null;
    if(this.pdfTask)void this.pdfTask.destroy().catch(error=>console.error('PDF preview cleanup failed',error));
    this.pdfTask=null;this.pdf=null;this.pages.set(0);
    for(const media of [this.audio()?.nativeElement,this.video()?.nativeElement])if(media){media.pause();media.removeAttribute('src');media.load();}
    if(this.mediaUrl())URL.revokeObjectURL(this.mediaUrl());this.mediaUrl.set('');
    const canvas=this.canvas()?.nativeElement;if(canvas){canvas.width=0;canvas.height=0;}
    this.loading.set(false);this.ready.set(false);
  }
  private fail(error:unknown,generation:number){
    if(generation!==this.generation)return;
    const detail=error instanceof Error?error.message:String(error);
    this.release();this.error.set(this.text('previewFailed')+' '+detail);
  }
  private deadline(generation:number){
    if(this.timer)clearTimeout(this.timer);
    this.timer=setTimeout(()=>this.fail(new Error(this.text('previewTimeout')),generation),20_000);
  }
  private complete(generation:number){
    if(generation!==this.generation)return;
    if(this.timer)clearTimeout(this.timer);this.timer=null;this.loading.set(false);this.ready.set(true);
  }
  async load(){
    if(this.loading())return;
    this.release();this.error.set('');
    const generation=this.generation,policy=this.detail().rich_preview,file=this.detail().file;
    this.loading.set(true);this.deadline(generation);this.controller=new AbortController();
    try{
      if(!policy?.available||!file||!policy.byte_limit||!policy.mime||!policy.kind||!file.sha256||file.size===undefined)throw new Error(this.text('previewUnavailable'));
      const allowed:{[kind:string]:string[]}={image:['image/png','image/jpeg','image/gif','image/webp','image/tiff','image/jp2'],pdf:['application/pdf'],audio:['audio/wav','audio/mpeg','audio/ogg','audio/mp4'],video:['video/mp4','video/webm']};
      const cap=policy.kind==='image'?8*1024*1024:policy.kind==='pdf'?12*1024*1024:32*1024*1024;
      if(!allowed[policy.kind]?.includes(policy.mime)||file.size<1||file.size>cap||file.size>policy.byte_limit)throw new Error(this.text('previewUnavailable'));
      const response=await this.api.response(this.contentPath(),{signal:this.controller.signal});
      if(!response.body)throw new Error(this.text('previewUnavailable'));
      const reader=response.body.getReader(),chunks:Uint8Array[]=[];let size=0;
      while(true){const {done,value}=await reader.read();if(done)break;size+=value.length;if(size>file.size||size>cap){await reader.cancel();throw new Error(this.text('previewSizeMismatch'));}chunks.push(value);}
      if(size!==file.size)throw new Error(this.text('previewSizeMismatch'));
      const buffer=new Uint8Array(size);let offset=0;for(const chunk of chunks){buffer.set(chunk,offset);offset+=chunk.length;}
      const digest=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',buffer)),byte=>byte.toString(16).padStart(2,'0')).join('');
      if(digest!==file.sha256)throw new Error(this.text('previewFixityMismatch'));
      if(generation!==this.generation)return;
      if(policy.kind==='pdf'){
        const pdfjs=await import('pdfjs-dist');if(generation!==this.generation)return;
        pdfjs.GlobalWorkerOptions.workerSrc='/pdfjs/pdf.worker.min.mjs';
        this.pdfTask=pdfjs.getDocument({data:buffer,enableXfa:false,disableFontFace:true,useSystemFonts:false,disableWorkerRendering:true,
          stopAtErrors:true,maxImageSize:16_000_000,canvasMaxAreaInBytes:64*1024*1024,
          cMapUrl:'/pdfjs/cmaps/',cMapPacked:true,standardFontDataUrl:'/pdfjs/standard_fonts/',wasmUrl:'/pdfjs/wasm/'});
        this.pdf=await this.pdfTask.promise;if(generation!==this.generation)return;
        if(this.pdf.numPages>200)throw new Error(this.text('previewPageLimit'));
        this.pages.set(this.pdf.numPages);this.page.set(1);await this.drawPage(generation);
      }else if(policy.kind==='image'){
        if(!policy.width||!policy.height||policy.width>8192||policy.height>8192||policy.width*policy.height>16_000_000)throw new Error(this.text('previewReason_image-limit'));
        if(policy.mime==='image/tiff'||policy.mime==='image/jp2'){
          this.imageWorker=new Worker(new URL('./preview-image.worker',import.meta.url),{type:'module'});
          const worker=this.imageWorker;
          const decoded=new Promise<PreviewImageResult>((resolve,reject)=>{this.rejectImage=reject;worker.onmessage=(event:MessageEvent<PreviewImageResult>)=>{this.rejectImage=null;resolve(event.data);};worker.onerror=()=>reject(new Error(this.text('previewImageFailed')));});
          const request:PreviewImageRequest={buffer:buffer.buffer,mime:policy.mime,width:policy.width,height:policy.height,wasmUrl:new URL('/pdfjs/wasm/',location.origin).href};
          worker.postMessage(request,[buffer.buffer]);
          const result=await decoded;if(generation!==this.generation)return;
          worker.terminate();this.imageWorker=null;
          if('error' in result)throw new Error(result.error);
          const {canvas,context}=this.targetCanvas(result.width,result.height);context.putImageData(new ImageData(result.pixels,canvas.width,canvas.height),0,0);
        }else{
          const bitmap=await createImageBitmap(new Blob([buffer],{type:policy.mime}));
          try{if(generation!==this.generation)return;const {context}=this.targetCanvas(bitmap.width,bitmap.height);context.drawImage(bitmap,0,0);}finally{bitmap.close();}
        }
        this.complete(generation);
      }else this.mediaUrl.set(URL.createObjectURL(new Blob([buffer],{type:policy.mime})));
    }catch(error){this.fail(error,generation);}
  }
  private targetCanvas(width:number,height:number){
    if(!Number.isInteger(width)||!Number.isInteger(height)||width<1||height<1||width>8192||height>8192||width*height>16_000_000)throw new Error(this.text('previewReason_image-limit'));
    const canvas=this.canvas()?.nativeElement,context=canvas?.getContext('2d');if(!canvas||!context)throw new Error(this.text('previewUnavailable'));
    canvas.width=width;canvas.height=height;return {canvas,context};
  }
  private async drawPage(generation:number){
    if(!this.pdf)throw new Error(this.text('previewUnavailable'));
    const page=await this.pdf.getPage(this.page());if(generation!==this.generation)return;
    const original=page.getViewport({scale:1});
    if(!Number.isFinite(original.width)||!Number.isFinite(original.height)||original.width<=0||original.height<=0)throw new Error(this.text('previewReason_image-limit'));
    const viewport=page.getViewport({scale:Math.min(1.5,1600/original.width,2400/original.height)});
    const {canvas}=this.targetCanvas(Math.ceil(viewport.width),Math.ceil(viewport.height));
    this.renderTask=page.render({canvas,viewport});await this.renderTask.promise;this.renderTask=null;page.cleanup();this.complete(generation);
  }
  async changePage(direction:number){
    if(this.loading()||!this.pdf||this.page()+direction<1||this.page()+direction>this.pages())return;
    this.page.update(page=>page+direction);this.ready.set(false);this.loading.set(true);this.deadline(this.generation);
    const generation=this.generation;try{await this.drawPage(generation);}catch(error){this.fail(error,generation);}
  }
  checkMedia(event:Event){
    const media=event.target;
    if(!this.mediaUrl()||!(media instanceof HTMLMediaElement)||media.currentSrc!==this.mediaUrl())return;
    if(!(media instanceof HTMLMediaElement)||!Number.isFinite(media.duration)||media.duration>7200||media.duration<=0
      ||media instanceof HTMLVideoElement&&(media.videoWidth>8192||media.videoHeight>8192||media.videoWidth*media.videoHeight>16_000_000)){
      this.fail(new Error(this.text('previewMediaLimit')),this.generation);return;
    }
    this.complete(this.generation);
  }
  mediaError(){if(this.mediaUrl())this.fail(new Error(this.text('previewMediaFailed')),this.generation);}
}
