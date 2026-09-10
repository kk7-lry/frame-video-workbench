// Exercise application state with mocked DOM/network, without launching a browser.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const test=require('node:test');
const source=fs.readFileSync(path.join(__dirname,'..','app.js'),'utf8');
const id='a'.repeat(32);
const task=(updates={})=>({id,kind:'link',status:'已就绪',mediaType:'video',mediaUrl:'/media/'+id+'.mp4',
  caption:{text:''},speech:{status:'待提取',text:''},ocr:{status:'待提取',text:''},...updates});

function harness(){
  const elements=new Map(),calls=[];
  const element=selector=>{
    if(!elements.has(selector))elements.set(selector,{value:'',checked:false,disabled:false,hidden:false,dataset:{},
      classList:{add(){},remove(){},toggle(){}},addEventListener(){},setAttribute(){},focus(){}});
    return elements.get(selector);
  };
  const storage=()=>{const values=new Map();return {getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key)};};
  let handler=async path=>{throw Error('Unexpected request: '+path);};
  const context=vm.createContext({document:{querySelector:element,querySelectorAll:()=>[],addEventListener(){}},
    window:{addEventListener(){}},localStorage:storage(),sessionStorage:storage(),setTimeout,clearTimeout,
    request:(...args)=>handler(...args),capture:async(keys,taskId)=>{calls.push({keys:Array.from(keys),id:taskId});}});
  assert.match(source,/boot\(\);\s*$/);
  vm.runInContext(source.replace(/boot\(\);\s*$/,'')+`
    api=request; extract=capture; renderLists=()=>{}; renderMedia=()=>{}; renderField=()=>{};
    toast=()=>{}; setOperation=()=>{}; selectTask=id=>{state.current=id;};
    globalThis.app={state,refresh,queueAutoExtract,runAutoExtract,retryCurrent,taskAction,parse,activateCreated};
  `,context);
  context.app.state.health={checked:true,speech:true,ocr:true};
  element('#autoExtract').checked=true;
  return {app:context.app,element,calls,session:context.sessionStorage,setRequest:fn=>{handler=fn;}};
}

test('automatic extraction waits for download completion',async()=>{
  const h=harness();h.app.state.tasks=[task({status:'下载中',mediaUrl:null})];
  h.app.queueAutoExtract(id);
  assert.equal(h.calls.length,0);assert(h.app.state.autoPending.has(id));
  h.app.state.tasks=[task()];h.app.runAutoExtract();
  assert.deepEqual(h.calls,[{keys:['speech','ocr'],id}]);
  assert(!h.app.state.autoPending.has(id));
});

test('existing recognition finishes before automatic extraction resumes',()=>{
  const h=harness();h.app.state.tasks=[task({speech:{status:'识别中',text:''}})];
  h.app.queueAutoExtract(id);
  assert.equal(h.calls.length,0);assert(h.app.state.autoPending.has(id));
  h.app.state.tasks=[task({speech:{status:'已完成',text:''}})];h.app.runAutoExtract();
  assert.deepEqual(h.calls,[{keys:['ocr'],id}]);
});

test('automatic extraction preserves edited empty text and unsaved drafts',()=>{
  const h=harness();h.app.state.tasks=[task({speech:{status:'已编辑',text:'',edited:true}})];
  h.app.state.dirty.set(`frame-draft-${id}-ocr`,'draft');
  h.app.queueAutoExtract(id);
  assert.equal(h.calls.length,0);assert(!h.app.state.autoPending.has(id));
});

test('images request OCR only and unchecked automatic extraction stays off',()=>{
  const h=harness();h.app.state.tasks=[task({mediaType:'image'})];
  h.element('#autoExtract').checked=false;h.app.queueAutoExtract(id);
  assert.equal(h.calls.length,0);assert.equal(h.app.state.autoPending.size,0);
  h.element('#autoExtract').checked=true;h.app.queueAutoExtract(id);
  assert.deepEqual(h.calls,[{keys:['ocr'],id}]);
});

for(const entry of ['current','list'])test(`${entry} retry waits for stale polling before preserving automatic work`,async()=>{
  const h=harness(),failed=task({status:'失败',mediaUrl:null}),queued=task({status:'排队中',mediaUrl:null});
  h.app.state.tasks=[failed];h.app.state.current=id;
  let resolveOld,reads=0;
  const old=new Promise(resolve=>{resolveOld=resolve;});
  h.setRequest(async path=>{
    if(path.endsWith('/retry'))return {ok:true};
    assert.equal(path,'/api/tasks');
    return ++reads===1?old:{tasks:[queued]};
  });
  const poll=h.app.refresh();
  const retry=entry==='current'?h.app.retryCurrent():h.app.taskAction({target:{closest:()=>({dataset:{action:'retry'},closest:()=>({dataset:{id}})})}});
  await Promise.resolve();await Promise.resolve();
  resolveOld({tasks:[failed]});await Promise.all([poll,retry]);
  assert.equal(reads,2);assert.equal(h.app.state.tasks[0].status,'排队中');
  assert(h.app.state.autoPending.has(id));assert.equal(h.calls.length,0);
  h.setRequest(async()=>({tasks:[task()]}));await h.app.refresh();
  assert.deepEqual(h.calls,[{keys:['speech','ocr'],id}]);
  assert.equal(h.element('#retryCurrentBtn').disabled,false);
});

test('pasting a ready existing task honors automatic extraction',async()=>{
  const h=harness();h.element('#urlInput').value='https://cdn.example.com/video.mp4';
  h.setRequest(async path=>path==='/api/parse'?{id,reused:true}:task());
  await h.app.parse();
  assert.equal(h.app.state.current,id);
  assert.deepEqual(h.calls,[{keys:['speech','ocr'],id}]);
  assert.equal(h.element('#parseBtn').disabled,false);
});

test('activating a created task waits for an older list response',async()=>{
  const h=harness();let resolveOld;
  const old=new Promise(resolve=>{resolveOld=resolve;});
  h.setRequest(async path=>path==='/api/tasks'?old:task());
  const poll=h.app.refresh(),activation=h.app.activateCreated(id);
  resolveOld({tasks:[]});await Promise.all([poll,activation]);
  assert.equal(h.app.state.current,id);assert.equal(h.app.state.tasks[0].id,id);
});
