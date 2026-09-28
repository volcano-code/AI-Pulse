import type { Answer, Citation } from "./types";
export class ApiError extends Error { constructor(public status:number, message:string) {super(message);} }
export async function response(path:string, init:RequestInit={}):Promise<Response> {
  const headers = new Headers(init.headers);
  const token = typeof window !== "undefined" ? sessionStorage.getItem("pulse-token") : null;
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.method && init.method !== "GET") headers.set("Content-Type", "application/json");
  const result = await fetch(`/api/v1${path}`, {...init, headers, cache:"no-store"});
  if (!result.ok) {
    const value = await result.json().catch(()=>({detail:`HTTP ${result.status}`}));
    throw new ApiError(result.status, typeof value.detail === "string" ? value.detail : JSON.stringify(value.detail));
  }
  return result;
}
export async function api<T>(path:string, init?:RequestInit):Promise<T> { return (await response(path,init)).json() as Promise<T>; }
export async function nullable<T>(path:string):Promise<T|null> {try{return await api<T>(path);}catch(e){if(e instanceof ApiError && e.status===404)return null;throw e;}}
export function safeUrl(value:string):string {try {const u=new URL(value);return ["http:","https:"].includes(u.protocol)?u.href:"#";}catch{return "#";}}
export async function streamEvidence(question:string, onCitation:(c:Citation)=>void, signal?:AbortSignal):Promise<Answer> {
 const result=await response("/ask/stream",{method:"POST",body:JSON.stringify({question}),signal});
 if(!result.body)throw new Error("Streaming response has no body");
 const reader=result.body.getReader(),decoder=new TextDecoder();let buffer="",answer:Answer|undefined;
 try {while(true){const {done,value}=await reader.read();if(done)break;buffer+=decoder.decode(value,{stream:true});let boundary:number;
  while((boundary=buffer.indexOf("\n\n"))>=0){const frame=buffer.slice(0,boundary);buffer=buffer.slice(boundary+2);
   const lines=frame.split("\n");const type=lines.find(x=>x.startsWith("event: "))?.slice(7);
   const text=lines.filter(x=>x.startsWith("data: ")).map(x=>x.slice(6)).join("\n");if(!text)continue;
   if(type==="citation")onCitation(JSON.parse(text) as Citation);if(type==="done")answer=JSON.parse(text) as Answer;
  }
 }} finally {reader.releaseLock();}
 if(!answer)throw new Error("Evidence stream ended without a done event");return answer;
}
