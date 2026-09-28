export function sliceCodePoints(value:string,start:number,end?:number):string { return Array.from(value).slice(start,end).join(""); }
