const $ = id => document.getElementById(id);
const audio = $('audio');
const canvas = $('waveform');
const ctx = canvas.getContext('2d');
const state = {music:null, analysis:null, bpm:null, markers:[], clips:[], ranges:[], selected:-1, interval:4, undo:[], plan:null, busy:false, dirty:false};
const esc = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const time = seconds => {const t = Math.max(0, seconds || 0); return `${String(Math.floor(t / 60)).padStart(2,'0')}:${(t % 60).toFixed(3).padStart(6,'0')}`;};
const clone = x => structuredClone(x);
let clickContext, lastClick=-1, drag=null;

function message(text, error=false) {
  $('status').classList.toggle('error', error);
  $('status').lastElementChild.textContent = text;
}

async function api(path, data, method='POST') {
  const options = {method, headers:{'X-BeatCut':'1'}};
  if(data instanceof FormData) options.body = data;
  else if(data !== undefined) {options.headers['Content-Type']='application/json'; options.body=JSON.stringify(data);}
  const response = await fetch(path, options);
  const result = await response.json();
  if(!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : JSON.stringify(result.detail || result));
  return result;
}

async function task(label, fn) {
  if(state.busy) return;
  state.busy = true; audio.pause(); $('edit-audio').pause();
  $('busy-label').textContent=label; $('busy').hidden=false; document.body.classList.add('processing');
  try {await fn();} catch(error) {message(error.message, true);}
  finally {state.busy=false; $('busy').hidden=true; document.body.classList.remove('processing');}
}

function dirty() {
  state.dirty=true; $('save-state').textContent='有未保存修改';
  state.plan=null; $('sequence').innerHTML='<div class="sequence-empty">设置已更新 · 请重新生成编排</div>';
  $('sequence-summary').textContent='等待更新'; $('export-result').hidden=true;
  $('edit-audio').pause(); $('edit-audio').hidden=true;
}

function project() {
  if(!state.music) throw new Error('请先导入一首音乐');
  const [width,height]=$('resolution').value.split(',').map(Number);
  return {version:1,name:$('project-name').value.trim() || '我的卡点剪辑',music_id:state.music.id,bpm:state.bpm,
    markers:state.markers.map(m=>({time:m.time,enabled:m.enabled,forced:m.forced})),ranges:state.ranges,
    clips:state.clips.map(c=>({id:c.id,start:c.start || 0,end:c.end ?? null,conform:c.conform || 'fill'})),
    first_id:$('first-clip').value || null,last_id:$('last-clip').value || null,interval:state.interval,
    pace:$('pace').value,order:$('order').value,seed:1,fps:$('fps').value,width,height,mode:$('mode').value};
}

function remember() {
  state.undo.push({markers:clone(state.markers),bpm:state.bpm}); if(state.undo.length>50) state.undo.shift(); $('undo').disabled=false;
}

function updateMarkers() {
  $('beat-count').textContent=`${state.markers.filter(m=>m.enabled).length} 个有效标记`;
  const m=state.markers[state.selected];
  $('selected-marker').textContent=m ? `标记 ${String(state.selected+1).padStart(3,'0')}` : '点击节拍线以编辑';
  for(const id of ['marker-time','marker-enabled','marker-forced','delete-marker']) $(id).disabled=!m;
  $('marker-time').value=m ? m.time.toFixed(3) : '';
  $('marker-enabled').checked=m?.enabled || false; $('marker-forced').checked=m?.forced || false;
  draw();
}

function updateRanges() {
  const duration=state.ranges.reduce((sum,[a,b])=>sum+b-a,0);
  $('range-summary').textContent=state.ranges.length>1 ? `${state.ranges.length} 段保留区间 · ${duration.toFixed(2)} 秒` : `${duration.toFixed(2)} 秒 · 连续音乐`;
  $('audio-sequence-duration').textContent=time(duration);
  draw();
}

function draw() {
  const viewport=$('wave-scroll').clientWidth;
  const width=Math.min(15000,Math.max(1,viewport)*Number($('zoom').value));
  const height=230, dpr=Math.min(2,window.devicePixelRatio || 1);
  if(canvas.width!==Math.round(width*dpr) || canvas.height!==height*dpr) {canvas.width=Math.round(width*dpr);canvas.height=height*dpr;canvas.style.width=width+'px';canvas.style.height=height+'px';}
  ctx.setTransform(dpr,0,0,dpr,0,0); ctx.clearRect(0,0,width,height);
  if(!state.music) return;
  const duration=state.music.duration;
  const x=t=>t/duration*width;
  ctx.fillStyle='#292d34';ctx.fillRect(0,0,width,height);
  for(const [a,b] of state.ranges){ctx.fillStyle='#2e3948';ctx.fillRect(x(a),25,x(b-a),height-25);}
  ctx.strokeStyle='#414c5a';ctx.lineWidth=1;ctx.font='9px LocalMono, monospace';ctx.fillStyle='#90a0b4';
  const step=Math.max(.5,Math.ceil(duration/(width/100)*2)/2);
  for(let t=0;t<=duration;t+=step){const px=x(t);ctx.beginPath();ctx.moveTo(px,25);ctx.lineTo(px,height);ctx.stroke();ctx.fillText(time(t).slice(0,-1),px+5,15);}
  ctx.strokeStyle='#bed3ee';ctx.lineWidth=Math.max(1,width/(state.analysis?.peaks.length || 3000)*.7);
  if(state.analysis){const peaks=state.analysis.peaks; const stride=Math.max(1,Math.floor(peaks.length/width));ctx.beginPath();for(let i=0;i<peaks.length;i+=stride){const px=i/peaks.length*width;ctx.moveTo(px,130+peaks[i][0]*80);ctx.lineTo(px,130+peaks[i][1]*80);}ctx.stroke();}
  state.markers.forEach((m,i)=>{const px=x(m.time);ctx.strokeStyle=!m.enabled?'#78818e':m.forced?'#ff947d':'#f1ce59';ctx.globalAlpha=m.enabled?.8:.3;ctx.lineWidth=i===state.selected?2:1;ctx.beginPath();ctx.moveTo(px,31);ctx.lineTo(px,216);ctx.stroke();if(i===state.selected || m.forced){ctx.fillStyle=ctx.strokeStyle;ctx.beginPath();ctx.moveTo(px-4,25);ctx.lineTo(px+4,25);ctx.lineTo(px,33);ctx.fill();}});ctx.globalAlpha=1;
  const px=x(audio.currentTime);ctx.strokeStyle='#e7f0fc';ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(px,25);ctx.lineTo(px,height);ctx.stroke();ctx.fillStyle='#e7f0fc';ctx.fillRect(px-3,20,6,5);
}

async function loadMusic(meta, doAnalyze=true) {
  state.music=meta;state.analysis=null;state.bpm=null;state.markers=[];state.selected=-1;state.undo=[];state.ranges=[[0,meta.duration]];
  $('music-name').textContent=meta.name; $('music-meta').textContent=`${time(meta.duration)} · 本地素材 · 点击更换音乐`;
  $('total-time').textContent=time(meta.duration);$('range-start').value=0;$('range-end').value=meta.duration.toFixed(3);
  $('target-duration').value=Math.max(2,Math.round(meta.duration/2));$('audio-sequence-name').textContent=meta.name;
  $('analyze').disabled=false;$('play').disabled=false;$('add-marker').disabled=false;$('undo').disabled=true;
  $('wave-empty').hidden=true;$('suggestions').innerHTML='';audio.src=`/api/media/${meta.id}/file`;
  $('bpm-value').textContent='—';dirty();updateMarkers();updateRanges();
  if(doAnalyze) await analyzeMusic();
}

async function analyzeMusic(bpm=null) {
  if(!state.music) throw new Error('请先选择音乐');
  $('busy-label').textContent='正在分析音乐节拍，首次运行需要初始化分析库…';
  const result=await api(`/api/analyze/${state.music.id}${bpm ? '?bpm='+bpm : ''}`);
  if(state.markers.length) remember();
  state.analysis=result;state.bpm=result.bpm>=20&&result.bpm<=400?result.bpm:null;state.markers=result.beats.map(t=>({time:t,enabled:true,forced:false}));state.selected=-1;
  $('bpm-value').textContent=result.bpm || '—';if(result.bpm) $('grid-bpm').value=result.bpm;
  if(result.beats.length) $('grid-first').value=result.beats[0].toFixed(3);
  dirty();updateMarkers();message(result.beats.length ? `分析完成：${result.beats.length} 个节拍，约 ${result.bpm} BPM。可以试听并微调标记。` : '没有找到清晰的节拍。可以使用手动网格或 Shift 点击添加标记。');
}

async function uploadFile(file,kind) {const data=new FormData();data.append('file',file);data.append('kind',kind);return api('/api/media',data);}
function importMusic(file) {if(file) task('正在读取音乐…',async()=>loadMusic(await uploadFile(file,'music')));}
function importClips(files) {task('正在导入镜头素材…',async()=>{for(const file of files){$('busy-label').textContent=`正在导入 ${file.name}…`;const item=await uploadFile(file,'clip');state.clips.push({...item,start:0,end:null,conform:'fill'});}renderClips();$('mode').value='clips';$('clips-details').open=true;dirty();message(`已导入 ${state.clips.length} 个镜头素材。`);});}

function renderClips() {
  $('clip-count').textContent=state.clips.length;
  $('clip-drop').innerHTML=state.clips.length ? state.clips.map((c,i)=>`<article class="clip-card" data-id="${c.id}"><img src="/api/media/${c.id}/thumb" alt="${esc(c.name)}"><button class="clip-remove" data-action="remove" aria-label="移除 ${esc(c.name)}">×</button><div class="clip-info"><strong title="${esc(c.name)}">${String(i+1).padStart(2,'0')} / ${esc(c.name)}</strong><small>${c.kind==='image'?'STILL IMAGE':time(c.duration)} · ${c.width} × ${c.height}</small><div class="clip-controls"><button data-action="up" title="上移" ${i===0?'disabled':''}>↑</button><button data-action="down" title="下移" ${i===state.clips.length-1?'disabled':''}>↓</button><select data-field="conform" aria-label="${esc(c.name)} 画面适配"><option value="fill" ${c.conform==='fill'?'selected':''}>填满</option><option value="fit" ${c.conform==='fit'?'selected':''}>适应</option></select></div>${c.kind==='image'?'':`<div class="clip-controls"><label>入点 / 秒<input type="number" min="0" max="${c.duration}" step="0.01" value="${c.start}" data-field="start"></label><label>出点 / 秒<input type="number" min="0" max="${c.duration}" step="0.01" value="${c.end??c.duration.toFixed(3)}" data-field="end"></label></div>`}</div></article>`).join('') : '<div class="empty-clips"><span>▧</span><p>拖入你的镜头，让音乐来安排节奏</p><small>也可以先导出标记，或用占位片段规划剪辑。</small></div>';
  for(const id of ['first-clip','last-clip']){const value=$(id).value;$(id).innerHTML='<option value="">自动</option>'+state.clips.map(c=>`<option value="${c.id}">${esc(c.name)}</option>`).join('');$(id).value=state.clips.some(c=>c.id===value)?value:'';}
}

function renderPlan(plan) {
  state.plan=plan;const count=$('mode').value==='markers'?0:plan.slots.length;
  $('sequence-summary').textContent=`${count} 个镜头 · ${plan.markers.length} 个标记 · ${plan.duration.toFixed(2)} 秒`;
  $('sequence').innerHTML=count ? plan.slots.map((s,i)=>`<div class="shot" style="width:${Math.max(55,Math.min(180,s.seconds*27))}px" title="${esc(s.name)} · ${s.seconds.toFixed(3)} 秒"><b>${String(i+1).padStart(2,'0')} · ${esc(s.name)}</b><small>${s.seconds.toFixed(2)}s</small></div>`).join('') : '<div class="sequence-empty">音乐与节拍标记 · 不包含视频镜头</div>';
  $('audio-sequence-duration').textContent=time(plan.duration);
  $('plan-warning').textContent=plan.warnings.length?plan.warnings.join(' '):'镜头按节拍排列，视频原声已静音。此处为编排示意，完整视频请导入 Final Cut Pro 播放。';
}

async function exportProject() {
  const result=await api('/api/export',project());renderPlan(result.plan);
  const labels={'timeline.fcpxml':'下载 FCPXML ↗','music.wav':'剪后音乐 WAV','markers.csv':'标记 CSV','project.beatcut.json':'项目文件'};
  $('export-result').innerHTML=`<span class="eyebrow">READY FOR YOUR NEXT EDIT</span><h2>时间线已生成</h2><p>在 Final Cut Pro 中选择「文件 → 导入 → XML」。导出引用本地素材，请保留本机素材数据目录，或在 FCP 中整合媒体。</p><div class="export-links">${Object.entries(result.files).map(([name,url])=>`<a href="${url}" download>${labels[name]}</a>`).join('')}</div><div class="export-path">${esc(result.path)}</div><button class="primary" id="open-fcp">在 Final Cut Pro 中打开</button><button id="reveal-export">在 Finder 中显示</button>`;
  $('open-fcp').onclick=()=>task('正在打开 Final Cut Pro…',()=>api(`/api/open-fcp/${result.id}`));
  $('export-result').hidden=false;$('reveal-export').onclick=()=>task('正在打开导出文件夹…',()=>api(`/api/reveal/${result.id}`));
  $('export-result').scrollIntoView({behavior:'smooth',block:'nearest'});message('导出完成。FCPXML 已生成，导入 FCP 后可继续调整剪辑。');
}

function addMarker(t) {
  if(!state.music || !Number.isFinite(t) || t<0 || t>=state.music.duration) return;
  if(state.markers.some(m=>Math.abs(m.time-t)<.001)) return;
  remember();const marker={time:t,enabled:true,forced:false};state.markers.push(marker);state.markers.sort((a,b)=>a.time-b.time);state.selected=state.markers.indexOf(marker);dirty();updateMarkers();
}

function togglePlay() {
  if(!state.music || state.busy) return;
  if(audio.paused){$('edit-audio').pause();audio.play().catch(e=>message(e.message,true));if($('metronome').checked){clickContext??=new AudioContext();clickContext.resume();}lastClick=-1;}else audio.pause();
}
audio.addEventListener('play',()=>{$('play').textContent='Ⅱ';requestAnimationFrame(tick);});
audio.addEventListener('pause',()=>{$('play').textContent='▶';});
audio.addEventListener('timeupdate',()=>{$('current-time').textContent=time(audio.currentTime);draw();});
audio.addEventListener('seeked',()=>{lastClick=-1;});
$('edit-audio').addEventListener('play',()=>audio.pause());
function tick() {
  if(audio.paused) return;
  if($('metronome').checked && clickContext){const i=state.markers.findIndex(m=>m.enabled&&Math.abs(m.time-audio.currentTime)<.035);if(i>=0&&i!==lastClick){const o=clickContext.createOscillator(),g=clickContext.createGain();o.frequency.value=state.markers[i].forced?1400:950;g.gain.setValueAtTime(.12,clickContext.currentTime);g.gain.exponentialRampToValueAtTime(.001,clickContext.currentTime+.045);o.connect(g).connect(clickContext.destination);o.start();o.stop(clickContext.currentTime+.05);lastClick=i;}}
  draw();requestAnimationFrame(tick);
}

$('music-drop').onclick=e=>{if(!e.target.closest('#analyze')) $('music-input').click();};
$('music-drop').onkeydown=e=>{if(e.key==='Enter') $('music-input').click();};
$('music-input').onchange=e=>{importMusic(e.target.files[0]);e.target.value='';};
$('add-clips').onclick=()=>$('clips-input').click();
$('clips-input').onchange=e=>{importClips([...e.target.files]);e.target.value='';};
for(const [id,handler] of [['music-drop',files=>importMusic(files[0])],['clip-drop',importClips]]){
  const zone=$(id);zone.ondragover=e=>{e.preventDefault();zone.classList.add('drag-over');};zone.ondragleave=()=>zone.classList.remove('drag-over');zone.ondrop=e=>{e.preventDefault();zone.classList.remove('drag-over');if(!state.busy) handler([...e.dataTransfer.files]);};
}
$('analyze').onclick=e=>{e.stopPropagation();task('正在分析节拍…',analyzeMusic);};
$('play').onclick=togglePlay;$('zoom').oninput=draw;
$('add-marker').onclick=()=>addMarker(audio.currentTime);
$('undo').onclick=()=>{if(!state.undo.length)return;const previous=state.undo.pop();state.markers=previous.markers;state.bpm=previous.bpm;$('bpm-value').textContent=state.bpm||'—';state.selected=-1;$('undo').disabled=!state.undo.length;dirty();updateMarkers();};
$('delete-marker').onclick=()=>{if(state.selected<0)return;remember();state.markers.splice(state.selected,1);state.selected=-1;dirty();updateMarkers();};
for(const id of ['marker-time','marker-enabled','marker-forced']) $(id).onchange=()=>{
  const m=state.markers[state.selected];if(!m)return;
  const t=Number($('marker-time').value);if(!Number.isFinite(t)||t<0||t>=state.music.duration){message('标记时间必须在音乐范围内',true);updateMarkers();return;}
  remember();m.time=t;m.enabled=$('marker-enabled').checked;m.forced=$('marker-forced').checked;state.markers.sort((a,b)=>a.time-b.time);state.selected=state.markers.indexOf(m);dirty();updateMarkers();
};

canvas.onpointerdown=e=>{
  if(!state.music || state.busy)return;
  const rect=canvas.getBoundingClientRect(),t=Math.max(0,Math.min(state.music.duration-.001,(e.clientX-rect.left)/rect.width*state.music.duration));
  if(e.shiftKey){addMarker(t);return;}
  let nearest=-1,distance=8;state.markers.forEach((m,i)=>{const d=Math.abs(m.time/state.music.duration*rect.width-(e.clientX-rect.left));if(d<distance){distance=d;nearest=i;}});
  state.selected=nearest;audio.currentTime=t;updateMarkers();
  if(nearest>=0){drag={marker:state.markers[nearest],x:e.clientX,remembered:false};canvas.setPointerCapture(e.pointerId);}
};
canvas.onpointermove=e=>{if(!drag)return;if(!drag.remembered&&Math.abs(e.clientX-drag.x)<3)return;if(!drag.remembered){remember();drag.remembered=true;}const rect=canvas.getBoundingClientRect();drag.marker.time=Math.max(0,Math.min(state.music.duration-.001,(e.clientX-rect.left)/rect.width*state.music.duration));updateMarkers();};
canvas.onpointerup=()=>{if(drag?.remembered){state.markers.sort((a,b)=>a.time-b.time);state.selected=state.markers.indexOf(drag.marker);dirty();updateMarkers();}drag=null;};
canvas.onpointercancel=()=>{drag=null;};
$('apply-grid').onclick=()=>{if(!state.music){message('请先导入音乐',true);return;}const bpm=Number($('grid-bpm').value),first=Number($('grid-first').value);if(!Number.isFinite(bpm)||bpm<20||bpm>400||!Number.isFinite(first)||first<0||first>=state.music.duration){message('请输入 20–400 BPM，以及音乐范围内的第一拍时间',true);return;}if((state.music.duration-first)*bpm/60>30000){message('网格超过 30000 个标记，请缩短音乐或降低 BPM',true);return;}remember();state.markers=[];for(let t=first;t<state.music.duration;t+=60/bpm)state.markers.push({time:Math.round(t*1e6)/1e6,enabled:true,forced:false});state.selected=-1;state.bpm=bpm;$('bpm-value').textContent=bpm;dirty();updateMarkers();message('已重建等距节拍网格。');};

$('apply-range').onclick=()=>{if(!state.music)return;const a=Number($('range-start').value),b=Number($('range-end').value);if(!Number.isFinite(a)||!Number.isFinite(b)||a<0||b<=a||b>state.music.duration+.001){message('请填写原音乐范围内的起止时间',true);return;}state.ranges=[[a,Math.min(b,state.music.duration)]];dirty();updateRanges();};
$('reset-range').onclick=()=>{if(!state.music)return;state.ranges=[[0,state.music.duration]];$('range-start').value=0;$('range-end').value=state.music.duration.toFixed(3);dirty();updateRanges();};
$('suggest').onclick=()=>task('正在比较按节拍对齐的拼接候选…',async()=>{const options=await api('/api/shorten',{project:project(),target:Number($('target-duration').value),tolerance:Number($('tolerance').value)});$('suggestions').innerHTML='';options.forEach((candidate,i)=>{const button=document.createElement('button');button.textContent=`方案 ${i+1} · ${candidate.duration.toFixed(2)}s · 相似度 ${(candidate.similarity*100).toFixed(0)}% · 接缝 ${time(candidate.join)}`;button.onclick=()=>{state.ranges=candidate.ranges;dirty();updateRanges();[...$('suggestions').children].forEach(c=>c.classList.remove('selected'));button.classList.add('selected');message(`已应用方案 ${i+1}。点击“试听所选音乐”检查 ${time(candidate.join)} 附近的接缝。`);};$('suggestions').append(button);});message('已找到拼接候选。选择方案后可试听。');});
$('preview-edit').onclick=()=>task('正在准备剪后音乐…',async()=>{const result=await api('/api/preview',project());$('edit-audio').src=result.url;$('edit-audio').hidden=false;await $('edit-audio').play();message('正在试听导出使用的音乐区间。');});

$('clip-drop').onclick=e=>{const button=e.target.closest('[data-action]'),card=e.target.closest('[data-id]');if(!button||!card)return;const i=state.clips.findIndex(c=>c.id===card.dataset.id);if(button.dataset.action==='remove')state.clips.splice(i,1);else{const j=i+(button.dataset.action==='up'?-1:1);if(j<0||j>=state.clips.length)return;[state.clips[i],state.clips[j]]=[state.clips[j],state.clips[i]];}renderClips();dirty();};
$('clip-drop').onchange=e=>{const card=e.target.closest('[data-id]'),field=e.target.dataset.field;if(!card||!field)return;const clip=state.clips.find(c=>c.id===card.dataset.id);clip[field]=field==='conform'?e.target.value:Number(e.target.value);dirty();};
$('intervals').onclick=e=>{const button=e.target.closest('[data-interval]');if(!button)return;state.interval=Number(button.dataset.interval);[...$('intervals').children].forEach(b=>b.classList.toggle('selected',b===button));dirty();};
for(const id of ['project-name','pace','order','first-clip','last-clip','mode','resolution','fps']) $(id).onchange=dirty;
$('make-plan').onclick=()=>task('正在编排镜头…',async()=>{renderPlan(await api('/api/plan',project()));message('编排完成，可以导出到 Final Cut Pro。');});
$('export').onclick=$('export-top').onclick=()=>task('正在生成 FCPXML、音乐和项目文件…',exportProject);

$('save-project').onclick=async()=>{
  try{
    const p=project(),text=JSON.stringify(p,null,2),name=p.name.replace(/[\\/:*?"<>|]/g,'_')+'.beatcut.json';
    if(window.webkit?.messageHandlers?.desktop){
      const saved=await window.webkit.messageHandlers.desktop.postMessage({action:'saveProject',text,name});
      if(!saved)return;
      state.dirty=false;$('save-state').textContent='已保存';message('项目已保存。素材保存在应用的本机数据目录。');
    }else{
      const url=URL.createObjectURL(new Blob([text],{type:'application/json'})),a=document.createElement('a');
      a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
      state.dirty=false;$('save-state').textContent='已下载';message('项目已下载，请同时保留本机素材数据目录。');
    }
  }catch(e){message(e.message,true);}
};
$('open-project').onclick=()=>$('project-input').click();
$('project-input').onchange=e=>{const file=e.target.files[0];e.target.value='';if(!file)return;task('正在恢复项目…',async()=>{
  const p=JSON.parse(await file.text());if(p.version!==1||!p.music_id||!Array.isArray(p.markers))throw new Error('这不是支持的 BeatCut 项目文件');
  // Validate all references before replacing the current editor state.
  await api('/api/project/validate',p);const music=await api(`/api/media/${p.music_id}`,undefined,'GET');const clips=await Promise.all((p.clips||[]).map(async c=>({...await api(`/api/media/${c.id}`,undefined,'GET'),...c})));
  await loadMusic(music,false);state.analysis=await api(`/api/analyze/${music.id}`);state.markers=p.markers;state.ranges=p.ranges.length?p.ranges:[[0,music.duration]];state.clips=clips;state.interval=p.interval;
  $('project-name').value=p.name;for(const key of ['pace','order','mode','fps'])$(key).value=p[key];$('resolution').value=`${p.width},${p.height}`;renderClips();$('first-clip').value=p.first_id||'';$('last-clip').value=p.last_id||'';[...$('intervals').children].forEach(b=>b.classList.toggle('selected',Number(b.dataset.interval)===p.interval));state.bpm=p.bpm||state.analysis.bpm||null;$('bpm-value').textContent=state.bpm||'—';$('grid-bpm').value=state.bpm||120;$('range-start').value=state.ranges[0][0];$('range-end').value=state.ranges.at(-1)[1];updateRanges();updateMarkers();$('clips-details').open=p.mode!=='markers';state.dirty=false;$('save-state').textContent='已恢复';message('项目、标记修改和素材编排已恢复。点击“生成编排”检查当前设置。');
});};
$('demo').onclick=()=>task('正在生成本地演示音频和镜头…',async()=>{const result=await api('/api/demo');await loadMusic(result.music);state.clips=result.clips.map(c=>({...c,start:0,end:null,conform:'fill'}));$('project-name').value='节奏练习 · 120 BPM';$('mode').value='clips';$('clips-details').open=true;renderClips();dirty();renderPlan(await api('/api/plan',project()));message('已载入实际生成的演示素材。可以播放、修改标记、尝试缩短音乐并导出。');});

document.addEventListener('keydown',e=>{if(e.target.closest('input,select,textarea,button,summary')||state.busy)return;if(e.code==='Space'){e.preventDefault();togglePlay();}if(e.key==='Delete'||e.key==='Backspace'){if(state.selected>=0){e.preventDefault();$('delete-marker').click();}}});
window.addEventListener('beforeunload',e=>{if(state.dirty){e.preventDefault();e.returnValue='';}});
new ResizeObserver(draw).observe($('wave-scroll'));
api('/api/status',undefined,'GET').then(s=>{if(!s.ffmpeg||!s.ffprobe)message('未找到 FFmpeg，请先安装：brew install ffmpeg',true);}).catch(()=>message('无法连接本机服务，请运行 start.command',true));
draw();

for(const [id,factor] of [['half-tempo',.5],['double-tempo',2]]) $(id).onclick=()=>task('正在按指定速度重新跟踪节拍…',async()=>{const bpm=state.bpm*factor;if(!state.music||!state.bpm)throw new Error('请先分析音乐');if(bpm<20||bpm>400)throw new Error('修正后的速度需要在 20–400 BPM 范围内');await analyzeMusic(bpm);});

window.beatcutCanClose=()=>!state.dirty;
$('mode').addEventListener('change',()=>{$('clips-details').open=$('mode').value!=='markers';});

for(const link of document.querySelectorAll('.mode-tabs a'))link.onclick=e=>{if(new URL(link.href).pathname===location.pathname){e.preventDefault();return;}if((state.dirty||state.busy)&&!confirm('当前项目有未保存的修改。取消后可先保存项目；继续切换会放弃这些修改。'))e.preventDefault();};
