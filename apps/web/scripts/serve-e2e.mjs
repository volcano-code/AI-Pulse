// Serve the actual Next standalone production artifact for browser acceptance.
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
const target = path.resolve('.next/standalone');
if (!fs.existsSync(path.join(target, 'server.js'))) throw new Error('Run npm run build before production E2E');
fs.cpSync('.next/static', path.join(target, '.next/static'), { recursive: true });
if (fs.existsSync('public')) fs.cpSync('public', path.join(target, 'public'), { recursive: true });
process.env.HOSTNAME = '127.0.0.1';
process.env.PORT = '3000';
process.env.NODE_ENV = 'production';
await import(pathToFileURL(path.join(target, 'server.js')).href);
