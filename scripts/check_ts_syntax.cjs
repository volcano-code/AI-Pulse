// Syntax-only check. Does NOT replace tsc --noEmit or next build.
const fs=require("fs"),path=require("path");
const root=path.resolve(__dirname,"../apps/web");
const ts=require(process.env.TYPESCRIPT_PATH||require.resolve("typescript",{paths:[root]}));
let files=0,errors=[];
function walk(p){for(const e of fs.readdirSync(p,{withFileTypes:true})){const f=path.join(p,e.name);if(e.isDirectory()&&!["node_modules",".next"].includes(e.name))walk(f);else if(e.isFile()&&/\.tsx?$/.test(f)&&!f.endsWith(".d.ts")){files++;const out=ts.transpileModule(fs.readFileSync(f,"utf8"),{fileName:f,reportDiagnostics:true,compilerOptions:{jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}});for(const d of out.diagnostics||[])if(d.category===ts.DiagnosticCategory.Error)errors.push({file:path.relative(root,f),message:ts.flattenDiagnosticMessageText(d.messageText," ")});}}}
walk(root);console.log(JSON.stringify({compiler:ts.version,scope:"Syntax only; not typecheck, dependency resolution or Next.js build",files,errors},null,2));process.exit(errors.length?1:0);
