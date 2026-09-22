/* User intent is interpreted only by Jev; this UI transports versioned audio and controls. */
const $ = id => document.getElementById(id);
const token = document.querySelector('meta[name="voice-token"]').content;
let state = null, capture = null, preparing = null, starting = false, ticket = 0;
let renderedVersion = '', renderedHistory = '', lastError = '';
let selectedDecision = null;
let renderedDashboard = '';
const phases = {observing:'正在看应用', choosing:'Jev 正在选择下一步', writing:'正在生成填写内容', waiting_for_text:'文本模型未提供参数，本次动作未执行；继续聆听补充', validating:'正在理解你的补充', executing:'正在操作', listening:'继续听你说', blocked:'任务尚未完成，暂时无法继续操作', paused:'执行已暂停'};
function showError(error) { lastError=String(error.message || error); $('error').textContent=lastError; $('error').hidden=false; }
function clearError() { lastError=''; $('error').hidden=true; }
async function api(path, body) {
  const options={headers:{'X-Voice-Token':token}};
  if(body!==undefined) Object.assign(options,{method:'POST',headers:{...options.headers,'Content-Type':'application/json'},body:JSON.stringify(body)});
  const response=await fetch('/api/'+path,options), data=await response.json();
  if(!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}
function node(tag,text,className) { const el=document.createElement(tag); el.textContent=text; if(className) el.className=className; return el; }
function render(data) {
  state=data; const live=data.session, on=starting || (capture && !capture.finishing);
  $('record').disabled=data.closing || !!capture?.finishing || data.voice.status==='finishing';
  $('record').classList.toggle('active',!!on);
  $('record').setAttribute('aria-label',on?'关闭麦克风':'开启麦克风');
  $('mic-label').textContent=starting?'正在准备 · 点击取消':on?'关闭麦克风':data.voice.status==='finishing'?'正在收尾…':'开启麦克风';
  $('phase').textContent=data.closing?'正在结束会话…':data.voice.status==='loading'?'首次加载本地语音模型…':data.voice.status==='warming'?'正在预热本地语音模型 · 未开启录音':live?(phases[live.phase]||live.phase):'边说边转写，指令明确时开始执行';
  const availability=live?.tool_availability, toolNotes=[];
  if(availability?.execution_mode==='computer_use') toolNotes.push('仅 Computer Use · 浏览器也通过应用界面操作');
  if(availability?.browser_connected===false) toolNotes.push('Browser Use 未连接，网页专用工具暂不可用');
  if(availability?.protected_surfaces?.length) toolNotes.push('当前页面是录音工作台，已禁止修改');
  for(const error of availability?.discovery_errors || []) toolNotes.push(error);
  $('tool-status').textContent=toolNotes.join(' · ');
  $('tool-status').hidden=!toolNotes.length;
  $('models').textContent=`${data.choice_model} · ${data.text_model}`;
  $('input-version').textContent=live?`当前 v${live.version}`:'尚无输入';
  $('trace').disabled=!data.trace_path;
  $('trace-path').textContent=data.trace_path?`本机记录：${data.trace_path}`:'';
  const settings=data.voice.capture_settings || {}, settingNames={echoCancellation:'回声消除',noiseSuppression:'降噪',autoGainControl:'自动增益',sampleRate:'采集采样率',channelCount:'声道数',audioContextSampleRate:'处理采样率'};
  $('capture-settings').textContent=Object.keys(settings).length
    ? '浏览器实际报告：'+Object.entries(settingNames).map(([key,label])=>`${label} ${settings[key]===undefined?'未报告':typeof settings[key]==='boolean'?(settings[key]?'开启':'关闭'):settings[key]}`).join(' · ')
    : '麦克风配置尚未上报。降噪和回声消除不区分说话人。';
  if(live?.error && live.error!==lastError) showError(live.error);
  const version=live?`${data.trace_path}/${live.version}`:'';
  if(version!==renderedVersion) {
    renderedVersion=version; $('transcript').replaceChildren();
    for(const segment of live?.segments || []) {
      const row=node('div',segment.text,'utterance'+(segment.final?'':' partial'));
      row.append(node('small',`v${segment.version} · ${segment.final?'已定稿':'转写中'}`)); $('transcript').append(row);
    }
    if(!live?.segments.length) $('transcript').append(node('p','你的话会显示在这里。可以边说边改口。','empty'));
    $('transcript').scrollTop=$('transcript').scrollHeight;
  }
  const history=JSON.stringify(live?.history || []);
  if(history!==renderedHistory) {
    renderedHistory=history; $('history').replaceChildren();
    (live?.history || []).forEach((action,index)=>{
      const row=node('div','','action'), body=node('div',''); row.append(node('span',String(index+1).padStart(2,'0'),'number'));
      const operation={fill:'填写',click:'点击',inspect:'读取',scroll:'滚动',select:'选择'}[action.kind] || action.kind;
      const effect=action.status==='uncertain'?'结果不确定':action.observed_change===false?'界面未见变化':action.observed_change===true?'界面有变化':'';
      body.append(node('strong',`${action.app} · ${action.channel} · ${operation}`),node('p',`${action.label}${action.text!=null?' → '+action.text:''}${effect?'（'+effect+'）':''}`));
      row.append(body); $('history').append(row);
    });
    const last=live?.history.at(-1); $('activity').textContent=last?`${last.app} · ${last.kind==='inspect'?'已读取':last.label}${last.text?' → '+last.text:''}`:'';
  }
  $('observations').replaceChildren();
  for(const [surface,page] of Object.entries(live?.observations || {})) $('observations').append(node('p',`${page.title} · ${surface}`),node('pre',page.text));
  $('events').textContent=(live?.events || []).slice(-45).map(e=>`${(e.ms/1000).toFixed(2)}s  ${e.type}${e.error?'  '+e.error:''}${e.reason?'  '+e.reason:''}${e.operation?'  '+e.operation:''}${e.latency_ms!=null?'  '+e.latency_ms+'ms':''}`).join('\n');
  renderDashboard(live?.dashboard);
  if(capture && data.voice.status==='error') abortCapture(new Error('语音进程已停止'));
}
function renderDashboard(d) {
  const dashboardKey=JSON.stringify([d,selectedDecision,state?.session?.version]);
  if(dashboardKey===renderedDashboard) return;
  renderedDashboard=dashboardKey;
  const counts=d?.counts || {}, timing=d?.latency || {}, cost=d?.cost;
  const metrics=[['模型请求',counts['model.start']||0],['HTTP 尝试',counts['model.http.start']||0],['已返回操作',counts['action.returned']||0],
    ['回改检查',(counts.pending||0)+(counts['pending.dispatch']||0)],['已上报费用',cost?.reported_calls?'$'+cost.reported_usd.toFixed(6):'—'],
    ['输入 / 输出 token',`${d?.tokens.input||0} / ${d?.tokens.output||0}`]];
  $('metrics').replaceChildren();
  for(const [label,value] of metrics) {const item=node('div','','metric'); item.append(node('span',label),node('strong',String(value))); $('metrics').append(item);}
  $('metrics').title=cost?`费用仅覆盖 ${cost.reported_calls}/${cost.completed_calls} 次已完成请求；未估算缺失费用。`:'';
  const decisions=d?.decisions || [], decision=decisions.find(x=>x.seq===selectedDecision) || decisions.at(-1);
  $('decision-history').replaceChildren();
  const liveButton=node('button','实时'); liveButton.className=selectedDecision===null?'chosen':'';
  liveButton.onclick=()=>{selectedDecision=null; renderDashboard(state?.session?.dashboard);}; $('decision-history').append(liveButton);
  for(const item of decisions.slice(-5)) {const button=node('button',`#${item.seq} ${item.operation}`); button.className=item.seq===selectedDecision?'chosen':'';
    button.onclick=()=>{selectedDecision=item.seq; renderDashboard(state?.session?.dashboard);}; $('decision-history').append(button);}
  $('decision-meta').textContent=decision?`输入 v${decision.version} · ${decision.latency_ms} ms`:'';
  const meanings={LISTEN:'继续聆听 · 本轮未选择动作',BLOCKED:'尚未完成 · 暂无可执行的推进方式',SWITCH_APP:'选择应用或标签页',WAIT:'等待界面变化'};
  const behind=decision && (state?.session?.version || 0)>decision.version;
  $('input-disposition').textContent=decision
    ? `${meanings[decision.operation] || '选择操作 · '+decision.operation}${behind?'（已有更新的转写）':''}`
    : '等待首次决策 · 尚未判断输入';
  $('decision-speech').textContent=decision?.input_preview?.length
    ? '本次决策的最近输入：\n'+decision.input_preview.map(s=>`v${s.version} ${s.truncated?'…':''}${s.text}`).join('\n')
    : decision?'本次决策输入快照尚未上报。':'';
  const outcomes={CANCELLED:'结果复核：当前任务已取消，继续聆听新指令。',SATISFIED:'结果复核：模型判断当前请求已满足；仍需核对实际结果。',UNSATISFIED:'结果复核：模型判断当前结果未满足请求，已重新选择。',UNKNOWN:'结果复核：证据不足，无法判断是否完成。'};
  $('outcome-review').textContent=outcomes[decision?.outcome_status] || '';
  const execution=d?.last_execution;
  const executionLabels={dispatch:'已派发 · 等待工具返回',returned:'工具已返回 · 尚不代表任务完成',uncertain:'已派发 · 结果不确定，请核对应用',not_dispatched:'尚未派发 · 本次操作未执行'};
  $('execution-status').textContent=execution
    ? `${executionLabels[execution.status] || execution.status}${execution.label?' · '+execution.label:''}` : '';
  $('probabilities').replaceChildren();
  for(const chart of decision?.charts || []) {
    const section=node('div','','chart'); section.append(node('h3',chart.title),node('p',`confidence ${(chart.confidence*100).toFixed(1)}%`,'hint'));
    for(const item of chart.entries) {
      const row=node('div','','probability'+(item.selected?' selected':'')), label=node('span',item.label,'candidate'); label.title=item.label;
      const track=node('div','','track'), fill=node('div','','fill'); fill.style.width=`${Math.max(0,Math.min(1,item.probability))*100}%`;
      track.append(fill); row.append(label,track,node('span',(item.probability*100).toFixed(1)+'%','percent')); section.append(row);
    }
    $('probabilities').append(section);
  }
  if(!decision) $('probabilities').append(node('p','等待模型返回候选概率','empty'));
  const pending=d?.last_pending;
  $('pending-info').textContent=pending?`最近改口检查 · 输入 v${pending.version} · ${pending.valid?'保留待执行动作':'丢弃待执行动作'}${pending.text_review==='REWRITE'?' · 文字已过时，需要重新生成':pending.text_review==='KEEP'?' · 文字仍符合当前要求':''} · ${pending.latency_ms} ms`:'';
  $('generated-text').textContent=d?.last_text?`文本模型输出（v${d.last_text.version}）：${d.last_text.value}`:'';
  $('latencies').replaceChildren();
  const names={jev:'Jev 请求',text_model:'文本模型', 'asr.partial':'ASR 部分转写', observe:'观察循环（含缓存）',native_state:'CU 状态读取',execute:'工具派发 → 返回', input_to_dispatch:'输入版本 → 派发', input_to_return:'输入版本 → 返回'};
  $('latencies').append(node('div','最近一次 / P50（最多 1024 次）','hint'));
  for(const [key,label] of Object.entries(names)) {const value=timing[key], row=node('div','','timing'); row.append(node('span',label),node('strong',value?`${value.last} / ${value.p50} ms`:'—')); $('latencies').append(row);}
  $('asr-debug').replaceChildren(); const asr=d?.last_asr;
  if(asr) {
    const line=node('div','','asr-text');
    if(asr.stable_text != null) line.append(node('span',asr.stable_text,'stable'),node('span',asr.volatile_text||'','volatile'));
    else line.append(node('span',asr.text || '','stable'));
    $('asr-debug').append(line,node('p',`ASR v${asr.base_revision} → v${asr.revision} · 回改 ${asr.rollback_chars||0} 字 · ${asr.latency_ms} ms`,'hint'));
  } else $('asr-debug').append(node('p','等待转写；绿色为暂稳前缀，橙色为本次可变部分。','hint'));
}
async function poll() {
  let retryDelay=0;
  try {
    const after=state?.state_cursor;
    render(await api('state'+(after?'?after='+encodeURIComponent(after):'')));
  } catch(error) {showError(error); retryDelay=600; if(capture) await abortCapture(error);}
  setTimeout(poll,retryDelay);
}
async function release(c) {
  if(!c) return; clearTimeout(c.timer); clearTimeout(c.flushTimeout); clearInterval(c.clock);
  c.stream?.getTracks().forEach(track=>track.stop()); c.source?.disconnect(); c.worklet?.disconnect();
  if(c.context && c.context.state!=='closed') await c.context.close(); $('timer').textContent='';
}
async function abortCapture(error) {
  const c=capture; if(!c) return; capture=null; c.failed=true;
  // Stop dispatch first; do not wait for microphone cleanup or ASR finalization.
  const paused=api('pause',{}).catch(()=>{}); await release(c); await paused;
  try {await api('voice/cancel',{});} catch(_) { /* Heartbeat also stops execution. */ }
  if(error) showError(error); if(state) render(state);
}
async function finishCapture() {
  const c=capture; if(!c || c.finishing) return; c.finishing=true; if(state) render(state);
  try {
    await api('pause',{});
    await new Promise((resolve,reject)=>{c.flushed=resolve; c.flushTimeout=setTimeout(()=>reject(new Error('音频结束失败')),3000); c.worklet.port.postMessage('finish');});
    clearTimeout(c.flushTimeout); await c.pending; if(c.failed) return;
    await api('voice/finish',{recording_id:c.id}); await release(c); capture=null;
  } catch(error) {await abortCapture(error);}
}
async function startCapture() {
  starting=true; clearError(); const own=++ticket; let stream,context; if(state) render(state);
  try {
    if(!navigator.mediaDevices?.getUserMedia) throw new Error('当前浏览器不支持麦克风，请在 Chrome 打开这个地址。');
    stream=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true,autoGainControl:true}});
    if(own!==ticket) {stream.getTracks().forEach(t=>t.stop()); return;}
    context=new AudioContext(); await context.resume(); preparing={stream,context};
    await context.audioWorklet.addModule('/pcm-worklet.js');
    if(own!==ticket) return;
    const trackSettings=stream.getAudioTracks()[0].getSettings();
    const captureSettings={audioContextSampleRate:context.sampleRate};
    for(const key of ['echoCancellation','noiseSuppression','autoGainControl','sampleRate','channelCount']) {
      if(trackSettings[key]!==undefined) captureSettings[key]=trackSettings[key];
    }
    const recording=await api('voice/start',{capture_settings:captureSettings}), deadline=Date.now()+90000;
    let readyCursor;
    while(own===ticket) {
      const data=await api('state'+(readyCursor?'?after='+encodeURIComponent(readyCursor):''));
      readyCursor=data.state_cursor; render(data);
      if(!data.connected || data.closing || data.voice.status==='error') throw new Error(data.session?.error || '录音准备失败');
      if(data.voice.status==='recording' && data.voice.recording_id===recording.recording_id) break;
      if(Date.now()>deadline) throw new Error('ASR 加载超时，请检查本地模型');
    }
    if(own!==ticket) {await api('voice/cancel',{}); return;}
    const c={stream,context,id:recording.recording_id,sequence:0,queued:0,pending:Promise.resolve(),started:Date.now(),failed:false};
    capture=c; preparing=null; c.source=context.createMediaStreamSource(stream); c.worklet=new AudioWorkletNode(context,'pcm16');
    const silent=context.createGain(); silent.gain.value=0; c.source.connect(c.worklet).connect(silent).connect(context.destination);
    c.worklet.port.onmessage=({data})=>{
      if(data.type==='flushed') {c.flushed?.(); return;} if(c.failed || data.type!=='pcm') return;
      if(++c.queued>20) {abortCapture(new Error('音频上传积压，已停止，没有跳过音频继续。')); return;}
      const pcm=btoa(String.fromCharCode(...new Uint8Array(data.bytes))), sequence=c.sequence++;
      c.pending=c.pending.then(async()=>{if(!c.failed) await api('voice/audio',{recording_id:c.id,sequence,pcm_s16le:pcm}); c.queued--;}).catch(error=>{abortCapture(error);});
    };
    stream.getAudioTracks()[0].onended=()=>{if(capture===c && !c.finishing) abortCapture(new Error('麦克风已断开'));};
    c.clock=setInterval(()=>{$('timer').textContent=`已开启 ${Math.floor((Date.now()-c.started)/1000)} 秒`;},500);
  } catch(error) {
    await release({stream,context}); preparing=null;
    try {await api('voice/cancel',{});} catch(_) { /* Startup may have failed. */ }
    if(own===ticket) showError(error);
  } finally {if(own===ticket) starting=false; if(state) render(state);}
}
$('record').onclick=async()=>{
  if(starting) {ticket++; starting=false; const p=preparing; preparing=null; await api('pause',{}).catch(()=>{}); await release(p); await api('voice/cancel',{}).catch(()=>{}); if(state) render(state); return;}
  if(capture) await finishCapture(); else await startCapture();
};
$('text-form').onsubmit=async event=>{
  event.preventDefault(); const text=$('text').value; if(!text.trim()) return; $('send').disabled=true;
  try {clearError(); if(!state?.connected) await api('connect',{}); await api('input',{text}); if(!state?.session?.enabled) await api('resume',{}); $('text').value='';} catch(error) {showError(error);} finally {$('send').disabled=false;}
};
$('reset').onclick=async()=>{try {if(capture) await abortCapture(); await api('disconnect',{}); clearError();} catch(error) {showError(error);}};
$('trace').onclick=async()=>{
  try {const response=await fetch('/api/trace',{headers:{'X-Voice-Token':token}}); if(!response.ok) throw new Error('Trace 下载失败');
    const url=URL.createObjectURL(await response.blob()),link=document.createElement('a'); link.href=url; link.download='jev-voice-trace.jsonl'; link.click(); setTimeout(()=>URL.revokeObjectURL(url),1000);
  } catch(error) {showError(error);}
};
window.addEventListener('pagehide',()=>{if(state?.connected) fetch('/api/pause',{method:'POST',keepalive:true,headers:{'X-Voice-Token':token,'Content-Type':'application/json'},body:'{}'}).catch(()=>{});});
poll();
