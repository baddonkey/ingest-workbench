export interface MetadataRow {id:string;text:string;attributes:Record<string,string>;children:Record<string,MetadataRow[]>}
export interface PreparationNote {id:string;node_id:string;text:string;purpose:'internal'|'description-candidate';created:string;created_by:string;updated:string;updated_by:string;revision:number}
export interface ReorderGroup {parent_id:string;child_kind:string;children:{id:string;source_id:string;title:string}[]}
export interface CreationOption {parent_id:string;child_kind:string;maximum:number;fields:MetadataField[]}
export interface MetadataField {id?:string;tag:string;label:string;type:string;minimum:number;maximum:number|null;choices:string[];choice_only:boolean;max_length:number;children:MetadataField[];attributes:{name:string;label:string;required:boolean}[];date:boolean;editable?:boolean;values?:MetadataRow[]}
export interface MetadataObject {id:string;kind:string;label:string;title:string;path:string;source_id:string|null;depth:number;fields:MetadataField[]}
export type MetadataNavigator = 'classification'|'hierarchy'|'inventory';
export interface MetadataNavigationRow {key:string;object_id:string;parent_key:string|null;depth:number}
export interface MetadataTreeObject extends MetadataObject {navigation_key:string;parent_key:string|null;navigation_depth:number}
export interface MetadataOperation {id:string;kind:string;actor:string;created:string;reason:string;before_metadata_sha256:string;after_metadata_sha256:string;evidence_sha256:string;changes:{tag?:string;node_id?:string;before?:MetadataRow[];after?:MetadataRow[];name?:string;related_operation_id?:string;note_id?:string;note_before?:PreparationNote|null;note_after?:PreparationNote|null;parent_id?:string;child_kind?:string;before_order?:string[];after_order?:string[];objects?:{id:string;source_id:string;title:string}[]}}
export interface PreparedInput {id:string;sha256:string;evidence_sha256:string;operation_id:string;predecessor_id:string|null;metadata_sha256:string;preparation_sha256:string;actor:string;created:string;reason:string}
export interface PreparedSelection {sip_id:string;prepared_id:string}
export interface MetadataSample {revision:number;items:{node_id:string;reference:string;name:string}[]}
export interface MetadataWorkspace {sip_id:string;source_sha256:string;profile:string;revision:number;nodes:MetadataObject[];navigation:Record<MetadataNavigator,MetadataNavigationRow[]>;notes:PreparationNote[];reorder_groups:ReorderGroup[];creation_options:CreationOption[];history:MetadataOperation[];prepared_inputs:PreparedInput[];state:{head:string|null;metadata_sha256:string;undo:unknown[];redo:unknown[];changed:boolean;legacy_draft_id:string|null}}
