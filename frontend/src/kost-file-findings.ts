import {Component,computed,inject,input,signal} from '@angular/core';
import {DatePipe} from '@angular/common';
import {ArchiveApi} from './archive-api';
import {KostReport,kostOutcome} from './kost-report.types';

@Component({selector:'archive-kost-file-findings',standalone:true,imports:[DatePipe],
  template:`@if(path()){
    <section class="file-validation" aria-label="File validation results">
      <h4>KOST file validation</h4>
      <p class="path">{{path()}}</p>
      @if(!inputSha256()){<p>No assessment for this working version.</p>}
      @else if(report() && (report()?.input_sha256!==inputSha256() || report()?.prepared_id!==preparedId())){
        <p>The selected report belongs to another SIP version.</p>
      }@else if(!entries().length){<p>Not assessed in the selected report.</p>}
      @for(entry of entries();track entry.id){
        <div class="file-check"><strong>{{outcome(entry)}}</strong><span>{{entry.check}} {{entry.format_version}}</span></div>
        @for(finding of entry.findings;track $index){
          <article [attr.data-severity]="finding.severity"><div><b>{{finding.severity==='validator_error' ? 'Validator error' : finding.severity}}</b><strong>{{finding.module}}</strong></div><p>{{finding.message}}</p></article>
        }
      }
      @if(entries().length){
        @if(report()?.checked_at;as checked){<small>Checked {{checked*1000 | date:'medium'}}</small>}
        <small>Run {{report()?.run_id}} · SHA-256 {{report()?.input_sha256}}</small>
        @if(report()?.report_path){<button type="button" (click)="download()" [disabled]="downloading()"><img src="/package-turbo/download.svg" width="14" height="14" alt="">Download run XML report</button>}
        @if(error()){<p role="alert">{{error()}}</p>}
      }
    </section>
  }`,
  styles:[`:host{display:block;min-width:0}.file-validation{margin:12px 0;padding-top:10px;border-top:1px solid #c2d2cc;font-size:12px;letter-spacing:0}h4{font-size:13px;margin:0 0 7px}.path,small{font-family:monospace;overflow-wrap:anywhere}.file-check{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0}article{padding:7px 9px;margin:5px 0;border-left:3px solid #aebfb7;background:#f0f5f2}article[data-severity=error],article[data-severity=validator_error]{border-color:#b42318;background:#fff0ed}article[data-severity=warning]{border-color:#b58b3f;background:#fff7e8}article>div{display:flex;flex-wrap:wrap;gap:8px}article b{text-transform:capitalize}p{margin:4px 0;white-space:pre-wrap;overflow-wrap:anywhere}small{display:block;font-size:10px;margin-top:8px;color:#586c66}`]})
export class KostFileFindings {
  report=input<KostReport|null>(null);path=input('');inputSha256=input('');preparedId=input<string|null>(null);
  readonly outcome=kostOutcome;
  private readonly api=inject(ArchiveApi);
  readonly downloading=signal(false);readonly error=signal('');
  readonly entries=computed(()=>{
    const report=this.report();
    return report?.available && report.input_sha256===this.inputSha256() && report.prepared_id===this.preparedId()
      ? (report.entries||[]).filter(entry=>entry.scope==='file' && entry.path===this.path()):[];
  });
  async download(){
    const report=this.report();if(!report?.report_path || !this.entries().length)return;
    this.downloading.set(true);this.error.set('');
    try{await this.api.download(report.report_path,'kost-'+report.run_id+'.xml');}
    catch(error){this.error.set(error instanceof Error?error.message:'Report download failed');}
    finally{this.downloading.set(false);}
  }
}