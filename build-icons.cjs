const fs = require('node:fs');
const path = require('node:path');
const lucide = require(process.argv[2] || 'lucide');
const mapping = {grid:'LayoutGrid',clock:'History',settings:'Settings2',link:'Link',upload:'Upload',arrow:'ArrowRight',play:'Play',download:'Download',text:'Type',mic:'Mic',scan:'ScanText',check:'Check',spark:'Sparkles',copy:'Copy',folder:'FolderOpen',search:'Search',help:'CircleHelp',x:'X',delete:'Trash2',refresh:'RefreshCw',shield:'ShieldCheck',image:'Image'};
const symbols = Object.entries(mapping).map(([id,name])=>{
  if(!lucide[name]) throw new Error(`Missing Lucide icon: ${name}`);
  const nodes=lucide[name].map(([tag,attrs])=>`<${tag} ${Object.entries(attrs).map(([k,v])=>`${k}="${String(v).replace(/"/g,'&quot;')}"`).join(' ')}/>`).join('');
  return ` <symbol id="i-${id}" viewBox="0 0 24 24">${nodes}</symbol>`;
}).join('\n');
const filename=path.join(__dirname,'index.html');
const html=fs.readFileSync(filename,'utf8').replace(/(<svg class="sprite"[^>]*>)[\s\S]*?(<\/svg>)/,`$1\n${symbols}\n$2`);
fs.writeFileSync(filename,html);
console.log(`Generated ${Object.keys(mapping).length} Lucide icon symbols.`);
