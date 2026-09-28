export type Mode = "live" | "replay";
export interface Status {version:string;data_mode:Mode;generation_mode:"live"|"extractive";model:string|null;articles:number;events:number;snapshots:number;briefs:number;sources:number;server_time:string;runtime:string;capabilities:Record<string,boolean>}
export interface Source {id:string;name:string;url:string;kind:string;enabled:boolean;last_success_at:string|null;last_attempt_at:string|null;last_error:string|null;failure_count:number}
export interface Preferences {timezone:string;topics:string[];blocked_keywords:string[];max_items:number;lookback_hours:number}
export interface BriefItem {id:string;position:number;title:string;summary:string;article_id:string;snapshot_id:string;source_name:string;source_url:string;published_at:string|null;captured_at:string;scope:string;topic:string;bookmarked:boolean;evidence_quote:string;quote_start:number;quote_end:number;verification:string;relevance_reason:string}
export interface Edition {id:string;local_date:string;created_at:string;status:string;data_mode:Mode;generation_mode:string}
export interface Brief extends Edition {cutoff_at:string;window_start:string;timezone:string;model:string|null;approved_at:string|null;items:BriefItem[];usage:{model_calls:number;prompt_tokens:number|null;completion_tokens:number|null;cost_usd:number|null};source_health:Source[]}
export interface Snapshot {id:string;title:string;text:string;content_hash:string;captured_at:string;scope:string;source_url:string}
export interface Run {id:string;kind:string;status:string;created_at:string;finished_at:string|null;events:{stage:string;message:string;at:string}[];result:Record<string,unknown>;error:string|null}
export interface Citation {article_id:string;snapshot_id:string;title:string;source_name:string;url:string;quote:string;quote_start:number;quote_end:number;published_at:string|null}
export interface Answer {question:string;mode:string;data_mode:Mode;answer:string;citations:Citation[];searched_documents:number;abstained:boolean}

export interface EventSummary {id:string;title:string;topic:string;created_at:string;updated_at:string;current_version_id:string|null;article_count:number;version_count:number}
