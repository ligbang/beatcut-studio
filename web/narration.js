const $=id=>document.getElementById(id),audio=$('voice-audio');
const state={music:null,cuts:[],aligned:null,dirty:false,busy:false,job:null,previewEnd:null};
const esc=value=>String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const parts=()=>$('script').value.split(/\r?\n/).map(s=>s.trim()).filter(Boolean);
const clock=t=>`${Math.floor(t/60).toString().padStart(2,'0')}:${(t%60).toFixed(2).padStart(5,'0')}`;
function message(text,error=false){$('status').classList.toggle('error',error);$('status').lastElementChild.textContent=text;}
async function api(path,data,method='POST'){
 const options={method,headers:{'X-BeatCut':'1'}};
 if(data instanceof FormData)options.body=data;
 else if(data!==undefined){options.body=JSON.stringify(data);options.headers['Content-Type']='application/json';}
 const response=await fetch(path,options),result=await response.json();
 if(!response.ok)throw new Error(typeof result.detail==='string'?result.detail:JSON.stringify(result.detail||result));
 return result;
}
function dirty(invalidate=false){state.dirty=true;$('save-state').textContent='有未保存修改';$('export-result').hidden=true;if(invalidate){state.aligned=null;state.cuts=[];$('paragraph-result').hidden=true;$('export').disabled=true;}}
function busy(value){state.busy=value;document.body.classList.toggle('voice-processing',value);for(const id of ['script','fps','project-name'])$(id).disabled=value;}
async function task(fn){if(state.busy)return;busy(true);audio.pause();try{await fn();}catch(e){message(e.message,true);}finally{busy(false);}}
function project(){if(!state.music||!state.aligned)throw new Error('请先完成段落对齐');return {kind:'narration',version:1,name:$('project-name').value.trim()||'旁白分段',music_id:state.music.id,script:$('script').value,cuts:state.cuts,fps:$('fps').value};}
function setMusic(meta){state.music=meta;audio.src=`/api/media/${meta.id}/file`;audio.hidden=false;$('music-name').textContent=meta.name;$('music-meta').textContent=`${clock(meta.duration)} · 点击更换旁白音频`;state.previewEnd=null;dirty(true);}
function importAudio(file){if(!file)return;task(async()=>{message('正在导入旁白…');const form=new FormData();form.append('file',file);form.append('kind','music');const meta=await api('/api/media',form);if(meta.duration>1800)throw new Error('当前支持最长 30 分钟旁白，请先分成较短的文件');setMusic(meta);message('旁白已导入，粘贴对应原文后开始对齐。');});}
function render(){
 const texts=parts(),bounds=[0,...state.cuts,state.music.duration];
 $('paragraph-result').hidden=false;$('export').disabled=false;$('result-count').textContent=texts.length;
 $('match-summary').textContent=state.aligned.coverage==null?'已恢复项目 · 请试听切点':`文本匹配 ${Math.round(state.aligned.coverage*100)}% · 原音频 ${clock(state.music.duration)}`;
 $('paragraph-list').innerHTML=texts.map((text,i)=>{
  const detail=state.aligned.paragraphs?.[i];
  return `<article class="paragraph-row ${detail?.warning?'needs-review':''}" data-index="${i}"><div class="paragraph-number">${String(i+1).padStart(2,'0')}</div><div class="paragraph-copy"><p>${esc(text)}</p>${detail?.warning?`<small class="review-warning">⚠ ${esc(detail.warning)}</small>`:''}</div><div class="paragraph-timing"><label>起点 <input type="number" step="0.01" min="0" value="${bounds[i].toFixed(3)}" data-cut="${i}" aria-label="第 ${i+1} 段起点" ${i===0?'disabled':''}> 秒</label><small>至 ${clock(bounds[i+1])} · ${(bounds[i+1]-bounds[i]).toFixed(2)} 秒</small><button data-play="${i}">▶ 试听此段</button></div></article>`;
 }).join('');
}
$('music-drop').onclick=()=>$('music-input').click();$('music-drop').onkeydown=e=>{if(e.key==='Enter')$('music-input').click();};
$('music-input').onchange=e=>{importAudio(e.target.files[0]);e.target.value='';};
$('music-drop').ondragover=e=>{e.preventDefault();$('music-drop').classList.add('drag-over');};$('music-drop').ondragleave=()=>$('music-drop').classList.remove('drag-over');
$('music-drop').ondrop=e=>{e.preventDefault();$('music-drop').classList.remove('drag-over');if(!state.busy)importAudio(e.dataTransfer.files[0]);};
$('script').oninput=()=>{$('paragraph-count').textContent=`${parts().length} 段`;dirty(true);};
$('project-name').onchange=$('fps').onchange=()=>dirty();
$('align').onclick=()=>task(async()=>{
 if(!state.music)throw new Error('请先导入旁白音频');if(parts().length<2)throw new Error('请提供至少两段文案，每段单独一行');
 const job=await api('/api/narration/align',{music_id:state.music.id,script:$('script').value});state.job=job.id;$('voice-progress').hidden=false;
 try{for(;;){
  const result=await api(`/api/narration/jobs/${job.id}`,undefined,'GET');$('progress-label').textContent=result.message;$('progress-bar').value=result.progress;message(result.message);
  if(result.state==='error')throw new Error(result.message);
  if(result.state==='cancelled'){message('已取消分析，原音频和文案仍保留。');break;}
  if(result.state==='done'){state.aligned=result.result;state.cuts=result.result.cuts;dirty();render();const warnings=result.result.paragraphs.filter(p=>p.warning).length;message(`已对齐 ${parts().length} 段旁白，全部音频与停顿保留。${warnings?`其中 ${warnings} 段请重点试听。`:'可以逐段试听或直接导出到 FCP。'}`);break;}
  await new Promise(resolve=>setTimeout(resolve,700));
 }}finally{state.job=null;$('voice-progress').hidden=true;}
});
$('cancel-align').onclick=async()=>{if(state.job){await api(`/api/narration/jobs/${state.job}/cancel`);message('正在停止，将在当前音频块处理完后取消…');}};
$('paragraph-list').onchange=e=>{if(!e.target.matches('[data-cut]'))return;const i=Number(e.target.dataset.cut),value=Number(e.target.value),bounds=[0,...state.cuts,state.music.duration];if(!Number.isFinite(value)||value<=bounds[i-1]||value>=bounds[i+1]){message('切点必须位于相邻切点之间',true);render();return;}state.cuts[i-1]=value;dirty();render();};
$('paragraph-list').onclick=e=>{const button=e.target.closest('[data-play]');if(!button)return;const i=Number(button.dataset.play),bounds=[0,...state.cuts,state.music.duration];audio.currentTime=bounds[i];state.previewEnd=bounds[i+1];audio.play().catch(e=>message(e.message,true));};
audio.addEventListener('timeupdate',()=>{if(state.previewEnd!==null&&audio.currentTime>=state.previewEnd){audio.pause();state.previewEnd=null;}});
audio.addEventListener('play',()=>{function tick(){if(audio.paused)return;if(state.previewEnd!==null&&audio.currentTime>=state.previewEnd){audio.pause();state.previewEnd=null;return;}requestAnimationFrame(tick);}requestAnimationFrame(tick);});
audio.addEventListener('seeked',()=>{if(state.previewEnd!==null&&audio.currentTime>=state.previewEnd)state.previewEnd=null;});
$('export').onclick=()=>task(async()=>{
 const result=await api('/api/narration/export',project());
 $('export-result').innerHTML=`<h2>旁白时间线与字幕已生成</h2><p>${result.paragraphs} 个音频片段 · 原音频 ${clock(result.duration)} · 按原文命名，全部停顿保留。</p><div class="export-links">${Object.entries(result.files).map(([name,url])=>`<a href="${url}" download>${{'timeline.fcpxml':'保存 FCPXML','subtitles.srt':'保存字幕 SRT','markers.csv':'段落时间表 CSV','project.beatcut.json':'旁白项目文件'}[name]}</a>`).join('')}</div><div class="export-path">${esc(result.path)}</div><button id="open-fcp" class="primary">在 Final Cut Pro 中打开</button><button id="reveal-export">在 Finder 中显示</button><p class="hint">FCPXML 引用本机素材。请保留应用数据目录，或在 FCP 中整合媒体。</p>`;
 $('export-result').hidden=false;$('open-fcp').onclick=()=>task(()=>api(`/api/open-fcp/${result.id}`));$('reveal-export').onclick=()=>task(()=>api(`/api/reveal/${result.id}`));$('export-result').scrollIntoView({block:'nearest',behavior:'smooth'});message('时间线与 SRT 字幕已导出，原始音频未修改。');
});
$('save-project').onclick=async()=>{try{const p=project(),text=JSON.stringify(p,null,2),name=p.name.replace(/[\\/:*?"<>|]/g,'_')+'.beatcut.json';if(window.webkit?.messageHandlers?.desktop){const saved=await window.webkit.messageHandlers.desktop.postMessage({action:'saveProject',text,name});if(!saved)return;}else{const url=URL.createObjectURL(new Blob([text],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}state.dirty=false;$('save-state').textContent='已保存';message('已保存旁白项目，媒体保留在本机数据目录。');}catch(e){message(e.message,true);}};
$('open-project').onclick=()=>$('project-input').click();
$('project-input').onchange=e=>{const file=e.target.files[0];e.target.value='';if(!file)return;task(async()=>{const p=JSON.parse(await file.text());if(p.kind!=='narration')throw new Error('这是音乐卡点项目，请切换到“音乐卡点”后打开。');await api('/api/narration/validate',p);const meta=await api(`/api/media/${p.music_id}`,undefined,'GET');setMusic(meta);$('script').value=p.script;$('project-name').value=p.name;$('fps').value=p.fps;state.cuts=p.cuts;state.aligned={coverage:null};$('paragraph-count').textContent=`${parts().length} 段`;render();state.dirty=false;$('save-state').textContent='已恢复';message('旁白项目与切点已恢复，无需重新分析。');});};
window.beatcutCanClose=()=>!state.dirty&&!state.busy;
window.addEventListener('beforeunload',e=>{if(state.dirty||state.busy){e.preventDefault();e.returnValue='';}});

for(const link of document.querySelectorAll('.mode-tabs a'))link.onclick=e=>{if(new URL(link.href).pathname===location.pathname){e.preventDefault();return;}if((state.dirty||state.busy)&&!confirm('当前项目有未保存的修改。取消后可先保存项目；继续切换会放弃这些修改。'))e.preventDefault();};
