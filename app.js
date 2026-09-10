'use strict';
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const state = {tasks:[], current:null, field:'caption', health:null, view:'workspace', extracting:new Set(), dirty:new Map(), saves:new Map(), deleting:null, online:false, autoPending:new Set(), cookiePlatform:null};
const busyStates = ['排队中','识别中','解析中','下载中','校验中'];
const icon = name => `<svg aria-hidden="true"><use href="#i-${name}"/></svg>`;
const escape = value => String(value??'').replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const getTask = id => state.tasks.find(t=>t.id===id);
const currentTask = () => getTask(state.current);
const draftKey = (id,field) => `frame-draft-${id}-${field}`;
let toastTimer, saveTimer, refreshRunning=null, lastList='', inputBusy=false;
function toast(message){$('#toast').textContent=message;$('#toast').classList.add('show');clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('#toast').classList.remove('show'),3600);}
function inputError(message){$('#inputError').textContent=message;$('#inputError').hidden=!message;}
function formatBytes(bytes){return bytes>=1024*1024?(bytes/1024/1024).toFixed(1)+' MB':Math.max(1,Math.round(bytes/1024))+' KB';}
function formatTime(seconds){return `${Math.floor(seconds/60).toString().padStart(2,'0')}:${Math.floor(seconds%60).toString().padStart(2,'0')}`;}
function timeLabel(created){const d=new Date(created*1000);return d.toLocaleDateString('zh-CN',{month:'2-digit',day:'2-digit'})+' '+d.toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'});}
function busy(task){return busyStates.includes(task.status)||['speech','ocr'].some(k=>busyStates.includes(task[k]?.status))||state.extracting.has(task.id);}
function statusOf(task){if(busyStates.includes(task.status))return task.status; if(['speech','ocr'].some(k=>busyStates.includes(task[k]?.status))||state.extracting.has(task.id))return '提取中';if(task.status==='失败')return '获取未完成';return task.status;}
async function api(path,options={}){
 const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),30000);
 try{const response=await fetch(path,{...options,signal:controller.signal});const type=response.headers.get('content-type')||'';
  if(!type.includes('application/json'))throw Error('服务正在启动或暂不可用，请稍后重试。');
  const data=await response.json();if(!response.ok)throw Error(data.error||'请求失败，请重试');return data;
 }catch(e){if(e.name==='AbortError')throw Error('请求超时，请稍后重试');if(e instanceof TypeError)throw Error('无法连接服务，请稍后刷新页面');throw e;}finally{clearTimeout(timer);}
}
const post=(path,data)=>api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data||{})});
async function activateCreated(id){if(refreshRunning)await refreshRunning;const task=await api('/api/tasks/'+id);const i=state.tasks.findIndex(t=>t.id===id);if(i<0)state.tasks.unshift(task);else state.tasks[i]=task;renderLists();selectTask(id);}
function setView(view){state.view=view;$('#workspaceView').hidden=view!=='workspace';$('#historyView').hidden=view!=='history';$('#pageName').textContent=view==='history'?'素材库':'工作台';$$('.nav-item[data-view]').forEach(b=>b.classList.toggle('active',b.dataset.view===view));if(view==='history')renderLists(true);window.scrollTo({top:0,behavior:'smooth'});}
function setInputMode(mode){$('#linkPanel').hidden=mode!=='link';$('#uploadPanel').hidden=mode!=='upload';for(const name of ['link','upload']){$('#'+name+'Tab').classList.toggle('active',mode===name);$('#'+name+'Tab').setAttribute('aria-selected',String(mode===name));}inputError('');}
function emptyList(filtered=false){return `<div class="empty-list">${icon(filtered?'search':'folder')}<div><h3>${filtered?'没有匹配的素材':'暂无素材'}</h3></div></div>`;}
function shortError(task){return ({cookies_required:'公开页面未返回视频',network_access_denied:'后台联网受限',network_timeout:'平台请求超时',platform_restricted:'平台暂时限制访问',download_unavailable:'未定位到可用视频'})[task.errorCode]||'获取未完成';}
function rowHTML(t){const status=statusOf(t), failed=t.status==='失败', pending=busy(t);const thumbnail=t.mediaType==='image'&&t.mediaUrl?`<img src="${escape(t.mediaUrl)}" alt="" loading="lazy">`:icon(t.mediaType==='image'?'image':'play');return `<div class="task-row" data-id="${t.id}"><span class="task-thumb ${t.mediaType==='image'?'image':''}">${thumbnail}</span><button class="task-summary" data-action="open"><strong>${escape(t.title==='正在读取视频'?'抖音链接任务':t.title)}</strong><span>${escape(t.platform)} · ${timeLabel(t.created)}${t.size?' · '+formatBytes(t.size):''} · ${escape(status)}</span>${t.error?`<span class="row-error">${escape(shortError(t))}</span>`:''}</button><span class="badge ${failed?'failed':pending?'pending':t.status==='文件已过期'?'expired':''}">${escape(status)}</span><span class="row-actions">${failed&&t.kind==='link'?`<button class="icon-button" data-action="retry" title="重新获取" aria-label="重新获取">${icon('refresh')}</button>`:''}<button class="icon-button" data-action="delete" ${pending?'disabled':''} title="删除素材" aria-label="删除素材">${icon('delete')}</button></span></div>`;}
function renderLists(force=false){const filter=$('#historyFilter').value;const key=JSON.stringify(state.tasks)+[...state.extracting].join()+$('#historySearch').value+filter;if(key===lastList&&!force)return;lastList=key;$('#navCount').textContent=state.tasks.length;$('#recentCount').textContent=`${state.tasks.length} 项`;$('#recentList').innerHTML=state.tasks.length?state.tasks.slice(0,4).map(rowHTML).join(''):emptyList();const query=$('#historySearch').value.trim().toLowerCase();const found=state.tasks.filter(t=>[t.title,t.platform,...['caption','speech','ocr'].map(k=>t[k]?.text)].join(' ').toLowerCase().includes(query)&&(filter==='all'||filter==='ready'&&!!t.mediaUrl||filter==='busy'&&busy(t)||filter==='failed'&&t.status==='失败'));$('#historyList').innerHTML=found.length?found.map(rowHTML).join(''):emptyList(!!query||filter!=='all');}
function recoverDraft(task){for(const field of ['caption','speech','ocr']){const key=draftKey(task.id,field);try{const draft=localStorage.getItem(key);if(draft!==null&&!state.dirty.has(key)){state.dirty.set(key,draft);}}catch{}}}
function selectTask(id,scroll=true){const task=getTask(id);if(!task)return;saveDrafts();state.current=id;state.field=task.mediaType==='image'?'ocr':task.kind==='file'?'speech':'caption';recoverDraft(task);setView('workspace');$('#resultSection').hidden=false;$('#features').hidden=true;renderMedia(true);renderField(true);if(scroll)requestAnimationFrame(()=>$('#resultSection').scrollIntoView({behavior:'smooth',block:'start'}));}
function renderMedia(force=false){const task=currentTask();if(!task)return;const preview=$('#mediaPreview');const signature=[task.id,task.mediaUrl,task.mediaUrl?'':task.status,task.mediaUrl?'':task.progress,task.errorCode].join('|');
 preview.classList.toggle('empty',!task.mediaUrl);
 if(force||preview.dataset.signature!==signature){preview.dataset.signature=signature;preview.replaceChildren();if(task.mediaUrl){const media=document.createElement(task.mediaType==='image'?'img':'video');media.src=task.mediaUrl;if(task.mediaType==='image')media.alt=task.title;else{media.controls=true;media.preload='metadata';media.playsInline=true;media.addEventListener('error',()=>toast('浏览器无法预览这种编码，可以下载源文件。'));}preview.append(media);}else{const failed=task.status==='失败';preview.innerHTML=`<div class="media-placeholder">${icon(failed?'link':task.status==='文件已过期'?'clock':'download')}<strong>${escape(failed?'暂未获取视频':statusOf(task))}${task.status==='下载中'&&Number.isFinite(task.progress)?' '+task.progress+'%':''}</strong>${failed&&task.error?`<span>${escape(shortError(task))}</span>`:''}${task.status==='下载中'?`<div class="progress-track"><span style="width:${Number.isFinite(task.progress)?Math.max(0,Math.min(100,task.progress)):12}%"></span></div>`:''}</div>`;}}
 $('#mediaTitle').textContent=task.title==='正在读取视频'?'抖音链接任务':task.title;$('#mediaTitle').title=task.title;$('#mediaMeta').textContent=[task.platform,task.author,task.duration?formatTime(task.duration):'',task.height?task.height+'p':'',task.size?formatBytes(task.size):''].filter(Boolean).join(' · ');
 const sourceNote=task.kind==='file'?'原文件保存 · 未修改画面':task.watermark||'源文件状态未核验';
 $('#sourceNote').textContent=sourceNote+(task.validation?.state==='decoded'?' · 首尾画面可读取':task.validation?.state==='unavailable'?' · 播放校验未完成':'');
 const download=$('#downloadBtn');download.setAttribute('aria-disabled',String(!task.mediaUrl));if(task.mediaUrl){download.href=task.mediaUrl+'?download=1';download.download='';}else download.removeAttribute('href');
 $('#sourceBtn').hidden=!task.source;if(task.source)$('#sourceBtn').href=task.source;
 $('#extractBtn').disabled=!task.mediaUrl||state.extracting.has(task.id)||busyStates.includes(task.speech?.status)||busyStates.includes(task.ocr?.status);
 $('#extractBtn').innerHTML=icon('spark')+(state.extracting.has(task.id)?'正在提取…':'一键提取');
 $('#retryCurrentBtn').hidden=task.status!=='失败'||task.kind!=='link';
 const needsNetworkHelp=task.errorCode==='network_access_denied';
 $('#connectPlatformBtn').hidden=!needsNetworkHelp;
 $('#connectPlatformBtn').textContent='查看恢复方法';
}
function fieldHint(task,key){const value=task[key]||{};if(value.error)return value.error;if(key==='caption')return task.kind==='file'?'本地文件无发布文案':task.caption?.text?'平台原文':'';if(key==='speech'){if(task.mediaType==='image')return '图片无音轨';if(!state.health?.speech)return '语音识别服务未就绪';return value.text?'本地识别 · 请核对人名与专有名词':'';}return value.text?'本地识别 · 按画面时间排列':'';}
function renderField(force=false){const task=currentTask();if(!task)return;const field=state.field,value=task[field]||{},key=draftKey(task.id,field);$$('.result-tab').forEach(b=>{b.classList.toggle('active',b.dataset.field===field);b.setAttribute('aria-selected',String(b.dataset.field===field));});const text=state.dirty.has(key)?state.dirty.get(key):(value.text||'');if(force||document.activeElement!==$('#textResult')&&!state.dirty.has(key))$('#textResult').value=text;
 $('#fieldStatus').textContent=value.status||'待提取';$('#wordCount').textContent=`${$('#textResult').value.length} 字`;$('#fieldHint').textContent=fieldHint(task,field);$('#saveStatus').textContent=state.dirty.has(key)?'有未保存的修改':value.edited?'修改已保存到本机':'修改后自动保存';$('#textResult').disabled=busyStates.includes(value.status)||state.extracting.has(task.id);$('#textResult').placeholder=busyStates.includes(value.status)?'正在本机识别，请稍候…':value.status==='已完成'?'未识别到清晰文字，你也可以手动补充。':'提取完成后，文字会出现在这里。';
 $('#singleExtractBtn').hidden=field==='caption';$('#singleExtractBtn').disabled=!task.mediaUrl||!state.health?.[field]||state.extracting.has(task.id)||busyStates.includes(value.status)||(field==='speech'&&task.mediaType==='image');
}
function setOperation(message){$('#operationMessage').hidden=!message;$('#operationMessage').textContent=message;$('#operationMessage').classList.toggle('failed',currentTask()?.status==='失败');}
function refresh(afterChange=false){
 if(refreshRunning)return afterChange?refreshRunning.then(()=>refresh()):refreshRunning;
 refreshRunning=(async()=>{try{const before=currentTask()?.status;const data=await api('/api/tasks');state.tasks=data.tasks;state.online=true;$('#connection').classList.add('online');$('#connection').innerHTML='<i></i>本机已连接';renderLists();if(state.current&&getTask(state.current)){renderMedia();renderField();if(currentTask().error)setOperation(currentTask().error);else if(busyStates.includes(currentTask().status))setOperation(statusOf(currentTask())+'…');else if(busyStates.includes(before))setOperation('');}else if(state.current){state.current=null;$('#resultSection').hidden=true;$('#features').hidden=false;}runAutoExtract();}catch{$('#connection').classList.remove('online');$('#connection').innerHTML='<i></i>服务未连接';state.online=false;}})().finally(()=>{refreshRunning=null;});
 return refreshRunning;
}
async function health(){try{state.health=await api('/api/health');renderDeployment();renderServices();renderNetwork();if(currentTask())renderField();}catch{state.health=null;renderServices();renderNetwork();}}
function renderDeployment(){
 const publicMode=state.health?.public;
 $('#storageNote').textContent=publicMode?'访客独立空间 · 文件临时保存':'本地存储 · 文件保留 24 小时';
 $('#retentionNote').hidden=!publicMode;
 $('#platformCredentials').hidden=!!publicMode;
 $('#networkHelpBtn').hidden=!!publicMode;
 $('#quickSettingsBtn').innerHTML=icon('settings')+'设置';
 $('#editionLabel').textContent=publicMode?'访客工作空间':'个人工作空间';
 $('#versionLabel').textContent=(publicMode?'公开版':'本机版')+' 0.3.0 · 仅保存有权使用的内容';
 $('#uploadLimit').textContent='MP4、MOV、WEBM · PNG、JPG · 最大 '+Math.round((state.health?.maxUploadBytes||500*1024*1024)/1024/1024)+' MB';
 if(state.online)$('#connection').innerHTML='<i></i>'+(publicMode?'服务已连接':'本机已连接');
}
function renderNetwork(){const net=state.health?.network;const outdated=state.health&&state.health.version!=='0.3.0';const blocked=net&&['access_denied','unreachable'].includes(net.state);$('#networkAlert').hidden=!blocked&&!outdated;$('#networkCheckBtn').hidden=!!outdated;$('#networkCheckBtn').disabled=net?.state==='checking';if(outdated){$('#networkAlertTitle').textContent='后台版本与页面不一致';$('#networkAlertMessage').textContent=state.health.public?'服务正在更新，请稍后刷新页面。':'请运行 Restart-Frame.cmd 加载 0.3.0，已有素材会保留。';}else if(blocked){$('#networkAlertTitle').textContent='后台暂时无法连接平台';$('#networkAlertMessage').textContent=state.health.public?'平台连接暂不可用，请稍后重试。':net.message;}}
async function checkConnection(){const button=$('#networkCheckBtn');button.disabled=true;try{await post('/api/network-check');await health();toast('已开始检测，结果会自动更新');}catch(e){toast(e.message);}finally{button.disabled=state.health?.network?.state==='checking';}}
function renderServices(){const h=state.health,net=h?.network;const networkLabel=net?.state==='connected'?'HTTPS 可达':net?.state==='access_denied'?'联网被拒绝':net?.state==='checking'?'检查中':net?.state==='unreachable'?'连接失败':'未检查';const rows=[['本机任务服务',state.online?'已连接':'未连接','上传、预览、文字保存与导出',state.online],['平台解析器',h?.download?'已安装':'未安装','已安装不代表具体视频一定可下载',h?.download],['平台连接',networkLabel,net?.message||'尚未检查后台服务连接',net?.state==='connected'],['画面文字识别',h?.ocr?'已就绪':h&&!h.checked?'检查中':'未就绪','Windows 中文 OCR · 本地运行',h?.ocr],['语音识别',h?.speech?'已就绪':h&&!h.checked?'检查中':'未就绪',h?.speechEngine||'检查本机组件',h?.speech]];$('#serviceList').innerHTML=rows.map(([name,label,detail,ready])=>`<div class="service-row"><span><strong>${escape(name)}</strong><small>${escape(detail)}</small></span><span class="badge ${ready?'':name==='平台连接'&&net?.state==='access_denied'?'failed':'expired'}">${escape(label)}</span></div>`).join('');}
function queueAutoExtract(id){if($('#autoExtract').checked){state.autoPending.add(id);persistAuto();runAutoExtract();}}
async function parse(){if(inputBusy)return;const input=$('#urlInput').value.trim();if(!input){inputError('请粘贴分享内容或视频链接');$('#urlInput').focus();return;}inputBusy=true;$('#parseBtn').disabled=true;$('#parseBtn').textContent='正在提交…';inputError('');try{const data=await post('/api/parse',{url:input});await activateCreated(data.id);queueAutoExtract(data.id);setOperation(currentTask().error||(!currentTask().mediaUrl?'正在获取视频…':''));if(data.reused)toast('已打开已有素材');}catch(e){inputError(e.message);}finally{inputBusy=false;$('#parseBtn').disabled=false;$('#parseBtn').innerHTML='获取视频'+icon('arrow');}}
function uploadFile(file){return new Promise((resolve,reject)=>{const xhr=new XMLHttpRequest();xhr.open('POST','/api/upload');xhr.setRequestHeader('Content-Type','application/octet-stream');xhr.setRequestHeader('X-Filename',encodeURIComponent(file.name));xhr.timeout=300000;xhr.upload.onprogress=e=>{if(e.lengthComputable)$('#uploadProgress span').style.width=(e.loaded/e.total*100)+'%';};xhr.onload=()=>{try{const d=JSON.parse(xhr.responseText);if(xhr.status>=400)reject(Error(d.error||'上传失败'));else resolve(d);}catch{reject(Error('本地服务未正确响应，请重启网站。'));}};xhr.onerror=()=>reject(Error('连接中断，请重试'));xhr.ontimeout=()=>reject(Error('上传超时，请重试'));xhr.send(file);});}
async function handleFile(file,autoOcr=false){if(!file||inputBusy)return;if(!/\.(mp4|mov|webm|m4v|png|jpe?g)$/i.test(file.name)){inputError('请选择 MP4、MOV、WEBM 视频或 PNG、JPG 图片。');return;}const limit=state.health?.maxUploadBytes||500*1024*1024;if(!file.size||file.size>limit){inputError('文件不能为空，且不能超过 '+Math.round(limit/1024/1024)+' MB。');return;}inputBusy=true;$('#uploadProgress').hidden=false;$('#uploadProgress span').style.width='0%';inputError('');try{const d=await uploadFile(file);await activateCreated(d.id);setOperation(state.health?.public?'文件已上传，请及时下载保存。':'文件已保存在本机。');if(autoOcr)await extract(['ocr']);}catch(e){inputError(e.message);}finally{inputBusy=false;$('#uploadProgress').hidden=true;$('#fileInput').value='';}}
async function example(){if(inputBusy)return;$('#sampleBtn').disabled=true;try{const image=$('#sampleBtn img');await image.decode();const canvas=document.createElement('canvas');canvas.width=image.naturalWidth;canvas.height=image.naturalHeight;canvas.getContext('2d').drawImage(image,0,0);const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/png'));if(!blob)throw Error('无法读取样本');await handleFile(new File([blob],'中文文字样本.png',{type:'image/png'}),true);}catch(e){toast(e.message);}finally{$('#sampleBtn').disabled=false;}}
function mediaReady(media,event,timeout=15000){return new Promise((resolve,reject)=>{const done=()=>{clean();resolve();};const fail=()=>{clean();reject(Error('浏览器无法读取这个视频编码。请换成 MP4（H.264）或 WEBM 文件。'));};const timer=setTimeout(fail,timeout);const clean=()=>{clearTimeout(timer);media.removeEventListener(event,done);media.removeEventListener('error',fail);};media.addEventListener(event,done,{once:true});media.addEventListener('error',fail,{once:true});});}
async function imageFrame(task){const image=new Image();const loaded=mediaReady(image,'load');image.src=task.mediaUrl;await loaded;const c=document.createElement('canvas');const scale=Math.min(1,1600/image.naturalWidth,1600/image.naturalHeight);c.width=Math.round(image.naturalWidth*scale);c.height=Math.round(image.naturalHeight*scale);c.getContext('2d').drawImage(image,0,0,c.width,c.height);return [{time:0,data:c.toDataURL('image/png')}];}
async function videoFrames(task){const v=document.createElement('video');v.preload='auto';v.muted=true;v.playsInline=true;const ready=mediaReady(v,'loadeddata');v.src=task.mediaUrl;await ready;const duration=v.duration;if(!Number.isFinite(duration)||duration<=0){v.removeAttribute('src');v.load();throw Error('无法读取视频时长，请使用标准 MP4 或 WEBM 文件。');}if(duration>600){v.removeAttribute('src');v.load();throw Error('本版文字识别支持 10 分钟以内的视频。');}const canvas=document.createElement('canvas'),g=canvas.getContext('2d');const subtitle=($('#ocrMode').value==='subtitle');const y=subtitle?Math.round(v.videoHeight*.55):0;const sourceHeight=v.videoHeight-y;const scale=Math.min(1,1280/v.videoWidth,1280/sourceHeight);canvas.width=Math.round(v.videoWidth*scale);canvas.height=Math.round(sourceHeight*scale);const frames=[];const step=Math.max(1,duration/149);let size=0;try{for(let t=0;t<duration;t+=step){const target=Math.min(t,duration-.05);if(Math.abs(v.currentTime-target)>.025){const done=mediaReady(v,'seeked',12000);v.currentTime=target;await done;}g.drawImage(v,0,y,v.videoWidth,sourceHeight,0,0,canvas.width,canvas.height);const data=canvas.toDataURL('image/png');size+=data.length;if(size>35*1024*1024)throw Error('画面数据较大，请在设置中选择“底部字幕”，或使用更短的视频。');frames.push({time:t,data});setOperation(`正在准备画面 ${frames.length}/${Math.ceil(duration/step)}，随后在本机识别…`);}}finally{v.removeAttribute('src');v.load();}return frames;}
function wavFromAudio(buffer){const pcm=buffer.getChannelData(0),bytes=new ArrayBuffer(44+pcm.length*2),v=new DataView(bytes);function str(o,s){for(let i=0;i<s.length;i++)v.setUint8(o+i,s.charCodeAt(i));}str(0,'RIFF');v.setUint32(4,36+pcm.length*2,true);str(8,'WAVE');str(12,'fmt ');v.setUint32(16,16,true);v.setUint16(20,1,true);v.setUint16(22,1,true);v.setUint32(24,16000,true);v.setUint32(28,32000,true);v.setUint16(32,2,true);v.setUint16(34,16,true);str(36,'data');v.setUint32(40,pcm.length*2,true);for(let i=0;i<pcm.length;i++){let s=Math.max(-1,Math.min(1,pcm[i]));v.setInt16(44+i*2,s<0?s*32768:s*32767,true);}return new Blob([bytes],{type:'audio/wav'});}
async function audioFile(task){if(task.size>150*1024*1024)throw Error('这段视频较大，浏览器语音提取限制为 150 MB，请使用较短的视频。');const response=await fetch(task.mediaUrl);if(!response.ok)throw Error('视频文件已过期');const data=await response.arrayBuffer();const context=new AudioContext();let decoded;try{decoded=await context.decodeAudioData(data);}catch{throw Error('未找到可解码的音轨；画面文字仍然可以提取。');}finally{await context.close();}if(decoded.duration>600)throw Error('语音识别支持 10 分钟以内的视频。');const offline=new OfflineAudioContext(1,Math.ceil(decoded.duration*16000),16000);const source=offline.createBufferSource();source.buffer=decoded;source.connect(offline.destination);source.start();return wavFromAudio(await offline.startRendering());}
async function waitForResult(id,key){const start=Date.now();while(Date.now()-start<950000){await new Promise(r=>setTimeout(r,1100));const t=await api('/api/tasks/'+id);const i=state.tasks.findIndex(x=>x.id===id);if(i>=0)state.tasks[i]=t;renderLists();if(state.current===id){renderField();renderMedia();}if(t[key].status==='已完成')return t[key];if(t[key].status==='失败')throw Error(t[key].error||'识别失败');}throw Error('任务仍在处理中，你可以在最近任务中查看结果。');}
async function extract(keys,taskId=state.current){
 const task=getTask(taskId);if(!task?.mediaUrl||state.extracting.has(task.id))return;
 let selected=keys||['speech','ocr'];if(task.mediaType==='image')selected=selected.filter(k=>k==='ocr');
 const requested=selected.filter(k=>state.health?.[k]),skipped=selected.filter(k=>!state.health?.[k]);
 if(!requested.length){toast('本机识别组件尚未就绪');return;}
 if(requested.some(k=>task[k]?.text)&&!window.confirm('重新提取会覆盖对应的文字结果，继续吗？'))return;
 state.extracting.add(task.id);const report=message=>{if(state.current===task.id)setOperation(message);};
 report('正在准备素材…');renderMedia();renderField();const messages=[];
 try{
  await saveDrafts();
  for(const key of requested){
   try{
    if(state.current===task.id){state.field=key;renderField(true);}
    if(key==='ocr'){
     const frames=task.mediaType==='image'?await imageFrame(task):await videoFrames(task);
     await post(`/api/tasks/${task.id}/ocr`,{frames});
    }else{
     report('正在准备音轨…');const wav=await audioFile(task);
     await api(`/api/tasks/${task.id}/speech`,{method:'POST',headers:{'Content-Type':'audio/wav'},body:wav});
    }
    report(key==='ocr'?'正在识别画面文字…':'正在识别口播…');
    await waitForResult(task.id,key);messages.push((key==='ocr'?'画面文字':'口播文字')+'已完成');
   }catch(e){messages.push((key==='ocr'?'画面文字':'口播文字')+'：'+e.message);}
  }
 }finally{
  state.extracting.delete(task.id);
  if(skipped.length)messages.push(skipped.map(k=>k==='ocr'?'画面识别':'语音识别').join('、')+'未就绪');
  report(messages.join('；'));await refresh();renderMedia();renderField(true);
 }
}
async function saveOne(key,text){
 if(state.saves.has(key))return state.saves.get(key);
 const match=key.match(/^frame-draft-([a-f0-9]{32})-(caption|speech|ocr)$/);if(!match)return;
 const [,id,field]=match;
 const promise=(async()=>{
  let succeeded=false;
  try{
   const task=await post(`/api/tasks/${id}/save`,{field,text});succeeded=true;
   const index=state.tasks.findIndex(x=>x.id===id);if(index>=0)state.tasks[index]=task;
   if(state.dirty.get(key)===text){state.dirty.delete(key);try{localStorage.removeItem(key);}catch{}}
   if(state.current===id&&state.field===field)$('#saveStatus').textContent=state.dirty.has(key)?'正在保存…':'修改已保存到本机';
  }catch{if(state.current===id)$('#saveStatus').textContent='暂未同步，草稿保留在浏览器';}
  finally{
   state.saves.delete(key);
   if(succeeded&&state.dirty.has(key))setTimeout(()=>saveOne(key,state.dirty.get(key)),50);
  }
 })();state.saves.set(key,promise);return promise;
}
async function saveDrafts(){const entries=[...state.dirty.entries()];await Promise.all(entries.map(([k,v])=>saveOne(k,v)));}
function editText(){const t=currentTask();if(!t)return;const key=draftKey(t.id,state.field),text=$('#textResult').value;state.dirty.set(key,text);try{localStorage.setItem(key,text);}catch{}$('#wordCount').textContent=text.length+' 字';$('#saveStatus').textContent='正在保存…';clearTimeout(saveTimer);saveTimer=setTimeout(saveDrafts,650);}
async function copyText(){const text=$('#textResult').value;if(!text.trim()){toast('还没有可复制的文字');return;}try{await navigator.clipboard.writeText(text);toast('文字已复制');}catch{const ta=$('#textResult');ta.select();if(document.execCommand('copy'))toast('文字已复制');else toast('已选中文字，请按 Ctrl+C 复制');}}
function exportText(){const t=currentTask(),text=$('#textResult').value;if(!text.trim()){toast('还没有可导出的文字');return;}const name={caption:'发布文案',speech:'口播文字',ocr:'画面文字'}[state.field];const blob=new Blob(['\ufeff'+text],{type:'text/plain;charset=utf-8'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=(t?.title||'拾帧').replace(/[<>:"/\\|?*]/g,'_').slice(0,70)+'-'+name+'.txt';document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),10000);}
async function taskAction(event){const action=event.target.closest('[data-action]');if(!action)return;const id=action.closest('[data-id]').dataset.id,task=getTask(id);if(action.dataset.action==='open'){selectTask(id);setOperation(task.error||'');}if(action.dataset.action==='retry'){try{const result=await post('/api/tasks/'+id+'/retry');await refresh(true);queueAutoExtract(id);toast(result.reused?'任务已在处理或已就绪':'已重新加入解析队列');}catch(e){toast(e.message);}}if(action.dataset.action==='delete'){state.deleting=id;$('#confirmText').textContent=`删除“${task.title}”及其本机文件和文字结果？`;$('#confirmDialog').showModal();}}
async function deleteTask(){const id=state.deleting;if(!id)return;$('#confirmDelete').disabled=true;try{await post('/api/tasks/'+id+'/delete');for(const field of ['caption','speech','ocr']){const key=draftKey(id,field);state.dirty.delete(key);try{localStorage.removeItem(key);}catch{}}state.autoPending.delete(id);persistAuto();$('#confirmDialog').close();await refresh(true);toast('任务已删除');}catch(e){toast(e.message);}finally{$('#confirmDelete').disabled=false;}}

const platformNames={douyin:'抖音',xiaohongshu:'小红书',kuaishou:'快手',tiktok:'TikTok',bilibili:'B站'};
async function loadPlatforms(){
 try{const data=await api('/api/platforms');$('#platformList').innerHTML=Object.entries(platformNames).map(([key,name])=>`<div class="platform-auth-row ${data[key]?.configured?'connected':''}" data-platform="${key}"><div><strong>${name}</strong><small>${data[key]?.configured?'登录文件已导入':key==='kuaishou'?'公开分享页 · 试用':'公开内容优先'}</small></div><button class="icon-button" data-auth="import" title="导入${name}登录文件" aria-label="导入${name}登录文件">${icon('upload')}</button>${data[key]?.configured?`<button class="icon-button" data-auth="clear" title="移除${name}登录文件" aria-label="移除${name}登录文件">${icon('x')}</button>`:''}</div>`).join('');}
 catch{$('#platformFeedback').textContent='当前后台需要更新，请运行 Restart-Frame.cmd 后重试。';}
}
async function openSettings(){await health();$('#platformFeedback').textContent='';$('#settingsDialog').showModal();if(!state.health?.public)await loadPlatforms();}
async function platformAction(event){const button=event.target.closest('[data-auth]');if(!button)return;const key=button.closest('[data-platform]').dataset.platform;if(button.dataset.auth==='import'){state.cookiePlatform=key;$('#cookieFileInput').click();}else{button.disabled=true;try{await post(`/api/platforms/${key}/clear`);await loadPlatforms();$('#platformFeedback').textContent=platformNames[key]+'登录文件已移除';}catch(e){$('#platformFeedback').textContent=e.message;}finally{button.disabled=false;}}}
async function importCookies(event){const file=event.target.files[0],key=state.cookiePlatform;if(!file||!key)return;event.target.value='';if(file.size>512*1024){$('#platformFeedback').textContent='文件不能超过 512KB';return;}try{await api(`/api/platforms/${key}/import`,{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:file});await loadPlatforms();$('#platformFeedback').textContent=platformNames[key]+'登录文件已导入，可直接重试原任务';}catch(e){$('#platformFeedback').textContent=e.message;}}
function detectPlatform(){const match=$('#urlInput').value.match(/https?:\/\/[^\s<>"\u3000]+/i);let name='新建素材';if(match){try{const url=new URL(match[0]),host=url.hostname;const domains={'douyin.com':'抖音','iesdouyin.com':'抖音','xiaohongshu.com':'小红书','xhslink.com':'小红书','kuaishou.com':'快手','gifshow.com':'快手','tiktok.com':'TikTok','bilibili.com':'B站','b23.tv':'B站'};name=Object.entries(domains).find(([d])=>host===d||host.endsWith('.'+d))?.[1]||(/\.(mp4|mov|m4v|webm)$/i.test(url.pathname)?'视频直链':'网页视频');}catch{name='检查链接';}}$('#detectedPlatform').textContent=name;}
function persistAuto(){try{sessionStorage.setItem('frame-auto-pending',JSON.stringify([...state.autoPending]));}catch{}}
function runAutoExtract(){
 if(state.extracting.size||!state.health?.checked)return;
 for(const id of state.autoPending){const task=getTask(id);if(!task||task.status==='失败'){state.autoPending.delete(id);persistAuto();continue;}if(!task.mediaUrl||busy(task))continue;
  state.autoPending.delete(id);persistAuto();const keys=['speech','ocr'].filter(k=>(task.mediaType!=='image'||k==='ocr')&&!task[k]?.text&&!task[k]?.edited&&!state.dirty.has(draftKey(id,k))&&task[k]?.status!=='已完成');
  if(keys.length)extract(keys,id).catch(e=>toast(e.message));break;
 }
}
async function retryCurrent(){const task=currentTask();if(!task||busy(task))return;$('#retryCurrentBtn').disabled=true;try{await post('/api/tasks/'+task.id+'/retry');await refresh(true);queueAutoExtract(task.id);}catch(e){toast(e.message);}finally{$('#retryCurrentBtn').disabled=false;}}

$$('[data-view]').forEach(b=>b.addEventListener('click',()=>setView(b.dataset.view)));
$('.brand').addEventListener('click',e=>{e.preventDefault();setView('workspace');});
$('#linkTab').onclick=()=>setInputMode('link');$('#uploadTab').onclick=()=>setInputMode('upload');$('#localShortcut').onclick=()=>setInputMode('upload');
$('#parseBtn').onclick=parse;$('#urlInput').addEventListener('keydown',e=>{if(e.key==='Enter'&&(e.ctrlKey||e.metaKey))parse();});
$('#pasteBtn').onclick=async()=>{try{$('#urlInput').value=await navigator.clipboard.readText();detectPlatform();$('#urlInput').focus();}catch{toast('请粘贴分享内容');$('#urlInput').focus();}};
$('#urlInput').addEventListener('input',detectPlatform);
$('#fileInput').addEventListener('change',e=>handleFile(e.target.files[0]));$('#sampleBtn').onclick=example;
$('#dropZone').addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();$('#fileInput').click();}});
for(const name of ['dragenter','dragover'])$('#dropZone').addEventListener(name,e=>{e.preventDefault();$('#dropZone').classList.add('drag');});
for(const name of ['dragleave','drop'])$('#dropZone').addEventListener(name,e=>{e.preventDefault();$('#dropZone').classList.remove('drag');});
$('#dropZone').addEventListener('drop',e=>handleFile(e.dataTransfer.files[0]));
document.addEventListener('dragover',e=>e.preventDefault());document.addEventListener('drop',e=>e.preventDefault());
$('#dismissResult').onclick=()=>{saveDrafts();state.current=null;$('#resultSection').hidden=true;$('#features').hidden=false;setOperation('');};
$$('.result-tab').forEach(b=>b.addEventListener('click',()=>{saveDrafts();state.field=b.dataset.field;renderField(true);}));
$('#extractBtn').onclick=()=>extract();$('#singleExtractBtn').onclick=()=>extract([state.field]);$('#textResult').oninput=editText;$('#textResult').onblur=saveDrafts;$('#copyBtn').onclick=copyText;$('#exportBtn').onclick=exportText;
$('#recentList').onclick=taskAction;$('#historyList').onclick=taskAction;$('#historySearch').oninput=()=>renderLists(true);
$('#historyFilter').onchange=()=>renderLists(true);
$('#settingsBtn').onclick=openSettings;$('#quickSettingsBtn').onclick=openSettings;$('#connectPlatformBtn').onclick=()=>{if(currentTask()?.errorCode==='network_access_denied')$('#networkHelpDialog').showModal();else openSettings();};$('#helpBtn').onclick=()=>$('#helpDialog').showModal();
$('#retryCurrentBtn').onclick=retryCurrent;$('#platformList').onclick=platformAction;$('#cookieFileInput').onchange=importCookies;
$('#networkCheckBtn').onclick=checkConnection;$('#networkHelpBtn').onclick=()=>$('#networkHelpDialog').showModal();
$$('.close-dialog').forEach(b=>b.onclick=()=>b.closest('dialog').close());$$('dialog').forEach(d=>d.addEventListener('click',e=>{if(e.target===d){const r=d.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)d.close();}}));
$('#cancelDelete').onclick=()=>$('#confirmDialog').close();$('#confirmDelete').onclick=deleteTask;
try{$('#ocrMode').value=localStorage.getItem('frame-ocr-mode')||'full';}catch{}
try{const pending=JSON.parse(sessionStorage.getItem('frame-auto-pending')||'[]');if(Array.isArray(pending))state.autoPending=new Set(pending.filter(id=>/^[a-f0-9]{32}$/.test(id)));$('#autoExtract').checked=localStorage.getItem('frame-auto-extract')==='true';}catch{}
$('#autoExtract').onchange=()=>{try{localStorage.setItem('frame-auto-extract',String($('#autoExtract').checked));}catch{}};
$('#ocrMode').onchange=()=>{try{localStorage.setItem('frame-ocr-mode',$('#ocrMode').value);}catch{}};
window.addEventListener('beforeunload',e=>{if(state.extracting.size){e.preventDefault();e.returnValue='';}});
async function boot(){await refresh();await health();renderLists(true);if(location.protocol==='file:'){inputError('请通过“启动网站”打开，文件上传和识别需要本机服务。');}setInterval(async()=>{await refresh();await health();},2500);}
boot();
