export interface KostFinding {module:string;message:string;severity:'error'|'warning'|'info'|'validator_error'}
export interface KostEntry {
  id:string;scope:'package'|'file';path:string|null;reported_path:string;
  mapping:'matched'|'unmapped'|'package';check:string;format_version:string;
  outcome:'valid'|'invalid'|'recognized'|'not_assessed';findings:KostFinding[];
}
export interface KostReport {
  run_id:string;input_sha256:string;report_sha256:string|null;prepared_id:string|null;
  reported_result:string|null;filename:string|null;available:boolean;reason?:string;parser_version?:number;
  checked_at?:number|null;report_path?:string;
  summary?:string;validator_error?:boolean;entries?:KostEntry[];
  file_counts?:{valid:number;invalid:number;recognized:number;not_assessed:number};
  finding_counts?:{error:number;warning:number;info:number;validator_error:number};
}
export function kostOutcome(entry:KostEntry):string {
  const outcome=({valid:'Valid',invalid:'Invalid',recognized:'Recognized, not validated',not_assessed:'Not assessed'})[entry.outcome];
  if(entry.findings.some(finding=>finding.severity==='validator_error'))return outcome+' · validator error';
  if(entry.outcome==='valid' && entry.findings.some(finding=>finding.severity==='warning'))return 'Valid with warning';
  return outcome;
}