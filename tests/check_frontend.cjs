// Static checks do not replace the blocked browser acceptance test.
const fs=require('node:fs'), path=require('node:path'), assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..'),html=fs.readFileSync(path.join(root,'index.html'),'utf8'),js=fs.readFileSync(path.join(root,'app.js'),'utf8'),css=fs.readFileSync(path.join(root,'style.css'),'utf8');
const ids=[...html.matchAll(/\bid="([^"]+)"/g)].map(x=>x[1]);
assert.equal(new Set(ids).size,ids.length,'HTML IDs must be unique');
for(const match of js.matchAll(/\$\('#([\w-]+)(?:[^']*)'\)/g))assert(ids.includes(match[1]),`Missing element: ${match[1]}`);
for(const match of html.matchAll(/<use href="#([^"]+)"/g))assert(ids.includes(match[1]),`Missing icon: ${match[1]}`);
for(const file of ['app.js','style.css','favicon.svg'])assert(fs.existsSync(path.join(root,file)));
assert(!html.includes('<script>'),'Inline scripts must not conflict with the Content Security Policy');
assert(!js.includes('setTimeout(()=>{meta.status'),'Task success cannot be simulated');
assert(js.includes('createObjectURL')&&js.includes('fetch('),'The app must contain actual file export and server requests');
assert(css.includes('@media(max-width:640px)'),'Mobile layout required');
assert(css.includes('letter-spacing:0'),'No negative or viewport-scaled typography');
console.log(`Static frontend checks passed: ${ids.length} unique IDs and all referenced controls/icons present.`);
