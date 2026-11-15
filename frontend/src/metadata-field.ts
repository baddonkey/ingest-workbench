import {Component,input,output,signal} from '@angular/core';
import {FormsModule} from '@angular/forms';
import {MetadataField,MetadataRow} from './metadata-editor.types';
import {EditorLanguage,editorText} from './metadata-editor.locale';
import {ContextMenu,ContextMenuState} from './context-menu';

@Component({selector:'archive-metadata-field',standalone:true,imports:[FormsModule,ContextMenu],template:`
  @for(row of rows();track row.id;let index=$index){
    <div class="metadata-row" (contextmenu)="rowContext($event,index)" (keydown)="rowContextKey($event,index)">
      @if(field().children.length){
        @if(dateCore();as core){<div class="date-point"><archive-metadata-field [field]="core" [rows]="row.children[core.tag]" [language]="language()" [disabled]="disabled()" (changed)="children(index,core.tag,$event)"/><details class="date-details"><summary [attr.aria-label]="text('dateAdditional')">⋯</summary>@for(child of field().children;track child.tag){@if(child.tag!==core.tag){<label>{{text(child.tag,child.label)}}</label><archive-metadata-field [field]="child" [rows]="row.children[child.tag]" [language]="language()" [disabled]="disabled()" (changed)="children(index,child.tag,$event)"/>}}</details></div>}
        @else{
        <div [class.span-fields]="field().date">@for(child of field().children;track child.tag){<fieldset><legend>{{text(child.tag,child.label)}} @if(child.minimum){<small>· {{text('required')}}</small>}</legend>
          <archive-metadata-field [field]="child" [rows]="row.children[child.tag]" [language]="language()" [disabled]="disabled()" (changed)="children(index,child.tag,$event)"/>
        </fieldset>}</div>}
      }@else{
        @if(field().choice_only){<select [attr.aria-label]="text(field().tag,field().label)" [ngModel]="row.text" (ngModelChange)="value(index,$event)" [disabled]="disabled()"><option value="" disabled>—</option>@for(choice of field().choices;track choice){<option [value]="choice">{{choice}}</option>}</select>}
        @else{<textarea rows="1" [attr.aria-label]="text(field().tag,field().label)" [ngModel]="row.text" (ngModelChange)="value(index,$event)" [disabled]="disabled()" [maxlength]="field().max_length"></textarea>}
      }
      @for(attribute of field().attributes;track attribute.name){<label>{{text(attribute.name,attribute.label)}}<input [ngModel]="row.attributes[attribute.name]" (ngModelChange)="attributeValue(index,attribute.name,$event)" [disabled]="disabled()" maxlength="8000"></label>}
    </div>
  }@empty{
    @if(field().maximum===1 && !field().children.length){
      @if(field().choice_only){<select [attr.aria-label]="text(field().tag,field().label)" [ngModel]="''" (ngModelChange)="emptyValue($event)" [disabled]="disabled()"><option value="">—</option>@for(choice of field().choices;track choice){<option [value]="choice">{{choice}}</option>}</select>}
      @else{<textarea rows="1" [attr.aria-label]="text(field().tag,field().label)" [ngModel]="''" (ngModelChange)="emptyValue($event)" [disabled]="disabled()" [maxlength]="field().max_length"></textarea>}
    }@else{<p class="absent">{{text('empty')}}</p>}
  }
  @if(!disabled() && field().maximum!==1 && rows().length<(field().maximum || 200)){<button class="secondary" (click)="add()"><img src="/package-turbo/file-plus.svg" alt="">{{text('add')}}</button>}
  <archive-context-menu [state]="contextMenu()" (closed)="contextMenu.set(null)"/>
`,styles:[`:host{display:block;min-width:0;font:inherit}textarea,select,input{display:block;width:100%;font:inherit;color:inherit;min-height:24px;height:24px;padding:2px 4px;border:1px solid var(--line,#c2d2cc);border-radius:2px;background:var(--paper,#fcfdfc);margin:0!important}textarea{resize:vertical;line-height:18px}textarea:focus{border-color:var(--accent,#16796f)}select{background:var(--control-gradient)}fieldset{border:1px solid var(--line);margin:5px 0;padding:6px;min-width:0}legend,label{font-size:12px}small{display:none}.metadata-row{margin:0}.span-fields{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:5px}.span-fields>fieldset{display:flex;align-items:center;gap:4px;border:0;margin:0;padding:0}.span-fields>fieldset>legend{float:left;padding:0;white-space:nowrap;font-weight:400}.span-fields archive-metadata-field{flex:1;min-width:0}.date-point{display:flex;gap:2px;min-width:0;position:relative}.date-point>archive-metadata-field{flex:1;min-width:0}.date-details{flex:none;font-size:11px}.date-details summary{padding:0 3px;min-height:24px;line-height:24px;list-style:none;color:var(--muted)}.date-details[open]{position:absolute;z-index:5;right:0;top:0;background:var(--paper);border:1px solid var(--line);padding:4px;min-width:140px;box-shadow:0 3px 8px #0002}.row-actions{display:none;gap:4px;flex-wrap:wrap}.metadata-row:focus-within>.row-actions,.metadata-row:hover>.row-actions{display:flex}.row-actions button,button{min-height:20px;padding:1px 5px;font-size:10px;border:1px solid var(--line);border-radius:2px;background:var(--control-gradient);color:inherit}img{width:13px;height:13px;vertical-align:middle;margin-right:3px}.absent{font-size:11px;color:var(--muted);margin:2px 0}textarea:disabled,select:disabled{background:var(--workspace);opacity:1;color:inherit}`]})
export class MetadataFieldEditor {
  field=input.required<MetadataField>(); rows=input.required<MetadataRow[]>(); language=input<EditorLanguage>('en'); disabled=input(false);
  changed=output<MetadataRow[]>();
  contextMenu=signal<ContextMenuState|null>(null);
  rowContext(event:MouseEvent|KeyboardEvent,index:number){event.preventDefault();event.stopPropagation();const invoker=event.currentTarget as HTMLElement,bounds=invoker.getBoundingClientRect();this.contextMenu.set({x:event instanceof MouseEvent?event.clientX:bounds.left,y:event instanceof MouseEvent?event.clientY:bounds.bottom,label:this.text(this.field().tag,this.field().label),invoker,commands:[
    {label:this.text('copyValue'),icon:'copy',run:()=>{void navigator.clipboard.writeText(this.rows()[index].text).catch(()=>{});}},
    {label:this.text('removeEntry'),icon:'x',disabled:this.disabled()||this.rows().length<=this.field().minimum,run:()=>this.remove(index)},
    {label:this.text('up'),disabled:this.disabled()||index===0,run:()=>this.move(index,-1)},
    {label:this.text('down'),disabled:this.disabled()||index===this.rows().length-1,run:()=>this.move(index,1)}]});}
  rowContextKey(event:KeyboardEvent,index:number){if(event.key==='ContextMenu'||event.shiftKey&&event.key==='F10')this.rowContext(event,index);}
  text(key:string,fallback?:string){return editorText(key,this.language(),fallback);}
  dateCore(){return this.field().date?this.field().children.find(f=>f.tag==='datum'):undefined;}
  update(index:number,fn:(row:MetadataRow)=>void){const rows=structuredClone(this.rows());fn(rows[index]);this.changed.emit(rows);}
  value(index:number,text:string){this.update(index,row=>row.text=text);}
  emptyValue(text:string){const row=this.newRow(this.field());row.text=text;this.changed.emit([row]);}
  attributeValue(index:number,name:string,text:string){this.update(index,row=>row.attributes[name]=text);}
  children(index:number,tag:string,values:MetadataRow[]){this.update(index,row=>row.children[tag]=values);}
  remove(index:number){this.changed.emit(this.rows().filter((_,i)=>i!==index));}
  move(index:number,delta:number){const rows=[...this.rows()];[rows[index],rows[index+delta]]=[rows[index+delta],rows[index]];this.changed.emit(rows);}
  newRow(field:MetadataField):MetadataRow{return {id:'new-'+crypto.randomUUID(),text:'',attributes:Object.fromEntries(field.attributes.map(a=>[a.name,''])),children:Object.fromEntries(field.children.map(c=>[c.tag,Array.from({length:c.minimum},()=>this.newRow(c))]))};}
  add(){this.changed.emit([...this.rows(),this.newRow(this.field())]);}
}
